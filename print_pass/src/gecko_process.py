"""geckodriver's process as the operating system sees it: its group, and Firefox's crash helper.

What starts geckodriver and what is read of it are the `Protocol`s below; the group's SIGTERM
and the probe for a member of it still running are two functions. geckodriver is started
leading a session of its own, and the Firefox it starts, with every child process Firefox
starts, stays in its group (measured), so the group is the one handle a stop has on all of
them -- all but one. Firefox's crash helper leaves the group: launchd is its parent, and its
group is another (measured). So a driver records it from a listing of every process while
its Firefox still runs, and a stop finds it again by that record (`recorded_helpers`). The
reader's own Firefox runs a helper too, which may outlive it and name a pid our Firefox is
given later. So `crash_helpers` records a helper only if, among other properties, it is no
older than the Firefox it names, both ages read from one listing: ours is started by our
Firefox, while the reader's started before its own Firefox exited, so before ours could be
given that pid. Ages are whole seconds, the wall clock now less a start that is a wall-clock
time too (measured: `ps`'s `lstart` is a calendar time, and its `etime` is now less that). So
only a full wrap of the pid space completed within one second -- from the reader's helper
starting to our Firefox starting, given that pid -- could pass the reader's helper off as
ours. A step back of the clock between those two starts widens that second by the step,
unless a step moves every running process's start with it; which one it does was not
measured, since stepping the clock needs root, so the wider residual is the one stated.

Every answer the operating system gives here was measured on macOS, the one platform the
Firefox engine runs on, but one: what a step of the wall clock does to an age. Each
function's Windows branch is type-checked but cannot run while the engine refuses every
platform but macOS.

Entry point assumptions: the standard library, the shared `bounded_run` (its tree kill and its
bounded runner), found by the sibling insert below, and this folder's `print_record`. `ps` is
whatever `PATH` finds, by choice: whoever can put a program early on the reader's `PATH` can
already run anything as the reader, so a full path here would guard nothing.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path
from typing import IO, Final, NamedTuple, Protocol

# The shared tree-killing subprocess runner: a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place,
# and put near the front of the path, ahead of every installed distribution, so none of the
# same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run
from print_record import EngineError, first_line

#: How long one listing of every process may take: `ps` answers in well under a second.
LISTING_TIMEOUT_SECONDS: Final = 10.0

#: What a listing that hits `LISTING_TIMEOUT_SECONDS` takes past it, inside the bounded
#: runner: the tree kill of `ps`, then the grace its output is read within. One listing ends
#: within the two together.
LISTING_OVERRUN_SECONDS: Final = (
    bounded_run.KILL_TIMEOUT_SECONDS + bounded_run.KILL_GRACE_SECONDS
)

#: The listing: every process, its arguments unclipped (`-ww`), with its id, its parent's,
#: its group's, the time since it started and its arguments, and no header (each `=` gives
#: its column an empty title).
_PS_ARGV: Final = ("ps", "-A", "-ww", "-o", "pid=,ppid=,pgid=,etime=,args=")

#: One row of the listing: three numbers, each right-aligned, in ASCII digits, nine at most,
#: as `ps` writes them; then the elapsed time after one space or more; then one space and
#: the arguments, which may be empty. The elapsed time is taken here as any run of characters
#: other than whitespace, for `parse_etime` to read, so that a row whose time cannot be read
#: is still a row, which the stop may need to find.
_LISTING_ROW: Final = re.compile(
    r" *([0-9]{1,9}) +([0-9]{1,9}) +([0-9]{1,9}) +(\S+)(?: (.*))?"
)

#: An elapsed time in `[[dd-]hh:]mm:ss` form, the form the Single UNIX Specification gives
#: `ps`'s `etime`. macOS's `ps` writes it so (measured): `mm:ss`, `hh:mm:ss` and
#: `dd-hh:mm:ss`, its days zero-padded, right-aligned in a column of eleven. Days, nine ASCII
#: digits at most here, come only before hours; hours, minutes and seconds are two digits
#: each, each within its range.
_ETIME: Final = re.compile(
    r"(?:(?:([0-9]{1,9})-)?([01][0-9]|2[0-3]):)?([0-5][0-9]):([0-5][0-9])"
)

#: A crash helper's arguments, as measured: its program, the pid of the Firefox it serves,
#: then the pipe it listens on, named for that same pid, and more. The program is a path
#: with no space in it, or the bare name; the pid has no leading zero. So the helper of a
#: Firefox installed under a path with a space -- Firefox Nightly and Developer Edition
#: install under such names -- is never recorded, and is left running.
_CRASH_HELPER: Final = re.compile(
    r"(?:\S*/)?crashhelper ([1-9][0-9]{0,8}) gecko-crash-server-pipe\.\1(?: |\Z)"
)

#: launchd's pid, and its group's id: the parent a crash helper has (measured), and the
#: highest group id never signaled -- group 0 is the sender's own.
_LAUNCHD: Final = 1


class ProcessRow(NamedTuple):
    """One process as a listing shows it: its id, its parent's and group's, its age, its arguments.

    Its age, `elapsed_s`, is the whole seconds since it started, or None when the listing's
    elapsed time could not be read.
    """

    pid: int
    ppid: int
    pgid: int
    elapsed_s: int | None
    args: str


class DriverProcess(Protocol):
    """A started geckodriver: its pid, its exit status, and a bounded wait for it.

    `subprocess.Popen` is one.
    """

    @property
    def pid(self) -> int:
        """Its process id, which is also its process group's."""

    def poll(self) -> int | None:
        """Its exit status, reaping it, or None while it runs."""

    def wait(self, timeout: float | None = None) -> int:
        """Its exit status once it ends; `subprocess.TimeoutExpired` past `timeout`."""


