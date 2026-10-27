"""`print_watchdog`: the bound on every Playwright call, a stalled one abandoning the run.

The machine these run on can be heavily loaded, so no test here races the clock. A block
that must outlive its bound waits on an Event the injected expiry sets, which proves the
ORDER (the watch fired while the block still ran) however slow the machine is. A block that
must NOT be expired runs under a bound far longer than the test, and the test then checks
that the watch thread ended without firing -- a watch left running would still be alive.

`abandon` ends the process with `os._exit`, which in-process would end the test run too, so
its real exit is proven in a child process. The in-process tests stand a recorder in for
`os._exit`, because a child's lines are invisible to this process's coverage. The exit must
happen even when stderr cannot take the report, so both halves also run with a broken
stderr: a child with fd 2 closed, and in-process streams that fail or are missing.

Entry point assumptions: standalone, or discovered by `unittest discover`. The inserts below
reach the subject's private `src/` beside this folder and the shared `bounded_run` beside
the tool's own, never the working directory.
"""

from __future__ import annotations

import contextlib
import errno
import io
import math
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# The shared library: a folder beside this tool's own, named rather than searched for, so a
# same-named folder above the clone is never imported in its place, and put near the front
# of the path, ahead of every installed distribution, so none of the same name can shadow
# it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run  # noqa: E402 - a folder beside the tool's, via the insert above
import print_watchdog  # noqa: E402 - the tool's private src/, via the insert above

#: Far longer than any block here takes, even on a loaded machine: a bound that never fires.
_GENEROUS = 30.0

#: The bound a block is made to outlive.
_SHORT = 0.05

#: Bound on each child process a test starts.
_CHILD_TIMEOUT = 30.0

#: How long a stalled block in a child waits before giving up and reporting it survived.
#: Shorter than `_CHILD_TIMEOUT`, so a watchdog that never fires fails as a clean assertion.
_CHILD_STALL_SECONDS = 20.0

#: The tool's "could not measure honestly" exit status, which an abandoned run must use.
_EXIT_CANNOT_MEASURE = 2

#: What a child prints if the process outlives the call that should have ended it.
_SURVIVED = "still running"

#: A child that abandons outright, then reports if it is somehow still alive.
#: argv: the subject's src/ folder, then the name of the abandoned call.
_ABANDON_SCRIPT = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import print_watchdog\n"
    "print_watchdog.abandon(sys.argv[2])\n"
    f"print({_SURVIVED!r})\n"
)

#: A child whose block stalls inside a default Watchdog's bound, as a hung renderer would.
#: argv: the subject's src/ folder, the name of the call, its bound, how long it stalls.
_STALL_SCRIPT = (
    "import sys, threading\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import print_watchdog\n"
    "with print_watchdog.Watchdog().bound(float(sys.argv[3]), sys.argv[2]):\n"
    "    threading.Event().wait(float(sys.argv[4]))\n"
    f"print({_SURVIVED!r})\n"
)

#: Put before a child script: closes the child's fd 2 first, so every write to stderr fails
#: the way it does once the stream behind it is gone.
_CLOSE_STDERR = "import os\nos.close(2)\n"


class _StepFailedError(Exception):
    """Raised inside a bounded block to show the watch still ends."""


class _ProcessEndedError(Exception):
    """Raised by the stand-in for `os._exit`, because the real call never returns."""


class _BrokenStderr(io.StringIO):
    """A stderr whose `write` or `flush` fails as a closed fd 2 or a broken pipe does."""

    def __init__(self, failing_step: str) -> None:
        """Fail on `failing_step`, which is "write" or "flush"."""
        super().__init__()
        self._failing_step = failing_step

    def write(self, text: str) -> int:
        """Take the text, unless this stream's failure is the write."""
        if self._failing_step == "write":
            raise OSError(errno.EPIPE, os.strerror(errno.EPIPE))
        return super().write(text)

    def flush(self) -> None:
        """Flush, unless this stream's failure is the flush."""
        if self._failing_step == "flush":
            raise OSError(errno.EBADF, os.strerror(errno.EBADF))
        super().flush()


def _closed_stderr() -> io.StringIO:
    """A stderr closed underneath the writer: its `write` raises `ValueError`."""
    stream = io.StringIO()
    stream.close()
    return stream


