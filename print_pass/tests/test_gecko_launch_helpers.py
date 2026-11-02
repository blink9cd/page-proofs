"""Suite: `Geckodriver` and Firefox's crash helper: recorded, awaited, and ended, never mistaken.

Firefox's crash helper leaves geckodriver's group (measured: launchd its parent, a group of its
own), so the group's end says nothing of it. The driver lists every process before it starts
geckodriver, notes each Firefox an engine names that runs in geckodriver's group, and records
each such Firefox's helper while that Firefox still runs there, if the same listing shows the
helper no older than that Firefox. Once the group has ended it polls a listing for the
recorded helpers, signaling one only past the timeout. Every seam is faked, by
`_gecko_launch_fixtures`, and each look-alike here (the reader's own helper above all, and
any helper never recorded) must never be signaled.

Entry point assumptions: standalone, or discovered by `unittest discover` with this folder
as its top-level directory, which is how the shared fixtures beside it are found.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_launch  # noqa: E402 - the tool's private src/, via the insert above
from _gecko_launch_fixtures import (  # noqa: E402 - beside this module
    FIREFOX,
    FIREFOX_ELAPSED_S,
    FIREFOX_PID,
    HELPER,
    HELPER_ELAPSED_S,
    HELPER_PGID,
    HELPER_PID,
    LAUNCHD_PID,
    PID,
    START,
    STOP,
    TIMEOUT_S,
    FakeHost,
    GeckoCase,
    helper_args,
    listening,
    outside_bounds,
)
from _print_playwright_fixtures import call  # noqa: E402 - beside this module
from gecko_process import ProcessRow  # noqa: E402 - the tool's private src/
from print_record import EngineError  # noqa: E402 - as above

#: The one line a helper that outlives its kill is reported with.
_OUTLIVED = "^Firefox's crash helper outlived its kill by 5 s$"

#: The stop's bound while no helper was recorded.
_NO_HELPER_BOUND = gecko_launch.watchdog_seconds(TIMEOUT_S)

#: The reader's own Firefox and its helper, started after ours: never ours.
_THEIR_HELPER = ProcessRow(6000, LAUNCHD_PID, 5999, 5, helper_args(7777))

#: A helper naming our Firefox's pid that ran before our start: the reader's, its Firefox
#: having had the pid ours was given since. It is listed as old as ours, so that only its
#: running before our start tells it apart.
_EARLIER_HELPER = ProcessRow(
    6100, LAUNCHD_PID, 6099, FIREFOX_ELAPSED_S, helper_args(FIREFOX_PID)
)

#: A process whose arguments quote our helper's, as a search for it would.
_SEARCH = ProcessRow(
    6200,
    6201,
    6200,
    1,
    f"/usr/bin/grep crashhelper {FIREFOX_PID} gecko-crash-server-pipe",
)

#: Every look-alike above, each in a group that must never be signaled.
_LOOK_ALIKES = (_THEIR_HELPER, _EARLIER_HELPER, _SEARCH)

#: A second helper naming our Firefox, with a token of its own, in a group of its own.
_SECOND_HELPER = ProcessRow(
    5160, LAUNCHD_PID, 5159, HELPER_ELAPSED_S, helper_args(FIREFOX_PID, "2")
)

#: The reader's helper, left behind when its Firefox exited, naming the pid ours was given
#: once the pid space wrapped round: started after our start, and a second older than ours.
_LINGERING_HELPER = ProcessRow(
    6300, LAUNCHD_PID, 6299, FIREFOX_ELAPSED_S + 1, helper_args(FIREFOX_PID, "3")
)

#: The reader's own Firefox, given the pid ours had once ours exited: in a group not ours.
_THEIR_FIREFOX_ON_OUR_PID = FIREFOX._replace(ppid=LAUNCHD_PID, pgid=FIREFOX_PID)


class HelperCase(GeckoCase):
    """A driver whose geckodriver names its port at once, with `FIREFOX` noted or not."""

    def afresh(self) -> None:
        """A new host, for a case that starts a driver more than once: no listing carries over."""
        self.host = FakeHost(self.scratch)

    def entered(self) -> gecko_launch.Geckodriver:
        """A started driver; the case stops it itself."""
        self.host.chunks = [listening(4444).encode()]
        return self.host.driver().__enter__()

    def noted(
        self, helpers: tuple[ProcessRow, ...] = (HELPER,)
    ) -> gecko_launch.Geckodriver:
        """A started driver with `FIREFOX` noted, as an engine notes the one its session names.

        The note's listing shows `helpers` beside Firefox. Firefox ends with its session,
        before the stop, so later listings show neither it nor them unless a case says so.
        """
        driver = self.entered()
        self.host.others = (FIREFOX, *helpers)
        driver.note_firefox(FIREFOX_PID)
        self.host.others = ()
        return driver

    def stop(self, driver: gecko_launch.Geckodriver) -> list[tuple[str, ...]]:
        """Stop `driver`; the calls its stop made, in order."""
        self.host.calls.clear()
        driver.__exit__(None, None, None)
        return list(self.host.calls)

    def signaled(self, calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
        """The signals in `calls` sent anywhere but geckodriver's own group."""
        return [
            entry
            for entry in calls
            if entry[0] in ("terminate", "kill_tree") and entry[1] != str(PID)
        ]


