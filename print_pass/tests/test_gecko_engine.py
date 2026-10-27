"""Grouped suite: a whole Firefox run -- one scratch, one dead port, one driver, a session each.

`run_targets` makes one scratch folder, holds one dead port, starts one geckodriver, runs
every state of every target in file order in a session of its own, and stops the driver.
Then, and only once that stop has returned, it checks the whole log for Firefox's non-local
guard and removes the scratch; a start or a stop that failed leaves the scratch in place and
says so on stderr. A state's error, an interrupt included, ends the states and is held while
the driver stops as for a clean exit, then raised; a stop that fails beside it is named with
it in one error. A scratch that cannot be made is an error naming what failed, its root
removed again. The run watchdog's expiry is `test_gecko_engine_guard.py`'s. Every seam is
`_gecko_engine_fixtures`' fake: no case starts geckodriver, holds a port or removes a real
scratch but its own temporary one.

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
from _gecko_client_fixtures import FakeTransport, session_reply  # noqa: E402 - beside it
from _gecko_engine_fixtures import (  # noqa: E402 - as above
    DEAD_PORT,
    DRIVER_URL,
    EXECUTABLE,
    EngineRun,
)
from _gecko_state_fixtures import REFUSAL  # noqa: E402 - as above
from _print_engine_fixtures import BASE_URL, state, target, targets_file  # noqa: E402
from gecko_budget import client_timeout  # noqa: E402 - the tool's private src/
from gecko_engine import LEFT_IN_PLACE  # noqa: E402 - as above
from gecko_launch import IsolationBreach  # noqa: E402 - as above
from print_record import EngineError  # noqa: E402 - as above
from print_targets import Viewport  # noqa: E402 - as above


class RunTests(unittest.TestCase):
    """A run that goes well: its order, its jobs, its client, and its scratch removed."""

    def test_a_run_holds_its_port_and_driver_around_every_state(self) -> None:
        run = EngineRun(self)
        two = target(states=(state("a"), state("b")))
        run.run(targets_file(two, target("about", page="/about/")))
        self.assertEqual(
            [entry for entry in run.log if entry[0] != "log_since"],
            [
                ("make_root",),
                ("hold",),
                ("driver", EXECUTABLE, run.root, 5.0),
                ("start",),
                ("state", "home/a"),
                ("state", "home/b"),
                ("state", "about/default"),
                ("stop", "NoneType"),
                ("release",),
                ("remove", run.root),
            ],
        )

    def test_the_records_come_back_in_file_order(self) -> None:
        run = EngineRun(self)
        two = target(states=(state("a"), state("b")))
        records = run.run(targets_file(two))
        self.assertEqual(
            [(r.target, r.state) for r in records], [("home", "a"), ("home", "b")]
        )

    def test_each_job_carries_the_run_and_the_files_settings(self) -> None:
        run = EngineRun(self)
        file = targets_file(
            viewport=Viewport(800, 600),
            backgrounds="both",
            expected_offsite_hosts=("fonts.example",),
        )
        run.run(file)
        (job,) = run.jobs
        self.assertEqual(job.base_url, BASE_URL)
        self.assertEqual(job.dead_port, DEAD_PORT)
        self.assertEqual(job.scratch_root, run.root)
        self.assertEqual(job.expected_hosts, ("fonts.example",))
        self.assertEqual(job.settings.viewport, Viewport(800, 600))
        self.assertEqual(job.settings.backgrounds, "both")
        self.assertEqual(job.settings.engine, "firefox")

    def test_the_scratch_is_made_under_the_root(self) -> None:
        run = EngineRun(self)
        run.run()
        assert run.driver is not None
        self.assertEqual(run.driver.scratch.root, run.root)
        self.assertTrue(run.driver.scratch.profiles.is_dir())

    def test_the_client_talks_to_the_driver_a_margin_above_the_timeout(self) -> None:
        run = EngineRun(self)
        transport = FakeTransport(run.log, [session_reply()])
        run.during["home/default"] = lambda client, driver: client.new_session({})
        run.run(transport=transport, timeout_s=7.0)
        sent = [entry for entry in run.log if entry[0] == "send"]
        self.assertEqual(
            sent,
            [
                (
                    "send",
                    "POST",
                    f"{DRIVER_URL}/session",
                    {"capabilities": {}},
                    client_timeout(7.0),
                )
            ],
        )

    def test_a_failed_runs_whole_log_is_checked_once_the_driver_has_stopped(
        self,
    ) -> None:
        run = EngineRun(self)
        run.failing["home/default"] = EngineError("home/default: print failed")
        with self.assertRaises(EngineError):
            run.run()
        self.assertLess(
            run.log.index(("stop", "NoneType")), run.log.index(("log_since", 0))
        )
        self.assertLess(
            run.log.index(("log_since", 0)), run.log.index(("remove", run.root))
        )

    def test_a_scratch_that_cannot_be_removed_fails_the_run(self) -> None:
        run = EngineRun(self)
        run.remove_error = EngineError("the scratch could not be removed")
        with self.assertRaises(EngineError) as caught:
            run.run()
        self.assertIs(caught.exception, run.remove_error)

    def test_the_default_root_is_a_fresh_absolute_folder(self) -> None:
        root, other = gecko_engine.fresh_root(), gecko_engine.fresh_root()
        self.addCleanup(root.rmdir)
        self.addCleanup(other.rmdir)
        self.assertTrue(root.is_absolute())
        self.assertEqual(list(root.iterdir()), [])
        self.assertNotEqual(root, other)


class FailureTests(unittest.TestCase):
    """A run that fails: the driver stopped first, a breach first, the scratch's fate said."""

    def test_a_states_error_stops_the_driver_removes_the_scratch_and_ends_the_run(
        self,
    ) -> None:
        run = EngineRun(self)
        failure = EngineError("home/b: / did not load: it failed")
        run.failing["home/b"] = failure
        with self.assertRaises(EngineError) as caught:
            run.run(targets_file(target(states=(state("a"), state("b"), state("c")))))
        self.assertIs(caught.exception, failure)
        self.assertNotIn(("state", "home/c"), run.log)
        self.assertIn(("stop", "NoneType"), run.log)
        self.assertIn(("remove", run.root), run.log)

    def test_a_scratch_not_removed_after_a_failure_is_said_and_the_failure_kept(
        self,
    ) -> None:
        run = EngineRun(self)
        failure = EngineError("home/default: print failed")
        run.failing["home/default"] = failure
        run.remove_error = EngineError(f"{run.root} could not be removed: busy")
        with self.assertRaises(EngineError) as caught:
            run.run()
        self.assertIs(caught.exception, failure)
        self.assertIn(("warn", f"{run.root} could not be removed: busy"), run.log)

    def test_the_refusal_in_the_log_of_a_run_that_succeeded_is_no_breach(self) -> None:
        # The guard ends the browser, so a run that succeeded was never refused: a page
        # can write the sentence to the log, and must not end a good run with exit 2.
        run = EngineRun(self, stop_writes=REFUSAL)
        run.run()
        self.assertIn(("remove", run.root), run.log)

    def test_a_breach_outranks_a_states_plain_error(self) -> None:
        run = EngineRun(self, stop_writes=REFUSAL)
        failure = EngineError("home/default: delete session: it failed")
        run.failing["home/default"] = failure
        with self.assertRaises(IsolationBreach) as caught:
            run.run()
        self.assertIs(caught.exception.__cause__, failure)

    def test_a_states_own_breach_is_kept_as_it_named_it(self) -> None:
        run = EngineRun(self, stop_writes=REFUSAL)
        breach = IsolationBreach("home/default: Firefox tried to connect directly")
        run.failing["home/default"] = breach
        with self.assertRaises(IsolationBreach) as caught:
            run.run()
        self.assertIs(caught.exception, breach)

    def test_a_stop_that_failed_leaves_the_scratch_and_says_where(self) -> None:
        stuck = EngineError("geckodriver outlived its kill")
        run = EngineRun(self, stop_error=stuck)
        with self.assertRaises(EngineError) as caught:
            run.run()
        self.assertIs(caught.exception, stuck)
        self.assertNotIn("remove", run.names())
        self.assertIn(
            ("warn", f"the scratch folder was left in place: {run.root}"), run.log
        )

    def test_a_start_that_failed_leaves_the_scratch_and_says_where(self) -> None:
        refused = EngineError("geckodriver named no port")
        run = EngineRun(self, start_error=refused)
        with self.assertRaises(EngineError) as caught:
            run.run()
        self.assertIs(caught.exception, refused)
        self.assertNotIn("remove", run.names())
        self.assertNotIn("state", run.names())
        self.assertEqual(
            run.log[-1], ("warn", f"the scratch folder was left in place: {run.root}")
        )

    def test_a_breach_found_after_a_failed_stop_is_the_error(self) -> None:
        run = EngineRun(self, stop_writes=REFUSAL, stop_error=EngineError("outlived"))
        with self.assertRaises(IsolationBreach):
            run.run()
        self.assertNotIn("remove", run.names())

    def test_a_driver_that_cannot_be_built_leaves_the_scratch(self) -> None:
        run = EngineRun(self)
        run.build_error = ValueError("a timeout must be more than 0 seconds, not 0")
        with self.assertRaises(ValueError):
            run.run()
        self.assertNotIn("start", run.names())
        self.assertEqual(
            run.log[-1], ("warn", f"the scratch folder was left in place: {run.root}")
        )

    def test_a_log_never_made_holds_no_breach(self) -> None:
        refused = EngineError("the processes could not be listed")
        run = EngineRun(self, start_error=refused, no_log=True)
        with self.assertRaises(EngineError) as caught:
            run.run()
        self.assertIs(caught.exception, refused)