def _abandon_recording_exits(what: str, stderr: io.StringIO | None) -> list[int]:
    """Run `abandon(what)` against `stderr`; return each code `os._exit` was called with.

    A recorder stands in for `os._exit`. Like the real call it never returns: it raises
    `_ProcessEndedError`, which is swallowed here, so any other error `abandon` lets out
    still reaches the test.
    """
    exits: list[int] = []

    def end_process(code: int) -> None:
        exits.append(code)
        raise _ProcessEndedError

    with (
        mock.patch.object(os, "_exit", side_effect=end_process),
        contextlib.redirect_stderr(stderr),
        contextlib.suppress(_ProcessEndedError),
    ):
        print_watchdog.abandon(what)
    return exits


def _run_stalled_child(script: str, what: str) -> subprocess.CompletedProcess[str]:
    """Run a stall script in a child, stalling on the call `what` far past its bound."""
    argv = [
        sys.executable,
        "-c",
        script,
        str(_TOOL_DIR / "src"),
        what,
        str(_SHORT),
        str(_CHILD_STALL_SECONDS),
    ]
    return bounded_run.run_bounded(argv, timeout=_CHILD_TIMEOUT)


def _new_threads_naming(
    what: str, before: set[threading.Thread]
) -> list[threading.Thread]:
    """The threads started since `before` whose name carries the watched call's `what`."""
    return [
        thread
        for thread in threading.enumerate()
        if thread not in before and what in thread.name
    ]


def _abandon_message(what: str) -> str:
    """The exact line `abandon` writes for the call `what`."""
    return f"print_pass: abandoned: {what} overran its bound\n"


class BoundTests(unittest.TestCase):
    """A block that ends in time is left alone; one that overruns is expired, naming it."""

    def test_a_block_that_finishes_in_time_never_expires(self) -> None:
        fired: list[str] = []
        # Typed as the protocol, so the type checker proves Watchdog satisfies it.
        bounder: print_watchdog.Bounder = print_watchdog.Watchdog(
            on_expire=fired.append
        )
        before = set(threading.enumerate())
        with bounder.bound(_GENEROUS, "a quick print"):
            watchers = _new_threads_naming("a quick print", before)
            self.assertEqual(len(watchers), 1)
            self.assertTrue(watchers[0].is_alive())
            # A daemon: a watch whose join gave up must never keep the process alive.
            self.assertTrue(watchers[0].daemon)
        # The block's exit stopped the watch and joined it: nothing is left waiting to fire.
        # Relies on the watch ending inside the module's bounded join (_JOIN_SECONDS).
        self.assertFalse(watchers[0].is_alive())
        self.assertEqual(fired, [])

    def test_a_block_that_overruns_is_expired_once_naming_the_call(self) -> None:
        expired = threading.Event()
        names: list[str] = []
        callers: list[threading.Thread] = []

        def expire(what: str) -> None:
            names.append(what)
            callers.append(threading.current_thread())
            expired.set()

        watchdog = print_watchdog.Watchdog(on_expire=expire)
        with watchdog.bound(_SHORT, "printing the page"):
            # The block cannot end before the watch fires: it waits for the expiry itself.
            self.assertTrue(expired.wait(timeout=_GENEROUS))
        self.assertEqual(names, ["printing the page"])
        # Fired from the watch thread, while this thread was still inside the block.
        self.assertIsNot(callers[0], threading.current_thread())

    def test_an_exception_in_the_block_still_stops_the_watch(self) -> None:
        fired: list[str] = []
        watchdog = print_watchdog.Watchdog(on_expire=fired.append)
        before = set(threading.enumerate())
        watchers: list[threading.Thread] = []
        with (
            self.assertRaises(_StepFailedError),
            watchdog.bound(_GENEROUS, "a failing print"),
        ):
            watchers.extend(_new_threads_naming("a failing print", before))
            raise _StepFailedError
        self.assertEqual(len(watchers), 1)
        # Relies on the watch ending inside the module's bounded join (_JOIN_SECONDS).
        self.assertFalse(watchers[0].is_alive())
        self.assertEqual(fired, [])

    def test_a_bound_no_lock_can_wait_is_refused_before_any_watch_starts(self) -> None:
        # Zero, negative and NaN would expire at once; a bound past what a lock can wait
        # would kill the watch thread on its first wait and leave the call unbounded.
        for seconds in (0.0, -1.0, math.nan, math.inf, threading.TIMEOUT_MAX * 2):
            with self.subTest(seconds=seconds):
                fired: list[str] = []
                watchdog = print_watchdog.Watchdog(on_expire=fired.append)
                before = set(threading.enumerate())
                bounded = watchdog.bound(seconds, "a bad bound")
                # Entering is where a `with` would run its block next, so a refusal
                # raised here means no block ever runs under the bad bound.
                with self.assertRaises(ValueError):
                    bounded.__enter__()
                self.assertEqual(_new_threads_naming("a bad bound", before), [])
                self.assertEqual(fired, [])

    def test_the_longest_bound_a_lock_can_wait_is_accepted(self) -> None:
        fired: list[str] = []
        watchdog = print_watchdog.Watchdog(on_expire=fired.append)
        with watchdog.bound(threading.TIMEOUT_MAX, "the longest bound"):
            pass
        self.assertEqual(fired, [])