class RunBounded(Protocol):
    """What runs a command to its end within a timeout: `bounded_run.run_bounded` is one."""

    def __call__(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """Run `argv`, its output captured as text; `subprocess.TimeoutExpired` past `timeout`."""


class Spawn(Protocol):
    """What starts geckodriver: `subprocess.Popen` is one."""

    def __call__(
        self,
        args: list[str],
        /,
        *,
        stdin: int,
        stdout: IO[bytes],
        stderr: int,
        env: dict[str, str],
        start_new_session: bool,
    ) -> DriverProcess:
        """Start `args` with these streams and environment, leading a session of its own."""


def terminate_group(pid: int) -> None:
    """Send SIGTERM to every process in group `pid`; none left to signal is no error.

    For geckodriver's group, one signal reaches geckodriver, the Firefox it started, and every
    child of that; for a crash helper's, the helper.
    """
    if sys.platform == "win32":
        # Windows has no process group to signal, so this ends the tree outright. It cannot
        # run while the engine refuses every platform but macOS; it is kept so that this
        # module type-checks for Windows too.
        bounded_run.kill_tree(pid)  # pragma: only-on win32
    else:  # pragma: only-on posix
        # ESRCH once the group is gone; EPERM while only zombies are left in it, an unreaped
        # leader among them (measured on macOS). Either way nothing is left to signal; the
        # leader's reap is its parent's to do.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGTERM)


def group_alive(pgid: int) -> bool:
    """Whether any process in group `pgid` still runs, asked with the null signal.

    No error means a member could be signaled, so one runs. ESRCH means no member is left.
    EPERM means no member could be signaled. On macOS that was measured only for zombies --
    the one member the leader, exited and unreaped, or a zombie child left once the leader
    was reaped -- and no member of geckodriver's group runs as another user, so EPERM means
    every member left is a zombie. A zombie runs nothing and writes nothing, so it counts as
    gone, as it does for `terminate_group`.
    """
    if sys.platform == "win32":
        # Windows has no process group to ask, and the engine refuses it before this could
        # run; the narrowing is what lets the POSIX-only `os.killpg` below type-check.
        raise NotImplementedError(
            "process groups are POSIX only"
        )  # pragma: only-on win32
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


# fuzz-target
def parse_listing(text: str) -> tuple[ProcessRow, ...]:
    """Each row a listing holds, as a `ProcessRow`; a line that is not one is skipped.

    Split on newlines alone. `ps` escapes a newline inside an argument, and each ASCII line
    break, but writes a Unicode line separator as it is (measured), so splitting on every line
    break `str.splitlines` knows would let an argument end a row early and start a forged one.
    The defense against a forged row rests on `ps` escaping a carriage return (measured): the
    listing is read in text mode, which turns a raw one into a newline before it gets here.
    """
    rows: list[ProcessRow] = []
    for line in text.split("\n"):
        match = _LISTING_ROW.fullmatch(line)
        if match is not None:
            pid, ppid, pgid, etime, args = match.groups(default="")
            age = parse_etime(etime)
            rows.append(ProcessRow(int(pid), int(ppid), int(pgid), age, args))
    return tuple(rows)


# fuzz-target
def parse_etime(text: str) -> int | None:
    """The whole seconds an elapsed time in `[[dd-]hh:]mm:ss` form gives; None for other text.

    Each field must be within its range, so that one time is longer than another exactly when
    its fields, read from the left, are larger. Anything else, padding included, is None,
    which no comparison of ages takes.
    """
    match = _ETIME.fullmatch(text)
    if match is None:
        return None
    days, hours, minutes, seconds = (int(field) for field in match.groups(default="0"))
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def list_processes(run: RunBounded = bounded_run.run_bounded) -> tuple[ProcessRow, ...]:
    """Every process running now, listed by `ps` within `LISTING_TIMEOUT_SECONDS`.

    Raises:
        EngineError: `ps` could not run, outlived its timeout, or exited non-zero.
    """
    try:
        listed = run(_PS_ARGV, timeout=LISTING_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise EngineError(
            f"ps hit its {LISTING_TIMEOUT_SECONDS:g} s timeout listing processes"
        ) from error
    except OSError as error:
        raise EngineError(
            f"ps could not run to list processes: {first_line(error)}"
        ) from error
    if listed.returncode != 0:
        said = first_line(listed.stderr) or "nothing on stderr"
        raise EngineError(f"ps exited {listed.returncode} listing processes: {said}")
    return parse_listing(listed.stdout)


def crash_helpers(
    rows: Sequence[ProcessRow],
    firefox_pids: Collection[int],
    present_before: Collection[int],
) -> tuple[ProcessRow, ...]:
    """The crash helpers in `rows` that serve one of `firefox_pids`: the rows a driver records.

    A row is one only when all of these hold: its arguments are a crash helper's, naming one
    of `firefox_pids` both as the Firefox it serves and in its pipe's name; launchd is its
    parent; it was not running before geckodriver started (`present_before`); its group is
    one a signal may go to; and it is no older than the Firefox it names, both ages read from
    `rows`. The name alone never qualifies a helper.

    The age is what passes over a helper the reader's Firefox left behind, should our Firefox
    be given that Firefox's pid: it started before that Firefox exited, so before ours was
    started. A helper whose age could not be read, or whose Firefox `rows` does not show, is
    not taken. Ages are whole seconds of the wall clock, so a helper started less than a
    second before the Firefox it names can read as no older: only a full pid wrap completed
    within that second could bring the reader's one through. A step back of the clock between
    the reader's helper starting and our Firefox starting may widen that second by the step,
    as the module's docstring says.

    A row is ours only while the Firefox it names is one of ours that still runs: a pid is a
    Firefox's own only while it runs, and once it has exited the reader's next Firefox may be
    given it, whose helper would then pass every check here.
    """
    return tuple(
        row
        for row in rows
        if row.pid not in present_before
        and row.ppid == _LAUNCHD
        and row.pgid > _LAUNCHD
        and (match := _CRASH_HELPER.match(row.args)) is not None
        and int(match[1]) in firefox_pids
        and _no_older_than_its_firefox(row, int(match[1]), rows)
    )


def _no_older_than_its_firefox(
    helper: ProcessRow, firefox_pid: int, rows: Iterable[ProcessRow]
) -> bool:
    """Whether `rows` shows Firefox `firefox_pid`, `helper` no older than it, both ages read.

    Should `rows` show that pid more than once, `helper` must be no older than each.
    """
    helper_age = helper.elapsed_s
    firefox_ages = [row.elapsed_s for row in rows if row.pid == firefox_pid]
    if helper_age is None or not firefox_ages:
        return False
    return all(age is not None and helper_age <= age for age in firefox_ages)


def recorded_helpers(
    rows: Iterable[ProcessRow], recorded: Collection[ProcessRow]
) -> tuple[ProcessRow, ...]:
    """The rows in `rows` that are a `recorded` crash helper still running: the stop's to signal.

    A row is one only when its pid and its whole arguments both equal a recorded row's, launchd
    is still its parent, and its group is one a signal may go to. A helper's arguments carry a
    token drawn at random for it (`org.mozilla.crashhelper.<token>`, measured), so a process
    that has taken a recorded helper's pid since does not show them. The group is the one this
    listing shows, which is where the helper is now.
    """
    keys = {(row.pid, row.args) for row in recorded}
    return tuple(
        row
        for row in rows
        if (row.pid, row.args) in keys and row.ppid == _LAUNCHD and row.pgid > _LAUNCHD
    )
