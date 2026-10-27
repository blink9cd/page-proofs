"""Grouped suite: the watchdog that bounds the engine calls Playwright gives no timeout."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# The shared runner, a folder beside this tool's own, named rather than searched for, so a
# same-named folder above the clone is never imported in its place, and put just after src/,
# ahead of every installed distribution, so none of the same name can shadow it.
sys.path.insert(1, str(_TOOL_DIR.parent / "bounded_run"))

import bounded_run  # noqa: E402 - a folder beside the tool's, via the insert above
import pass_watchdog  # noqa: E402 - the tool's private src/, via the insert above

#: Long enough that a step inside it never overruns on a loaded machine.
_GENEROUS = 30.0

#: Short enough that a test waiting past it stays quick.
_SHORT = 0.05


class BoundTests(unittest.TestCase):
    """A step that finishes in time is left alone; one that overruns is abandoned."""

    def test_a_step_that_finishes_in_time_never_fires(self) -> None:
        fired: list[str] = []
        watchdog = pass_watchdog.Watchdog(on_expire=fired.append)
        with watchdog.bound(_GENEROUS, "a quick step"):
            pass
        self.assertEqual(fired, [])

    def test_a_step_that_overruns_fires_once_naming_the_step(self) -> None:
        fired = threading.Event()
        names: list[str] = []

        def expire(what: str) -> None:
            names.append(what)
            fired.set()

        watchdog = pass_watchdog.Watchdog(on_expire=expire)
        with watchdog.bound(_SHORT, "the probe's layout"):
            self.assertTrue(fired.wait(timeout=_GENEROUS))
        self.assertEqual(names, ["the probe's layout"])

    def test_the_watch_ends_with_the_step(self) -> None:
        fired = threading.Event()
        watchdog = pass_watchdog.Watchdog(on_expire=lambda _what: fired.set())
        with watchdog.bound(_SHORT, "a quick step"):
            pass
        # Wait well past the bound: a watch still running would fire within it.
        self.assertFalse(fired.wait(timeout=_SHORT * 4))


class AbandonTests(unittest.TestCase):
    """The default expiry ends the whole process with exit 2 and says why."""

    def test_abandon_exits_two_naming_the_step(self) -> None:
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); import pass_watchdog; "
            "pass_watchdog.abandon('closing webkit'); print('still running')"
        )
        done = bounded_run.run_bounded(
            [sys.executable, "-c", script, str(_TOOL_DIR / "src")], timeout=_GENEROUS
        )
        self.assertEqual(done.returncode, pass_watchdog.EXIT_ABANDONED)
        self.assertIn("closing webkit", done.stderr)
        self.assertNotIn("still running", done.stdout)

    def test_the_default_watchdog_abandons(self) -> None:
        self.assertIs(pass_watchdog.Watchdog().on_expire, pass_watchdog.abandon)

    def test_exit_two_is_the_cannot_measure_code(self) -> None:
        self.assertEqual(pass_watchdog.EXIT_ABANDONED, 2)


if __name__ == "__main__":
    unittest.main()
