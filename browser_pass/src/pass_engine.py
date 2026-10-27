"""Drive the pinned engines through Playwright: one rendering at a time, measured honestly.

Each rendering gets a fresh browser context, so no storage, cache or mode choice carries over
from the last one, and its emulation is set on the engine itself: the viewport, the color scheme,
reduced motion OFF, and scripting through the engine's own switch. Then, in order:

1. **Load**, with every console error, uncaught page error, HTTP >= 400 response and failed
   request collected from the first byte (`pass_listeners`: a page with no file is excused its
   own expected 404, and nothing else) -- and, with scripts on, every Content Security Policy
   violation, through the collector `pass_csp` puts on the context before its page.
2. **Settle**: the layout signature is polled until it holds still for a quiet window. At `load`
   a scripted page can still be building itself, and an animation can still be moving -- so
   before each poll every animation still running on the document's timeline, in the
   document or in any open shadow root, is brought to rest (`pass_motion`), and a paced or
   endless one is measured at rest, not in a pause.
3. **Reach the state**, for a rendering of one (`pass_states`): its actions run in order, then
   the page settles again. Its stored values were set on the context before the load.
4. **Read the emulation back** from inside the page. Any disagreement with what was asked for
   is an instrument fault, and the rendering is not measured.
5. **Measure** the four layout detectors and the CSP detector, and take the per-element digest.
6. **Prove** (with `--prove`): plant one fault per detector and read every detector again, the
   CSP plant last, so the policy it adds refuses nothing the page restyles while they are read.
7. **Load it again for late shifts** (Chromium, scripts on, never a state): in a context of its
   own, so the detectors above are neither slowed nor perturbed -- see `pass_late_load`.

**Firefox reads its per-user data folder at startup even when handed a throwaway profile**,
and on macOS that folder is the machine user's real Firefox profile store. So Firefox is
launched with that folder, and its local twin, redirected into the run's scratch folder: it
never touches a real profile. Chromium and WebKit take a throwaway profile from Playwright and need nothing extra.

**An isolation** (`pass_extension`) reaches the engines through `launch` alone: under
`--loopback-only` each engine is launched with its egress guard, and an engine with an extension
gets a `pass_extension_launch.PersistentBrowser` in place of a browser, whose every context is a
fresh persistent launch with the extension confirmed loaded. `render` asks either for its
contexts the same way, and an extension that did not load is the rendering's instrument fault.

Entry point assumptions: Playwright is importable (the repository's `browser` extra), and
`PLAYWRIGHT_BROWSERS_PATH` names the folder holding its pinned builds before `run_matrix` starts
Playwright, which reads it once at startup.
"""

from __future__ import annotations

import itertools
import os
import time
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import Any

import pass_csp
import pass_probe
from pass_checks import (
    CSP_VIOLATION,
    Observation,
    emulation_faults,
    findings_of,
    listener_plants_arrived,
    merged_findings,
    page_state_from,
    plant_took_from,
    plant_verdicts,
    poll_until,
    wait_until_settled,
)
from pass_digest import parse_digest
from pass_extension import NO_ISOLATION, Isolation
from pass_extension_launch import ExtensionFault, PersistentBrowser, extra_bound
from pass_guard import guard_options
from pass_late_load import LateOutcome, measure_late_shift
from pass_listeners import Listeners
from pass_motion import NO_MOTION, Motion, motion_from, tally
from pass_probe import (
    MS_PER_SECOND,
    POLL_INTERVAL_SECONDS,
    pause,
    probe,
)
from pass_report import RenderingResult
from pass_shift import LATE_SHIFT, NOT_MEASURED, LateShift, late_findings, not_measured
from pass_states import run_actions, state_bound, state_for, storage_state
from pass_targets import Rendering, State, server_path
from pass_watchdog import Bounder, Watchdog
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    StorageState,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError

#: What a rendering's late-shift record says until its own load has run: never a zero.
_LATE_NOT_YET = LateShift(NOT_MEASURED, reason="the page load was not measured")

#: What a trackpad Mac shows. A headless Gecko has drawn a CLASSIC 15px scrollbar, which lays a
#: 390px viewport out at 375; the read-back refuses that either way, and this avoids it.
_FIREFOX_PREFS: Mapping[str, str | float | bool] = {"ui.useOverlayScrollbars": 1}