class HeldErrorTests(unittest.TestCase):
    """A state's error is held while the driver stops as for a clean exit, then raised."""

    def test_a_held_programming_error_is_raised_once_the_scratch_is_removed(
        self,
    ) -> None:
        run = EngineRun(self)
        wrong = ValueError("not an action a targets file can hold")
        run.failing["home/default"] = wrong
        with self.assertRaises(ValueError) as caught:
            run.run()
        self.assertIs(caught.exception, wrong)
        self.assertIn(("stop", "NoneType"), run.log)
        self.assertEqual(run.log[-1], ("remove", run.root))

    def test_a_held_interrupt_is_raised_once_the_scratch_is_removed(self) -> None:
        run = EngineRun(self)
        interrupt = KeyboardInterrupt()
        run.failing["home/default"] = interrupt
        with self.assertRaises(KeyboardInterrupt) as caught:
            run.run()
        self.assertIs(caught.exception, interrupt)
        self.assertIn(("stop", "NoneType"), run.log)
        self.assertEqual(run.log[-1], ("remove", run.root))

    def test_a_states_error_and_a_failed_stop_are_named_in_one_error(self) -> None:
        run = EngineRun(self, stop_error=EngineError("outlived its kill\nat a frame"))
        failure = EngineError("home/default: print failed: gone\nat a frame")
        run.failing["home/default"] = failure
        with self.assertRaises(EngineError) as caught:
            run.run()
        self.assertEqual(
            str(caught.exception),
            "home/default: print failed: gone; "
            "and geckodriver could not be stopped: outlived its kill",
        )
        self.assertIs(caught.exception.__cause__, failure)
        self.assertNotIn("remove", run.names())
        self.assertEqual(run.log[-1], ("warn", f"{LEFT_IN_PLACE}: {run.root}"))

    def test_a_held_interrupt_and_a_failed_stop_name_the_interrupt(self) -> None:
        run = EngineRun(self, stop_error=EngineError("outlived its kill"))
        interrupt = KeyboardInterrupt()
        run.failing["home/default"] = interrupt
        with self.assertRaises(EngineError) as caught:
            run.run()
        self.assertEqual(
            str(caught.exception),
            "KeyboardInterrupt; and geckodriver could not be stopped: outlived its kill",
        )
        self.assertIs(caught.exception.__cause__, interrupt)
        self.assertNotIn("remove", run.names())

    def test_a_held_breach_outranks_a_failed_stop_unchanged(self) -> None:
        run = EngineRun(self, stop_error=EngineError("outlived its kill"))
        breach = IsolationBreach("home/default: Firefox tried to connect directly")
        run.failing["home/default"] = breach
        with self.assertRaises(IsolationBreach) as caught:
            run.run()
        self.assertIs(caught.exception, breach)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIn(
            ("warn", "geckodriver could not be stopped: outlived its kill"), run.log
        )
        self.assertNotIn("remove", run.names())
        self.assertEqual(run.log[-1], ("warn", f"{LEFT_IN_PLACE}: {run.root}"))

    def test_a_breach_in_the_whole_log_outranks_both_named_errors(self) -> None:
        run = EngineRun(self, stop_writes=REFUSAL, stop_error=EngineError("outlived"))
        failure = EngineError("home/default: print failed")
        run.failing["home/default"] = failure
        with self.assertRaises(IsolationBreach) as caught:
            run.run()
        both = caught.exception.__cause__
        self.assertEqual(
            str(both),
            "home/default: print failed; and geckodriver could not be stopped: outlived",
        )
        assert both is not None
        self.assertIs(both.__cause__, failure)
        self.assertNotIn("remove", run.names())


