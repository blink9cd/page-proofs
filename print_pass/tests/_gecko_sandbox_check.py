"""The fence's check from outside: libSystem's `sandbox_check`, asked about the canary process.

The canaries prove the fence by behavior, and the canary asks `sandbox_check` about its own
lookup of the resolver's mach service before it looks a name up. This proves the fence a
second way, as the profile itself answers, from outside: while the canary waits under the
fence, the proof asks `sandbox_check` whether the canary is sandboxed, which it must be;
whether it may look up the resolver's mach service and read the canary file, both of which
must be denied; and whether it may look up a service the profile leaves alone, the control,
which must not be. The asking process must itself read as not sandboxed, or its answers
could not be trusted; and since the canary must read as sandboxed, that question is shown to
tell the two apart. A rule that applies but matches nothing (a renamed service) answers
0 here.

The binding to `sandbox_check` is the canary's, which can import nothing but the standard
library; its module says what the function is and where it comes from. A host where it
cannot be reached FAILS the proof, never skips it: the proof runs only where `sandbox-exec`
does, and every such host has it.

Entry point assumptions: imported by the proof's fixtures, or by a suite that unittest
discovery loaded with this folder as its top-level directory. It imports the canary beside
it and the standard library alone, and loads libSystem only when a check is made.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Final, Self, cast

import _gecko_canary as canary  # beside this module

#: The control: a mach service the profile leaves to its allow-default base.
CONTROL_SERVICE: Final = "com.apple.cfprefsd.daemon"

#: The questions, by what each asks.
CANARY_SANDBOXED: Final = "whether the canary is sandboxed"
DNSSD_DENIED: Final = f"the canary's lookup of {canary.DNSSD_SERVICE}"
READ_DENIED: Final = "the canary's read under the canary folder"
CONTROL_ALLOWED: Final = f"the canary's lookup of {CONTROL_SERVICE}, the control"
CHECKER_UNSANDBOXED: Final = "whether the checking process is sandboxed"

#: Each question, and the one answer a fence that holds gives: 1 is denied, or sandboxed.
REQUIRED_ANSWERS: Final = (
    (CANARY_SANDBOXED, 1),
    (DNSSD_DENIED, 1),
    (READ_DENIED, 1),
    (CONTROL_ALLOWED, 0),
    (CHECKER_UNSANDBOXED, 0),
)

#: How often the checker looks for the canary's report, and how long it waits to be joined
#: once told to stop: one look, and a call per question, each answered at once.
_POLL_SECONDS: Final = 0.01
_JOIN_SECONDS: Final = 2.0

#: What the checker holds until it has finished: a check that never ran fails the proof.
_NEVER_FINISHED: Final = "sandbox_check never finished"


def canary_answers(
    check: canary.SandboxCheck, pid: int, canary_file: Path, own_pid: int
) -> dict[str, int]:
    """What `check` answers to each question about the canary `pid`, and the asker `own_pid`."""
    return {
        CANARY_SANDBOXED: check.is_sandboxed(pid),
        DNSSD_DENIED: check.denies_lookup(pid, canary.DNSSD_SERVICE),
        READ_DENIED: check.denies_read(pid, canary_file),
        CONTROL_ALLOWED: check.denies_lookup(pid, CONTROL_SERVICE),
        CHECKER_UNSANDBOXED: check.is_sandboxed(own_pid),
    }


def answer_failures(answers: Mapping[str, int]) -> tuple[str, ...]:
    """Each question `answers` gave other than a fence that holds would; empty when none."""
    return tuple(
        f"sandbox_check: {question} answered {answers.get(question)}, not {required}"
        for question, required in REQUIRED_ANSWERS
        if answers.get(question) != required
    )


class CanaryChecker:
    """On a thread of its own: waits for the canary's report, asks about its pid, releases it.

    Entering starts the thread; leaving stops its wait and joins it within a bound. Until
    the check has finished, `failures` says it never did, so a thread that died or never
    ran fails the proof rather than passing it.
    """

    def __init__(
        self,
        report: Path,
        canary_file: Path,
        *,
        bound_seconds: float,
        load: Callable[[], canary.SandboxCheck] = canary.load_sandbox_check,
        poll_seconds: float = _POLL_SECONDS,
    ) -> None:
        """Keep where the canary reports, what it must not read, and the seams."""
        self._report = report
        self._canary_file = canary_file
        self._bound_seconds = bound_seconds
        self._load = load
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self.check, daemon=True)
        self.answers: dict[str, int] = {}
        self.failures: tuple[str, ...] = (_NEVER_FINISHED,)

    def __enter__(self) -> Self:
        """Start the check on its thread."""
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the wait for a report, and join the thread within a bound."""
        self._stop.set()
        self._thread.join(_JOIN_SECONDS)

    def check(self) -> None:
        """The thread's body: wait for the report, ask, then mark the canary checked."""
        pid = self._await_pid()
        if pid is None:
            self.failures = (
                "sandbox_check: the canary wrote no report naming its pid",
            )
            return
        try:
            self.failures = self._ask(pid)
        finally:
            # Released whatever was answered: a canary held to its bound adds only time.
            canary.checked_marker(self._report).touch()

    def _await_pid(self) -> int | None:
        """The pid the canary's report names, once written; None when stopped or bounded."""
        deadline = time.monotonic() + self._bound_seconds
        while not self._report.is_file():
            if time.monotonic() >= deadline or self._stop.wait(self._poll_seconds):
                return None
        # Written whole by the canary, through a staged copy, so never read half-written.
        text = self._report.read_text(encoding="utf-8")
        return cast(canary.CanaryReport, json.loads(text))["pid"]

    def _ask(self, pid: int) -> tuple[str, ...]:
        """Ask `sandbox_check` about canary `pid`; each answer it gave wrongly, or none loaded."""
        try:
            check = self._load()
        except (OSError, AttributeError, ValueError) as error:
            return (f"sandbox_check cannot be reached on this host: {error}",)
        self.answers = canary_answers(check, pid, self._canary_file, os.getpid())
        return answer_failures(self.answers)
