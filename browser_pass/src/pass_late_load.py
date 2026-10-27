"""The late-script layout-shift detector's own load: every script held until the page has painted.

Whether a page visibly moves when its scripts upgrade it is a race. On a fast connection the
scripts usually arrive before the first paint and nothing moves; on a slow one the reader sees the
page, then sees it jump. An ordinary load therefore reads zero on the very page that jumps, and
throttling the network only makes the race likelier to go one way. This load decides it instead:

1. **Hold** every script request the page makes, letting everything else through.
2. **Release** them once the page has painted -- or at once, when a held script is one the page
   cannot paint without (`blocking="render"`, or a classic script in `<head>`), since no paint
   will come until it runs. The release time is marked inside the page.
3. **Wait** until the page is PRESENTABLE -- painted, then in its own fonts -- which the page
   marks from its frames, not from its main thread: on a loaded machine the layout can be
   loaded and still before a frame has even shown it. A page refused whatever comes is not
   waited for.
4. **Settle** on the layout signature, then **read** every layout shift a buffered observer,
   installed before any of the page's own code, recorded after the release -- or after the
   page became presentable, when that came later. Unlike the page-load settle it stills only
   motion PROVABLY layout-inert (`_settled`, `pass_inert`): seeking an animation that moves
   layout would itself record a layout shift, charged to the scripts. So a page whose motion
   not provably layout-inert never pauses for the quiet window is refused here as never
   settling.
5. **Prove** (with `--prove`): plant a box, move it two frames later, and require the move to be
   reported above the threshold.

It runs in a context of its own, after the page-load detectors have finished with theirs, so they
are neither slowed nor perturbed. The verdicts are `pass_shift`'s. The caller may watch this
context for CSP violations too (`pass_csp`): what it reports is read once the load is measured,
before its plant, and the caller merges it with what the page load found.

Entry point assumptions: Playwright is importable (the repository's `browser` extra); the caller opens
the context in the rendering's state, closes it afterwards, and runs this only where
`pass_shift.not_measured` says the rendering is measured.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

import pass_csp
import pass_inert
import pass_probe
from pass_checks import poll_until, wait_until_settled
from pass_motion import NO_MOTION, Motion, motion_from, tally
from pass_probe import (
    MS_PER_SECOND,
    POLL_INTERVAL_SECONDS,
    init_script,
    pause,
    probe,
)
from pass_shift import (
    MEASURED,
    NOT_MEASURED,
    HoldState,
    LateShift,
    awaits_presentable,
    hold_state_from,
    names_plant,
    plant_verdict,
    record_faults,
    shift_record_from,
)
from pass_targets import Rendering
from playwright.sync_api import BrowserContext, Page, Route
from playwright.sync_api import Error as PlaywrightError


@dataclass(frozen=True)
class LateOutcome:
    """What the late-script load produced: its record, faults, plant's verdict, CSP findings."""

    late: LateShift
    faults: tuple[str, ...] = ()
    verdict: str | None = None
    #: The CSP violations this load reported, one line each, read before its plant.
    csp: tuple[str, ...] = ()
    #: What its settle stilled or left running (`_settled`), kept when the settle ran out.
    motion: Motion = NO_MOTION


class ScriptHold:
    """Holds every script request until `release`; every other request goes straight through."""

    def __init__(self) -> None:
        """Start holding."""
        self._holding = True
        self._held: list[Route] = []

    def route(self, route: Route) -> None:
        """Playwright's route handler: hold a script while holding, pass anything else on."""
        if self._holding and route.request.resource_type == "script":
            self._held.append(route)
        else:
            route.continue_()

    def release(self) -> list[str]:
        """Let every held script through, in the order the page asked; return their urls."""
        self._holding = False
        urls = [route.request.url for route in self._held]
        for route in self._held:
            route.continue_()
        self._held.clear()
        return urls


