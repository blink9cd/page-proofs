"""The Firefox engine: every state of every target printed through geckodriver, one session each.

`run_targets` is the one entry point, the Firefox counterpart of `print_engine.run_targets`.
It makes ONE scratch folder for Firefox's own folders, holds ONE dead port for the proxy,
starts ONE geckodriver, and runs every state in file order, each in a WebDriver session of
its own (`gecko_state`), the client's transport timeout a margin above the run's. A scratch
that cannot be made is an `EngineError` naming what failed, and a root made for it is
removed again, since nothing has run in it.

A state's error ends the states and is held, so the driver stops as for a clean exit: any
error, a Ctrl-C's `KeyboardInterrupt` included, which the stop's own watchdog bound keeps
bounded. Once that stop has returned, a run holding an error has the whole log checked for
Firefox's non-local guard, whose breach is the run's error before any other (a run that
succeeded is not: the guard ends the browser, and a page can write its sentence to the log),
the scratch is removed, and the held error is raised; a removal that fails is the run's error too, or, after another, said beside it. A
stop that fails while an error is held is one `EngineError` naming both, unless the held
error is a breach, which stands as it is. A start or a stop that failed leaves the scratch
where it is and says so: removal's precondition is that nothing writes into it any more,
which only a stop that returned vouches for.

`RunGuard` is the run watchdog's `on_expire`: it kills the driver's whole tree, writes one
line naming the scratch it leaves in place, then abandons as the watchdog always has. The
abandon ends the process from the watch thread, so nothing after it runs, and a run whose
guard expired never removes its scratch.

Entry point assumptions: the run is on macOS, which the caller has checked, with geckodriver
found; `base_url` is the loopback server's. Every seam is injected, so a suite runs a whole
run with nothing started.
"""

from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Protocol

import print_plan
from gecko_budget import client_timeout
from gecko_client import GeckoClient, Transport, urllib_transport
from gecko_job import ClientLike, DriverLike, StateJob
from gecko_launch import (
    DeadPort,
    Geckodriver,
    IsolationBreach,
    Scratch,
    isolation_breach,
    make_scratch,
    remove_scratch,
)
from gecko_state import run_state
from print_engine import effective_settings
from print_record import EngineError, EngineSettings, StateRecord, first_line
from print_targets import TargetsFile
from print_watchdog import Bounder, abandon

#: What a run says of a scratch folder it could not remove, before the folder's path.
LEFT_IN_PLACE: Final = "the scratch folder was left in place"

#: What a run says, after the error it held, of a driver whose stop failed too.
NOT_STOPPED: Final = "and geckodriver could not be stopped"

#: Runs one state in a session of its own, as `gecko_state.run_state` does.
type RunOne = Callable[[ClientLike, DriverLike, StateJob], StateRecord]


class Killable(Protocol):
    """What the guard ends at expiry: anything with a `kill` safe from another thread."""

    def kill(self) -> None:
        """Kill the whole tree now."""


class RunDriver(DriverLike, Killable, Protocol):
    """geckodriver as a run drives it: `gecko_launch.Geckodriver`, started by its block."""

    @property
    def base_url(self) -> str:
        """Where WebDriver commands go."""

    def __enter__(self) -> object:
        """Start geckodriver."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop geckodriver's whole group."""


class StartDriver(Protocol):
    """Builds the run's driver, as `Geckodriver`'s constructor does."""

    def __call__(
        self, executable: str, scratch: Scratch, *, timeout_s: float, bounder: Bounder
    ) -> RunDriver:
        """A driver of `executable` over `scratch`, not started yet."""


def fresh_root() -> Path:
    """A fresh, empty scratch root of the run's own, from `tempfile.mkdtemp`."""
    return Path(tempfile.mkdtemp())