class StartListingTests(HelperCase):
    """Every process is listed before geckodriver starts; a listing that fails starts nothing."""

    def test_the_listing_comes_first_inside_the_start_bound(self) -> None:
        self.started()
        self.assertEqual(
            self.host.calls[:3],
            [
                call("bound", START, gecko_launch.watchdog_seconds(TIMEOUT_S)),
                call("processes"),
                self.host.named("popen")[0],
            ],
        )

    def test_a_failed_listing_starts_nothing_and_says_why(self) -> None:
        self.host.listing_error = EngineError(
            "ps hit its 10 s timeout listing processes"
        )
        driver = self.host.driver()
        with self.assertRaisesRegex(EngineError, "^ps hit its 10 s timeout"):
            driver.__enter__()
        self.assertEqual(self.host.named("popen", "kill_tree", "terminate"), [])
        self.assertIsNone(self.host.stdout)
        self.assertFalse(self.scratch.log.exists())
        self.assertEqual(self.host.calls[-1], call("unbound", START))


class NoteFirefoxTests(HelperCase):
    """A Firefox is noted only while it runs in geckodriver's group."""

    def test_a_firefox_in_the_group_is_noted_and_its_helper_recorded_and_looked_for(
        self,
    ) -> None:
        calls = self.stop(self.noted())
        self.assertEqual(
            [entry for entry in calls if entry[0] in ("group_alive", "processes")],
            [call("group_alive", PID), call("processes")],
        )

    def test_a_process_outside_the_group_is_refused_and_not_noted(self) -> None:
        driver = self.entered()
        self.host.others = (FIREFOX._replace(pgid=FIREFOX_PID), _THEIR_HELPER, HELPER)
        for pid in (FIREFOX_PID, _THEIR_HELPER.pid, 0, -1):
            with self.subTest(pid=pid):
                with self.assertRaisesRegex(
                    EngineError,
                    f"^Firefox {pid} is not running in geckodriver's group$",
                ):
                    driver.note_firefox(pid)
        # Gone by the stop: a helper recorded past the group check then fails this case by
        # the listing the stop makes for it, not by the kill it would outlive here.
        self.host.others = ()
        self.assertNotIn(call("processes"), self.stop(driver))

    def test_nothing_is_noted_before_the_start_or_after_the_stop(self) -> None:
        self.host.chunks = [listening(4444).encode()]
        driver = self.host.driver()
        with self.assertRaises(RuntimeError):
            driver.note_firefox(FIREFOX_PID)
        driver.__enter__()
        self.stop(driver)
        self.host.calls.clear()
        with self.assertRaises(RuntimeError):
            driver.note_firefox(FIREFOX_PID)
        self.assertEqual(self.host.calls, [])

    def test_the_only_call_outside_a_bound_is_the_notes_own_bounded_listing(
        self,
    ) -> None:
        # `list_processes` bounds that one itself.
        driver = self.noted()
        driver.__exit__(None, None, None)
        self.assertEqual(outside_bounds(self.host.calls), [call("processes")])