#: One rendering's bound, in `--timeout`s plus a margin. Each step may take one `--timeout`, and
#: the probe calls between them are quick: the page load, its settling, its proof and its CSP
#: plant's wait, then the late-script load's navigation, its hold until the first paint, the rest
#: of its load, its wait until the page is presentable, its settling and its proof.
RENDER_BOUND_TIMEOUTS = 10
RENDER_BOUND_MARGIN_SECONDS = 30.0

#: A browser that has not closed in this long has wedged.
CLOSE_BOUND_SECONDS = 60.0

#: What `launch` hands each engine's renderings: a browser, or one launch per context.
Launched = Browser | PersistentBrowser


class EngineError(RuntimeError):
    """An engine could not be launched -- most often, its pinned build is not installed."""


def playwright_version() -> str:
    """The installed Playwright's version, as its package metadata records it."""
    return metadata.version("playwright")


def rendering_bound(timeout: float, state: State | None = None) -> float:
    """How long one rendering may take, at `timeout` per step, before it is abandoned.

    A rendering of a `state` is given its steps' time on top (`pass_states.state_bound`).
    """
    return (
        RENDER_BOUND_TIMEOUTS * timeout
        + RENDER_BOUND_MARGIN_SECONDS
        + state_bound(state, timeout)
    )


def firefox_env(env: Mapping[str, str], scratch: Path) -> dict[str, str]:
    """`env` with Firefox's per-user data folders redirected into `scratch`."""
    return {
        **env,
        "MOZ_APP_DATA": str(scratch / "firefox-app-data"),
        "MOZ_LOCAL_APP_DATA": str(scratch / "firefox-local-app-data"),
    }


def launch_options(engine: str, scratch: Path, isolation: Isolation) -> dict[str, Any]:
    """Everything `engine` is launched with but its timeout: its own needs, then its guard's."""
    options = guard_options(engine, isolation)
    if engine == "firefox":
        env = firefox_env(os.environ, scratch)
        for folder in ("MOZ_APP_DATA", "MOZ_LOCAL_APP_DATA"):
            Path(env[folder]).mkdir(parents=True, exist_ok=True)
        prefs = {**_FIREFOX_PREFS, **options.get("firefox_user_prefs", {})}
        options.update(env=env, firefox_user_prefs=prefs)
    return options


def launch(
    playwright: Playwright,
    engine: str,
    *,
    scratch: Path,
    timeout: float,
    isolation: Isolation = NO_ISOLATION,
) -> Launched:
    """Launch one engine headless; refuse with the engine's name if it cannot start.

    With an extension for `engine` in `isolation`, nothing launches yet: each context does.
    """
    browser_type = getattr(playwright, engine)
    options = launch_options(engine, scratch, isolation)
    extension = isolation.extension(engine)
    if extension is not None:
        return PersistentBrowser(
            browser_type, extension, scratch=scratch, timeout=timeout, options=options
        )
    try:
        return browser_type.launch(timeout=timeout * MS_PER_SECOND, **options)
    except PlaywrightError as error:
        first_line = str(error).strip().splitlines()[0]
        raise EngineError(f"cannot launch {engine}: {first_line}") from error


def open_context(
    browser: Launched, rendering: Rendering, *, storage: StorageState | None = None
) -> BrowserContext:
    """A fresh context in exactly the state `rendering` asks for, holding `storage` if any.

    Raises:
        ExtensionFault: `browser` launches a context per call, and this one's extension did not
            load.
    """
    return browser.new_context(
        viewport={"width": rendering.width, "height": rendering.height},
        color_scheme="dark" if rendering.mode == "dark" else "light",
        reduced_motion="no-preference",
        java_script_enabled=rendering.scripts == "on",
        device_scale_factor=1,
        storage_state=storage,
    )


def _faulted(rendering: Rendering, motion: Motion, *faults: str) -> RenderingResult:
    """A rendering that could not be measured honestly, with whatever motion it stilled first."""
    return RenderingResult(
        rendering,
        faults,
        findings={},
        verdicts={},
        digest={},
        late_shift=_LATE_NOT_YET,
        motion=motion,
    )