class RunGuard:
    """The run watchdog's expiry: kill the driver, say where the scratch was left, abandon."""

    def __init__(
        self,
        *,
        warn: Callable[[str], None] = print_plan.warn,
        abandon: Callable[[str], None] = abandon,
    ) -> None:
        """Watch nothing yet; `abandon` is what ends the run, the watchdog's own by default."""
        self.expired = threading.Event()
        self.abandon = abandon
        self._warn = warn
        # `watch` runs on the main thread and `on_expire` on the watch thread.
        self._lock = threading.Lock()
        self._driver: Killable | None = None
        self._root: Path | None = None

    def watch(self, driver: Killable, root: Path) -> None:
        """Kill `driver` at expiry, and name `root` as the scratch left in place."""
        with self._lock:
            self._driver, self._root = driver, root

    def on_expire(self, what: str) -> None:
        """Kill the driver, then say where the scratch was left, then abandon, overran `what`.

        Each step runs even when the one before it raised, and the abandon always.
        """
        self.expired.set()
        with self._lock:
            driver, root = self._driver, self._root
        try:
            if driver is not None:
                driver.kill()
        finally:
            try:
                if root is not None:
                    self._warn(f"{LEFT_IN_PLACE}: {root}")
            finally:
                self.abandon(what)


def run_targets(
    base_url: str,
    targets: TargetsFile,
    settings: EngineSettings,
    *,
    executable: str,
    bounder: Bounder,
    guard: RunGuard,
    run_one: RunOne = run_state,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    transport: Transport = urllib_transport,
    make_root: Callable[[], Path] = fresh_root,
    start_driver: StartDriver = Geckodriver,
    hold_port: Callable[[], AbstractContextManager[int]] = DeadPort,
    remove: Callable[[Path], None] = remove_scratch,
    warn: Callable[[str], None] = print_plan.warn,
) -> tuple[StateRecord, ...]:
    """Print every state of every target in `targets`, served at `base_url`; one record each.

    Raises:
        IsolationBreach: Firefox's non-local guard refused a connection.
        EngineError: the scratch could not be made, or a state, the driver's start or stop,
            or the scratch's removal failed.
        BaseException: whatever else a state raised, a `KeyboardInterrupt` included, once
            the driver's stop has returned.
    """
    effective = effective_settings(settings, targets)
    root = _made_root(make_root)
    scratch = _made_scratch(root, remove, warn)
    end = _RunEnd(root, guard, remove, warn)
    try:
        with hold_port() as dead_port:
            driver = start_driver(
                executable, scratch, timeout_s=effective.timeout_s, bounder=bounder
            )
            end.driver = driver
            guard.watch(driver, root)
            jobs = [
                StateJob(
                    target=target,
                    state=state,
                    settings=effective,
                    base_url=base_url,
                    dead_port=dead_port,
                    expected_hosts=targets.expected_offsite_hosts,
                    scratch_root=root,
                    bounder=bounder,
                    clock=clock,
                    sleep=sleep,
                )
                for target in targets.targets
                for state in target.states
            ]
            timeout_s = client_timeout(effective.timeout_s)
            records, held = _drive(
                driver,
                jobs,
                run_one,
                lambda url: GeckoClient(
                    url, transport=transport, bounder=bounder, timeout_s=timeout_s
                ),
                warn,
            )
            end.stopped = True
    except BaseException as error:
        raise end.ended(error) or error
    final = end.ended(held)
    if final is not None:
        raise final
    return records


def _made_root(make_root: Callable[[], Path]) -> Path:
    """The run's fresh scratch root, as `make_root` makes it.

    Raises:
        EngineError: the root could not be made, naming why.
    """
    try:
        return make_root()
    except OSError as error:
        raise _not_made(error) from error


def _made_scratch(
    root: Path, remove: Callable[[Path], None], warn: Callable[[str], None]
) -> Scratch:
    """The scratch's folders, made under `root`; `root` removed again if they could not be.

    Nothing has run in `root` yet, so removal's precondition, that nothing writes into it,
    holds. A removal that fails is said, as `remove_scratch`'s error says it, naming `root`.

    Raises:
        EngineError: a folder could not be made, naming `root` and why; or `make_scratch`
            refused `root`, as it says.
    """
    try:
        return make_scratch(root)
    except OSError as error:
        try:
            remove(root)
        except EngineError as removal:
            warn(first_line(removal))
        raise _not_made(error, root) from error


