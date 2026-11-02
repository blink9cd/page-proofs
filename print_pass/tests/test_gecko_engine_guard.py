"""Grouped suite: the run watchdog's expiry (kill, say the scratch was left, abandon).

`RunGuard.on_expire` is what the run's watchdog calls when a bounded call overruns. It
kills the driver's whole tree, writes one stderr line naming the scratch folder it leaves in
place, then abandons as the watchdog always has; each step runs even when the one before it
fails, and the abandon always. A run whose guard expired never removes its scratch: the
abandon ends the process from the watch thread, so that removal's precondition, that nothing
writes into the scratch any more, could not be vouched for.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_engine  # noqa: E402 - the tool's private src/, via the insert above
from _gecko_engine_fixtures import EngineRun, Entry  # noqa: E402 - beside this module
from print_record import EngineError  # noqa: E402 - the tool's private src/
from print_watchdog import abandon  # noqa: E402 - as above


class ExpiryTests(unittest.TestCase):
    """The watchdog's expiry: kill, say the scratch was left, abandon; never remove it."""

    def test_an_expiry_kills_then_says_then_abandons(self) -> None:
        run = EngineRun(self)
        run.during["home/default"] = lambda client, driver: run.guard.on_expire(
            "webdriver print"
        )
        run.failing["home/default"] = EngineError(
            "print: unknown error: the browser went away"
        )
        with self.assertRaises(EngineError):
            run.run()
        expiry = run.log.index(("kill",))
        self.assertEqual(
            run.log[expiry : expiry + 3],
            [
                ("kill",),
                ("warn", f"the scratch folder was left in place: {run.root}"),
                ("abandon", "webdriver print"),
            ],
        )
        self.assertIs(run.guard.expired.is_set(), True)

    def test_an_expired_run_never_removes_its_scratch_nor_says_so_twice(self) -> None:
        run = EngineRun(self)
        run.during["home/default"] = lambda client, driver: run.guard.on_expire("x")
        run.failing["home/default"] = EngineError("gone")
        with self.assertRaises(EngineError):
            run.run()
        self.assertNotIn("remove", run.names())
        self.assertEqual(run.names().count("warn"), 1)

    def test_an_expiry_after_a_good_run_still_removes_nothing(self) -> None:
        run = EngineRun(self)
        run.during["home/default"] = lambda client, driver: run.guard.on_expire("x")
        run.run()
        self.assertNotIn("remove", run.names())


class GuardTests(unittest.TestCase):
    """`RunGuard` alone: what it does with nothing watched, and when a step of it fails."""

    def setUp(self) -> None:
        """A guard writing one log."""
        self.log: list[Entry] = []
        self.guard = gecko_engine.RunGuard(
            warn=lambda line: self.log.append(("warn", line)),
            abandon=lambda what: self.log.append(("abandon", what)),
        )

    def test_an_expiry_before_any_driver_only_abandons(self) -> None:
        self.guard.on_expire("geckodriver start")
        self.assertEqual(self.log, [("abandon", "geckodriver start")])
        self.assertTrue(self.guard.expired.is_set())

    def test_a_kill_that_fails_still_says_and_abandons(self) -> None:
        class _Unkillable:
            def kill(self) -> None:
                raise OSError("no such process")

        self.guard.watch(_Unkillable(), Path("/scratch-root"))
        with self.assertRaises(OSError):
            self.guard.on_expire("x")
        self.assertEqual(
            self.log,
            [
                ("warn", "the scratch folder was left in place: /scratch-root"),
                ("abandon", "x"),
            ],
        )

    def test_a_line_that_cannot_be_written_still_abandons(self) -> None:
        def broken(line: str) -> None:
            raise OSError("stderr closed")

        guard = gecko_engine.RunGuard(
            warn=broken, abandon=lambda what: self.log.append(("abandon", what))
        )
        guard.watch(_Killable(self.log), Path("/scratch-root"))
        with self.assertRaises(OSError):
            guard.on_expire("x")
        self.assertEqual(self.log, [("kill",), ("abandon", "x")])

    def test_the_default_abandon_is_the_watchdogs(self) -> None:
        self.assertIs(gecko_engine.RunGuard().abandon, abandon)


class _Killable:
    """A driver whose kill is logged."""

    def __init__(self, log: list[Entry]) -> None:
        """Write into `log`."""
        self._log = log

    def kill(self) -> None:
        """Log the kill."""
        self._log.append(("kill",))


if __name__ == "__main__":
    unittest.main()