def measure(
    page: Page,
    listeners: Listeners,
    rendering: Rendering,
    base_url: str,
    *,
    timeout: float,
    prove: bool,
    state: State | None = None,
) -> RenderingResult:
    """Load, settle, reach `state` if any, read back, measure and (optionally) prove."""
    try:
        page.goto(
            base_url + rendering.page,
            wait_until="load",
            timeout=timeout * MS_PER_SECOND,
        )
    except PlaywrightError as error:
        return _faulted(
            rendering, NO_MOTION, f"the page did not load: {str(error).splitlines()[0]}"
        )
    fault, motion = _reach(page, state, timeout=timeout)
    if fault is not None:
        return _faulted(rendering, motion, fault)
    faults = emulation_faults(rendering, page_state_from(probe(page, "state")))
    if faults:
        return _faulted(rendering, motion, *faults)
    csp = pass_csp.findings(page, listeners.csp)
    before = listeners.observation(probe(page, "layout"), csp=csp)
    digest = parse_digest(probe(page, "digest"))
    verdicts = (
        _prove(page, listeners, before, rendering, timeout=timeout) if prove else {}
    )
    return RenderingResult(
        rendering, (), findings_of(before), verdicts, digest, _LATE_NOT_YET, motion
    )


def _reach(
    page: Page, state: State | None, *, timeout: float
) -> tuple[str | None, Motion]:
    """Settle the loaded page; for a `state`, run its actions and settle once more.

    Returns the fault that stops the rendering, if one does, and the motion stilled on the way:
    both settles' stillings sum, and what still ran is the second's (`pass_motion.tally`).
    """
    settled, motion = _settle(page, timeout=timeout)
    if not settled:
        return f"the layout was still changing after {timeout:g}s", motion
    if state is None:
        return None, motion
    fault = run_actions(page, state, timeout=timeout)
    if fault is not None:
        return fault, motion
    settled, after = _settle(page, timeout=timeout)
    if not settled:
        fault = (
            f"state {state.name}: the layout was still changing "
            f"{timeout:g}s after its actions"
        )
    return fault, tally(motion, after)


def _settle(page: Page, *, timeout: float) -> tuple[bool, Motion]:
    """Whether the layout held still for the quiet window, and the motion stilled on the way.

    Every read stills first, then signs: an animation that starts mid-settle is caught on the
    next poll, and a seek that moves a box changes the signature, so the window restarts.
    """
    motion = NO_MOTION

    def read() -> object:
        nonlocal motion
        motion = tally(motion, motion_from(probe(page, "stillMotion")))
        return probe(page, "signature")

    settled = wait_until_settled(
        read,
        pause=pause(page),
        clock=time.monotonic,
        # Read off the module now, not copied in at import: the live suites set a shorter
        # window there, and one place is the only place to set it.
        quiet=pass_probe.SETTLE_QUIET_SECONDS,
        within=timeout,
        interval=POLL_INTERVAL_SECONDS,
    )
    return settled, motion


def _prove(
    page: Page,
    listeners: Listeners,
    before: Observation,
    rendering: Rendering,
    *,
    timeout: float,
) -> dict[str, str]:
    """Plant one fault per detector, wait for the reported ones to arrive, and judge each."""
    took = plant_took_from(probe(page, "plant"))
    scripts_on = rendering.scripts == "on"
    poll_until(
        lambda: listener_plants_arrived(
            listeners.observation(_NO_LAYOUT), scripts_on=scripts_on
        ),
        pause=pause(page),
        clock=time.monotonic,
        within=timeout,
        interval=POLL_INTERVAL_SECONDS,
    )
    after = listeners.observation(probe(page, "layout"))
    # Last, once every other plant is read: its policy then refuses no restyle they could see.
    caught = pass_csp.prove(page, listeners.csp, timeout=timeout)
    return plant_verdicts(before, after, took, scripts_on=scripts_on, csp_caught=caught)


#: A layout record with nothing in it, for polling on the listeners alone.
_NO_LAYOUT = {
    "overflow": 0,
    "pastRightEdge": [],
    "hiddenRendered": [],
    "smallTargets": [],
}


def render(
    browser: Launched,
    rendering: Rendering,
    base_url: str,
    *,
    timeout: float,
    prove: bool,
    state: State | None = None,
    require_csp: bool = False,
    missing: bool = False,
) -> RenderingResult:
    """One rendering in its own context, closed afterwards whatever happened, then late shifts.

    A rendering of a `state` has its stored values in that context, and no late-script load.
    With `require_csp`, a page load declaring no policy is a finding (`pass_csp`); a payload its
    CSP collector was sent that the listener never sends is the rendering's instrument fault.
    `missing` says the page has no file, so its own 404 is expected (`pass_listeners`).
    """
    try:
        context = open_context(
            browser, rendering, storage=storage_state(state, base_url)
        )
    except ExtensionFault as error:
        return _faulted(rendering, NO_MOTION, str(error))
    try:
        csp = pass_csp.watch(context, rendering, base_url, require_csp=require_csp)
        page = pass_csp.open_page(context, csp)
        listeners = Listeners(
            page, base_url, csp, missing=rendering.page if missing else None
        )
        result = measure(
            page,
            listeners,
            rendering,
            base_url,
            timeout=timeout,
            prove=prove,
            state=state,
        )
    except pass_csp.CspWatchError as error:
        result = _faulted(rendering, NO_MOTION, pass_csp.csp_fault(error))
    finally:
        context.close()
    if result.faults:
        return result  # not measured, and neither is anything a second load would show
    return with_late_shift(
        result,
        _late_outcome(browser, rendering, base_url, timeout=timeout, prove=prove),
    )