class AbandonTests(unittest.TestCase):
    """The default expiry names the call and ends the whole process with exit 2."""

    def test_abandon_ends_the_process_with_two_naming_the_call(self) -> None:
        done = bounded_run.run_bounded(
            [
                sys.executable,
                "-c",
                _ABANDON_SCRIPT,
                str(_TOOL_DIR / "src"),
                "closing the page",
            ],
            timeout=_CHILD_TIMEOUT,
        )
        self.assertEqual(done.returncode, _EXIT_CANNOT_MEASURE)
        self.assertEqual(done.stderr, _abandon_message("closing the page"))
        self.assertNotIn(_SURVIVED, done.stdout)

    def test_the_default_watchdog_abandons_a_stalled_block(self) -> None:
        # The stall runs on the main thread, as a hung Playwright call would, so this also
        # proves an exit from the watch thread ends a process whose main thread is stuck.
        done = _run_stalled_child(_STALL_SCRIPT, "printing a stalled page")
        self.assertEqual(done.returncode, _EXIT_CANNOT_MEASURE)
        self.assertEqual(done.stderr, _abandon_message("printing a stalled page"))
        self.assertNotIn(_SURVIVED, done.stdout)

    def test_the_default_watchdog_abandons_a_stalled_block_with_fd_2_closed(
        self,
    ) -> None:
        # With nowhere to report, the exit must come anyway: a report that raised on the
        # watch thread would end only that thread, and the stall would run its course.
        done = _run_stalled_child(_CLOSE_STDERR + _STALL_SCRIPT, "printing, no stderr")
        self.assertEqual(done.returncode, _EXIT_CANNOT_MEASURE)
        self.assertNotIn(_SURVIVED, done.stdout)

    def test_abandon_exits_when_the_stderr_write_fails(self) -> None:
        exits = _abandon_recording_exits("closing the page", _BrokenStderr("write"))
        self.assertEqual(exits, [_EXIT_CANNOT_MEASURE])

    def test_abandon_exits_when_the_stderr_flush_fails(self) -> None:
        exits = _abandon_recording_exits("closing the page", _BrokenStderr("flush"))
        self.assertEqual(exits, [_EXIT_CANNOT_MEASURE])

    def test_abandon_exits_when_stderr_is_closed(self) -> None:
        exits = _abandon_recording_exits("closing the page", _closed_stderr())
        self.assertEqual(exits, [_EXIT_CANNOT_MEASURE])

    def test_abandon_exits_when_there_is_no_stderr(self) -> None:
        # sys.stderr is None in a process started with no stream behind fd 2.
        exits = _abandon_recording_exits("closing the page", None)
        self.assertEqual(exits, [_EXIT_CANNOT_MEASURE])

    def test_abandon_writes_then_flushes_its_message_before_it_exits(self) -> None:
        # os._exit skips the interpreter's own flush at shutdown, so the order is the point.
        events: list[str] = []

        class _RecordingStream(io.StringIO):
            def write(self, text: str) -> int:
                events.append("write")
                return super().write(text)

            def flush(self) -> None:
                events.append("flush")
                super().flush()

        stream = _RecordingStream()
        with (
            mock.patch.object(
                os, "_exit", side_effect=lambda code: events.append(f"exit {code}")
            ),
            contextlib.redirect_stderr(stream),
        ):
            print_watchdog.abandon("closing the browser")
        self.assertEqual(stream.getvalue(), _abandon_message("closing the browser"))
        self.assertEqual(events, ["write", "flush", f"exit {_EXIT_CANNOT_MEASURE}"])

    def test_the_abandoned_status_is_the_cannot_measure_code(self) -> None:
        self.assertEqual(print_watchdog.EXIT_ABANDONED, _EXIT_CANNOT_MEASURE)


if __name__ == "__main__":
    unittest.main()
