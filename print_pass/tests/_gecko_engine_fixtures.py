"""Shared by the Firefox engine's suites: a run's every seam, faked, writing one log.

`EngineRun` hands `gecko_engine.run_targets` a scratch root under the case's own temporary
folder, a fake driver, a fake dead port, a fake removal and a fake state runner, all writing
one log, so a case runs a whole Firefox run in one line and then reads what it did, in
order. `FakeEngineDriver` is `Geckodriver` as a run sees it. Nothing here starts
geckodriver, holds a port, or removes a scratch but the case's own temporary one.

Entry point assumptions: imported by a suite under this folder, after its bootstrap; the
insert below reaches the tool's private `src/` from this file's own location.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Final, TypedDict, Unpack

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_engine  # noqa: E402 - the tool's private src/, via the insert above
from _gecko_client_fixtures import FakeBounder, FakeTransport  # noqa: E402 - beside it
from _gecko_state_fixtures import firefox_settings  # noqa: E402 - as above
from _print_engine_fixtures import BASE_URL, targets_file  # noqa: E402 - as above
from _print_report_fixtures import state_record  # noqa: E402 - as above
from gecko_launch import Scratch  # noqa: E402 - the tool's private src/
from gecko_state import StateJob  # noqa: E402 - as above
from print_record import EngineError, StateRecord  # noqa: E402 - as above
from print_targets import TargetsFile  # noqa: E402 - as above

#: Where the fake geckodriver listens.
DRIVER_URL: Final = "http://127.0.0.1:4444"

#: The executable the run is handed, as `find_geckodriver` would name it.
EXECUTABLE: Final = "/opt/fake/bin/geckodriver"

#: The dead port the fake holds.
DEAD_PORT: Final = 40002

#: One entry of the run's log.
type Entry = tuple[object, ...]


class DriverScript(TypedDict, total=False):
    """How a case's fake driver behaves, each part as `FakeEngineDriver` takes it."""

    start_error: Exception
    stop_error: Exception
    stop_writes: str
    no_log: bool


class FakeEngineDriver:
    """`Geckodriver` as a run sees it: started, stopped, its log, and its kill, each logged."""

    def __init__(
        self,
        log: list[Entry],
        scratch: Scratch,
        *,
        start_error: Exception | None = None,
        stop_error: Exception | None = None,
        stop_writes: str = "",
        no_log: bool = False,
    ) -> None:
        """A driver that fails its start or stop as given, writing `stop_writes` as it stops."""
        self.log = log
        self.scratch = scratch
        self.text = ""
        self._start_error = start_error
        self._stop_error = stop_error
        self._stop_writes = stop_writes
        self._no_log = no_log

    base_url = DRIVER_URL

    def __enter__(self) -> FakeEngineDriver:
        """Log the start, or fail it."""
        self.log.append(("start",))
        if self._start_error is not None:
            raise self._start_error
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Log the stop, naming what the block raised; write to the log; fail as given."""
        self.log.append(("stop", type(exc).__name__))
        self.text += self._stop_writes
        if self._stop_error is not None:
            raise self._stop_error

    def write(self, text: str) -> None:
        """Firefox or geckodriver writing to the log."""
        self.text += text

    def log_offset(self) -> int:
        """How many bytes the log holds."""
        return len(self.text.encode("utf-8"))

    def log_since(self, offset: int) -> str:
        """The log from `offset`, or the error of a log never made."""
        self.log.append(("log_since", offset))
        if self._no_log:
            raise FileNotFoundError(self.scratch.log)
        return self.text.encode("utf-8")[offset:].decode("utf-8")

    def note_firefox(self, pid: int) -> None:
        """Log the pid."""
        self.log.append(("note_firefox", pid))

    def record_helpers(self) -> None:
        """Log the listing."""
        self.log.append(("record_helpers",))

    def kill(self) -> None:
        """Log the kill."""
        self.log.append(("kill",))


class EngineRun:
    """One run's fakes, its log, and the records and errors its states give."""

    def __init__(self, case: unittest.TestCase, **driver: Unpack[DriverScript]) -> None:
        """A scratch root under the case's own temporary folder, and fakes writing one log."""
        folder = tempfile.TemporaryDirectory()
        case.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "scratch"
        self.pdf_dir = Path(folder.name) / "pdfs"
        self.log: list[Entry] = []
        self.jobs: list[StateJob] = []
        self.failing: dict[str, BaseException] = {}
        self.during: dict[str, object] = {}
        self.remove_error: EngineError | None = None
        self.build_error: Exception | None = None
        self.root_error: OSError | None = None
        self.scratch_blocked = False
        self.driver_options = driver
        self.driver: FakeEngineDriver | None = None
        self.guard = gecko_engine.RunGuard(
            warn=lambda line: self.log.append(("warn", line)),
            abandon=lambda what: self.log.append(("abandon", what)),
        )

    def make_root(self) -> Path:
        """Make the root, as `mkdtemp` would, and log it; or fail as the case says.

        A blocked scratch has a file where its first folder goes, so making it fails.
        """
        self.log.append(("make_root",))
        if self.root_error is not None:
            raise self.root_error
        self.root.mkdir()
        if self.scratch_blocked:
            (self.root / "app-data").write_bytes(b"")
        return self.root

    def start_driver(
        self, executable: str, scratch: Scratch, *, timeout_s: float, bounder: object
    ) -> FakeEngineDriver:
        """Build the fake driver, logging what it was handed; or fail as the case says."""
        self.log.append(("driver", executable, scratch.root, timeout_s))
        if self.build_error is not None:
            raise self.build_error
        self.driver = FakeEngineDriver(self.log, scratch, **self.driver_options)
        return self.driver

    @contextmanager
    def hold_port(self) -> Iterator[int]:
        """Hold the dead port, logging its hold and release."""
        self.log.append(("hold",))
        try:
            yield DEAD_PORT
        finally:
            self.log.append(("release",))

    def remove(self, root: Path) -> None:
        """Log the removal, or fail it."""
        self.log.append(("remove", root))
        if self.remove_error is not None:
            raise self.remove_error

    def run_one(self, client: object, driver: object, job: StateJob) -> StateRecord:
        """Run one state: log it, do what the case scripts during it, then record or fail."""
        self.jobs.append(job)
        self.log.append(("state", job.where))
        action = self.during.get(job.where)
        if callable(action):
            action(client, driver)
        if job.where in self.failing:
            raise self.failing[job.where]
        return dataclasses.replace(
            state_record(self.pdf_dir.parent),
            target=job.target.name,
            state=job.state.name,
        )

    def run(
        self,
        targets: TargetsFile | None = None,
        *,
        transport: FakeTransport | None = None,
        **settings: object,
    ) -> tuple[StateRecord, ...]:
        """Run `targets` (one default target) under the Firefox settings, `settings` changed."""
        return gecko_engine.run_targets(
            BASE_URL,
            targets or targets_file(),
            firefox_settings(self.pdf_dir, **settings),
            executable=EXECUTABLE,
            bounder=FakeBounder(self.log),
            guard=self.guard,
            run_one=self.run_one,
            transport=transport or FakeTransport(self.log, []),
            make_root=self.make_root,
            start_driver=self.start_driver,
            hold_port=self.hold_port,
            remove=self.remove,
            warn=lambda line: self.log.append(("warn", line)),
        )

    def names(self) -> list[object]:
        """The log's entries by name alone."""
        return [entry[0] for entry in self.log]