class HelperStopTests(HelperCase):
    """Once the group has ended, our helper is awaited, then SIGTERM'd, then killed as a tree."""

    def test_without_a_noted_firefox_nothing_is_listed(self) -> None:
        self.assertNotIn(call("processes"), self.stop(self.entered()))

    def test_the_helpers_are_looked_for_only_once_the_group_has_ended(self) -> None:
        driver = self.noted()
        self.host.stragglers = 1
        calls = self.stop(driver)
        probes = [entry for entry in calls if entry[0] in ("group_alive", "processes")]
        self.assertEqual(
            probes,
            [call("group_alive", PID), call("group_alive", PID), call("processes")],
        )

    def test_a_helper_already_gone_is_neither_awaited_nor_signaled(self) -> None:
        calls = self.stop(self.noted())
        self.assertEqual(self.signaled(calls), [])
        self.assertEqual([entry for entry in calls if entry[0] == "sleep"], [])

    def test_a_helper_that_ends_on_its_own_is_awaited_between_short_sleeps(
        self,
    ) -> None:
        driver = self.noted()
        self.host.helpers, self.host.helper_listings = (HELPER,), 2
        calls = self.stop(driver)
        self.assertEqual(self.signaled(calls), [])
        self.assertEqual(
            [entry for entry in calls if entry[0] in ("processes", "sleep")],
            [*[call("processes"), call("sleep", gecko_launch.POLL_SECONDS)] * 2]
            + [call("processes")],
        )

    def test_a_helper_past_the_timeout_gets_its_groups_sigterm(self) -> None:
        driver = self.noted()
        self.host.helpers, self.host.helper_listings = (HELPER,), math.inf
        self.assertEqual(
            self.signaled(self.stop(driver)), [call("terminate", HELPER_PGID)]
        )

    def test_a_helper_that_outlives_its_sigterm_gets_a_tree_kill(self) -> None:
        driver = self.noted()
        self.host.helpers, self.host.helper_listings = (HELPER,), math.inf
        self.host.helper_term_ends = False
        self.assertEqual(
            self.signaled(self.stop(driver)),
            [call("terminate", HELPER_PGID), call("kill_tree", HELPER_PGID)],
        )

    def test_a_helper_that_outlives_its_kill_is_reported_and_the_log_closed(
        self,
    ) -> None:
        driver = self.noted()
        self.host.helpers, self.host.helper_listings = (HELPER,), math.inf
        self.host.helper_term_ends = self.host.helper_kill_ends = False
        with self.assertRaisesRegex(EngineError, _OUTLIVED):
            self.stop(driver)
        self.assertEqual(self.host.calls[-1], call("unbound", STOP))
        assert self.host.stdout is not None
        self.assertTrue(self.host.stdout.closed)

    def test_each_helper_group_is_signaled_once(self) -> None:
        second = HELPER._replace(pid=HELPER_PID + 1)
        driver = self.noted((HELPER, second))
        self.host.helpers, self.host.helper_listings = (HELPER, second), math.inf
        self.assertEqual(
            self.signaled(self.stop(driver)), [call("terminate", HELPER_PGID)]
        )

    def test_a_look_alike_is_never_signaled_beside_ours(self) -> None:
        self.host.before = (_EARLIER_HELPER,)
        driver = self.noted((HELPER, *_LOOK_ALIKES))
        self.host.others = _LOOK_ALIKES
        self.host.helpers, self.host.helper_listings = (HELPER,), math.inf
        self.host.helper_term_ends = self.host.helper_kill_ends = False
        with self.assertRaisesRegex(EngineError, _OUTLIVED):
            self.stop(driver)
        self.assertEqual(
            self.signaled(self.host.calls),
            [call("terminate", HELPER_PGID), call("kill_tree", HELPER_PGID)],
        )

    def test_look_alikes_alone_are_neither_awaited_nor_signaled(self) -> None:
        self.host.before = (_EARLIER_HELPER,)
        driver = self.noted(_LOOK_ALIKES)
        self.host.others = _LOOK_ALIKES
        calls = self.stop(driver)
        self.assertEqual(self.signaled(calls), [])
        self.assertEqual([entry for entry in calls if entry[0] == "sleep"], [])

    def test_a_watchdog_kill_while_the_helpers_are_polled_signals_nothing(
        self,
    ) -> None:
        # geckodriver is reaped and its group gone by then: its id may be another process's.
        driver = self.noted()
        self.host.helpers, self.host.helper_listings = (HELPER,), 2
        self.host.on_listing = driver.kill
        calls = self.stop(driver)
        self.assertEqual([entry for entry in calls if entry[0] == "kill_tree"], [])

    def test_the_stop_bound_counts_the_recorded_helpers(self) -> None:
        for helpers in ((), (HELPER,), (HELPER, _SECOND_HELPER)):
            with self.subTest(helpers=len(helpers)):
                self.afresh()
                calls = self.stop(self.noted(helpers))
                bound = gecko_launch.watchdog_seconds(TIMEOUT_S, len(helpers))
                self.assertEqual(calls[0], call("bound", STOP, bound))
        self.assertGreater(
            gecko_launch.watchdog_seconds(TIMEOUT_S, 1),
            gecko_launch.watchdog_seconds(TIMEOUT_S),
        )


