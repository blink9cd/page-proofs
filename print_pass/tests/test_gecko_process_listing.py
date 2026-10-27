"""Suite: the process listing, and which crash helper in it is ours -- never one that only looks it.

Firefox's crash helper leaves geckodriver's group, so a driver records it from a listing of
every process while its Firefox runs, and the stop finds it again by that record. The reader's
own Firefox runs one too, and one may linger after its Firefox exits, so a helper is recorded
only by properties that together rule out every other process, its age beside its Firefox's
among them; the stop takes only a recorded one, and every look-alike here must be passed over.
The listing itself runs through a faked runner, but for one case that reads this host's real
listing and finds this very process in it, its age read.

Entry point assumptions: standalone, or discovered by `unittest discover`. The insert below
reaches the subject's private `src/` beside this folder, never the working directory.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_process  # noqa: E402 - the tool's private src/, via the insert above
from _gecko_launch_fixtures import (  # noqa: E402 - beside this module
    FIREFOX,
    FIREFOX_ELAPSED_S,
    FIREFOX_PID,
    HELPER,
    HELPER_ELAPSED_S,
    HELPER_PGID,
    HELPER_PID,
    LAUNCHD_PID,
    helper_args,
)
from gecko_process import ProcessRow  # noqa: E402 - the tool's private src/
from print_record import EngineError  # noqa: E402 - as above

#: The listing's command: every process, wide, with its id, parent, group, elapsed time and
#: arguments.
_PS = ["ps", "-A", "-ww", "-o", "pid=,ppid=,pgid=,etime=,args="]

#: Lines laid out as macOS's `ps` writes them for that command: each number right-aligned in a
#: column of five, then the elapsed time right-aligned in one of eleven, then one space, then
#: the arguments as they were given. The columns, and the elapsed time's `mm:ss`, `hh:mm:ss`
#: and `dd-hh:mm:ss` forms with the days zero-padded, are as measured on macOS. The last line
#: is synthetic: a six-digit pid, wider than its column, which no listing showed.
_LISTING_LINES = (
    "    1     0     1 12-03:04:05 /sbin/launchd",
    " 4242 53349  4242       01:02 geckodriver --host 127.0.0.1 --port 0",
    f" {HELPER_PID}     1  {HELPER_PGID}       00:{HELPER_ELAPSED_S} "
    + helper_args(FIREFOX_PID),
    "123456     1 123456    02:03:04 /usr/libexec/a daemon with a long pid",
)

#: The rows those lines are.
_LISTING_ROWS = (
    ProcessRow(1, 0, 1, ((12 * 24 + 3) * 60 + 4) * 60 + 5, "/sbin/launchd"),
    ProcessRow(4242, 53349, 4242, 62, "geckodriver --host 127.0.0.1 --port 0"),
    HELPER,
    ProcessRow(
        123456,
        1,
        123456,
        (2 * 60 + 3) * 60 + 4,
        "/usr/libexec/a daemon with a long pid",
    ),
)

#: Another Firefox the reader runs, and its own crash helper: never ours.
_THEIR_FIREFOX_PID = 7777
_THEIR_HELPER = ProcessRow(6000, LAUNCHD_PID, 5999, 5, helper_args(_THEIR_FIREFOX_PID))

#: A digit `int()` reads as 4 that no listing holds: ARABIC-INDIC DIGIT FOUR.
_NON_ASCII_FOUR = chr(0x0664)


def _helper_row(args: str, *, pid: int = HELPER_PID) -> ProcessRow:
    """A row shaped as our helper's, launchd its parent and a group of its own, with `args`."""
    return ProcessRow(
        pid=pid,
        ppid=LAUNCHD_PID,
        pgid=HELPER_PGID,
        elapsed_s=HELPER_ELAPSED_S,
        args=args,
    )


