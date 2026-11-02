"""geckodriver's process: started, its port awaited, and its whole group ended.

The Firefox engine drives Firefox through geckodriver, Mozilla's WebDriver server. This module
owns `Geckodriver`, which starts that server, waits for its port, and ends its whole process
group, and Firefox's crash helper, which leaves that group. Everything else geckodriver is
handed is re-exported below, so that an engine needs this one import for all of it: the
platform the engine may run on, where geckodriver is, and the environment and command it runs
with, which redirect Firefox's own folders into the scratch and set its non-local tripwire
(`gecko_command`); what a session asks Firefox for (the prefs that keep its network on
loopback, the prefs a state asks for, the new-session body, and the dead port the proxy points
at (`gecko_prefs`); the scratch folder Firefox's own folders are redirected into, and its
removal (`gecko_scratch`); what geckodriver's log says (`gecko_log`); and the watchdog's
bound on a start or a stop, with the poll it counts (`gecko_bound`).

Entry point assumptions: the standard library, the shared `bounded_run` (its tree kill), found
by the sibling insert below, and this folder's `gecko_bound`, `gecko_command`, `gecko_log`,
`gecko_prefs`, `gecko_process`, `gecko_scratch`, `print_record` and `print_watchdog`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Final, Self

# The shared tree-killing subprocess runner: a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place,
# and put near the front of the path, ahead of every installed distribution, so none of the
# same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run
from gecko_bound import POLL_SECONDS as POLL_SECONDS
from gecko_bound import watchdog_seconds as watchdog_seconds
from gecko_command import GECKODRIVER as GECKODRIVER
from gecko_command import driver_command as driver_command
from gecko_command import driver_env as driver_env
from gecko_command import find_geckodriver as find_geckodriver
from gecko_command import require_measured_platform as require_measured_platform
from gecko_log import IsolationBreach as IsolationBreach
from gecko_log import isolation_breach as isolation_breach
from gecko_log import listening_port as listening_port
from gecko_prefs import LOOPBACK
from gecko_prefs import DeadPort as DeadPort
from gecko_prefs import capabilities as capabilities
from gecko_prefs import isolation_prefs as isolation_prefs
from gecko_prefs import state_prefs as state_prefs
from gecko_process import LISTING_OVERRUN_SECONDS as LISTING_OVERRUN_SECONDS
from gecko_process import LISTING_TIMEOUT_SECONDS as LISTING_TIMEOUT_SECONDS
from gecko_process import (
    DriverProcess,
    ProcessRow,
    Spawn,
    crash_helpers,
    group_alive,
    list_processes,
    recorded_helpers,
    terminate_group,
)
from gecko_scratch import Scratch as Scratch
from gecko_scratch import make_scratch as make_scratch
from gecko_scratch import remove_scratch as remove_scratch
from print_record import EngineError, first_line
from print_watchdog import Bounder

#: The most of the log one `log_since` reads: a page can flood its console, which the log
#: carries, for as long as its state lasts, and a read past this would hold all of it. Far
#: beyond any measured state's slice; a state whose slice is longer says so (`gecko_state`).
MAX_LOG_READ_BYTES: Final = 32_000_000

_START: Final = f"{GECKODRIVER} start"
_STOP: Final = f"{GECKODRIVER} stop"


@dataclass(frozen=True)
class _Running:
    """A started geckodriver: its process, the port it named, and the log it writes to."""

    process: DriverProcess
    port: int
    log: IO[bytes]


class Geckodriver:
    """geckodriver, for one engine run: started, its port awaited, and its whole group ended.

    `with Geckodriver(...) as driver:` starts geckodriver on loopback, leading a session of
    its own and writing to the scratch log, and returns once the log names its port; a driver
    starts geckodriver once, and a second entry is refused;
    `driver.base_url` is where WebDriver commands then go; `driver.note_firefox` notes each
    Firefox a session names and records its crash helper, and `driver.record_helpers` records
    one started since, each only while that Firefox runs and only if no older than it. Leaving
    the block signals the whole process group (geckodriver, the Firefox it started and every
    child of that), reaps geckodriver, then polls until no member of the group still runs,
    killing the tree if one outlives the timeout. Then it polls until no recorded crash helper
    runs, signaling one only past the timeout: a stop that returns has seen the whole group
    end, and every recorded helper. A helper never recorded is left running, and the stop
    cannot vouch for it. Four such are known: one whose Firefox had exited before any
    recording; one under a path with a space in it, as Firefox Nightly and Developer Edition
    install, which is never recognized; one whose age, or its Firefox's, a listing shows in a
    form that cannot be read; and one that reads older than its Firefox because the system's
    wall clock, which ages are read from, stepped back between the Firefox starting and it
    starting, should a step leave a running process's start where it was. Each errs the safe
    way: a helper of ours is left running, and none of the reader's is signaled. The log is
    closed either way. The start and the stop each run inside a watchdog bound, and every
    step inside them has a bound of its own. `kill` ends the tree at once, from any thread.
    """

    def __init__(
        self,
        executable: str,
        scratch: Scratch,
        *,
        timeout_s: float,
        bounder: Bounder,
        popen: Spawn = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        kill_tree: Callable[[int], None] = bounded_run.kill_tree,
        terminate: Callable[[int], None] = terminate_group,
        group_alive: Callable[[int], bool] = group_alive,
        processes: Callable[[], tuple[ProcessRow, ...]] = list_processes,
    ) -> None:
        """Keep the seams; nothing starts until the block does.

        Raises:
            ValueError: `timeout_s` is not more than 0.
        """
        if not timeout_s > 0:
            raise ValueError(
                f"a timeout must be more than 0 seconds, not {timeout_s!r}"
            )
        self._executable = executable
        self._scratch = scratch
        self._timeout_s = timeout_s
        self._bounder = bounder
        self._popen = popen
        self._clock = clock
        self._sleep = sleep
        self._kill_tree = kill_tree
        self._terminate = terminate
        self._group_alive = group_alive
        self._processes = processes
        # Guards `_process` and `_killed`, which `kill` reads from the watchdog's thread. Its
        # check and set are one step, so `kill` signals the tree at most once (the stop's own
        # kill of a group that outlives the timeout is a second signal, by design), and a
        # `kill` that takes the lock after `_forget` signals nothing. The kill itself runs
        # outside the lock: one that read the pid just before a stop forgot it still signals
        # that pid, which would have to be reused within that moment to reach another process.
        self._lock = threading.Lock()
        self._process: DriverProcess | None = None
        self._killed = False
        self._running: _Running | None = None
        # Set by the first entry, never cleared: a second would truncate the log the first
        # writes, and after a failed start its `kill` would be spent.
        self._entered = False
        # Every pid a listing showed before geckodriver started, and each Firefox noted since:
        # with each helper's age beside its Firefox's, what `crash_helpers` tells our helper
        # from every other process by, while that Firefox runs.
        self._present_before: frozenset[int] = frozenset()
        self._firefox_pids: set[int] = set()
        # Each helper's row as a listing first showed it while its Firefox ran here, keyed by
        # its pid and its arguments: by those the stop finds it again, and nothing else. Its
        # age grows from one listing to the next, so it is no part of the key.
        self._helpers: dict[tuple[int, str], ProcessRow] = {}

    @property
    def base_url(self) -> str:
        """Where WebDriver commands go: `http://127.0.0.1:<port>`.

        Raises:
            RuntimeError: geckodriver has not been started.
        """
        return f"http://{LOOPBACK}:{self._run().port}"

    def log_offset(self) -> int:
        """How many bytes the log holds now: where a later `log_since` starts reading."""
        return self._scratch.log.stat().st_size

    def log_since(self, offset: int) -> str:
        """The log from byte `offset` on, as UTF-8, bad bytes replaced.

        At most its last `MAX_LOG_READ_BYTES`, so a page flooding its console cannot fill
        memory, nor push out of the read the guard's refusal, which is written last, as the
        browser ends. Past the cap, the line the cut fell in is dropped, so text a page wrote
        mid-line is never read as the start of a line.
        """
        with self._scratch.log.open("rb") as log:
            start = max(offset, log.seek(0, os.SEEK_END) - MAX_LOG_READ_BYTES)
            log.seek(start)
            text = log.read(MAX_LOG_READ_BYTES).decode("utf-8", errors="replace")
        return text if start == offset else text.partition("\n")[2]

    def note_firefox(self, pid: int) -> None:
        """Note the Firefox a session's capabilities name, by `pid`, and record its crash helper.

        It is noted only once a listing shows it running in geckodriver's group, and the
        helpers that same listing shows for it are recorded, as `record_helpers` would. Its
        one listing runs outside every watchdog bound here, within `LISTING_TIMEOUT_SECONDS`
        and `LISTING_OVERRUN_SECONDS` more, which whatever bounds the session must allow.

        Raises:
            RuntimeError: geckodriver is not running.
            EngineError: no process `pid` runs in geckodriver's group, or the listing failed.
        """
        group = self._run().process.pid
        rows = self._processes()
        if not any(row.pid == pid and row.pgid == group for row in rows):
            raise EngineError(f"Firefox {pid} is not running in {GECKODRIVER}'s group")
        self._firefox_pids.add(pid)
        self._record(rows, group)

    def record_helpers(self) -> None:
        """Record the crash helper of each noted Firefox still running, if not recorded yet.

        An engine calls this just before it deletes each session, while that session's
        Firefox still runs. A helper is recordable only then: a pid is a Firefox's own only
        while it runs, and once it has exited the reader's next Firefox may be given that pid,
        whose helper would name it just as ours did. So nothing is recorded for a noted Firefox
        a listing no longer shows in geckodriver's group, and a helper of it never recorded is
        left running by the stop, which then cannot vouch for it. Its one listing runs outside
        every watchdog bound here, within `LISTING_TIMEOUT_SECONDS` and
        `LISTING_OVERRUN_SECONDS` more, which whatever bounds the session must allow.

        Raises:
            RuntimeError: geckodriver is not running.
            EngineError: the listing failed.
        """
        group = self._run().process.pid
        self._record(self._processes(), group)

    def _record(self, rows: tuple[ProcessRow, ...], group: int) -> None:
        """Record the helpers `rows` shows for each noted Firefox it shows running in `group`."""
        running = {row.pid for row in rows if row.pgid == group} & self._firefox_pids
        for helper in crash_helpers(rows, running, self._present_before):
            self._helpers.setdefault((helper.pid, helper.args), helper)

    def kill(self) -> None:
        """Kill geckodriver's whole tree now. Safe from the watchdog's thread.

        Nothing happens before a start, after a stop that returned, or on a second call.
        """
        with self._lock:
            if self._process is None or self._killed:
                return
            self._killed = True
            pid = self._process.pid
        self._kill_tree(pid)

    def __enter__(self) -> Self:
        """Start geckodriver and wait for its port, inside the watchdog's bound.

        Raises:
            RuntimeError: this driver was entered before, whether that start ran or failed.
            EngineError: the processes running could not be listed, and nothing was started;
                or geckodriver could not be run, exited, or named no port within the timeout,
                and what it started is killed and reaped first, and the log closed.
        """
        if self._entered:
            raise RuntimeError(
                f"this {GECKODRIVER} driver has been started once already"
            )
        self._entered = True
        with self._bounder.bound(watchdog_seconds(self._timeout_s), _START):
            # Every process already running, so that no crash helper started before this
            # geckodriver (the reader's own) is taken for ours, even should our Firefox be
            # given the pid its Firefox had.
            self._present_before = frozenset(row.pid for row in self._processes())
            log = self._scratch.log.open("wb")
            try:
                process = self._spawn(log)
                port = self._await_port(process)
            except BaseException:
                self._abandon_start(log)
                raise
            self._running = _Running(process, port, log)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """End geckodriver's whole group inside the watchdog's bound, then close the log.

        Raises:
            EngineError: geckodriver, a member of its group, or a recorded crash helper
                outlived its kill, or a listing of the processes failed.
        """
        running = self._run()
        # Taken, so a second exit raises instead of signaling a pid that may be reused.
        self._running = None
        bound = watchdog_seconds(self._timeout_s, len(self._helpers))
        try:
            with self._bounder.bound(bound, _STOP):
                self._stop(running.process)
        finally:
            running.log.close()

    def _run(self) -> _Running:
        """The started geckodriver; a RuntimeError before a start."""
        if self._running is None:
            raise RuntimeError(f"{GECKODRIVER} has not been started")
        return self._running

    def _spawn(self, log: IO[bytes]) -> DriverProcess:
        """Start geckodriver, writing to `log`, leading a session and group of its own."""
        try:
            process = self._popen(
                driver_command(self._executable, self._scratch),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=driver_env(os.environ, self._scratch),
                start_new_session=True,
            )
        except OSError as error:
            raise EngineError(
                f"{GECKODRIVER} did not start: {first_line(error)}"
            ) from error
        with self._lock:
            self._process = process
        return process

    def _await_port(self, process: DriverProcess) -> int:
        """The port geckodriver names, polled for until it exits or the timeout passes."""
        deadline = self._clock() + self._timeout_s
        while True:
            port = listening_port(self.log_since(0))
            if port is not None:
                return port
            status = process.poll()
            if status is not None:
                raise EngineError(
                    f"{GECKODRIVER} exited with status {status} before naming its port"
                )
            if self._clock() >= deadline:
                raise EngineError(
                    f"{GECKODRIVER} named no port within {self._timeout_s:g} s"
                )
            self._sleep(POLL_SECONDS)

    def _abandon_start(self, log: IO[bytes]) -> None:
        """Kill and reap whatever a failed start began, then close the log.

        The kill is spent, so a later `kill` does nothing even with the process remembered.
        """
        try:
            process = self._process
            if process is not None:
                self.kill()
                self._reap(process)
        finally:
            log.close()

    def _stop(self, process: DriverProcess) -> None:
        """SIGTERM the group and reap geckodriver, see the rest of its group end, then the helpers.

        Past the timeout, geckodriver's tree is killed outright and reaped. It is forgotten
        only once its group has ended: until then no new process can take its id, so a
        watchdog `kill` meanwhile still reaches whatever is left of the group. Once forgotten,
        a `kill` while the helpers are awaited signals nothing, since that id may then be
        another process's.
        """
        self._terminate(process.pid)
        try:
            process.wait(timeout=self._timeout_s)
        except subprocess.TimeoutExpired:
            self.kill()
            self._reap(process)
        self._await_group_end(process.pid)
        self._forget()
        self._end_crash_helpers()

    def _reap(self, process: DriverProcess) -> None:
        """Wait out a killed geckodriver.

        Raises:
            EngineError: it outlived its kill.
        """
        try:
            process.wait(timeout=self._timeout_s)
        except subprocess.TimeoutExpired as error:
            raise EngineError(
                f"{GECKODRIVER} outlived its kill by {self._timeout_s:g} s"
            ) from error

    def _await_group_end(self, pgid: int) -> None:
        """Poll until no member of group `pgid` runs; past the timeout, kill it and poll again.

        Raises:
            EngineError: a member outlived the kill.
        """
        if not self._still_there(lambda: self._group_alive(pgid)):
            return
        # The tree kill itself, not `kill`, which is spent once geckodriver was killed. A
        # member was just seen, so the group's id is not free for another process to take.
        self._kill_tree(pgid)
        if self._still_there(lambda: self._group_alive(pgid)):
            raise EngineError(
                f"{GECKODRIVER}'s group outlived its kill by {self._timeout_s:g} s"
            )

    def _end_crash_helpers(self) -> None:
        """Poll until no recorded crash helper runs; past the timeout, signal each.

        Each helper left past the timeout has its group sent SIGTERM, and is polled for
        again; one left past that has its group killed as a tree, and is polled for once
        more. Only the group a listing shows a recorded helper in is signaled, with that
        group's descendants; a helper never recorded is left running. That rests on macOS
        never giving a new process a pid that is the id of a group still in being, so the
        group a listing showed is the helper's until its last member ends. Nothing is listed
        while no helper was recorded.

        Raises:
            EngineError: a helper outlived its kill, or a listing failed.
        """
        if not self._helpers:
            return
        for end in (self._terminate, self._kill_tree):
            helpers = self._still_there(self._crash_helpers)
            if not helpers:
                return
            for pgid in sorted({helper.pgid for helper in helpers}):
                # The listing is a moment old. Should the group's last member end since, the
                # signal reaches another process only if a new group took this id within
                # that moment.
                end(pgid)
        if self._still_there(self._crash_helpers):
            raise EngineError(
                f"Firefox's crash helper outlived its kill by {self._timeout_s:g} s"
            )

    def _crash_helpers(self) -> tuple[ProcessRow, ...]:
        """The recorded crash helpers that a listing shows running now."""
        return recorded_helpers(self._processes(), self._helpers.values())

    def _still_there[T](self, probe: Callable[[], T]) -> T:
        """What `probe` finds, asked every poll until it finds nothing or the timeout passes."""
        deadline = self._clock() + self._timeout_s
        while (found := probe()) and self._clock() < deadline:
            self._sleep(POLL_SECONDS)
        return found

    def _forget(self) -> None:
        """Drop the stopped process: its pid may now be another process's."""
        with self._lock:
            self._process = None
