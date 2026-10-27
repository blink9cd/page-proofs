"""Shared fakes for the suites of `Geckodriver`: a scripted geckodriver, its process and group.

Every seam the class takes is faked here: the process (`FakeProcess`, which ends when a fake
signal says it does), the start (`FakeHost.popen`, which writes geckodriver's output through the
very file handle it is given, as the real child would), the clock (one second a reading), the
sleep (which writes the next scripted chunk of output, so time passing is what makes a port
appear), the group's SIGTERM, the tree kill, the probe for members left in the group, and the
process listing, in which a crash helper outside the group shows until a fake signal to its
own group ends it. Nothing here starts a process or signals one: no pid or group a fake
reports is ever handed to the operating system.

Entry point assumptions: imported by a suite beside it. The insert below reaches the tool's
private `src/` from this file's own location, never the working directory.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import IO

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import gecko_launch  # noqa: E402 - the tool's private src/, via the insert above
from gecko_process import ProcessRow  # noqa: E402 - as above
from _print_playwright_fixtures import (  # noqa: E402 - beside this module
    CallLog,
    FakeBounder,
    FakeClock,
    call,
)

#: The pid every fake process reports. Never signaled: each signal seam here is a fake.
PID = 4242

#: The Firefox a session's capabilities name, its crash helper, and the helper's own group,
#: apart from geckodriver's as measured: launchd its parent, a group of its own.
FIREFOX_PID = 4243
HELPER_PID = 5150
HELPER_PGID = 5149
LAUNCHD_PID = 1


def helper_args(firefox_pid: int, token: str = "1") -> str:
    """A crash helper's arguments, shaped as measured: its program, the pid, the pipe, more.

    The more holds `token`, which stands for the one drawn at random for each helper.
    """
    return (
        "/Applications/Firefox.app/Contents/MacOS/crashhelper "
        f"{firefox_pid} gecko-crash-server-pipe.{firefox_pid} "
        f"<user-temp>/TemporaryItems/ org.mozilla.crashhelper.{token} 20260924084938"
    )


#: How long our Firefox and its helper have run when a listing shows them, in whole seconds:
#: Firefox starts its helper, so the helper is the younger.
FIREFOX_ELAPSED_S = 20
HELPER_ELAPSED_S = 19

#: The helper the fakes list for `FIREFOX_PID`.
HELPER = ProcessRow(
    pid=HELPER_PID,
    ppid=LAUNCHD_PID,
    pgid=HELPER_PGID,
    elapsed_s=HELPER_ELAPSED_S,
    args=helper_args(FIREFOX_PID),
)

#: Our Firefox as a listing shows it while its session runs: geckodriver's child, in its group.
FIREFOX = ProcessRow(
    pid=FIREFOX_PID,
    ppid=PID,
    pgid=PID,
    elapsed_s=FIREFOX_ELAPSED_S,
    args="/Applications/Firefox.app/Contents/MacOS/firefox --marionette -foreground",
)

#: The status a process killed outright reports: minus SIGKILL's number, written out because
#: the signal module names SIGKILL on POSIX alone.
KILLED = -9

#: The timeout every case's driver is given; with a clock moving a second a reading, the
#: start gives up after this many readings.
TIMEOUT_S = 5.0

#: The names the start's and the stop's watchdog bounds carry.
START = "geckodriver start"
STOP = "geckodriver stop"


def listening(port: int) -> str:
    """The line geckodriver writes naming the port it bound."""
    return f"1791229212618\tgeckodriver\tINFO\tListening on 127.0.0.1:{port}\n"


class FakeProcess:
    """A started geckodriver, as far as the class reads one: its pid, `poll` and `wait`."""

    def __init__(self, calls: CallLog) -> None:
        """Running, until a fake signal or the case ends it."""
        self.pid = PID
        self.returncode: int | None = None
        self._calls = calls

    def poll(self) -> int | None:
        """Log the poll; the exit status, or None while it runs."""
        self._calls.append(call("poll"))
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """Log the wait; the exit status, or `TimeoutExpired` while it still runs."""
        self._calls.append(call("wait", timeout))
        if self.returncode is None:
            raise subprocess.TimeoutExpired("geckodriver", timeout or 0.0)
        return self.returncode


class FakeHost:
    """geckodriver's world, scripted: what it writes and when, and which signals end it.

    A plain class, not a dataclass: a dataclass needs its module registered in `sys.modules`
    to build, and a suite run by a fuzzing campaign was measured not to be.
    """

    def __init__(self, scratch: gecko_launch.Scratch) -> None:
        """Nothing written, every signal ending the process, one process every seam shares.

        `stragglers` is how many probes of the group still find a member once its leader is
        reaped (`math.inf`: until a tree kill ends them, when `kill_ends_group` says it does,
        past the first `group_survives_kills` kills). `on_probe`, when set, runs once, at the
        next probe of the group.

        The first listing, the start's, shows `before`. Each later one shows `others`, then
        `helpers` while `helper_listings` is above 0, one fewer each time (`math.inf`: until a
        signal to a helper's group ends them, as `helper_term_ends` and `helper_kill_ends`
        say). `on_listing`, when set, runs once, at the next listing after the start's.
        """
        self.scratch = scratch
        self.chunks: list[bytes] = []
        self.term_ends = True
        self.kill_ends = True
        self.stragglers = 0.0
        self.kill_ends_group = True
        self.group_survives_kills = 0
        self.on_probe: Callable[[], None] | None = None
        self.before: tuple[ProcessRow, ...] = ()
        self.others: tuple[ProcessRow, ...] = ()
        self.helpers: tuple[ProcessRow, ...] = ()
        self.helper_listings = 0.0
        self.helper_term_ends = True
        self.helper_kill_ends = True
        self.listing_error: Exception | None = None
        self.on_listing: Callable[[], None] | None = None
        self._listed = 0
        self.spawn_error: OSError | None = None
        self.calls: CallLog = []
        self.env: dict[str, str] = {}
        self.stdout: IO[bytes] | None = None
        self.process = FakeProcess(self.calls)

    def popen(
        self,
        args: list[str],
        /,
        *,
        stdin: int,
        stdout: IO[bytes],
        stderr: int,
        env: dict[str, str],
        start_new_session: bool,
    ) -> FakeProcess:
        """Log the start, keep its environment and log handle, and write the first chunk."""
        self.calls.append(
            call(
                "popen",
                *args,
                stdin=stdin,
                stderr=stderr,
                start_new_session=start_new_session,
            )
        )
        self.env, self.stdout = env, stdout
        if self.spawn_error is not None:
            raise self.spawn_error
        self._write_next()
        return self.process

    def sleep(self, seconds: float) -> None:
        """Log the sleep; time passing is what lets the next chunk arrive."""
        self.calls.append(call("sleep", seconds))
        self._write_next()

    def terminate(self, pid: int) -> None:
        """Log the group's SIGTERM; it ends the process, or a helper, when the script says so."""
        self.calls.append(call("terminate", pid))
        if pid != PID:
            self._end_helpers(pid, ends=self.helper_term_ends)
        elif self.term_ends:
            self.process.returncode = -signal.SIGTERM

    def kill_tree(self, pid: int) -> None:
        """Log the tree kill; it ends the process, the group or a helper, as scripted."""
        self.calls.append(call("kill_tree", pid))
        if pid != PID:
            self._end_helpers(pid, ends=self.helper_kill_ends)
            return
        if self.kill_ends:
            self.process.returncode = KILLED
        if self.group_survives_kills > 0:
            self.group_survives_kills -= 1
        elif self.kill_ends_group:
            self.stragglers = 0.0

    def group_alive(self, pgid: int) -> bool:
        """Log the probe; whether a straggler is still in the group, one fewer each time."""
        self.calls.append(call("group_alive", pgid))
        hook, self.on_probe = self.on_probe, None
        if hook is not None:
            hook()
        if self.stragglers > 0:
            self.stragglers -= 1
            return True
        return False

    def processes(self) -> tuple[ProcessRow, ...]:
        """Log the listing; the start's shows `before`, each later one the scripted rows."""
        self.calls.append(call("processes"))
        if self.listing_error is not None:
            raise self.listing_error
        self._listed += 1
        if self._listed == 1:
            return self.before
        hook, self.on_listing = self.on_listing, None
        if hook is not None:
            hook()
        if self.helper_listings > 0:
            self.helper_listings -= 1
            return (*self.others, *self.helpers)
        return self.others

    def _end_helpers(self, pgid: int, *, ends: bool) -> None:
        """End the helpers when a signal reaches their group and the script says it ends them."""
        if ends and pgid in {helper.pgid for helper in self.helpers}:
            self.helper_listings = 0.0

    def write(self, data: bytes) -> None:
        """Write to the log as geckodriver does: straight to the descriptor it was handed."""
        assert self.stdout is not None, "geckodriver was never started"
        os.write(self.stdout.fileno(), data)

    def _write_next(self) -> None:
        """Write the next scripted chunk, if any is left."""
        if self.chunks:
            self.write(self.chunks.pop(0))

    def driver(self, timeout_s: float = TIMEOUT_S) -> gecko_launch.Geckodriver:
        """A driver over every fake seam here."""
        return gecko_launch.Geckodriver(
            "bin/geckodriver",
            self.scratch,
            timeout_s=timeout_s,
            bounder=FakeBounder(self.calls),
            popen=self.popen,
            clock=FakeClock(),
            sleep=self.sleep,
            kill_tree=self.kill_tree,
            terminate=self.terminate,
            group_alive=self.group_alive,
            processes=self.processes,
        )

    def named(self, *names: str) -> list[tuple[str, ...]]:
        """The calls logged under any of `names`, in order."""
        return [entry for entry in self.calls if entry[0] in names]


def outside_bounds(calls: CallLog) -> list[tuple[str, ...]]:
    """Every call made while no bound was open: each one could hang the run unwatched."""
    depth, outside = 0, []
    for entry in calls:
        if entry[0] == "bound":
            depth += 1
        elif entry[0] == "unbound":
            depth -= 1
        elif depth == 0:
            outside.append(entry)
    return outside


class GeckoCase(unittest.TestCase):
    """A fresh scratch folder and host per case."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.scratch = gecko_launch.make_scratch(Path(folder.name))
        self.host = FakeHost(self.scratch)

    def started(self, *chunks: bytes) -> gecko_launch.Geckodriver:
        """A driver whose geckodriver names port 4444 at once, entered and left on cleanup."""
        self.host.chunks = [listening(4444).encode(), *chunks]
        driver = self.host.driver()
        driver.__enter__()
        self.addCleanup(driver.__exit__, None, None, None)
        return driver