class FakeRunner:
    """The bounded runner, recorded: it answers as scripted, or raises the scripted error."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        """Answer every run with this output and exit, until a case says otherwise."""
        self.calls: list[tuple[list[str], float]] = []
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.error: BaseException | None = None

    def __call__(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """Record the run; answer with the scripted output, or raise the scripted error."""
        self.calls.append((list(argv), timeout))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(
            list(argv), self.returncode, self.stdout, self.stderr
        )


class ParseListingTests(unittest.TestCase):
    """Each row `ps` printed is read whole; a line that is not one is skipped, never raised on."""

    def test_the_listing_lines_are_their_rows(self) -> None:
        text = "\n".join(_LISTING_LINES) + "\n"
        self.assertEqual(gecko_process.parse_listing(text), _LISTING_ROWS)

    def test_a_row_with_no_arguments_has_empty_ones(self) -> None:
        self.assertEqual(
            gecko_process.parse_listing("   77     1    77       00:05\n"),
            (ProcessRow(77, 1, 77, 5, ""),),
        )

    def test_an_elapsed_time_that_cannot_be_read_keeps_its_row_with_none(self) -> None:
        # The row is still a process the stop may need to find; only its age is unknown.
        for elapsed in ("??", "1:2:3", "24:00:00", f"00:0{_NON_ASCII_FOUR}"):
            with self.subTest(elapsed=elapsed):
                self.assertEqual(
                    gecko_process.parse_listing(
                        f"   77     1    77 {elapsed} /bin/x\n"
                    ),
                    (ProcessRow(77, 1, 77, None, "/bin/x"),),
                )

    def test_a_line_that_is_not_a_row_is_skipped(self) -> None:
        four = _NON_ASCII_FOUR
        for line in (
            "",
            "  PID  PPID  PGID ELAPSED ARGS",
            "   12    1",
            "   12    1    12",
            f"   1{four}    1    12 00:01 x",
            "   -1    1    12 00:01 x",
            "1234567890     1     1 00:01 a pid of ten digits",
            "   12    1   12x 00:01 y",
        ):
            with self.subTest(line=line):
                self.assertEqual(gecko_process.parse_listing(line + "\n"), ())

    def test_only_a_newline_ends_a_row(self) -> None:
        # `ps` escapes a newline inside an argument, and each ASCII line break, but writes a
        # Unicode line separator as it is (measured): none may split a row or forge one.
        forged = f"{HELPER_PID}     1  {HELPER_PGID} 00:01 {helper_args(FIREFOX_PID)}"
        for brk in ("\r", "\x0b", "\x0c", "\x1c", "\x85", chr(0x2028)):
            with self.subTest(brk=repr(brk)):
                line = f"   90     1    90 00:09 echo{brk}{forged}"
                self.assertEqual(
                    gecko_process.parse_listing(line + "\n"),
                    (ProcessRow(90, 1, 90, 9, f"echo{brk}{forged}"),),
                )


class ParseEtimeTests(unittest.TestCase):
    """Each `[[dd-]hh:]mm:ss` form reads as its whole seconds; anything else as None."""

    def read(self, cases: dict[str, int | None]) -> None:
        """Each text in `cases` reads as the seconds it is mapped to."""
        for text, seconds in cases.items():
            with self.subTest(text=text):
                self.assertEqual(gecko_process.parse_etime(text), seconds)

    def test_minutes_and_seconds(self) -> None:
        self.read({"00:00": 0, "01:02": 62, "59:59": 59 * 60 + 59})

    def test_hours_minutes_and_seconds(self) -> None:
        self.read({"01:02:03": 3723, "00:00:07": 7, "23:59:59": 86399})

    def test_days_hours_minutes_and_seconds(self) -> None:
        day = 24 * 60 * 60
        self.read(
            {
                "2-03:04:05": 2 * day + 3 * 3600 + 4 * 60 + 5,
                "0-00:00:00": 0,
                "123-00:00:00": 123 * day,
                "999999999-23:59:59": 999_999_999 * day + day - 1,
            }
        )

    def test_anything_else_is_none(self) -> None:
        malformed = (
            "",
            "5:06",
            "005:06",
            "05:6",
            "60:00",
            "05:60",
            "24:00:00",
            "1-24:00:00",
            "1-05:06",
            "-05:06",
            "+1-00:00:00",
            "1000000000-00:00:00",
            " 05:06",
            "05:06 ",
            "05:06\n",
            "05:06:07:08",
            "1-2-03:04:05",
            "05-06",
            "05.06",
            "x",
            f"0{_NON_ASCII_FOUR}:00",
        )
        self.read(dict.fromkeys(malformed))


class CrashHelpersTests(unittest.TestCase):
    """A recording takes a helper serving a running Firefox we noted, started since our start."""

    def found(
        self, *rows: ProcessRow, before: frozenset[int] = frozenset()
    ) -> tuple[ProcessRow, ...]:
        """The helpers `crash_helpers` finds among `FIREFOX` and `rows`, `FIREFOX_PID` noted."""
        return gecko_process.crash_helpers((FIREFOX, *rows), {FIREFOX_PID}, before)

    def test_our_helper_is_found(self) -> None:
        self.assertEqual(self.found(HELPER, _THEIR_HELPER), (HELPER,))

    def test_a_helper_named_by_its_program_alone_or_with_nothing_after_is_found(
        self,
    ) -> None:
        for args in (
            f"crashhelper {FIREFOX_PID} gecko-crash-server-pipe.{FIREFOX_PID}",
            f"./crashhelper {FIREFOX_PID} gecko-crash-server-pipe.{FIREFOX_PID} x",
        ):
            with self.subTest(args=args):
                row = _helper_row(args)
                self.assertEqual(self.found(row), (row,))

    def test_another_firefoxs_helper_is_never_ours(self) -> None:
        self.assertEqual(self.found(_THEIR_HELPER), ())

    def test_a_helper_running_before_our_start_is_never_ours(self) -> None:
        # Its Firefox may have had the pid ours was given since, so it may name our pid.
        self.assertEqual(self.found(HELPER, before=frozenset({HELPER_PID})), ())

    def test_nothing_is_ours_while_no_firefox_was_noted(self) -> None:
        self.assertEqual(gecko_process.crash_helpers((HELPER,), set(), frozenset()), ())

    def test_a_look_alike_is_never_ours(self) -> None:
        pid = FIREFOX_PID
        for args in (
            f"/usr/bin/grep crashhelper {pid} gecko-crash-server-pipe.{pid}",
            f"sample crashhelper {pid} gecko-crash-server-pipe.{pid}",
            f"/x/xcrashhelper {pid} gecko-crash-server-pipe.{pid}",
            f"/x/crashhelpers {pid} gecko-crash-server-pipe.{pid}",
            f"/x/crashhelper {pid} gecko-crash-server-pipe.{pid + 1}",
            f"/x/crashhelper {pid} gecko-crash-server-pipe.{pid}0",
            f"/x/crashhelper {pid}0 gecko-crash-server-pipe.{pid}0",
            f"/x/crashhelper 0{pid} gecko-crash-server-pipe.0{pid}",
            f"/x/crashhelper {pid} gecko-crash-server-pipe-{pid}",
            f"/x/crashhelper  {pid} gecko-crash-server-pipe.{pid}",
            f"/x/crashhelper {pid}",
            "/x/crashhelper",
            f" /x/crashhelper {pid} gecko-crash-server-pipe.{pid}",
        ):
            with self.subTest(args=args):
                self.assertEqual(self.found(_helper_row(args)), ())

    def test_a_helper_whose_parent_is_not_launchd_is_never_ours(self) -> None:
        # Ours is double-forked away from Firefox; one still a child of something else is not.
        self.assertEqual(self.found(HELPER._replace(ppid=FIREFOX_PID)), ())

    def test_a_helper_in_group_0_or_1_is_never_signaled(self) -> None:
        # A signal to group 0 reaches the sender's own group; group 1 is launchd's.
        for pgid in (0, 1):
            with self.subTest(pgid=pgid):
                self.assertEqual(self.found(HELPER._replace(pgid=pgid)), ())

    def test_a_helper_older_than_the_firefox_it_names_is_never_ours(self) -> None:
        # The reader's Firefox may have started it and exited, and ours been given its pid.
        for elapsed_s in (FIREFOX_ELAPSED_S + 1, FIREFOX_ELAPSED_S + 24 * 60 * 60):
            with self.subTest(elapsed_s=elapsed_s):
                self.assertEqual(self.found(HELPER._replace(elapsed_s=elapsed_s)), ())

    def test_a_helper_younger_than_or_as_old_as_its_firefox_is_ours(self) -> None:
        for elapsed_s in (0, HELPER_ELAPSED_S, FIREFOX_ELAPSED_S):
            with self.subTest(elapsed_s=elapsed_s):
                row = HELPER._replace(elapsed_s=elapsed_s)
                self.assertEqual(self.found(row), (row,))

    def test_an_age_unread_or_a_firefox_unlisted_is_never_ours(self) -> None:
        for rows in (
            (FIREFOX, HELPER._replace(elapsed_s=None)),
            (FIREFOX._replace(elapsed_s=None), HELPER),
            (HELPER,),
            (FIREFOX._replace(pid=FIREFOX_PID + 1), HELPER),
        ):
            with self.subTest(rows=rows):
                found = gecko_process.crash_helpers(rows, {FIREFOX_PID}, frozenset())
                self.assertEqual(found, ())

    def test_the_helper_is_no_older_than_each_row_its_firefoxs_pid_shows(self) -> None:
        younger = FIREFOX._replace(elapsed_s=HELPER_ELAPSED_S - 1)
        self.assertEqual(self.found(younger, HELPER), ())


class RecordedHelpersTests(unittest.TestCase):
    """At the stop, ours is a listed row whose pid and arguments both equal a recorded row's."""

    def found(self, *rows: ProcessRow) -> tuple[ProcessRow, ...]:
        """The rows `recorded_helpers` finds among `rows`, with `HELPER` alone recorded."""
        return gecko_process.recorded_helpers(rows, {HELPER})

    def test_a_recorded_helper_still_running_is_found(self) -> None:
        self.assertEqual(self.found(FIREFOX, HELPER, _THEIR_HELPER), (HELPER,))

    def test_its_group_is_the_one_listed_now(self) -> None:
        moved = HELPER._replace(pgid=HELPER_PGID + 10)
        self.assertEqual(self.found(moved), (moved,))

    def test_a_helper_never_recorded_is_never_ours(self) -> None:
        # Each names our Firefox, under launchd, in a group of its own: all `crash_helpers`
        # asks, but none was recorded while our Firefox ran.
        for row in (
            HELPER._replace(pid=HELPER_PID + 1),
            HELPER._replace(args=helper_args(FIREFOX_PID, "2")),
        ):
            with self.subTest(row=row):
                self.assertEqual(self.found(row), ())
        self.assertEqual(gecko_process.recorded_helpers((HELPER,), set()), ())

    def test_a_recorded_pid_showing_other_arguments_is_never_ours(self) -> None:
        # The pid is the helper's only while it runs; its token-bearing arguments are its own.
        for args in (
            helper_args(FIREFOX_PID, "2"),
            f"{HELPER.args} ",
            HELPER.args[:-1],
            "/usr/bin/true",
            "",
        ):
            with self.subTest(args=args):
                self.assertEqual(self.found(HELPER._replace(args=args)), ())

    def test_a_recorded_row_under_another_parent_or_in_group_0_or_1_is_never_ours(
        self,
    ) -> None:
        for row in (
            HELPER._replace(ppid=FIREFOX_PID),
            HELPER._replace(pgid=0),
            HELPER._replace(pgid=1),
        ):
            with self.subTest(row=row):
                self.assertEqual(self.found(row), ())


