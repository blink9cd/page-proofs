"""Suite: `Geckodriver`'s stop and `kill`: the whole group ended, within a bound, once.

Every seam the class takes is faked, by `_gecko_launch_fixtures`: nothing here starts a process
or signals one, and the pid every fake reports is never handed to the operating system.

Entry point assumptions: standalone, or discovered by `unittest discover` with this folder
as its top-level directory, which is how the shared fixtures beside it are found.
"""

from __future__ import annotations

import math
import sys
import threading
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_launch  # noqa: E402 - the tool's private src/, via the insert above
from _gecko_launch_fixtures import (  # noqa: E402 - beside this module
    PID,
    STOP,
    TIMEOUT_S,
    GeckoCase,
    listening,
    outside_bounds,
)
from _print_playwright_fixtures import call  # noqa: E402 - beside this module
from print_record import EngineError  # noqa: E402 - the tool's private src/

#: How long a case's thread may take to return from `kill`.
_JOIN_S = 30.0


class StopTests(GeckoCase):
    """Leaving the block ends geckodriver's whole group, within a bound, and closes the log."""

    def test_a_stop_terminates_the_group_and_waits(self) -> None:
        self.host.chunks = [listening(4444).encode()]
        with self.host.driver():
            self.host.calls.clear()
        bound = gecko_launch.watchdog_seconds(TIMEOUT_S)
        self.assertEqual(
            self.host.calls,
            [
                call("bound", STOP, bound),
                call("terminate", PID),
                call("wait", TIMEOUT_S),
                call("group_alive", PID),
                call("unbound", STOP),
            ],
        )
        assert self.host.stdout is not None
        self.assertTrue(self.host.stdout.closed)

    def test_a_stop_that_overruns_kills_the_tree_and_reaps(self) -> None:
        self.host.term_ends = False
        self.host.chunks = [listening(4444).encode()]
        with self.host.driver():
            self.host.calls.clear()
        self.assertEqual(
            self.host.named("terminate", "wait", "kill_tree", "group_alive"),
            [
                call("terminate", PID),
                call("wait", TIMEOUT_S),
                call("kill_tree", PID),
                call("wait", TIMEOUT_S),
                call("group_alive", PID),
            ],
        )
        self.assertEqual(outside_bounds(self.host.calls), [])

    def test_a_tree_that_outlives_its_kill_is_reported_and_the_log_still_closed(
        self,
    ) -> None:
        self.host.term_ends = self.host.kill_ends = False
        self.host.chunks = [listening(4444).encode()]
        driver = self.host.driver().__enter__()
        with self.assertRaisesRegex(
            EngineError, "^geckodriver outlived its kill by 5 s$"
        ):
            driver.__exit__(None, None, None)
        self.assertEqual(self.host.calls[-1], call("unbound", STOP))
        assert self.host.stdout is not None
        self.assertTrue(self.host.stdout.closed)

    def test_a_second_exit_signals_nothing(self) -> None:
        # The first exit reaped geckodriver, so its pid may already be another process's.
        self.host.chunks = [listening(4444).encode()]
        driver = self.host.driver().__enter__()
        driver.__exit__(None, None, None)
        self.host.calls.clear()
        with self.assertRaises(RuntimeError):
            driver.__exit__(None, None, None)
        self.assertEqual(self.host.calls, [])

    def test_an_error_in_the_block_still_stops_it_and_goes_on(self) -> None:
        self.host.chunks = [listening(4444).encode()]
        with self.assertRaises(KeyError), self.host.driver():
            raise KeyError("the block's own")
        self.assertEqual(self.host.named("terminate"), [call("terminate", PID)])


