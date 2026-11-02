"""Grouped suite: what the live Firefox suite's watch is built from, proven without Firefox.

While the live Firefox run lasts, a watcher lists every process and records the fenced shim's
process group and its Firefox's crash helper; after the run it reports what survived, and kills
that alone, by recorded rows. And the run's lookups are read from Firefox's host-resolver log.
Both are checked here on stand-in listings and log lines: which rows the watcher records,
reports as survivors and signals (never one by its name), how its watch ends, and which
log lines count as a lookup.

Entry point assumptions: discovered by the folder's aggregator, or run alone from this
folder; the fixtures' bootstrap puts the tool's folder and its private `src/` on the path.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Final

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
from _gecko_watch_fixtures import JOIN_SECONDS, GroupWatcher, looked_up_hosts

from gecko_process import ProcessRow  # the tool's private src/
from print_record import EngineError  # as above

#: Pids the stand-in listings use: geckodriver's group, a Firefox in it, its crash helper,
#: and a helper of the reader's own that names a Firefox outside the group.
_GROUP: Final = 500
_FIREFOX: Final = 501
_HELPER: Final = 900
_READERS_HELPER: Final = 950
_LAUNCHD: Final = 1


def _helper_args(firefox: int, token: str = "a1") -> str:
    """A crash helper's arguments, as measured, serving `firefox`."""
    return (
        f"/Applications/Firefox.app/Contents/MacOS/crashhelper {firefox} "
        f"gecko-crash-server-pipe.{firefox} /srv/tmp org.mozilla.crashhelper.{token} 1"
    )


class HostLookupTest(unittest.TestCase):
    """Firefox's host-resolver log: each host it resolved or asked the system for, and no other."""

    def test_each_resolved_or_asked_host_is_a_lookup(self) -> None:
        log = "\n".join(
            (
                "[Parent 1: Socket Thread]: D/nsHostResolver Resolving host"
                " [127.0.0.1]<> type 0. [this=1]",
                "[Parent 1: DNS Resolver #1]: E/nsHostResolver DNS resolve task -"
                " Calling getaddrinfo for host [print-pass.invalid].",
                "[Parent 1: Socket Thread]: D/nsHostResolver   DNS lookup for host"
                " [192.0.2.1] blocking pending 'getaddrinfo' or trr query",
            )
        )
        self.assertEqual(
            looked_up_hosts(log), ("127.0.0.1", "print-pass.invalid", "192.0.2.1")
        )

    def test_a_line_naming_a_host_without_looking_it_up_is_not_a_lookup(self) -> None:
        log = "\n".join(
            (
                "D/nsHostResolver   Using cached address for IP Literal [127.0.0.1].",
                "D/nsHostResolver   Host is IP Literal [127.0.0.1].",
                "D/nsHostResolver TRRService::ReadPrefs"
                " network.trr.builtin-excluded-domains host:[localhost]",
                "D/nsHostResolver confirmationNS = example.com",
            )
        )
        self.assertEqual(looked_up_hosts(log), ())


class _Listings:
    """Stand-in process listings: each call gives the next, the last one again once spent."""

    def __init__(self, *listings: tuple[ProcessRow, ...]) -> None:
        """Keep the listings, in the order they are given out."""
        self._listings = list(listings)
        self.calls = 0

    def __call__(self) -> tuple[ProcessRow, ...]:
        """The next listing."""
        self.calls += 1
        return self._listings[min(self.calls, len(self._listings)) - 1]


