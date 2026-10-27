"""The live Firefox suite's proof of its fence: the canaries run under it, and checked from outside.

A fence that silently fails to apply looks exactly like one that works, so before anything
launches `prove_fence` runs the canaries of `_gecko_canary` under the very profile file the
run will use, in a process and in a child it starts, with a listener of its own on each
loopback address for them to reach; and, while the canary waits under the profile, asks
libSystem's `sandbox_check` about it from outside, through `_gecko_sandbox_check`. A run is
made only when every probe gave what the fence must, both listeners heard from both
processes, and every answer was a held fence's.

Entry point assumptions: imported by a suite that unittest discovery loaded with this folder
as its top-level directory; the first import below puts the tool's folder and its private
`src/` on the path. macOS alone has `sandbox-exec`; the verdict here runs anywhere.
"""

from __future__ import annotations

import contextlib
import errno
import json
import socket
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
from _gecko_live_fixtures import SANDBOX_EXEC, Fence

import _gecko_canary as canary  # beside this module
import _gecko_sandbox_check as sandbox  # as above
import bounded_run  # the shared runner, a folder beside the tool's, via the CLI's own insert

#: Bound on the whole proof: `sandbox-exec`, two interpreters' starts and their probes,
#: each of which the fence answers at once, and the canary's wait to be checked. Measured at
#: a quarter second, at a load average of 42 on 10 cores. Kept short: a hang here is spent
#: from the aggregator's own ten-minute budget, which every live run's bound shares.
FENCE_PROOF_SECONDS: Final = 10.0

#: The canary processes: the one the proof starts, and the child it starts.
_CANARY_RUNS: Final = 2

#: The loopback listeners, in the order the verdict names them: one on each address the
#: canary probes, both at one port.
LISTENER_HOSTS: Final = (canary.LOOPBACK_V4, canary.LOOPBACK_V6)

#: How many fresh ports are tried for one free on both addresses at once.
_BIND_ATTEMPTS: Final = 5

#: Each listener's queue: room for every canary's connection, never accepted until the
#: canaries have ended.
_LISTEN_BACKLOG: Final = 8

#: Bound on reading one queued connection, whose sender has already closed it.
_RECV_SECONDS: Final = 2.0
_RECV_BYTES: Final = 64


@dataclass(frozen=True)
class Proof:
    """How the canaries ended: what failed, if anything, each process's report, and the checks.

    `checks` is each question `sandbox_check` was asked and its answer; empty when it was
    never asked.
    """

    failures: tuple[str, ...]
    parent: canary.CanaryReport | None
    child: canary.CanaryReport | None
    checks: Mapping[str, int] = field(default_factory=dict)


def canary_failures(
    parent: canary.CanaryReport | None,
    child: canary.CanaryReport | None,
    received: Mapping[str, Sequence[bytes]],
) -> tuple[str, ...]:
    """Every way the canaries fell short of a fence that holds; empty when it held.

    Each process must have run every probe and got what `_gecko_canary.REQUIRED` says; the
    child must name the canary as its parent; and the listener on each loopback address,
    keyed by it in `received`, must have received the canaries' payload once from each.
    """
    failures = [
        *_probe_failures("the canary", parent),
        *_probe_failures("its child", child),
    ]
    if parent is not None and child is not None and child["ppid"] != parent["pid"]:
        failures.append(
            f"its child names parent {child['ppid']}, not the canary's pid {parent['pid']}"
        )
    for host in LISTENER_HOSTS:
        got = list(received.get(host, ()))
        if got != [canary.PAYLOAD] * _CANARY_RUNS:
            failures.append(
                f"the {host} listener received {got!r}, not {canary.PAYLOAD!r} twice"
            )
    return tuple(failures)


def _probe_failures(who: str, report: canary.CanaryReport | None) -> list[str]:
    """Each probe `who` did not run, or that gave other than what the fence must give."""
    if report is None:
        return [f"{who} wrote no report"]
    given = {item["probe"]: item["outcome"] for item in report["results"]}
    return [
        f"{who}: {probe} gave {given[probe]}, not {required}"
        if probe in given
        else f"{who}: {probe} did not run"
        for probe, required in canary.REQUIRED
        if given.get(probe) != required
    ]