class ScratchMadeTests(unittest.TestCase):
    """A scratch that cannot be made is exit 2 naming what failed; its root is not left."""

    def test_a_root_that_cannot_be_made_is_an_engine_error(self) -> None:
        run = EngineRun(self)
        denied = PermissionError(13, "Permission denied")
        run.root_error = denied
        with self.assertRaises(EngineError) as caught:
            run.run()
        self.assertEqual(
            str(caught.exception),
            "the scratch folder was not made: "
            "PermissionError: [Errno 13] Permission denied",
        )
        self.assertIs(caught.exception.__cause__, denied)
        self.assertEqual(run.log, [("make_root",)])

    def test_a_scratch_that_cannot_be_made_is_an_error_and_its_root_removed(
        self,
    ) -> None:
        run = EngineRun(self)
        run.scratch_blocked = True
        with self.assertRaises(EngineError) as caught:
            run.run()
        self.assertTrue(
            str(caught.exception).startswith(
                f"the scratch folder {run.root} was not made: FileExistsError: "
            )
        )
        self.assertIn("app-data", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, FileExistsError)
        self.assertEqual(run.log, [("make_root",), ("remove", run.root)])

    def test_a_root_not_removed_after_the_scratch_failed_is_named(self) -> None:
        run = EngineRun(self)
        run.scratch_blocked = True
        run.remove_error = EngineError(
            f"the scratch folder {run.root} was not removed: busy"
        )
        with self.assertRaises(EngineError) as caught:
            run.run()
        self.assertIn("was not made", str(caught.exception))
        self.assertEqual(
            run.log[-1],
            ("warn", f"the scratch folder {run.root} was not removed: busy"),
        )


if __name__ == "__main__":
    unittest.main()