class _Clock:
    """A clock that moves on by a second each time it is read."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """The time, then a second later."""
        self.now += 1.0
        return self.now


def _frozen() -> float:
    """A clock that never moves: a watch on it ends only when stopped."""
    return 0.0


#: The run's bound each stand-in watch is given.
_BOUND: Final = 3.0


def _row(pid: int, ppid: int, pgid: int, age: int, args: str) -> ProcessRow:
    """One listed process."""
    return ProcessRow(pid, ppid, pgid, age, args)


#: Before the run: the reader's own helper, and nothing of the run's.
_BEFORE: Final = (
    _row(_READERS_HELPER, _LAUNCHD, _READERS_HELPER, 99, _helper_args(42)),
)

#: During it: geckodriver and its Firefox in the group, Firefox's helper outside it, and
#: the reader's helper, naming a Firefox that is not in the group.
_DURING: Final = (
    *_BEFORE,
    _row(_GROUP, 2, _GROUP, 5, "geckodriver --port 0"),
    _row(_FIREFOX, _GROUP, _GROUP, 4, "firefox --marionette"),
    _row(_HELPER, _LAUNCHD, _HELPER, 3, _helper_args(_FIREFOX)),
)


class GroupWatcherTest(unittest.TestCase):
    """The watcher records the shim's group and its Firefox's helper, by pid and arguments only."""

    def setUp(self) -> None:
        """A folder for the shim's pid file, holding the group's id."""
        folder = tempfile.TemporaryDirectory(prefix="print_pass-watch-")
        self.addCleanup(folder.cleanup)
        self.pid_file = Path(folder.name) / "shim.pid"
        self.pid_file.write_text(f"{_GROUP}\n", encoding="utf-8", newline="\n")
        self.killed: list[int] = []

    def watcher(
        self, listings: _Listings, *, clock: Callable[[], float] = _frozen
    ) -> GroupWatcher:
        """A watcher over `listings`, polling at once, its bound 3 s on a stand-in clock.

        The clock stands still unless one is given, so a watch entered and left at once is
        ended by its stop alone, however its thread is scheduled.
        """
        return GroupWatcher(
            self.pid_file,
            bound_seconds=_BOUND,
            processes=listings,
            clock=clock,
            poll_seconds=0.0,
            kill_tree=self.killed.append,
        )

    def test_the_group_and_its_firefoxs_helper_are_recorded_and_nothing_else(
        self,
    ) -> None:
        # The watch lists once before it checks whether it was stopped, so a watch stopped
        # at once has still listed the run's processes.
        watcher = self.watcher(_Listings(_BEFORE, _DURING))
        with watcher:
            pass
        self.assertEqual(watcher.group, _GROUP)
        self.assertEqual(
            sorted(watcher.members),
            [(_GROUP, _DURING[1].args), (_FIREFOX, _DURING[2].args)],
        )
        self.assertEqual(list(watcher.helpers), [(_HELPER, _helper_args(_FIREFOX))])
        self.assertEqual(watcher.errors, [])

    def test_nothing_is_recorded_before_the_shim_names_its_group(self) -> None:
        self.pid_file.unlink()
        watcher = self.watcher(_Listings(_BEFORE, _DURING))
        with watcher:
            pass
        self.assertIsNone(watcher.group)
        self.assertEqual((watcher.members, watcher.helpers), ({}, {}))

    def test_a_listing_that_fails_is_an_error_and_ends_the_watch(self) -> None:
        calls: list[int] = []

        def first_then_failing() -> tuple[ProcessRow, ...]:
            calls.append(len(calls))
            if len(calls) > 1:
                raise EngineError("ps exited 1 listing processes")
            return _BEFORE

        watcher = GroupWatcher(
            self.pid_file,
            bound_seconds=3.0,
            processes=first_then_failing,
            clock=_Clock(),
        )
        with watcher:
            pass
        self.assertEqual(watcher.errors, ["EngineError: ps exited 1 listing processes"])
        self.assertEqual(len(calls), 2)

    def test_a_pid_file_holding_no_pid_is_an_error(self) -> None:
        self.pid_file.write_text("half\n", encoding="utf-8", newline="\n")
        watcher = self.watcher(_Listings(_BEFORE, _DURING))
        with watcher:
            pass
        self.assertEqual(len(watcher.errors), 1)
        self.assertTrue(watcher.errors[0].startswith("ValueError: "), watcher.errors)

    def test_survivors_are_the_groups_rows_and_the_exact_helper_rows(self) -> None:
        watcher = self.watcher(_Listings(_BEFORE, _DURING))
        with watcher:
            pass
        # The helper's pid, given since to a process whose arguments carry another token.
        reused = _row(_HELPER, _LAUNCHD, _HELPER, 1, _helper_args(_FIREFOX, token="b2"))
        self.assertEqual(
            watcher.survivors((_BEFORE[0], _DURING[2], reused)), (_DURING[2],)
        )
        self.assertEqual(watcher.survivors(_DURING), _DURING[1:])

    def test_only_a_group_still_showing_a_recorded_row_is_signaled(self) -> None:
        watcher = self.watcher(_Listings(_BEFORE, _DURING))
        with watcher:
            pass
        # The group's id, taken since by a process the watch never saw.
        watcher.end_survivors((_row(777, _LAUNCHD, _GROUP, 1, "another program"),))
        self.assertEqual(self.killed, [])
        watcher.end_survivors(_DURING)
        self.assertEqual(self.killed, [_GROUP, _HELPER])

    def test_the_watch_outlasts_the_runs_bound_by_its_join_then_records_its_end(
        self,
    ) -> None:
        """Never stopped, the watch lists until the run's bound and its join have passed.

        On a clock a second a read, the deadline is read at 1 s, and each later read below
        it lists once more.
        """
        listings = _Listings(_BEFORE, _DURING)
        watcher = self.watcher(listings, clock=_Clock())
        watcher.watch()
        limit = _BOUND + JOIN_SECONDS
        self.assertEqual(listings.calls, math.ceil(limit) - 1)
        self.assertEqual(
            watcher.errors,
            [f"the watch reached its bound of {limit:g} s without being stopped"],
        )

    def test_a_watch_stopped_as_its_bound_passes_records_no_end(self) -> None:
        """Stopped between its last wait and its deadline's check: the stop ended it.

        The clock reads the deadline's start, then the loop's first check, then a time at
        the deadline, and the watch is stopped at that last read.
        """
        limit = _BOUND + JOIN_SECONDS
        reads = iter((0.0, 0.0, limit))
        listings = _Listings(_BEFORE, _DURING)
        watcher: GroupWatcher

        def clock() -> float:
            now = next(reads)
            if now >= limit:
                watcher.stop()
            return now

        watcher = self.watcher(listings, clock=clock)
        watcher.watch()
        self.assertEqual(listings.calls, 1)
        self.assertEqual(watcher.errors, [])


if __name__ == "__main__":
    unittest.main()