def _faulted(fault: str, motion: Motion = NO_MOTION) -> LateOutcome:
    """A late-script load that could not be measured honestly, and the motion it stilled."""
    text = f"late_shift: {fault}"
    return LateOutcome(LateShift(NOT_MEASURED, reason=text), (text,), motion=motion)


def _first_line(error: PlaywrightError) -> str:
    """The first line of a Playwright error, which is where it says what went wrong."""
    return str(error).strip().splitlines()[0]


def measure_late_shift(
    context: BrowserContext,
    rendering: Rendering,
    base_url: str,
    *,
    timeout: float,
    prove: bool,
    csp: pass_csp.CspWatch = pass_csp.UNWATCHED,
) -> LateOutcome:
    """Load `rendering` with its scripts held until the first paint, and read what moved after.

    `csp` is what the caller installed on `context` to watch its Content Security Policy.
    """
    context.add_init_script(init_script("watchShifts"))
    page = pass_csp.open_page(context, csp)
    hold = ScriptHold()
    page.route("**/*", hold.route)
    try:
        page.goto(
            base_url + rendering.page,
            wait_until="commit",
            timeout=timeout * MS_PER_SECOND,
        )
    except PlaywrightError as error:
        return _faulted(f"the page did not load: {_first_line(error)}")
    try:
        held, released, state = _hold_until_painted(page, hold, timeout=timeout)
    except PlaywrightError as error:
        return _faulted(f"the held scripts could not be released: {_first_line(error)}")
    if not released and state.painted:
        # Cut short while its fonts were still arriving: a face swapping in after the release
        # would be charged to the scripts, which is what waiting for the fonts prevents.
        return _faulted(
            f"the page's fonts had not loaded after {timeout:g}s, so a face swapping in "
            "later cannot be told from a late script"
        )
    try:
        page.wait_for_load_state("load", timeout=timeout * MS_PER_SECOND)
    except PlaywrightError as error:
        return _faulted(f"the page did not finish loading: {_first_line(error)}")
    if not _wait_until_presentable(page, held, timeout=timeout):
        return _faulted(
            f"the page had not painted in its own fonts after {timeout:g}s, so a face "
            "swapping in later cannot be told from a late script"
        )
    settled, motion = _settled(page, timeout=timeout)
    if not settled:
        return _faulted(f"the layout was still changing after {timeout:g}s", motion)
    record = shift_record_from(probe(page, "lateShifts", heldUrls=held))
    faults = record_faults(record)
    if faults:
        late = LateShift(NOT_MEASURED, reason=faults[0])
        return LateOutcome(late, tuple(faults), motion=motion)
    late = LateShift(MEASURED, shifts=record.shifts)
    outcome = _measured(page, late, held, csp, timeout=timeout, prove=prove)
    return replace(outcome, motion=motion)


def _measured(
    page: Page,
    late: LateShift,
    held: list[str],
    csp: pass_csp.CspWatch,
    *,
    timeout: float,
    prove: bool,
) -> LateOutcome:
    """A measured load's outcome: its CSP findings read first, then its plant's verdict.

    A payload the collector was sent that its listener never sends is a fault, never a finding.
    """
    try:
        found = pass_csp.violations(csp)
    except pass_csp.CspWatchError as error:
        fault = pass_csp.csp_fault(error)
        return LateOutcome(LateShift(NOT_MEASURED, reason=fault), (fault,))
    verdict = _prove(page, held, timeout=timeout) if prove else None
    return LateOutcome(late, verdict=verdict, csp=found)


