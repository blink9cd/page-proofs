"""Suite: `Geckodriver`, the process that drives Firefox: started, awaited, its log read.

Every seam the class takes is faked, by `_gecko_launch_fixtures`: nothing here starts a process
or signals one. Only the start, the log and the construction are proven here: the stop and
`kill` were split off so that neither suite outgrows the length cap.

Entry point assumptions: standalone, or discovered by `unittest discover` with this folder
as its top-level directory, which is how the shared fixtures beside it are found.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_bound  # noqa: E402 - the tool's private src/, via the insert above
import gecko_launch  # noqa: E402 - as above
import gecko_process  # noqa: E402 - as above
from _gecko_launch_fixtures import (  # noqa: E402 - beside this module
    PID,
    START,
    TIMEOUT_S,
    GeckoCase,
    listening,
    outside_bounds,
)
from _print_playwright_fixtures import call  # noqa: E402 - beside this module
from print_record import EngineError  # noqa: E402 - the tool's private src/

#: A character UTF-8 writes in two bytes: LATIN SMALL LETTER E WITH ACUTE.
_TWO_BYTE_CHARACTER = chr(0xE9)

#: What a byte that is not UTF-8 decodes to: REPLACEMENT CHARACTER.
_REPLACEMENT = chr(0xFFFD)

#: What a listing that hits its own timeout takes past it, inside the bounded runner: the tree
#: kill of `ps`, then the grace its killed output is read within.
_LISTING_OVERRUN_S = (
    gecko_launch.bounded_run.KILL_TIMEOUT_SECONDS
    + gecko_launch.bounded_run.KILL_GRACE_SECONDS
)


class InterfaceTests(unittest.TestCase):
    """`gecko_launch` gives each of these as the very object its own module defines."""

    def test_each_is_the_same_object_there(self) -> None:
        for module, name in (
            (gecko_bound, "POLL_SECONDS"),
            (gecko_bound, "watchdog_seconds"),
            (gecko_process, "LISTING_TIMEOUT_SECONDS"),
            (gecko_process, "LISTING_OVERRUN_SECONDS"),
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(gecko_launch, name), getattr(module, name))

    def test_a_listings_overrun_is_the_runners_kill_and_grace(self) -> None:
        # Public, so that whatever bounds a session can leave room for the listings
        # `note_firefox` and `record_helpers` make outside the start and the stop.
        self.assertEqual(gecko_launch.LISTING_OVERRUN_SECONDS, _LISTING_OVERRUN_S)


class StartTests(GeckoCase):
    """geckodriver starts in a session of its own, and the port it names is the base URL."""

    def test_the_port_named_at_once_is_the_base_url(self) -> None:
        self.host.chunks = [listening(4444).encode()]
        with self.host.driver() as driver:
            self.assertEqual(driver.base_url, "http://127.0.0.1:4444")
        self.assertEqual(self.host.named("sleep", "kill_tree"), [])
        self.assertEqual(outside_bounds(self.host.calls), [])

    def test_it_runs_the_command_in_a_session_of_its_own(self) -> None:
        self.started()
        command = gecko_launch.driver_command("bin/geckodriver", self.scratch)
        self.assertEqual(
            self.host.named("popen"),
            [
                call(
                    "popen",
                    *command,
                    stdin=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            ],
        )

    def test_its_output_goes_to_the_scratch_log(self) -> None:
        self.started()
        assert self.host.stdout is not None
        self.assertEqual(Path(self.host.stdout.name), self.scratch.log)
        self.assertTrue(self.host.stdout.writable())

    def test_its_environment_is_this_one_redirected_into_the_scratch(self) -> None:
        with mock.patch.dict(os.environ, {"PRINT_PASS_PROBE": "kept"}):
            self.started()
        self.assertEqual(self.host.env["PRINT_PASS_PROBE"], "kept")
        self.assertEqual(self.host.env["MOZ_APP_DATA"], str(self.scratch.app_data))

    def test_a_port_named_later_is_awaited_between_short_sleeps(self) -> None:
        self.host.chunks = [b"starting\n", b"", listening(5555).encode()]
        with self.host.driver() as driver:
            self.assertEqual(driver.base_url, "http://127.0.0.1:5555")
        sleeps = self.host.named("sleep")
        self.assertEqual(sleeps, [call("sleep", gecko_launch.POLL_SECONDS)] * 2)
        self.assertLess(gecko_launch.POLL_SECONDS, 1.0)

    def test_the_start_is_bounded_above_its_own_steps(self) -> None:
        self.started()
        bound = gecko_launch.watchdog_seconds(TIMEOUT_S)
        self.assertEqual(self.host.calls[0], call("bound", START, bound))
        self.assertEqual(self.host.named("unbound")[0], call("unbound", START))


class FailedStartTests(GeckoCase):
    """A start that fails kills what it started, closes the log, and says why in one line."""

    def enter(self) -> str:
        """Enter a driver that must fail; the first line of what it raised."""
        driver = self.host.driver()
        with self.assertRaises(EngineError) as raised:
            driver.__enter__()
        message = str(raised.exception)
        self.assertNotIn("\n", message)
        self.assertEqual(self.host.calls[-1], call("unbound", START))
        assert self.host.stdout is not None
        self.assertTrue(self.host.stdout.closed)
        return message

    def test_one_that_exits_first_is_killed_reaped_and_reported(self) -> None:
        self.host.chunks = [b"error: no Firefox\n"]
        self.host.process.returncode = 1
        message = self.enter()
        self.assertEqual(
            message, "geckodriver exited with status 1 before naming its port"
        )
        self.assertEqual(
            self.host.named("kill_tree", "wait"),
            [call("kill_tree", PID), call("wait", TIMEOUT_S)],
        )

    def test_one_that_names_no_port_in_time_is_killed_reaped_and_reported(self) -> None:
        message = self.enter()
        self.assertEqual(message, "geckodriver named no port within 5 s")
        self.assertEqual(
            self.host.named("kill_tree", "wait"),
            [call("kill_tree", PID), call("wait", TIMEOUT_S)],
        )
        # Read at 0 for the deadline, then once a poll: four sleeps, and the fifth reading
        # is the one past it.
        self.assertEqual(len(self.host.named("sleep")), 4)
        self.assertEqual(outside_bounds(self.host.calls), [])

    def test_one_that_cannot_be_run_is_reported_and_nothing_killed(self) -> None:
        self.host.spawn_error = FileNotFoundError(2, "No such file or directory\nmore")
        message = self.enter()
        self.assertEqual(
            message, "geckodriver did not start: [Errno 2] No such file or directory"
        )
        self.assertEqual(self.host.named("kill_tree", "wait", "terminate"), [])

    def test_one_whose_kill_leaves_it_running_says_so(self) -> None:
        self.host.kill_ends = False
        message = self.enter()
        self.assertEqual(message, "geckodriver outlived its kill by 5 s")


class SingleUseTests(GeckoCase):
    """A driver starts geckodriver once: a second entry is refused, whatever the first did."""

    def enter_again(self, driver: gecko_launch.Geckodriver) -> None:
        """Enter `driver` again: a RuntimeError, with nothing called or started."""
        before = list(self.host.calls)
        with self.assertRaises(RuntimeError):
            driver.__enter__()
        self.assertEqual(self.host.calls, before)
        self.assertEqual(len(self.host.named("popen")), 1)

    def test_a_running_driver_is_not_started_again(self) -> None:
        self.enter_again(self.started())

    def test_a_driver_whose_geckodriver_exited_is_not_started_again(self) -> None:
        self.host.chunks = [b"error: no Firefox\n"]
        self.host.process.returncode = 1
        driver = self.host.driver()
        with self.assertRaises(EngineError):
            driver.__enter__()
        self.enter_again(driver)

    def test_a_driver_whose_geckodriver_could_not_run_is_not_started_again(
        self,
    ) -> None:
        self.host.spawn_error = FileNotFoundError(2, "No such file or directory")
        driver = self.host.driver()
        with self.assertRaises(EngineError):
            driver.__enter__()
        self.enter_again(driver)

    def test_a_stopped_driver_is_not_started_again(self) -> None:
        self.host.chunks = [listening(4444).encode()]
        with self.host.driver() as driver:
            pass
        self.enter_again(driver)


class LogTests(GeckoCase):
    """The log reads from a byte offset, as UTF-8, a bad byte becoming a replacement."""

    def test_only_what_came_after_the_offset_is_read(self) -> None:
        driver = self.started()
        offset = driver.log_offset()
        self.assertEqual(offset, len(listening(4444).encode()))
        self.host.write(b"JavaScript error: page.js, line 1: boom\n")
        self.assertEqual(
            driver.log_since(offset), "JavaScript error: page.js, line 1: boom\n"
        )

    def test_the_offset_counts_bytes_not_characters(self) -> None:
        driver = self.started()
        before = driver.log_offset()
        self.host.write(f"{_TWO_BYTE_CHARACTER}\n".encode())
        self.assertEqual(driver.log_offset() - before, 3)

    def test_past_the_cap_the_last_whole_lines_within_it_are_read(self) -> None:
        # A page can flood its console, which the log carries, for as long as the run
        # lasts: one read holds no more of it than the cap, and the end of it, where the
        # guard writes the refusal that ended the browser. The line the cut fell in is
        # dropped, so no text a page wrote mid-line is read as a line's start.
        driver = self.started()
        offset = driver.log_offset()
        self.host.write(b"0123456789\nabc\n")
        with mock.patch.object(gecko_launch, "MAX_LOG_READ_BYTES", 6):
            self.assertEqual(driver.log_since(offset), "abc\n")

    def test_a_slice_the_cap_holds_exactly_is_read_whole(self) -> None:
        driver = self.started()
        offset = driver.log_offset()
        self.host.write(b"0123\n")
        with mock.patch.object(gecko_launch, "MAX_LOG_READ_BYTES", 5):
            self.assertEqual(driver.log_since(offset), "0123\n")

    def test_a_refusal_after_a_flood_past_the_cap_is_still_read(self) -> None:
        refusal = (
            b"FATAL ERROR: Non-local network connections are disabled and a connection "
            b"attempt to 192.0.2.1 (192.0.2.1) was made.\n"
        )
        driver = self.started()
        offset = driver.log_offset()
        self.host.write(b"console.log: " + b"x" * 1000 + b"\n" + refusal)
        with mock.patch.object(gecko_launch, "MAX_LOG_READ_BYTES", len(refusal) + 10):
            self.assertIsNotNone(
                gecko_launch.isolation_breach(driver.log_since(offset))
            )

    def test_a_byte_that_is_not_utf8_reads_as_a_replacement(self) -> None:
        driver = self.started()
        offset = driver.log_offset()
        self.host.write(b"\xff\n")
        self.assertEqual(driver.log_since(offset), f"{_REPLACEMENT}\n")


class ConstructionTests(GeckoCase):
    """What the driver refuses before it starts anything, and what it has not got yet."""

    def test_there_is_no_base_url_before_the_start(self) -> None:
        with self.assertRaises(RuntimeError):
            _ = self.host.driver().base_url

    def test_a_timeout_that_is_not_positive_is_refused(self) -> None:
        for timeout_s in (0.0, -1.0, math.nan):
            with self.subTest(timeout_s=timeout_s), self.assertRaises(ValueError):
                self.host.driver(timeout_s)
        self.assertEqual(self.host.calls, [])

    def test_the_stop_bound_sits_above_every_step_inside_it(self) -> None:
        # A stop waits up to the timeout for geckodriver, may kill the tree and reap for up
        # to the timeout again, then polls up to the timeout for the rest of its group, and
        # may kill and poll once more. With helpers recorded, it then polls for them three
        # times at most, a SIGTERM and then a tree kill of each one's group between, each
        # poll's last listing bounded by the listing's own timeout; one listing past that
        # timeout also waits out the runner's tree kill of `ps` and its grace, then ends the
        # stop. Each poll may sleep once more after its deadline, which a timeout shorter
        # than that sleep cannot cover as slack. The watchdog must fire only when one of
        # those bounds failed.
        kill_s = gecko_launch.bounded_run.KILL_TIMEOUT_SECONDS
        poll_s = gecko_launch.POLL_SECONDS
        listing_s = gecko_process.LISTING_TIMEOUT_SECONDS
        for timeout_s in (0.001, 0.01, poll_s, 0.5, 30.0):
            for helpers in (0, 1, 3):
                helper_polls = 3 if helpers else 0
                steps = (
                    (4 + helper_polls) * timeout_s
                    + (2 + helpers) * kill_s
                    + (2 + helper_polls) * poll_s
                    + helper_polls * listing_s
                    + (_LISTING_OVERRUN_S if helpers else 0.0)
                )
                with self.subTest(timeout_s=timeout_s, helpers=helpers):
                    bound = gecko_launch.watchdog_seconds(timeout_s, helpers)
                    self.assertGreater(bound, steps)

    def test_the_start_bound_sits_above_every_step_inside_it(self) -> None:
        # A start lists every process, waits up to the timeout for the port, and may kill the
        # tree and reap for up to the timeout again. A listing past its own timeout waits out
        # the runner's tree kill of `ps` and its grace, then ends the start with nothing run.
        kill_s = gecko_launch.bounded_run.KILL_TIMEOUT_SECONDS
        listing_s = gecko_process.LISTING_TIMEOUT_SECONDS
        for timeout_s in (0.001, gecko_launch.POLL_SECONDS, 0.5, 30.0):
            with self.subTest(timeout_s=timeout_s):
                started = listing_s + 2 * timeout_s + kill_s
                refused = listing_s + _LISTING_OVERRUN_S
                bound = gecko_launch.watchdog_seconds(timeout_s)
                self.assertGreater(bound, started)
                self.assertGreater(bound, refused)


if __name__ == "__main__":
    unittest.main()