def _not_made(error: OSError, root: Path | None = None) -> EngineError:
    """The error a scratch that could not be made is reported with: `root`, if made, and why.

    Worded as `make_scratch` words a root it refused. The OS error's class is named too,
    since a bare one's text can be empty.
    """
    where = "" if root is None else f" {root}"
    reason = f"{type(error).__name__}: {first_line(error)}"
    return EngineError(f"the scratch folder{where} was not made: {reason}")


def _drive(
    driver: RunDriver,
    jobs: Sequence[StateJob],
    run_one: RunOne,
    client_for: Callable[[str], ClientLike],
    warn: Callable[[str], None],
) -> tuple[tuple[StateRecord, ...], BaseException | None]:
    """Run each job in turn while the driver runs; the records, and the error held, if any.

    The first error a state raises ends the states and is held, so the driver's stop, as the
    block exits, runs as for a clean exit; the error is returned once the stop has returned.

    Raises:
        IsolationBreach: the error held, when it is one and the stop failed: unchanged, the
            stop's failure said beside it.
        EngineError: the stop failed with any other error held: both named, from the held.
        Exception: the start failed, or the stop failed with no error held.
    """
    records: list[StateRecord] = []
    held: BaseException | None = None
    try:
        with driver:
            held = _run_jobs(driver, jobs, run_one, client_for, records)
    except Exception as stop:
        if held is None:
            raise
        failed = stop
    else:
        return tuple(records), held
    # Raised here, outside the handler, so a held breach leaves exactly as it was raised.
    if isinstance(held, IsolationBreach):
        warn(f"geckodriver could not be stopped: {first_line(failed)}")
        raise held
    said = first_line(held) or type(held).__name__  # an interrupt's text is empty
    raise EngineError(f"{said}; {NOT_STOPPED}: {first_line(failed)}") from held


def _run_jobs(
    driver: RunDriver,
    jobs: Sequence[StateJob],
    run_one: RunOne,
    client_for: Callable[[str], ClientLike],
    records: list[StateRecord],
) -> BaseException | None:
    """Run each job in turn, adding its record to `records`; the first error, held, if any.

    The error is returned, not raised, so the driver's stop is told nothing went wrong.
    """
    try:
        client = client_for(driver.base_url)
        for job in jobs:
            records.append(run_one(client, driver, job))
    except (Exception, KeyboardInterrupt) as error:
        return error
    return None


@dataclass
class _RunEnd:
    """How a run ends: its breach check, then its scratch removed or left, and its error."""

    root: Path
    guard: RunGuard
    remove: Callable[[Path], None]
    warn: Callable[[str], None]
    driver: DriverLike | None = None
    stopped: bool = False  # the driver's stop returned

    def ended(self, error: BaseException | None) -> BaseException | None:
        """End the run after `error`, or none: the error it ends with, if any."""
        final = self._breach_first(error)
        if self.guard.expired.is_set():
            return final  # its expiry has said where the scratch was left
        if not self.stopped:
            self.warn(f"{LEFT_IN_PLACE}: {self.root}")
            return final
        try:
            self.remove(self.root)
        except EngineError as removal:
            if final is None:
                return removal
            self.warn(first_line(removal))
        return final

    def _breach_first(self, error: BaseException | None) -> BaseException | None:
        """A breach the whole log names, raised from `error`; else `error` as it was.

        A breach a state already named is kept as it named it. A run that succeeded is never
        checked: the guard ends the browser, and a page can write its sentence to the log.
        """
        if error is None or isinstance(error, IsolationBreach):
            return error
        breach = self._whole_log_breach()
        if breach is None:
            return error
        found = IsolationBreach(breach)
        found.__cause__ = error
        return found

    def _whole_log_breach(self) -> str | None:
        """The breach the driver's whole log names, if the log was ever made."""
        if self.driver is None:
            return None
        try:
            log_text = self.driver.log_since(0)
        except FileNotFoundError:
            return None  # the start failed before it made the log
        return isolation_breach(log_text)