def _hold_until_painted(
    page: Page, hold: ScriptHold, *, timeout: float
) -> tuple[list[str], bool, HoldState]:
    """Hold the scripts until the page paints or cannot, mark the release, and release them.

    Returns the held urls, whether the hold ended on its own terms rather than running out of
    `timeout`, and the last state read. A page that never painted is released anyway and left
    to `pass_shift.record_faults`, since a held script blocking the rendering makes it
    measurable; one that painted but never finished its fonts is the caller's to refuse.
    """
    state = HoldState(painted=False, fonts_loaded=False, blocked=False)

    def ended() -> bool:
        nonlocal state
        state = hold_state_from(probe(page, "holdState"))
        return state.ended

    released = poll_until(
        ended,
        pause=pause(page),
        clock=time.monotonic,
        within=timeout,
        interval=POLL_INTERVAL_SECONDS,
    )
    probe(page, "markRelease")
    return hold.release(), released, state


def _wait_until_presentable(page: Page, held: list[str], *, timeout: float) -> bool:
    """Wait until the page's record no longer awaits its presentable moment; False on timeout.

    That moment -- painted, then in its own fonts -- is marked by the page's frames, which a
    loaded machine can leave behind its main thread: measured with frames stalled at `load`,
    the layout was loaded and still while the timeline held no paint at all, and the page
    became presentable as soon as frames resumed. Read before then, its record refuses as
    never painted in its own fonts a page that was a moment from it. A record refused
    whatever comes is not waited on, so its own fault is reported at once.
    """

    def arrived() -> bool:
        record = shift_record_from(probe(page, "lateShifts", heldUrls=held))
        return not awaits_presentable(record)

    return poll_until(
        arrived,
        pause=pause(page),
        clock=time.monotonic,
        within=timeout,
        interval=POLL_INTERVAL_SECONDS,
    )


def _settled(page: Page, *, timeout: float) -> tuple[bool, Motion]:
    """Whether the layout holds still for the quiet window, and the motion stilled on the way.

    Each read first stills PROVABLY layout-inert motion alone: an animation naming at least one
    property, every one of them in the class `LAYOUT_INERT_PROPERTIES` in
    `page_probe_motion.js` holds -- a CSS animation's names read, over a DevTools session, from
    the `@keyframes` rule Chromium's style resolver applies to it (`pass_inert`), since its
    `getKeyframes()` leaves custom properties out. A seek of it moves no layout, so the Layout
    Instability API reports nothing for it and the scripts are charged with nothing, while left
    running, an endless transform changes the signature every frame and the page never
    settles. Any other animation, and one whose names are unknown, is left running, since a
    seek that moved layout would be a shift charged to the scripts -- so a page whose layout
    never stops moving is still refused, as a reader would feel it move.
    """
    motion = NO_MOTION
    session = pass_inert.open_session(page)

    def read() -> object:
        nonlocal motion
        still = pass_inert.still_inert(page, session)
        motion = tally(motion, motion_from(still))
        return probe(page, "signature")

    try:
        settled = wait_until_settled(
            read,
            pause=pause(page),
            clock=time.monotonic,
            # Read off the module now, not copied in at import, so a window set on pass_probe
            # (the live suites set a shorter one) reaches this settle as well.
            quiet=pass_probe.SETTLE_QUIET_SECONDS,
            within=timeout,
            interval=POLL_INTERVAL_SECONDS,
        )
    finally:
        # However the settle ended, a read that failed included, the session ends with it.
        pass_inert.close_session(session)
    return settled, motion


def _prove(page: Page, held: list[str], *, timeout: float) -> str:
    """Plant a shift, wait for it to be reported, and judge whether the detector caught it."""
    if probe(page, "plantShift") is not True:
        return plant_verdict(took=False, moved=False, after=LateShift(MEASURED))

    def reported() -> LateShift:
        record = shift_record_from(probe(page, "lateShifts", heldUrls=held))
        return LateShift(MEASURED, shifts=record.shifts)

    poll_until(
        lambda: names_plant(reported()),
        pause=pause(page),
        clock=time.monotonic,
        within=timeout,
        interval=POLL_INTERVAL_SECONDS,
    )
    moved = probe(page, "plantMoved") is True
    return plant_verdict(took=True, moved=moved, after=reported())
