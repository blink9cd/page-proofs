"""Suite: the engine suites' own stand-ins -- the guard `FakeRun.unbounded()`, and the bounder.

Every engine suite that asserts `unbounded() == []` trusts this guard to report a Playwright
call the watchdog would not bound as its own. That trust is the fixture's rule, not the
engine's behavior, so it is pinned here, on logs a case writes itself, where each shape the
rule must report can be built without an engine able to make it. Beside it, the watchdog's
stand-in refuses each bound the watchdog itself would, so a suite sees an out-of-range one.
"""

from __future__ import annotations

import math
import sys
import tempfile
import threading
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

from _print_engine_fixtures import (  # noqa: E402 - beside this module
    BASE_URL,
    TIMED_BOUND_S,
    TIMEOUT_S,
    FakeRun,
)
from _print_playwright_fixtures import CallLog, FakeBounder, call  # noqa: E402 - as above


class UnboundedTests(unittest.TestCase):
    """What the guard reports, for logs no engine run has to produce."""

    def setUp(self) -> None:
        """Give the case a PDF folder of its own, which no hand-built log ever writes to."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pdf_dir = Path(folder.name) / "pdfs"

    def fake(self) -> FakeRun:
        """A run whose log is empty until the case writes it."""
        return FakeRun(self.pdf_dir)

    def test_only_a_calls_own_bound_innermost_counts(self) -> None:
        abort = call("route.abort", "https://example.com/x.png", "blockedbyclient")
        shadowed = call("route.abort", "https://example.com/y.png", "blockedbyclient")
        load = "page.goto home/default"
        sockets = "context.route_web_socket home/default"
        run = self.fake()
        run.calls.extend(
            [
                call("page.pdf"),  # in no bound
                call("bound", load, TIMED_BOUND_S),
                call("page.goto", BASE_URL),
                abort,  # in the load's bound, not its own
                call("bound", "route.abort home/default", TIMEOUT_S),
                abort,  # in its own, inside the load's
                call("unbound", "route.abort home/default"),
                call("unbound", load),
                call("bound", sockets, TIMEOUT_S),
                call("context.route"),  # in a bound whose name only begins with its own
                call("unbound", sockets),
                call("bound", "route.abort home/default", TIMEOUT_S),
                call("bound", load, TIMED_BOUND_S),
                shadowed,  # in the load's bound, inside its own
                call("unbound", load),
                # The abort's own bound closes having bounded no call directly at all.
                call("unbound", "route.abort home/default"),
            ]
        )
        self.assertEqual(
            run.unbounded(),
            [
                call("page.pdf"),
                abort,
                call("context.route"),
                shadowed,
                call("unbound", "route.abort home/default"),
            ],
        )

    def test_one_bound_covers_one_call_unless_its_calls_may_share_one(self) -> None:
        prints = "page.pdf home/default"
        listeners = "page.on home/default"
        load = "page.goto home/default"
        second = call("page.pdf", print_background=True)
        run = self.fake()
        run.calls.extend(
            [
                call("bound", prints, TIMEOUT_S),
                call("page.pdf", print_background=False),
                second,  # a second print, in the first one's bound
                call("unbound", prints),
                call("bound", listeners, TIMEOUT_S),
                call("page.on", "pageerror"),
                call("page.on", "requestfailed"),  # the listeners may share a bound
                call("page.on", "response"),
                call("unbound", listeners),
                call("bound", load, TIMED_BOUND_S),
                call("bound", "route.abort home/default", TIMEOUT_S),
                call("route.abort", "https://example.com/x.png", "blockedbyclient"),
                call("unbound", "route.abort home/default"),
                call("page.goto", BASE_URL),  # the first call DIRECTLY in its bound
                call("unbound", load),
            ]
        )
        self.assertEqual(run.unbounded(), [second])


class FakeBounderTests(unittest.TestCase):
    """The watchdog's stand-in refuses, on entry, every bound the watchdog would."""

    def test_a_bound_the_watchdog_refuses_is_refused_before_its_block(self) -> None:
        calls: CallLog = []
        bounder = FakeBounder(calls)
        for seconds in (0.0, -1.0, math.nan, math.inf, threading.TIMEOUT_MAX * 2):
            with (
                self.subTest(seconds=seconds),
                self.assertRaises(ValueError),
                bounder.bound(seconds, "page.pdf home/default"),
            ):
                calls.append(call("page.pdf"))
        self.assertEqual(calls, [])

    def test_a_bound_the_watchdog_keeps_is_logged_around_its_block(self) -> None:
        calls: CallLog = []
        with FakeBounder(calls).bound(threading.TIMEOUT_MAX, "page.pdf home/default"):
            calls.append(call("page.pdf"))
        self.assertEqual(
            calls,
            [
                call("bound", "page.pdf home/default", threading.TIMEOUT_MAX),
                call("page.pdf"),
                call("unbound", "page.pdf home/default"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
