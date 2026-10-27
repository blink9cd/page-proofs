"""Stand-ins for browser_pass's late-script load suites: a page that answers from a script.

`test_pass_late_load.py` and `test_pass_late_faults.py` drive `pass_late_load` through
`FakePage`, handed out by `FakeContext`: it answers the probe with scripted records and raises
where a browser would. It offers no DevTools session, as an engine but Chromium offers none, so
the late settle's stilling runs through the probe alone.

Entry point assumptions: imported by a suite in this folder; it puts the tool's `src/` on
`sys.path`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pass_late_load  # noqa: E402 - the tool's private src/, via the insert above
import pass_motion  # noqa: E402 - the tool's private src/, via the insert above
import pass_probe  # noqa: E402 - the tool's private src/, via the insert above
from pass_motion import NO_MOTION, Motion  # noqa: E402 - the tool's private src/
from pass_targets import Rendering  # noqa: E402 - the tool's private src/
from playwright.sync_api import Error as PlaywrightError  # noqa: E402 - after the insert

#: Short enough that a poll which cannot succeed gives up at once.
TIMEOUT = 0.05

#: For a poll that DOES succeed: long enough that a loaded machine never cuts it short between
#: two reads, and never spent, since only a poll that fails would reach it.
ROOMY_TIMEOUT = 30.0

RENDERING = Rendering("chromium", "/", 390, 844, "light", "on")

#: A render-blocking page's record before its frames have shown it: released at once, nothing
#: painted yet, so no presentable moment either.
NOT_YET_PRESENTABLE: dict[str, object] = {
    "supported": True,
    "released": 10.0,
    "paints": [],
    "presentableAt": None,
    "blockingHeld": True,
    "entries": [],
}

#: The same page once its frames came: painted at 30ms, its face swapping in at 40ms -- before
#: the presentable moment at 80ms, so the swap is not the scripts' doing.
PRESENTABLE: dict[str, object] = {
    **NOT_YET_PRESENTABLE,
    "paints": [30.0],
    "presentableAt": 80.0,
    "entries": [
        {"start": 40.0, "value": 0.0238, "recentInput": False, "sources": ["body>p:1"]}
    ],
}

#: A page that never painted while its scripts were held, with nothing held blocking it: refused
#: by the record whatever comes, so nothing is worth waiting for.
UNPAINTED_UNBLOCKED: dict[str, object] = {
    **NOT_YET_PRESENTABLE,
    "released": 100.0,
    "paints": [140.0],
    "blockingHeld": False,
}


class FakeRoute:
    """One held request, which may refuse to be replayed the way a closed page's would."""

    def __init__(self, url: str, *, raises: bool = False) -> None:
        """A script request for `url`; with `raises`, one whose page has gone."""
        self.request = type("Request", (), {"url": url, "resource_type": "script"})()
        self._raises = raises
        self.continued = False

    def continue_(self) -> None:
        """Replay the request, or raise as Playwright does once its page has gone."""
        if self._raises:
            raise PlaywrightError("Route is already handled!")
        self.continued = True


class _SessionlessContext:
    """A page's context that refuses a DevTools session, as Playwright does but in Chromium."""

    def new_cdp_session(self, page: object) -> object:
        """Refuse, with Playwright's own error."""
        raise PlaywrightError(
            "BrowserContext.new_cdp_session: CDP session is only available in Chromium"
        )


class FakePage:
    """A page that answers the probe with scripted records and never touches a browser."""

    #: Where the late settle asks for a DevTools session, and is refused one.
    context = _SessionlessContext()

    def __init__(
        self,
        hold_state: dict[str, bool],
        *,
        asks_for: FakeRoute | None = None,
        late_shifts: tuple[dict[str, object], ...] = (PRESENTABLE,),
        motions: tuple[Motion, ...] = (NO_MOTION,),
    ) -> None:
        """A page reporting `hold_state`, asking for `asks_for` as it loads, if anything."""
        self.hold_state = hold_state
        self.asks_for = asks_for
        self.handler: Callable[[FakeRoute], None] | None = None
        self.load_waited = False
        # Read back in order, the last one for good: what frames still to come would change.
        self._late_shifts = list(late_shifts)
        # What each `stillMotion` reports in turn, the last one for good.
        self._motions = list(motions)
        #: Every operation asked, with what its call added to the shared configuration.
        self.asked: list[tuple[object, dict[str, object]]] = []

    def route(self, pattern: str, handler: Callable[[FakeRoute], None]) -> None:
        """Keep the route handler, which `goto` hands the page's own request."""
        self.handler = handler

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
        """Load the page, which asks for its script while the hold is on."""
        if self.asks_for is not None:
            assert self.handler is not None
            self.handler(self.asks_for)

    def wait_for_timeout(self, milliseconds: float) -> None:
        """A poll's pause, which costs nothing here."""

    def wait_for_load_state(self, state: str, *, timeout: float) -> None:
        """Record that the load was waited for."""
        self.load_waited = True

    def evaluate(self, source: str, argument: list[object]) -> object:
        """Answer one probe operation from the script, logging it."""
        operation, config = argument
        assert isinstance(config, dict)
        extra = {k: v for k, v in config.items() if k not in pass_probe.PROBE_CONFIG}
        self.asked.append((operation, extra))
        if operation == "stillMotion":
            motion = (
                self._motions.pop(0) if len(self._motions) > 1 else self._motions[0]
            )
            return pass_motion.motion_record(motion)
        if operation == "holdState":
            return self.hold_state
        if operation == "markRelease":
            return True
        if operation == "signature":
            # Held still, but this page's `--timeout` runs out before the quiet window, so a
            # load that gets this far ends as a settle fault: enough to see the release itself.
            return [0, 0, 0, "loaded", 0]
        if operation == "lateShifts":
            if len(self._late_shifts) > 1:
                return self._late_shifts.pop(0)
            return self._late_shifts[0]
        raise AssertionError(f"the fake page was asked for {operation!r}")


class FakeContext:
    """Hands out one scripted page, as a browser context would."""

    def __init__(self, page: FakePage) -> None:
        """A context whose one page is `page`."""
        self.page = page
        self.init_scripts: list[str] = []

    def add_init_script(self, script: str) -> None:
        """Keep the script, as a context runs it in each page it opens."""
        self.init_scripts.append(script)

    def new_page(self) -> FakePage:
        """The one scripted page."""
        return self.page


def measure(
    page: FakePage, *, timeout: float = TIMEOUT, prove: bool = False
) -> pass_late_load.LateOutcome:
    """Run the late-script load against `page`, proving its detector with `prove`."""
    return pass_late_load.measure_late_shift(
        FakeContext(page),  # type: ignore[arg-type] # a scripted stand-in for the context
        RENDERING,
        "http://127.0.0.1:0",
        timeout=timeout,
        prove=prove,
    )