class GroupEndTests(GeckoCase):
    """Once geckodriver is reaped, the stop polls until no member of its group is left."""

    #: The probes and sleeps a stop makes while a straggler outlives the whole timeout: with
    #: a clock moving a second a reading, the deadline is read, then one reading a probe.
    _PROBED_PAST_THE_TIMEOUT: tuple[tuple[str, ...], ...] = (
        *[call("group_alive", PID), call("sleep", gecko_launch.POLL_SECONDS)] * 4,
        call("group_alive", PID),
    )

    def stop(self) -> list[tuple[str, ...]]:
        """Start and stop a driver; the probes, sleeps and kills its stop made, in order."""
        self.host.chunks = [listening(4444).encode()]
        with self.host.driver():
            self.host.calls.clear()
        self.assertEqual(outside_bounds(self.host.calls), [])
        return self.host.named("group_alive", "sleep", "kill_tree")

    def test_stragglers_are_polled_for_until_they_are_gone(self) -> None:
        self.host.stragglers = 3
        self.assertEqual(
            self.stop(),
            [
                *[call("group_alive", PID), call("sleep", gecko_launch.POLL_SECONDS)]
                * 3,
                call("group_alive", PID),
            ],
        )

    def test_stragglers_past_the_timeout_are_killed_and_polled_for_again(self) -> None:
        self.host.stragglers = math.inf
        self.assertEqual(
            self.stop(),
            [
                *self._PROBED_PAST_THE_TIMEOUT,
                call("kill_tree", PID),
                call("group_alive", PID),
            ],
        )

    def test_a_watchdog_kill_during_the_poll_still_reaches_the_group(self) -> None:
        # geckodriver is forgotten only once its group has ended, so its pid still names
        # the group while the poll runs.
        self.host.stragglers = 1
        self.host.chunks = [listening(4444).encode()]
        driver = self.host.driver()
        self.host.on_probe = driver.kill
        with driver:
            self.host.calls.clear()
        self.assertEqual(self.host.named("kill_tree"), [call("kill_tree", PID)])

    def test_stragglers_of_a_killed_geckodriver_get_a_tree_kill_of_their_own(
        self,
    ) -> None:
        # geckodriver's own kill is spent once it was killed, so the group's kill must not
        # go through it.
        self.host.term_ends = False
        self.host.stragglers = math.inf
        self.host.group_survives_kills = 1
        self.host.chunks = [listening(4444).encode()]
        with self.host.driver():
            self.host.calls.clear()
        self.assertEqual(self.host.named("kill_tree"), [call("kill_tree", PID)] * 2)

    def test_stragglers_that_outlive_their_kill_are_reported(self) -> None:
        self.host.stragglers = math.inf
        self.host.kill_ends_group = False
        self.host.chunks = [listening(4444).encode()]
        driver = self.host.driver().__enter__()
        self.host.calls.clear()
        with self.assertRaisesRegex(
            EngineError, "^geckodriver's group outlived its kill by 5 s$"
        ):
            driver.__exit__(None, None, None)
        self.assertEqual(
            self.host.named("group_alive", "sleep", "kill_tree"),
            [
                *self._PROBED_PAST_THE_TIMEOUT,
                call("kill_tree", PID),
                *self._PROBED_PAST_THE_TIMEOUT,
            ],
        )
        self.assertEqual(self.host.calls[-1], call("unbound", STOP))
        assert self.host.stdout is not None
        self.assertTrue(self.host.stdout.closed)


class KillTests(GeckoCase):
    """`kill` ends the whole tree once, from any thread; later calls do nothing."""

    def test_a_kill_before_the_start_does_nothing(self) -> None:
        self.host.driver().kill()
        self.assertEqual(self.host.calls, [])

    def test_a_kill_after_a_clean_stop_does_nothing(self) -> None:
        # The stop reaped geckodriver and saw its group end, so its pid may be reused.
        self.host.chunks = [listening(4444).encode()]
        with self.host.driver() as driver:
            pass
        self.host.calls.clear()
        driver.kill()
        self.assertEqual(self.host.calls, [])

    def test_a_second_kill_does_nothing(self) -> None:
        driver = self.started()
        driver.kill()
        driver.kill()
        self.assertEqual(self.host.named("kill_tree"), [call("kill_tree", PID)])

    def test_kills_from_several_threads_kill_once(self) -> None:
        driver = self.started()
        threads = [threading.Thread(target=driver.kill) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=_JOIN_S)
        self.assertEqual(self.host.named("kill_tree"), [call("kill_tree", PID)])

    def test_a_stop_after_a_kill_reaps_without_killing_again(self) -> None:
        self.host.chunks = [listening(4444).encode()]
        with self.host.driver() as driver:
            driver.kill()
        self.assertEqual(self.host.named("kill_tree"), [call("kill_tree", PID)])
        self.assertEqual(self.host.named("wait"), [call("wait", TIMEOUT_S)])


if __name__ == "__main__":
    unittest.main()
