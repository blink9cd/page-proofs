"""Shared by the engine's suites: targets and settings built in one line, and one fake run.

Each builder takes only the keys a case is about and fills the rest with the defaults a
targets file gives. `FakeRun` owns one fake Playwright, the watchdog's stand-in and a clock
that moves one second per reading, all writing one log, so a case runs the engine in one line
and then reads what it did; `StateTestCase` is the base of every suite that runs one target's
states that way. Nothing here reaches a browser or the network.

Entry point assumptions: imported by a suite under this folder. The insert below reaches the
tool's private `src/` from this file's own location, so the import order of a suite does not
matter; the fakes come from `_print_playwright_fixtures` beside this file.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_engine  # noqa: E402 - the tool's private src/, via the insert above
from _print_playwright_fixtures import (  # noqa: E402 - beside this module
    CallLog,
    FakeBounder,
    FakeClock,
    FakePage,
    FakePlaywright,
    FakeScript,
    call,
)
from print_targets import Action, Read, State, Target, TargetsFile, Viewport  # noqa: E402

#: The origin the tool's own server would hand the engine.
BASE_URL = "http://127.0.0.1:8123"

#: The per-page timeout every case runs under, and the same in Playwright's milliseconds.
TIMEOUT_S = 5.0
TIMEOUT_MS = 5000.0

#: The watchdog's bound on a call that is handed the timeout: twice it, so the call's own
#: timeout fires first. Spelled out rather than read from the engine, so a change to the
#: engine's factor fails the cases that use it.
TIMED_BOUND_S = 10.0

#: The settings Playwright's own headless launch passes in its one `--blink-settings`: a
#: hovering, fine pointer. Chromium honors only the last such switch, so every launch's own
#: carries these first. Spelled out, and pinned against the installed Playwright's bundle.
PLAYWRIGHT_HEADLESS_SETTINGS = (
    "primaryHoverType=2,availableHoverTypes=2,"
    "primaryPointerType=4,availablePointerTypes=4"
)

#: The one `--blink-settings` switch every launch carries: Playwright's headless settings,
#: then a lazy image within a million pixels of the window loading as an eager one, at each
#: connection type Chromium keys the margin by. Spelled out rather than read from the
#: engine, so a typo in its copy fails here.
BLINK_SETTINGS_SWITCH = (
    f"--blink-settings={PLAYWRIGHT_HEADLESS_SETTINGS},"
    "lazyLoadingImageMarginPxUnknown=1000000,"
    "lazyLoadingImageMarginPxOffline=1000000,lazyLoadingImageMarginPxSlow2G=1000000,"
    "lazyLoadingImageMarginPx2G=1000000,lazyLoadingImageMarginPx3G=1000000,"
    "lazyLoadingImageMarginPx4G=1000000"
)

#: The window a state is laid out in when nothing says otherwise.
VIEWPORT = Viewport(width=1280, height=800)

#: The log entries the watchdog's stand-in writes; every other entry is a Playwright call.
_BOUND = "bound"
_UNBOUND = "unbound"

#: The calls one bound may cover more than one of, and why. Playwright's synchronous
#: `page.on` registers the listener on the page's event emitter in place: unlike a call made
#: through `SyncBase._sync`, it never hands control to the dispatcher and waits for nothing.
#: The first listener of an event the driver sends only on request (`response` and
#: `requestfailed`, not `pageerror`) also writes one `updateSubscription` message to the
#: driver's pipe, sent expecting no reply. So a bound over the page's three registrations
#: bounds local work and at most two buffered writes, which no wedged dispatcher or driver
#: can stall, and a bound of each one's own would size and name nothing the shared one does
#: not. Any other call gets a bound to itself: two prints in one would share one's budget.
_MAY_SHARE_A_BOUND = frozenset({"page.on"})


def timed_bound(what: str) -> tuple[tuple[str, ...], ...]:
    """The log entries around a call handed the timeout, bounded as `what`: twice it."""
    return call("bound", what, TIMED_BOUND_S), call("unbound", what)


def _bounded_call(bound_name: str) -> str:
    """The call a bound is named for: its name up to where the call ran, `page.goto`."""
    return bound_name.partition(" ")[0]


@dataclass
class _OpenBound:
    """A bound the log has opened and not yet closed, and the calls made directly in it."""

    name: str
    calls: int = 0

    def take(self, call_name: str) -> bool:
        """Count one call made directly inside this bound; True when the bound is its own.

        It is when the bound is named for the call, and the call is either the first made
        directly in it or one `_MAY_SHARE_A_BOUND` names.
        """
        self.calls += 1
        if _bounded_call(self.name) != call_name:
            return False
        return self.calls == 1 or call_name in _MAY_SHARE_A_BOUND


def state(
    name: str = "default",
    *,
    color_scheme: str = "native",
    reduced_motion: str = "native",
    storage: tuple[tuple[str, str], ...] = (),
    actions: tuple[Action, ...] = (),
    print_at_ms: int | None = None,
    page_width: int | None = None,
) -> State:
    """A state with every key at its default but those given."""
    return State(
        name=name,
        color_scheme=color_scheme,
        reduced_motion=reduced_motion,
        storage=storage,
        actions=actions,
        print_at_ms=print_at_ms,
        page_width=page_width,
    )


def target(
    name: str = "home",
    *,
    page: str = "/",
    states: tuple[State, ...] = (),
    reads: tuple[Read, ...] = (),
    storage_reads: tuple[str, ...] = (),
) -> Target:
    """A target; no states given is the one default state, as a targets file gives."""
    return Target(
        name=name,
        page=page,
        storage_reads=storage_reads,
        reads=reads,
        states=states or (state(),),
    )


def targets_file(
    *targets: Target,
    viewport: Viewport | None = None,
    backgrounds: str | None = None,
    expected_offsite_hosts: tuple[str, ...] = (),
) -> TargetsFile:
    """A targets file holding `targets`, one default target when none are given."""
    return TargetsFile(
        viewport=viewport,
        backgrounds=backgrounds,
        expected_offsite_hosts=expected_offsite_hosts,
        targets=targets or (target(),),
    )


def engine_settings(
    pdf_dir: Path,
    *,
    engine: Literal["shell", "chrome"] = "shell",
    backgrounds: Literal["off", "on", "both"] = "off",
    header_footer: bool = False,
    viewport: Viewport = VIEWPORT,
    timeout_s: float = TIMEOUT_S,
) -> print_engine.EngineSettings:
    """The command line's defaults, writing PDFs into `pdf_dir`, but for the keys given."""
    return print_engine.EngineSettings(
        engine=engine,
        backgrounds=backgrounds,
        header_footer=header_footer,
        viewport=viewport,
        timeout_s=timeout_s,
        pdf_dir=pdf_dir,
    )