class ListProcessesTests(unittest.TestCase):
    """`ps` lists every process within its own timeout; any failure is an EngineError."""

    def refusal(self, runner: FakeRunner) -> str:
        """List through `runner`, which must fail; the one-line message."""
        with self.assertRaises(EngineError) as raised:
            gecko_process.list_processes(runner)
        message = str(raised.exception)
        self.assertNotIn("\n", message)
        self.assertIn("ps", message)
        return message

    def test_the_command_its_bound_and_its_rows(self) -> None:
        runner = FakeRunner("\n".join(_LISTING_LINES) + "\n")
        self.assertEqual(gecko_process.list_processes(runner), _LISTING_ROWS)
        self.assertEqual(runner.calls, [(_PS, gecko_process.LISTING_TIMEOUT_SECONDS)])

    def test_its_runner_is_the_bounded_one(self) -> None:
        run = inspect.signature(gecko_process.list_processes).parameters["run"]
        self.assertIs(run.default, gecko_process.bounded_run.run_bounded)

    def test_a_listing_that_fails_says_why(self) -> None:
        message = self.refusal(FakeRunner(returncode=1, stderr="ps: illegal option\n"))
        self.assertIn("exited 1", message)
        self.assertIn("illegal option", message)

    def test_a_listing_that_says_nothing_still_says_it_failed(self) -> None:
        self.assertIn("nothing on stderr", self.refusal(FakeRunner(returncode=1)))

    def test_a_listing_that_cannot_run_says_so(self) -> None:
        runner = FakeRunner()
        runner.error = FileNotFoundError(2, "No such file or directory")
        self.assertIn("could not run", self.refusal(runner))

    def test_a_listing_past_its_timeout_says_so(self) -> None:
        runner = FakeRunner()
        runner.error = subprocess.TimeoutExpired("ps", 10.0)
        self.assertIn("hit its 10 s timeout", self.refusal(runner))

    def test_the_real_listing_holds_this_very_process(self) -> None:
        if sys.platform == "win32":
            # The narrowing is also what lets the POSIX-only `os.getpgrp` below type-check.
            raise unittest.SkipTest("Windows has no ps")  # pragma: only-on win32
        mine = [row for row in gecko_process.list_processes() if row.pid == os.getpid()]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].ppid, os.getppid())
        self.assertEqual(mine[0].pgid, os.getpgrp())
        # Its age read: should the system change the form of `etime`, this fails, rather than
        # every crash helper going unrecorded without a word.
        self.assertIsNotNone(mine[0].elapsed_s)
        self.assertNotEqual(mine[0].args, "")


if __name__ == "__main__":
    unittest.main()