class RecordHelpersTests(HelperCase):
    """Only a helper recorded while its Firefox ran in geckodriver's group is ever signaled."""

    def test_a_helper_never_recorded_is_never_listed_for_or_signaled(self) -> None:
        # It names a noted Firefox, launchd is its parent and its group is its own: all a
        # helper of ours shows, but for a recording made while that Firefox ran.
        driver = self.noted(())
        self.host.helpers, self.host.helper_listings = (HELPER,), math.inf
        calls = self.stop(driver)
        self.assertEqual(self.signaled(calls), [])
        self.assertNotIn(call("processes"), calls)

    def test_beside_a_recorded_helper_one_never_recorded_is_never_signaled(
        self,
    ) -> None:
        driver = self.noted()
        self.host.others = (_SECOND_HELPER,)
        self.host.helpers, self.host.helper_listings = (HELPER,), math.inf
        self.assertEqual(
            self.signaled(self.stop(driver)), [call("terminate", HELPER_PGID)]
        )

    def test_a_recorded_pid_now_showing_other_arguments_is_never_signaled(
        self,
    ) -> None:
        for args in (helper_args(FIREFOX_PID, "2"), f"{HELPER.args} x", "/bin/sleep 9"):
            with self.subTest(args=args):
                self.afresh()
                driver = self.noted()
                self.host.others = (HELPER._replace(args=args),)
                calls = self.stop(driver)
                # Looked for, so a helper was recorded; never awaited, never signaled.
                self.assertIn(call("processes"), calls)
                self.assertEqual(self.signaled(calls), [])
                self.assertEqual([entry for entry in calls if entry[0] == "sleep"], [])

    def test_a_helper_started_after_the_note_is_recorded_while_firefox_runs(
        self,
    ) -> None:
        driver = self.noted(())
        self.host.others = (FIREFOX, HELPER)
        driver.record_helpers()
        self.host.others = ()
        self.host.helpers, self.host.helper_listings = (HELPER,), math.inf
        self.assertEqual(
            self.signaled(self.stop(driver)), [call("terminate", HELPER_PGID)]
        )

    def test_once_the_firefox_has_exited_nothing_new_is_recorded(self) -> None:
        # Its pid may then be the reader's next Firefox's, whose helper names it as ours did.
        for others in ((HELPER,), (_THEIR_FIREFOX_ON_OUR_PID, HELPER)):
            with self.subTest(listed=len(others)):
                self.afresh()
                driver = self.noted(())
                self.host.others = others
                driver.record_helpers()
                calls = self.stop(driver)
                self.assertEqual(self.signaled(calls), [])
                self.assertEqual(calls[0], call("bound", STOP, _NO_HELPER_BOUND))
                self.assertNotIn(call("processes"), calls)

    def test_a_helper_recorded_again_is_one_helper(self) -> None:
        # A later listing shows it older; by its pid and arguments it is the same helper.
        driver = self.noted()
        self.host.others = (FIREFOX, HELPER)
        driver.record_helpers()
        self.host.others = (
            FIREFOX._replace(elapsed_s=FIREFOX_ELAPSED_S + 5),
            HELPER._replace(elapsed_s=HELPER_ELAPSED_S + 5),
        )
        driver.record_helpers()
        self.host.others = ()
        bound = gecko_launch.watchdog_seconds(TIMEOUT_S, 1)
        self.assertEqual(self.stop(driver)[0], call("bound", STOP, bound))

    def test_a_look_alike_is_never_recorded(self) -> None:
        self.host.before = (_EARLIER_HELPER,)
        driver = self.noted(())
        self.host.others = (FIREFOX, *_LOOK_ALIKES)
        driver.record_helpers()
        self.host.others = _LOOK_ALIKES
        calls = self.stop(driver)
        self.assertEqual(self.signaled(calls), [])
        self.assertNotIn(call("processes"), calls)

    def test_nothing_is_recorded_before_the_start_or_after_the_stop(self) -> None:
        self.host.chunks = [listening(4444).encode()]
        driver = self.host.driver()
        with self.assertRaises(RuntimeError):
            driver.record_helpers()
        driver.__enter__()
        self.stop(driver)
        self.host.calls.clear()
        with self.assertRaises(RuntimeError):
            driver.record_helpers()
        self.assertEqual(self.host.calls, [])

    def test_its_listing_is_bounded_by_itself_outside_the_watchdog(self) -> None:
        # `list_processes` bounds it; it runs inside a session, not inside a start or a stop.
        driver = self.noted()
        driver.record_helpers()
        driver.__exit__(None, None, None)
        self.assertEqual(outside_bounds(self.host.calls), [call("processes")] * 2)