def prove_fence(fence: Fence) -> Proof:
    """Run the canaries under `fence.profile`, in a process and its child; how they ended.

    The listeners the loopback probes reach are this process's own, outside the fence. They
    are read only after the canaries end: their connections wait in each one's queue. The
    checker asks about the canary from this process too, which is outside the fence.
    """
    reports = (fence.proof / "canary.json", fence.proof / "child.json")
    checker = sandbox.CanaryChecker(
        reports[0], fence.canary_file, bound_seconds=FENCE_PROOF_SECONDS
    )
    with _loopback_listeners() as listeners, checker:
        config = canary.encode_config(
            loopback_port=listeners[0].getsockname()[1],
            canary_file=fence.canary_file,
            report=reports[0],
            child_report=reports[1],
        )
        try:
            answer = _run_canary(fence.profile, config)
        except subprocess.TimeoutExpired:
            ended = f"the canaries did not end within {FENCE_PROOF_SECONDS:g} s"
            return Proof((ended,), None, None)
        received = {
            host: _drain(listener)
            for host, listener in zip(LISTENER_HOSTS, listeners, strict=True)
        }
    parent, child = (_load_report(report) for report in reports)
    failures = (*canary_failures(parent, child, received), *checker.failures)
    if answer.returncode != 0:
        said = answer.stderr.strip().splitlines()[-1:] or ["nothing on stderr"]
        failures = (*failures, f"the canary exited {answer.returncode}: {said[0]}")
    return Proof(failures, parent, child, checker.answers)


def _run_canary(profile: Path, config: str) -> subprocess.CompletedProcess[str]:
    """Run the canary under `profile`, told `config` on stdin, within the proof's bound.

    Raises:
        subprocess.TimeoutExpired: the canaries did not end within it; their tree is killed.
    """
    argv = [
        *(SANDBOX_EXEC, "-f", str(profile)),
        *(sys.executable, "-I", str(Path(canary.__file__).resolve())),
    ]
    return bounded_run.run_bounded(argv, timeout=FENCE_PROOF_SECONDS, stdin_text=config)


@contextlib.contextmanager
def _loopback_listeners() -> Iterator[tuple[socket.socket, ...]]:
    """A listener on each of `LISTENER_HOSTS`, all at one port: the one the canary is told.

    The port is taken on the first address, then asked for on the next; one another
    program holds there is let go, and a fresh one tried.

    Raises:
        OSError: no port was free on every address within the attempts, or a bind failed
            for another reason.
    """
    for _ in range(_BIND_ATTEMPTS):
        with contextlib.ExitStack() as stack:
            first = stack.enter_context(_listener(LISTENER_HOSTS[0], 0))
            port = first.getsockname()[1]
            try:
                rest = [
                    stack.enter_context(_listener(host, port))
                    for host in LISTENER_HOSTS[1:]
                ]
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    raise
                continue
            yield (first, *rest)
            return
    raise OSError(errno.EADDRINUSE, "no loopback port was free on both addresses")


def _listener(host: str, port: int) -> socket.socket:
    """A listener on `host`, at `port`, or a free port when 0."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    return socket.create_server((host, port), family=family, backlog=_LISTEN_BACKLOG)


def _drain(listener: socket.socket) -> list[bytes]:
    """What each connection waiting on `listener` sent, without waiting for another."""
    listener.setblocking(False)
    received: list[bytes] = []
    while True:
        try:
            connection, _ = listener.accept()
        except BlockingIOError:
            return received
        with connection:
            connection.settimeout(_RECV_SECONDS)
            received.append(_read_all(connection))


def _read_all(connection: socket.socket) -> bytes:
    """Every byte `connection` holds until its sender's close; what came, should it fail."""
    chunks: list[bytes] = []
    try:
        while chunk := connection.recv(_RECV_BYTES):
            chunks.append(chunk)
    except OSError as error:
        # Kept in what was received, so the verdict shows the failure beside what came.
        chunks.append(f"<{type(error).__name__}>".encode())
    return b"".join(chunks)


def _load_report(path: Path) -> canary.CanaryReport | None:
    """The report a canary wrote at `path`; None when it wrote none."""
    if not path.is_file():
        return None
    return cast(canary.CanaryReport, json.loads(path.read_text(encoding="utf-8")))
