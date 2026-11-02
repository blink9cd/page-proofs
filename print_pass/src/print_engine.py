"""The engine: print every state of every target to PDF through Playwright, and record each.

`run_targets` is the one entry point. It launches each browser the first time a state needs
it (the engine the settings name, and installed Chrome forced dark for any `native-dark`
state, since the pinned shell ignores that flag), runs the states in file order, and closes
every browser however the run ends. Firefox is no engine of this module's: it prints
through geckodriver's WebDriver Print, and handed here it is refused as the programming
error it is. Every launch and close runs inside the watchdog, as every call a state makes
does. What one state does is `print_engine_state`'s; what the engine may call on
Playwright, and hands it, is `print_playwright`'s; what it is given and records is
`print_record`'s, and what counts as a problem `print_judge`'s. This module re-exports the
public names of all four, so callers import the engine from one place.

Entry point assumptions: Playwright is never imported here. The caller passes the real
synchronous API (or a fake) in, and a base URL on the loopback address its own server binds;
every request off that address is aborted and reported.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from typing import Final, TypeGuard

from print_engine_state import SETTLE_POLL_MS, TIMED_CALL_BOUND_FACTOR, run_state
from print_judge import EMULATED_DARK_WARNING
from print_playwright import (
    LETTER,
    LOOPBACK_HOST,
    MARGIN,
    MARGIN_INCHES,
    MS_PER_SECOND,
    BrowserLike,
    BrowserTypeLike,
    PlaywrightLike,
    context_options,
    is_loopback,
    print_options,
)
from print_record import (
    Backgrounds,
    Engine,
    EngineError,
    EngineSettings,
    MadePdf,
    NetworkRecord,
    Problem,
    StateRecord,
    engine_errors,
    pdf_name,
)
from print_targets import BACKGROUNDS, NATIVE_DARK, State, TargetsFile
from print_watchdog import Bounder

__all__ = [
    "EMULATED_DARK_WARNING",
    "LETTER",
    "MARGIN",
    "MARGIN_INCHES",
    "SETTLE_POLL_MS",
    "TIMED_CALL_BOUND_FACTOR",
    "EngineError",
    "EngineSettings",
    "MadePdf",
    "NetworkRecord",
    "PlaywrightLike",
    "Problem",
    "StateRecord",
    "context_options",
    "effective_settings",
    "is_loopback",
    "pdf_name",
    "print_options",
    "run_targets",
]

# The engines' names. Installed Google Chrome's is also its Playwright channel.
_SHELL: Final = "shell"
_CHROME: Final = "chrome"

# The engines this module launches. Firefox prints through geckodriver's WebDriver Print,
# on a path of its own, and is never launched here.
_PLAYWRIGHT_ENGINES: Final = (_SHELL, _CHROME)

# The switch installed Chrome is launched with for a `native-dark` state: a real dark setting.
_FORCE_DARK_MODE: Final = "--force-dark-mode"

# Every browser launches with no hostname resolving, the backstop for what no route sees: a
# `<link rel="preconnect">` or a DNS prefetch opens a connection, or sends a lookup, that no
# request carries. Mapped to ~NOTFOUND, every hostname, `localhost` included, fails before a
# query leaves the machine. Chromium applies the rules to IP literals as well, so the one
# exclusion names the address the site is served on; without it the site itself fails.
_NO_NAME_RESOLUTION: Final = (
    f"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE {LOOPBACK_HOST}"
)

# How near the window, on each side, a lazy image starts loading: a million CSS pixels,
# about a thousand printed pages, the margin Firefox's sessions are given too
# (`_LAZY_LOAD_MARGIN`, in `gecko_prefs`).
_LAZY_LOAD_MARGIN_PX: Final = 1_000_000

# The connection types Chromium keys its lazy-image margin by, one setting each. The margin
# a launch runs under is the one for the connection type it reports, so every type is set.
_CONNECTION_TYPES: Final = ("Unknown", "Offline", "Slow2G", "2G", "3G", "4G")

# Every browser launches with Chromium's lazy-image margin widened to `_LAZY_LOAD_MARGIN_PX`.
# Chromium starts loading a lazy image only once it is within its own margin of the window,
# so a carousel's slides, clipped far to the side in a horizontal scroller, never started:
# the probe read them incomplete and the settle waited out the timeout, while the print
# loaded them anyway. Where a page's script turns a stacked column of images into such a
# scroller after load, it is a race the script usually wins (measured: 7 of 8 runs on
# installed Chrome left the last slides incomplete, and slides 4,000 px apart stayed so in
# every run, on the shell and installed Chrome alike). With it, a lazy image within the
# margin loads as an eager one, so the probe reads it as the print will hold it, and a
# broken one is still caught (measured: 8 of 8 runs complete on both, and a missing slide
# fetched, answered 404 and read broken). The Chromium counterpart of Firefox's
# `dom.lazy-loading.margin.*` prefs. The older `lazyImageLoadingDistanceThresholdPx*` names
# are ignored by current Chrome (measured). They travel in `_BLINK_SETTINGS`, below.
_LAZY_IMAGE_SETTINGS: Final = tuple(
    f"lazyLoadingImageMarginPx{kind}={_LAZY_LOAD_MARGIN_PX}"
    for kind in _CONNECTION_TYPES
)

# The settings Playwright's own headless launch passes in a `--blink-settings` of its own,
# ahead of the caller's switches: a hovering, fine pointer, as a desktop page expects.
# Chromium honors only the LAST `--blink-settings` (measured: a second switch replaced the
# first's hover and pointer, on the shell and installed Chrome alike), so a launch passing
# its own would silently drop these unless it repeats them.
_PLAYWRIGHT_HEADLESS_SETTINGS: Final = (
    "primaryHoverType=2",
    "availableHoverTypes=2",
    "primaryPointerType=4",
    "availablePointerTypes=4",
)

# The one `--blink-settings` every launch carries: Playwright's headless settings, kept,
# then the widened lazy-image margin.
_BLINK_SETTINGS: Final = "--blink-settings=" + ",".join(
    (*_PLAYWRIGHT_HEADLESS_SETTINGS, *_LAZY_IMAGE_SETTINGS)
)

# The switches every launch carries, whichever browser it starts.
_EVERY_LAUNCH: Final = (_NO_NAME_RESOLUTION, _BLINK_SETTINGS)

# What a launch failure names, per browser a run can launch.
_LAUNCH_NAMES: Final = {
    _SHELL: "the shell engine (Playwright's pinned headless shell)",
    _CHROME: "the chrome engine (installed Google Chrome)",
    NATIVE_DARK: f"installed Google Chrome with {_FORCE_DARK_MODE}, for a {NATIVE_DARK} state",
}


def effective_settings(
    settings: EngineSettings, targets: TargetsFile
) -> EngineSettings:
    """`settings` with the viewport and backgrounds a targets file sets in their place.

    A key the file sets wins over the matching setting; a key it leaves out keeps the
    setting.

    Raises:
        ValueError: the file's backgrounds is not one the engine prints.
    """
    viewport = settings.viewport if targets.viewport is None else targets.viewport
    backgrounds = (
        settings.backgrounds
        if targets.backgrounds is None
        else _backgrounds(targets.backgrounds)
    )
    return dataclasses.replace(settings, viewport=viewport, backgrounds=backgrounds)


def run_targets(
    playwright: PlaywrightLike,
    base_url: str,
    targets: TargetsFile,
    settings: EngineSettings,
    *,
    bounder: Bounder,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[StateRecord, ...]:
    """Print every state of every target in `targets`, served at `base_url`; one record each.

    Raises:
        ValueError: `settings.engine` is not one Playwright launches, or
            `settings.timeout_s` is not a bound Playwright and the watchdog can keep.
        EngineError: the browser failed a call other than an action's or the probe's: a
            launch, opening or closing a state's context, a page load, a wait, a print, or a
            browser close. It names the state or the browser.
    """
    _check_engine(settings.engine)
    _check_timeout(settings.timeout_s)
    effective = effective_settings(settings, targets)
    records: list[StateRecord] = []
    with ExitStack() as closer:
        launcher = _Launcher(playwright.chromium, effective, bounder, closer)
        for target in targets.targets:
            for state in target.states:
                browser, engine = launcher.browser_for(state)
                records.append(
                    run_state(
                        browser,
                        engine,
                        target,
                        state,
                        effective,
                        base_url=base_url,
                        expected_hosts=targets.expected_offsite_hosts,
                        bounder=bounder,
                        clock=clock,
                    )
                )
    return tuple(records)


def _backgrounds(value: str) -> Backgrounds:
    """`value` as one of the backgrounds settings the engine prints."""
    if not _is_backgrounds(value):
        raise ValueError(
            f"backgrounds must be one of {', '.join(BACKGROUNDS)}, not {value!r}"
        )
    return value


def _is_backgrounds(value: str) -> TypeGuard[Backgrounds]:
    """Whether `value` is one of the backgrounds settings `print_targets` defines."""
    return value in BACKGROUNDS


def _check_engine(engine: Engine) -> None:
    """Refuse an engine Playwright does not launch, before anything launches.

    A programming error, never a launch of some Chromium in its place: no browser this
    module launches could print a run recorded as Firefox's, which prints through
    geckodriver on a path of its own.
    """
    if engine not in _PLAYWRIGHT_ENGINES:
        raise ValueError(_not_launched_here(engine))


def _not_launched_here(key: str) -> str:
    """Why `key` is refused: no engine Playwright launches here."""
    return (
        f"{key} is not an engine Playwright launches here: "
        f"only {', '.join(_PLAYWRIGHT_ENGINES)}, and {NATIVE_DARK} through installed Chrome"
    )


def _check_timeout(seconds: float) -> None:
    """Refuse a timeout before anything launches.

    Zero is no timeout at all to Playwright, so a load could hang for good. A negative one,
    NaN, or one that, multiplied by `TIMED_CALL_BOUND_FACTOR` for a call handed it, passes
    `threading.TIMEOUT_MAX`, gives a bound the watchdog refuses, but only once the first
    bounded call is reached.
    """
    if not 0 < seconds * TIMED_CALL_BOUND_FACTOR <= threading.TIMEOUT_MAX:
        raise ValueError(
            "timeout_s must be more than 0 and at most "
            f"{threading.TIMEOUT_MAX / TIMED_CALL_BOUND_FACTOR} seconds, not {seconds!r}"
        )


class _Launcher:
    """The browsers of one run, each launched on first need and closed by the run's stack."""

    def __init__(
        self,
        chromium: BrowserTypeLike,
        settings: EngineSettings,
        bounder: Bounder,
        closer: ExitStack,
    ) -> None:
        """Launch nothing yet; `closer` closes each browser as the run ends."""
        self._chromium = chromium
        self._settings = settings
        self._bounder = bounder
        self._closer = closer
        self._browsers: dict[str, BrowserLike] = {}

    def browser_for(self, state: State) -> tuple[BrowserLike, Engine]:
        """The browser `state` prints in, and the engine it is recorded under."""
        native_dark = state.color_scheme == NATIVE_DARK
        key = NATIVE_DARK if native_dark else self._settings.engine
        if key not in self._browsers:
            self._browsers[key] = self._launch(key)
        engine: Engine = _CHROME if native_dark else self._settings.engine
        return self._browsers[key], engine

    def _launch(self, key: str) -> BrowserLike:
        """Launch the browser `key` names, inside the watchdog, and have the run close it.

        The launch is handed the timeout and bounded at `TIMED_CALL_BOUND_FACTOR` times it.
        An absent Chrome is an `EngineError` naming the browser: exit 2, never a crash.
        """
        timeout_s = self._settings.timeout_s
        with (
            engine_errors(f"could not launch {_LAUNCH_NAMES[key]}"),
            self._bounder.bound(
                timeout_s * TIMED_CALL_BOUND_FACTOR, f"chromium.launch {key}"
            ),
        ):
            browser = _launch_call(self._chromium, key, timeout_s * MS_PER_SECOND)
        self._closer.callback(self._close, browser, key)
        return browser

    def _close(self, browser: BrowserLike, key: str) -> None:
        """Close one browser inside the watchdog: `close` takes no timeout of its own.

        It is bounded at `TIMED_CALL_BOUND_FACTOR` times the timeout, as a context's close
        is: installed Chrome's has taken just over 30 s under load, and a bound at the
        timeout would abandon the run at its very end. A browser that fails to close is an
        `EngineError` naming it; the run's stack still closes every other.
        """
        with (
            engine_errors(f"could not close {_LAUNCH_NAMES[key]}"),
            self._bounder.bound(
                self._settings.timeout_s * TIMED_CALL_BOUND_FACTOR,
                f"browser.close {key}",
            ),
        ):
            browser.close()


def _launch_call(chromium: BrowserTypeLike, key: str, timeout_ms: float) -> BrowserLike:
    """Launch the browser `key` names: the shell, installed Chrome, or Chrome forced dark.

    Each resolves no hostname at all (`_NO_NAME_RESOLUTION`), and loads a lazy image within
    `_LAZY_LOAD_MARGIN_PX` of the window as an eager one while keeping Playwright's own
    headless hover and pointer (`_BLINK_SETTINGS`).

    Raises:
        ValueError: `key` names none of the three. The shell is named, never the fallback,
            so a key meant for another engine is refused rather than launched as the shell.
    """
    if key == NATIVE_DARK:
        return chromium.launch(
            channel=_CHROME,
            args=[_FORCE_DARK_MODE, *_EVERY_LAUNCH],
            timeout=timeout_ms,
        )
    if key == _CHROME:
        return chromium.launch(
            channel=_CHROME, args=[*_EVERY_LAUNCH], timeout=timeout_ms
        )
    if key == _SHELL:
        return chromium.launch(args=[*_EVERY_LAUNCH], timeout=timeout_ms)
    raise ValueError(_not_launched_here(key))