class HelperAgeTests(HelperCase):
    """A helper is recorded only if no older than the Firefox it names, in the same listing."""

    def test_a_lingering_helper_older_than_our_firefox_is_never_recorded_or_signaled(
        self,
    ) -> None:
        # The reader's Firefox started it and exited, and ours was given that Firefox's pid.
        # It started after our start, so only its age tells it from ours.
        driver = self.noted((_LINGERING_HELPER,))
        self.host.others = (FIREFOX, _LINGERING_HELPER)
        driver.record_helpers()
        self.host.others = ()
        self.host.helpers, self.host.helper_listings = (_LINGERING_HELPER,), math.inf
        calls = self.stop(driver)
        self.assertEqual(self.signaled(calls), [])
        self.assertNotIn(call("processes"), calls)

    def test_a_helper_younger_than_or_as_old_as_its_firefox_is_recorded(self) -> None:
        for elapsed_s in (0, HELPER_ELAPSED_S, FIREFOX_ELAPSED_S):
            with self.subTest(elapsed_s=elapsed_s):
                self.afresh()
                helper = HELPER._replace(elapsed_s=elapsed_s)
                driver = self.noted((helper,))
                self.host.helpers, self.host.helper_listings = (helper,), math.inf
                self.assertEqual(
                    self.signaled(self.stop(driver)), [call("terminate", HELPER_PGID)]
                )

    def test_an_age_that_could_not_be_read_is_never_recorded(self) -> None:
        # The helper's, or its Firefox's: either way the two cannot be compared.
        for listed in (
            (FIREFOX, HELPER._replace(elapsed_s=None)),
            (FIREFOX._replace(elapsed_s=None), HELPER),
        ):
            with self.subTest(listed=listed):
                self.afresh()
                driver = self.entered()
                self.host.others = listed
                driver.note_firefox(FIREFOX_PID)
                driver.record_helpers()
                self.host.others = ()
                self.host.helpers, self.host.helper_listings = (HELPER,), math.inf
                calls = self.stop(driver)
                self.assertEqual(self.signaled(calls), [])
                self.assertNotIn(call("processes"), calls)


if __name__ == "__main__":
    unittest.main()