def _late_outcome(
    browser: Launched,
    rendering: Rendering,
    base_url: str,
    *,
    timeout: float,
    prove: bool,
) -> LateOutcome:
    """The late-script load in a context of its own, or why this rendering gets none."""
    skipped = not_measured(rendering)
    if skipped is not None:
        return LateOutcome(skipped, verdict=skipped.status if prove else None)
    try:
        context = open_context(browser, rendering)
    except ExtensionFault as error:
        fault = f"{LATE_SHIFT}: {error}"
        return LateOutcome(LateShift(NOT_MEASURED, reason=fault), (fault,))
    try:
        csp = pass_csp.watch(context, rendering, base_url)
        return measure_late_shift(
            context, rendering, base_url, timeout=timeout, prove=prove, csp=csp
        )
    finally:
        context.close()


def with_late_shift(result: RenderingResult, outcome: LateOutcome) -> RenderingResult:
    """`result` joined with its late-script load: record, findings, faults, verdict and motion.

    Both loads watch for CSP violations, so what each found is combined, never overwritten.
    """
    late = {LATE_SHIFT: late_findings(outcome.late), CSP_VIOLATION: outcome.csp}
    verdict = {LATE_SHIFT: outcome.verdict} if outcome.verdict else {}
    return replace(
        result,
        faults=result.faults + outcome.faults,
        findings=merged_findings(result.findings, late),
        verdicts={**result.verdicts, **verdict},
        late_shift=outcome.late,
        late_motion=outcome.motion,
    )


def run_matrix(
    renderings: Sequence[Rendering],
    base_url: str,
    *,
    timeout: float,
    prove: bool,
    scratch: Path,
    progress: Callable[[RenderingResult], None],
    watchdog: Bounder | None = None,
    states: Sequence[State] = (),
    require_csp: bool = False,
    missing_pages: Set[str] = frozenset(),
    isolation: Isolation = NO_ISOLATION,
) -> tuple[dict[str, str], list[RenderingResult]]:
    """Every rendering, one browser launch per engine; returns each engine's version too.

    A rendering naming a state is reached in that state, found in `states` by name. With
    `require_csp`, a page load whose markup declares no policy is a finding. A rendering whose
    page's server path is in `missing_pages` has no file, so its own 404 is expected. Each
    engine is launched with `isolation`; one with an extension launches once per context, so
    its version is read once its renderings have run.

    Each rendering, and each browser's close, runs inside `watchdog`'s bound (by default one
    that abandons the run), because the probe's `evaluate` and every `close` take no timeout.
    """
    bounder = watchdog or Watchdog()
    by_name = {state.name: state for state in states}
    versions: dict[str, str] = {}
    results: list[RenderingResult] = []
    with sync_playwright() as playwright:
        for engine, group in itertools.groupby(renderings, key=lambda r: r.engine):
            browser = launch(
                playwright,
                engine,
                scratch=scratch,
                timeout=timeout,
                isolation=isolation.serving(base_url),
            )
            try:
                for rendering in group:
                    state = state_for(rendering, by_name)
                    bound = rendering_bound(timeout, state) + extra_bound(
                        browser, timeout
                    )
                    with bounder.bound(bound, f"rendering {rendering.label}"):
                        result = render(
                            browser,
                            rendering,
                            base_url,
                            timeout=timeout,
                            prove=prove,
                            state=state,
                            require_csp=require_csp,
                            missing=server_path(rendering.page) in missing_pages,
                        )
                    progress(result)
                    results.append(result)
                versions[engine] = browser.version
            finally:
                with bounder.bound(CLOSE_BOUND_SECONDS, f"closing {engine}"):
                    browser.close()
    return versions, results
