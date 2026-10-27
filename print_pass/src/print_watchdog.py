"""A watchdog for every Playwright call: abandon the run, never hang.

`page.pdf`, `page.evaluate` and every `close()` take no timeout, and a call that takes one
is only as good as Playwright's synchronous dispatcher, which delivers that timeout: a
dispatcher wedged by a handler never does. Playwright's synchronous API may be called only
from the thread that started it, so a stalled call cannot be interrupted from another thread
either. What a second thread CAN do is watch the clock. Each call runs inside
`Watchdog.bound`; if it overruns, the watch thread calls `on_expire(what)`. The default,
`abandon`, names the call on stderr and ends the process with exit 2 -- the tool's "could
not measure honestly". Ending the process closes Playwright's driver pipe, and the driver
then closes its browsers, so a stalled print fails fast instead of hanging the run.

Entry point assumptions: none beyond the standard library.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Final, Protocol

#: The exit status of an abandoned run: the tool's "could not measure honestly".
EXIT_ABANDONED: Final = 2

#: The name the abandon message leads with, so a mixed log says which tool gave up.
_TOOL_NAME: Final = "print_pass"

#: How long a block's exit waits for its watch thread to end. Once the block has set the
#: event the thread only has to wake and return, so this is reached only when `on_expire`
#: is itself still running; it keeps a hung expiry from hanging the block's exit too.
_JOIN_SECONDS: Final = 5.0


class Bounder(Protocol):
    """Anything that runs a block inside a time bound; the engine accepts a stand-in."""

    def bound(self, seconds: float, what: str) -> AbstractContextManager[None]:
        """Return a context manager running its block inside `seconds`, naming it `what`."""


def abandon(what: str) -> None:
    """Report the overrun call on stderr and end the process at once with `EXIT_ABANDONED`.

    The report is best effort; the exit happens even when stderr cannot take it.
    """
    try:
        sys.stderr.write(f"{_TOOL_NAME}: abandoned: {what} overran its bound\n")
        # os._exit skips the interpreter's shutdown, its flush of buffered streams
        # included, so the message is flushed here or it could die with the process.
        sys.stderr.flush()
    finally:
        # Unconditional, because the exit is the point: a stderr that is closed, a broken
        # pipe or None raises above, and an error escaping here would end only the watch
        # thread, silently, leaving the call it watches hung for good.
        # os._exit, not sys.exit: this runs on the watch thread, where SystemExit would
        # end only that thread, while the main thread stays stuck inside Playwright.
        os._exit(EXIT_ABANDONED)


def check_bound(seconds: float) -> None:
    """Refuse a bound the watch thread could not wait out, before any block runs.

    Zero, a negative number and NaN would expire at once. A bound past
    `threading.TIMEOUT_MAX` raises inside the watch thread's first wait, which would kill
    the thread silently and leave the call unbounded -- the one failure this module exists
    to prevent. Public so a stand-in for the watchdog refuses exactly what it refuses.

    Raises:
        ValueError: `seconds` is not more than 0 and at most `threading.TIMEOUT_MAX`.
    """
    if not 0 < seconds <= threading.TIMEOUT_MAX:
        raise ValueError(
            "a bound must be more than 0 and at most "
            f"{threading.TIMEOUT_MAX} seconds, not {seconds!r}"
        )


class Watchdog:
    """Calls `on_expire(what)` from a watch thread when a bounded block overruns."""

    def __init__(self, on_expire: Callable[[str], None] = abandon) -> None:
        """Keep the expiry action; the default abandons the whole run."""
        self._on_expire = on_expire

    @contextmanager
    def bound(self, seconds: float, what: str) -> Iterator[None]:
        """Run the block; if it is still running after `seconds`, call `on_expire(what)`.

        Raises `ValueError` on entry, before the block runs, for a bound that is not
        positive or is longer than a lock can wait.
        """
        check_bound(seconds)
        finished = threading.Event()

        def watch() -> None:
            # wait() is False only when the bound passed with the block still running.
            if not finished.wait(timeout=seconds):
                self._on_expire(what)

        # Named for the call, so a thread dump says which call is being watched. A daemon,
        # so a watch whose join gave up can never keep the process alive on its own.
        watcher = threading.Thread(
            target=watch, name=f"{_TOOL_NAME} watchdog: {what}", daemon=True
        )
        watcher.start()
        try:
            yield
        finally:
            finished.set()
            watcher.join(timeout=_JOIN_SECONDS)
