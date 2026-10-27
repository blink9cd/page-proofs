"""A watchdog for the engine calls Playwright gives no timeout: abandon the run, never hang.

`page.evaluate`, `BrowserContext.close` and `Browser.close` take no timeout, and Playwright's
synchronous API may only be called from the thread that started it, so a stalled renderer cannot
be interrupted from another thread either. What a second thread CAN do is watch the clock. Each
bounded step runs inside `Watchdog.bound`; if it overruns, the watchdog calls `on_expire` from
its own thread. The default, `abandon`, names the step on stderr and ends the process with exit 2
-- the tool's "could not measure honestly" -- which also closes Playwright's driver pipe, and the
driver then closes its browsers. A hung run therefore fails fast instead of producing nothing.

Entry point assumptions: none beyond the standard library.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

#: The exit status of an abandoned run: the tool's "could not measure honestly".
EXIT_ABANDONED = 2

#: How long the step's end waits for its watch thread to notice and exit.
_WATCH_JOIN_SECONDS = 5.0


class Bounder(Protocol):
    """Anything that can run a step inside a time bound (the engine accepts a stand-in)."""

    def bound(self, seconds: float, what: str) -> AbstractContextManager[None]:
        """A context manager running its block inside `seconds`, naming the step `what`."""
        ...  # pragma: no cover - a protocol has no body to run


def abandon(what: str) -> None:
    """Report the overrun step and end the process at once with `EXIT_ABANDONED`."""
    # Covered by a child-process test: in-process, os._exit would end the test run itself.
    sys.stderr.write(  # pragma: no cover - runs only in the child process
        f"browser_pass: {what} did not finish in time; abandoning the run\n"
    )
    sys.stderr.flush()  # pragma: no cover - runs only in the child process
    os._exit(EXIT_ABANDONED)  # pragma: no cover - runs only in the child process


class Watchdog:
    """Runs `on_expire(what)` from a watch thread when a bounded step overruns."""

    def __init__(self, on_expire: Callable[[str], None] = abandon) -> None:
        """Keep the expiry action; the default abandons the whole run."""
        self.on_expire = on_expire

    @contextmanager
    def bound(self, seconds: float, what: str) -> Iterator[None]:
        """Run the block; if it is still running after `seconds`, call `on_expire(what)`."""
        finished = threading.Event()

        def watch() -> None:
            if not finished.wait(timeout=seconds):
                self.on_expire(what)

        watcher = threading.Thread(target=watch, name=f"watchdog: {what}", daemon=True)
        watcher.start()
        try:
            yield
        finally:
            finished.set()
            watcher.join(timeout=_WATCH_JOIN_SECONDS)