class FakeRun:
    """One engine run against the fakes, and what it did."""

    def __init__(self, pdf_dir: Path, script: FakeScript | None = None) -> None:
        """A fake Playwright doing what `script` says, with its bounder and clock."""
        self.pdf_dir = pdf_dir
        self.playwright = FakePlaywright(script)
        self.calls: CallLog = self.playwright.calls
        self.bounder = FakeBounder(self.calls)
        self.clock = FakeClock()

    def run(
        self,
        targets: TargetsFile | None = None,
        settings: print_engine.EngineSettings | None = None,
    ) -> tuple[print_engine.StateRecord, ...]:
        """Run the engine over `targets` (one default target) under `settings` (defaults)."""
        return print_engine.run_targets(
            self.playwright,
            BASE_URL,
            targets_file() if targets is None else targets,
            engine_settings(self.pdf_dir) if settings is None else settings,
            bounder=self.bounder,
            clock=self.clock,
        )

    def named(self, name: str) -> list[tuple[str, ...]]:
        """Every logged call named `name`, in order."""
        return [entry for entry in self.calls if entry[0] == name]

    def around(self, entry: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
        """The log entries just before and just after the first `entry`."""
        at = self.calls.index(entry)
        return self.calls[at - 1], self.calls[at + 1]

    def pages(self) -> list[FakePage]:
        """Every page opened, in order, across every browser and context."""
        return [
            page
            for browser in self.playwright.chromium.browsers
            for context in browser.contexts
            for page in context.pages
        ]

    def unbounded(self) -> list[tuple[str, ...]]:
        """Every Playwright call that did not run in a bound of its own.

        A call's own bound is the innermost one open around it, named for it (the call, then
        where it ran: `page.goto home/default`), with no call made directly in it before,
        unless the call is one `_MAY_SHARE_A_BOUND` names. Each open bound runs its own
        timer, and whichever expires first ends the run, so only a bound of the call's own
        is sized for it and names it. An enclosing call's bound gives it that call's
        remaining budget and name, as the load's would give the route's abort, which
        counting depth would pass; a bound shared with an earlier call gives it only what
        that call left. A bound that closes having bounded no call directly is reported by
        its closing entry: it sizes and names a call that is not there, and the one it was
        meant for ran under another.

        Every entry the fakes log but the bounds' own is a Playwright call, so a call the
        engine starts making is checked here with no list to update.
        """
        open_bounds: list[_OpenBound] = []
        outside: list[tuple[str, ...]] = []
        for entry in self.calls:
            if entry[0] == _BOUND:
                open_bounds.append(_OpenBound(entry[1]))
            elif entry[0] == _UNBOUND:
                if open_bounds.pop().calls == 0:
                    outside.append(entry)
            elif not (open_bounds and open_bounds[-1].take(entry[0])):
                outside.append(entry)
        return outside


class StateTestCase(unittest.TestCase):
    """A fresh folder for PDFs per case, and a one-target run."""

    def setUp(self) -> None:
        """Give the case a PDF folder of its own, removed after it."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pdf_dir = Path(folder.name) / "pdfs"

    def run_states(
        self,
        *states: State,
        script: FakeScript | None = None,
        header_footer: bool = False,
    ) -> tuple[FakeRun, tuple[print_engine.StateRecord, ...]]:
        """Run `states` as one target's, under the default settings, against `script`."""
        run = FakeRun(self.pdf_dir, script)
        records = run.run(
            targets_file(target(states=states)),
            engine_settings(self.pdf_dir, header_footer=header_footer),
        )
        return run, records

    def run_one(
        self, the_state: State | None = None, script: FakeScript | None = None
    ) -> tuple[FakeRun, print_engine.StateRecord]:
        """Run one state (the default one when none is given) and return its record."""
        run, (record,) = self.run_states(the_state or state(), script=script)
        return run, record
