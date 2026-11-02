"""The live Firefox suite's watch: geckodriver's group and Firefox's crash helper, and its lookups.

`GroupWatcher` lists every process on a thread of its own while a live run lasts, at a fixed
poll, within a bound. It records every row in the process group the fenced shim names in its
pid file (geckodriver, the Firefox it starts and that Firefox's children), and every crash
helper serving one of that group's processes, as `gecko_process.crash_helpers` recognizes one:
by its arguments, its parent and its age beside its Firefox's, never by its name, since the
reader may be running Firefox too. After the run it reports what still runs of either, and,
only should something have survived, kills a group still showing a recorded row and a
recorded helper's group: by recorded pid and arguments alone.

`looked_up_hosts` reads Firefox's host-resolver log, which the shim turns on, for each host
Firefox resolved or asked the system for.

Entry point assumptions: imported by a suite that unittest discovery loaded with this folder
as its top-level directory; the first import below puts the tool's folder and its private
`src/` on the path.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Final, Self

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
import _print_pass_live_fixtures  # noqa: F401 - imported for that bootstrap alone

import bounded_run  # the shared runner, a folder beside the tool's, via the CLI's own insert
from gecko_process import (  # the tool's private src/
    LISTING_OVERRUN_SECONDS,
    LISTING_TIMEOUT_SECONDS,
    ProcessRow,
    crash_helpers,
    list_processes,
    recorded_helpers,
)
from print_record import EngineError  # as above

#: How often the watcher lists every process: a listing takes well under this.
WATCH_POLL_SECONDS: Final = 0.25

#: Bound on the wait for the watcher's thread once told to stop: one listing, and a poll.
JOIN_SECONDS: Final = (
    LISTING_TIMEOUT_SECONDS + LISTING_OVERRUN_SECONDS + WATCH_POLL_SECONDS
)

#: The names a lookup may resolve while Firefox stays on loopback.
LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "::1", "localhost"})

#: A lookup in the host-resolver log: Firefox resolving a host (`Resolving host [<name>]`),
#: or asking the system for one (`for host [<name>]`). A line naming an address it already
#: holds reads `IP Literal [<address>]`, and is no lookup.
_LOOKUP: Final = re.compile(r"host \[([^\]]*)\]")

#: What Firefox ends each process's log name with.
_MOZ_LOG_SUFFIX: Final = ".moz_log"

#: A row's key: its pid and its whole arguments, which a later process given the pid lacks.
type RowKey = tuple[int, str]


class GroupWatcher:
    """Lists every process while a run lasts; records the shim's group and its Firefox's helpers.

    Entering lists every process once, so no helper running before the run is taken for
    the run's, then starts the watch on a thread; leaving stops it. `errors` holds what
    ended a watch early, and `listings` how many it made.
    """

    def __init__(
        self,
        pid_file: Path,
        *,
        bound_seconds: float,
        processes: Callable[[], tuple[ProcessRow, ...]] = list_processes,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = WATCH_POLL_SECONDS,
        kill_tree: Callable[[int], None] = bounded_run.kill_tree,
    ) -> None:
        """Keep the seams; nothing is listed until the watch is entered."""
        self._pid_file = pid_file
        self._bound_seconds = bound_seconds
        self._processes = processes
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._kill_tree = kill_tree
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self.watch, daemon=True)
        self._present_before: frozenset[int] = frozenset()
        self._group: int | None = None
        self.members: dict[RowKey, ProcessRow] = {}
        self.helpers: dict[RowKey, ProcessRow] = {}
        self.errors: list[str] = []
        self.listings = 0

    @property
    def group(self) -> int | None:
        """The pid the shim recorded, which is geckodriver's group's id; None until it has."""
        if self._group is None and self._pid_file.is_file():
            self._group = int(self._pid_file.read_text(encoding="utf-8"))
        return self._group

    def __enter__(self) -> Self:
        """Note every process running now, then start the watch on its thread."""
        self._present_before = frozenset(row.pid for row in self._processes())
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the watch, and wait for its thread within a bound."""
        self.stop()
        self._thread.join(JOIN_SECONDS)
        if self._thread.is_alive():
            self.errors.append(f"the watch outlived its join of {JOIN_SECONDS:g} s")

    def watch(self) -> None:
        """List every process and record what is the run's, each poll, until stopped or bound.

        The thread's body. Its bound is the run's and its join beside it: the watch starts
        before the run and must still be listing when the run is killed at its own. A watch
        that reaches it unstopped, a listing that fails, or a pid file that does not hold a
        pid ends the watch, the error kept in `errors`.
        """
        limit = self._bound_seconds + JOIN_SECONDS
        deadline = self._clock() + limit
        while self._clock() < deadline:
            try:
                self._observe(self._processes())
            except (EngineError, OSError, ValueError) as error:
                self.errors.append(f"{type(error).__name__}: {error}")
                return
            if self._stop.wait(self._poll_seconds):
                return
        # Stopped after its last wait but by the time its deadline was checked: the stop
        # ended it, not the bound.
        if not self._stop.is_set():
            self.errors.append(
                f"the watch reached its bound of {limit:g} s without being stopped"
            )

    def stop(self) -> None:
        """Tell the watch to end: it does at its next poll, or at once if it is waiting."""
        self._stop.set()

    def _observe(self, rows: Sequence[ProcessRow]) -> None:
        """Record each row of `rows` in the group, and each helper serving one of them."""
        self.listings += 1
        group = self.group
        if group is None:
            return
        members = [row for row in rows if row.pgid == group]
        for row in members:
            self.members.setdefault((row.pid, row.args), row)
        running = {row.pid for row in members}
        for helper in crash_helpers(rows, running, self._present_before):
            self.helpers.setdefault((helper.pid, helper.args), helper)

    def survivors(self, rows: Sequence[ProcessRow]) -> tuple[ProcessRow, ...]:
        """What `rows` shows of the run still running: any row in the group, any recorded helper."""
        group = self.group
        in_group = tuple(row for row in rows if group is not None and row.pgid == group)
        return in_group + recorded_helpers(rows, self.helpers.values())

    def end_survivors(self, rows: Sequence[ProcessRow]) -> None:
        """Kill what `rows` shows of the run still running, found by recorded rows alone.

        The group is killed only while `rows` shows a row the watch recorded in it, so that
        its id is still the run's; each helper's group, only while `rows` shows that helper's
        exact recorded row. A process that has taken a recorded id since is never signaled.
        """
        group = self.group
        groups = {
            row.pgid
            for row in rows
            if row.pgid == group and (row.pid, row.args) in self.members
        }
        helpers = {row.pgid for row in recorded_helpers(rows, self.helpers.values())}
        for pgid in (*sorted(groups), *sorted(helpers - groups)):
            self._kill_tree(pgid)


def looked_up_hosts(text: str) -> tuple[str, ...]:
    """Each host a host-resolver log `text` shows Firefox resolving or asking the system for."""
    return tuple(match[1] for match in _LOOKUP.finditer(text))


def moz_log_text(moz_log: Path) -> str:
    """Every process's log beside `moz_log`, the name given `MOZ_LOG_FILE`, one after another."""
    logs = sorted(moz_log.parent.glob(f"{moz_log.name}*{_MOZ_LOG_SUFFIX}"))
    return "".join(log.read_text(encoding="utf-8", errors="replace") for log in logs)
