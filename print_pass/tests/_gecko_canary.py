"""The fence's canaries: run INSIDE a Seatbelt profile, they write each probe's outcome as JSON.

`_gecko_proof_fixtures.prove_fence` runs this file under `sandbox-exec -f <profile>`, as a
script with `-I`, so it imports the standard library alone. It probes in the fixed order of
`REQUIRED` and stops at the first probe whose outcome is not the one the fence must give, so
a broken fence is asked as little as it can be. The TCP and UDP targets off loopback are
documentation addresses (RFC 5737 and RFC 3849), which route nowhere. The lookup's name is an
RFC 6761 `.invalid` one, but a lookup the fence let through would still reach the system's
resolver, which may ask the network about it. So both ways to the resolver are proven shut
first. Its socket is looked at, then connected to, sending nothing: a socket that is not
there fails, and a fence that lets it connect stops the canary. Then libSystem's
`sandbox_check` is asked whether this very process may look up the resolver's mach service:
any answer but denied, or a check that cannot be made, stops the canary too. Only then is the
lookup made. Given a child's report path, and every probe answered as required, it runs
itself again as a child, so the same probes show the profile holding in a process the fenced
one starts, as Firefox is started by geckodriver; then it waits, alive under the profile, for
the proof to ask `sandbox_check` about it from outside.

`sandbox_check` is not in the macOS SDK's headers: libSystem exports it, with its
`SANDBOX_CHECK_NO_REPORT` flag, and its parameters are a pid, an operation's name, a filter
type and, for a filter, its value. The filter types' numbers are libsandbox's own. The flag
keeps a denied answer out of the kernel's reports, where it would read as a real denial. The
binding is here, and the proof's checker imports it, because this file can import nothing
but the standard library.

It takes no command line: the proof and the parent canary are its only callers, so what it
is told arrives as one JSON object on stdin, written by `encode_config` or `child_config` and
read by `parse_config`, which refuses anything else before a probe runs.

`REQUIRED` is the one list of the probes and the outcome each must give: the judge reads it
too. Never run this file outside the fence: it would make the very connections it probes.

Entry point assumptions: run as `python -I <this file>`, with a `CanaryConfig` as JSON on
stdin; imported, it runs nothing.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, TypedDict

#: What a probe's outcome reads when its action completed.
OK: Final = "ok"

#: What a lookup that failed reads: the resolver's own error, whatever its code.
LOOKUP_FAILED: Final = "gaierror"

#: What a socket call or a file read the fence denies fails with.
DENIED: Final = errno.errorcode[errno.EPERM]

#: What each TCP probe sends once connected: what each loopback listener must receive.
PAYLOAD: Final = b"canary\n"

#: The system resolver's socket, by its resolved path, which Seatbelt matches: the fence
#: denies connecting to it, and the canary probes that before it looks any name up.
RESOLVER_SOCKET: Final = "/private/var/run/mDNSResponder"

#: The resolver's mach service: the fence denies looking it up, and the canary asks
#: `sandbox_check` that it does before it looks any name up.
DNSSD_SERVICE: Final = "com.apple.dnssd.service"

#: macOS's C library, which exports `sandbox_check` and its no-report flag.
LIBSYSTEM: Final = "/usr/lib/libSystem.B.dylib"

#: The exported flag that keeps a check out of the kernel's Sandbox reports.
_NO_REPORT_SYMBOL: Final = "SANDBOX_CHECK_NO_REPORT"

#: libsandbox's filter types: none, a path, a global name.
_FILTER_NONE: Final = 0
_FILTER_PATH: Final = 1
_FILTER_GLOBAL_NAME: Final = 2

#: The operations asked about, as the profile language names them.
_MACH_LOOKUP: Final = b"mach-lookup"
_FILE_READ_DATA: Final = b"file-read-data"

#: What the service probe reads when `sandbox_check` answers that the profile denies the
#: lookup: the answer itself, as every answer it gives is read.
CHECK_DENIED: Final = "1"

#: The loopback addresses a listener of the proof's waits on, each at the one port the
#: config names.
LOOPBACK_V4: Final = "127.0.0.1"
LOOPBACK_V6: Final = "::1"

#: The off-loopback targets: documentation addresses (RFC 5737 and RFC 3849) and a name
#: no resolver may answer (RFC 6761), at the ports HTTP and DNS use.
_OFFSITE_V4: Final = "192.0.2.1"
_OFFSITE_V6: Final = "2001:db8::1"
_INVALID_NAME: Final = "canary.print-pass.invalid"
_HTTP_PORT: Final = 80
_DNS_PORT: Final = 53

#: Bound on each socket call: the fence denies at once, so only a broken fence waits.
_SOCKET_TIMEOUT_SECONDS: Final = 3.0

#: Bound on the child's whole run: an interpreter's start and the same probes, measured
#: at a quarter second for both processes together. With the wait to be checked after
#: it, it leaves the whole proof's bound room for this process's own start and probes.
CHILD_TIMEOUT_SECONDS: Final = 6.0

#: Bound on the wait to be checked. The proof asks `sandbox_check` as soon as the report
#: is written, which is before the child runs, so the answer is usually there before the
#: wait begins.
CHECK_HOLD_SECONDS: Final = 2.0
_HOLD_POLL_SECONDS: Final = 0.01

#: What the report's staged copy, and the proof's mark that it has checked, add to the
#: report's name.
_PARTIAL_SUFFIX: Final = ".partial"
_CHECKED_SUFFIX: Final = ".checked"

#: The ports a TCP listener can hold.
_PORTS: Final = range(1, 65536)

#: The probes, by name.
TCP_V4: Final = f"tcp {_OFFSITE_V4}:{_HTTP_PORT}"
TCP_V6: Final = f"tcp [{_OFFSITE_V6}]:{_HTTP_PORT}"
UDP_V4: Final = f"udp sendto {_OFFSITE_V4}:{_DNS_PORT}"
RESOLVER: Final = f"unix connect {RESOLVER_SOCKET}"
RESOLVER_SERVICE: Final = f"sandbox_check mach-lookup {DNSSD_SERVICE}"
LOOKUP: Final = f"getaddrinfo {_INVALID_NAME}"
LOOPBACK_TCP: Final = f"tcp {LOOPBACK_V4} listener"
LOOPBACK_V6_TCP: Final = f"tcp [{LOOPBACK_V6}] listener"
CANARY_READ: Final = "read under the canary folder"

#: Each probe, in the order run, and the one outcome the fence must give it. The
#: resolver's socket and its mach service come just before the lookup: only a fence
#: that refuses both lets a lookup be made.
REQUIRED: Final = (
    (TCP_V4, DENIED),
    (TCP_V6, DENIED),
    (UDP_V4, DENIED),
    (RESOLVER, DENIED),
    (RESOLVER_SERVICE, CHECK_DENIED),
    (LOOKUP, LOOKUP_FAILED),
    (LOOPBACK_TCP, OK),
    (LOOPBACK_V6_TCP, OK),
    (CANARY_READ, DENIED),
)


class ProbeResult(TypedDict):
    """One probe, and the outcome it gave."""

    probe: str
    outcome: str


class CanaryReport(TypedDict):
    """What one canary process writes: its pid, its parent's, and each probe it ran."""

    pid: int
    ppid: int
    results: list[ProbeResult]


class CanaryConfig(TypedDict):
    """What one canary is told: the listeners' port, the file to read, where to report.

    `child_report` is where the child it starts reports; None starts no child.
    """

    loopback_port: int
    canary_file: str
    report: str
    child_report: str | None


#: The config's keys: exactly these, no more and no fewer.
_CONFIG_KEYS: Final = CanaryConfig.__required_keys__


class _CheckFunction(Protocol):
    """`sandbox_check` as called: a pid, an operation, a filter type, and the filter's value."""

    def __call__(
        self, pid: int, operation: bytes | None, filter_type: int, /, *value: bytes
    ) -> int:
        """1 when the operation is denied, or for no operation when sandboxed; else 0, or -1."""
        ...


@dataclass(frozen=True)
class SandboxCheck:
    """`sandbox_check`, bound, and the flag its every filtered question carries."""

    call: _CheckFunction
    no_report: int

    def is_sandboxed(self, pid: int) -> int:
        """1 when `pid` runs under a Seatbelt profile, 0 when not, -1 on an error."""
        return self.call(pid, None, _FILTER_NONE)

    def denies_lookup(self, pid: int, service: str) -> int:
        """1 when `pid`'s profile denies looking up the mach `service`, 0 when it allows it."""
        return self.call(
            pid, _MACH_LOOKUP, _FILTER_GLOBAL_NAME | self.no_report, service.encode()
        )

    def denies_read(self, pid: int, path: Path) -> int:
        """1 when `pid`'s profile denies reading `path`'s data, 0 when it allows it."""
        return self.call(
            pid, _FILE_READ_DATA, _FILTER_PATH | self.no_report, str(path).encode()
        )


def load_sandbox_check(library: str = LIBSYSTEM) -> SandboxCheck:
    """`sandbox_check` and its no-report flag, from `library`.

    Raises:
        OSError: `library` cannot be loaded.
        AttributeError: it exports no `sandbox_check`.
        ValueError: it exports no no-report flag.
    """
    loaded = ctypes.CDLL(library, use_errno=True)
    function = loaded.sandbox_check
    function.restype = ctypes.c_int
    # The three fixed parameters alone: ctypes passes a filter's value beyond them as a C
    # variadic argument, which is how arm64 macOS takes it.
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int)
    no_report = ctypes.c_int.in_dll(loaded, _NO_REPORT_SYMBOL).value
    return SandboxCheck(function, no_report)


def encode_config(
    *, loopback_port: int, canary_file: Path, report: Path, child_report: Path | None
) -> str:
    """The JSON a canary reads on stdin, telling it where to probe and where to report."""
    config: CanaryConfig = {
        "loopback_port": loopback_port,
        "canary_file": str(canary_file),
        "report": str(report),
        "child_report": None if child_report is None else str(child_report),
    }
    return json.dumps(config)


def parse_config(text: str) -> CanaryConfig:
    """The `CanaryConfig` in `text`, each key checked before any probe can run.

    Raises:
        ValueError: `text` is not a JSON object holding exactly the config's keys, or a
            key's value is not what it must be; the message names the key.
    """
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"the canary's config is not JSON: {error}") from error
    if not isinstance(loaded, dict):
        raise ValueError(
            f"the canary's config is not a JSON object: a {type(loaded).__name__}"
        )
    missing = sorted(_CONFIG_KEYS - loaded.keys())
    if missing:
        raise ValueError(f"the canary's config lacks {', '.join(map(repr, missing))}")
    unknown = sorted(map(str, loaded.keys() - _CONFIG_KEYS))
    if unknown:
        raise ValueError(
            f"the canary's config holds {', '.join(map(repr, unknown))},"
            " which no canary reads"
        )
    child_report = loaded["child_report"]
    return {
        "loopback_port": _port(loaded["loopback_port"]),
        "canary_file": _absolute("canary_file", loaded["canary_file"]),
        "report": _absolute("report", loaded["report"]),
        "child_report": (
            None if child_report is None else _absolute("child_report", child_report)
        ),
    }


def _port(value: object) -> int:
    """`value` as the listeners' port: an integer, not a boolean, that a port can be."""
    if isinstance(value, bool) or not isinstance(value, int) or value not in _PORTS:
        raise ValueError(
            f"loopback_port is not a port from {_PORTS.start} to {_PORTS.stop - 1}:"
            f" {value!r}"
        )
    return value


def _absolute(key: str, value: object) -> str:
    """`value`, the config's `key`, as a non-empty absolute path."""
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError(f"{key} is not an absolute path: {value!r}")
    return value


def child_config(config: CanaryConfig, report: str) -> CanaryConfig:
    """The child's config: its parent's targets, `report` to write, and no child of its own."""
    return {
        "loopback_port": config["loopback_port"],
        "canary_file": config["canary_file"],
        "report": report,
        "child_report": None,
    }


def outcome_of(action: Callable[[], str | None]) -> str:
    """What `action` gave: `OK`, `LOOKUP_FAILED`, the errno's name, or the error's type.

    An action that names its own outcome -- the service check, `sandbox_check`'s answer --
    gives that instead of `OK`.
    """
    try:
        named = action()
    except socket.gaierror:
        return LOOKUP_FAILED
    except OSError as error:
        if error.errno is None:
            return type(error).__name__
        return errno.errorcode.get(error.errno, str(error.errno))
    return OK if named is None else named


def run_probes(actions: Mapping[str, Callable[[], str | None]]) -> list[ProbeResult]:
    """Run each probe of `REQUIRED` in turn, stopping after the first not answered as required."""
    results: list[ProbeResult] = []
    for probe, required in REQUIRED:
        outcome = outcome_of(actions[probe])
        results.append({"probe": probe, "outcome": outcome})
        if outcome != required:
            break
    return results


def passed(results: Sequence[ProbeResult]) -> bool:
    """Whether `results` holds every probe of `REQUIRED`, in order, each as required."""
    return [(item["probe"], item["outcome"]) for item in results] == list(REQUIRED)


def write_report(path: Path, report: CanaryReport) -> None:
    """Write `report` to `path` whole or not at all: the proof may read it at any moment."""
    staged = path.with_name(path.name + _PARTIAL_SUFFIX)
    staged.write_text(json.dumps(report), encoding="utf-8", newline="\n")
    os.replace(staged, path)


def checked_marker(report: Path) -> Path:
    """The file the proof writes beside a canary's `report` once it has checked that canary."""
    return report.with_name(report.name + _CHECKED_SUFFIX)


def await_check(marker: Path, bound_seconds: float = CHECK_HOLD_SECONDS) -> bool:
    """Wait, within `bound_seconds`, for the proof's `marker`; whether it came."""
    deadline = time.monotonic() + bound_seconds
    while not marker.exists():
        if time.monotonic() >= deadline:
            return False
        time.sleep(_HOLD_POLL_SECONDS)
    return True


def _tcp(family: socket.AddressFamily, host: str, port: int) -> None:
    """Connect to `host:port` and send `PAYLOAD`."""
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(_SOCKET_TIMEOUT_SECONDS)
        sock.connect((host, port))
        sock.sendall(PAYLOAD)


def _udp(host: str, port: int) -> None:
    """Send one byte to `host:port`."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(_SOCKET_TIMEOUT_SECONDS)
        sock.sendto(b"\x00", (host, port))


def _unix_connect(path: str) -> None:
    """Connect a stream socket to the unix socket at `path`, and send nothing.

    The path is looked at first, so a socket the system has moved or replaced fails here,
    whatever a fence would answer a connect to that path: only a socket that is there can
    be refused, and only a refusal passes the probe.

    Raises:
        OSError: ENOENT when nothing is at `path`, ENOTSOCK when what is there is no
            socket; otherwise whatever the connect gives.
    """
    if not stat.S_ISSOCK(os.stat(path).st_mode):
        raise OSError(errno.ENOTSOCK, f"not a socket: {path}")
    if sys.platform == "win32":
        # The canary runs under `sandbox-exec` alone, which is macOS's; the narrowing is
        # what lets the POSIX-only address family below type-check for Windows too.
        raise OSError(errno.EAFNOSUPPORT, "no unix sockets")  # pragma: only-on win32
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(_SOCKET_TIMEOUT_SECONDS)
        sock.connect(path)


def _check_own_lookup(service: str) -> str:
    """`sandbox_check`'s answer, as text, on whether this process may look up `service`.

    Asked by the canary about itself, under the profile, with the no-report flag. A library
    that exports no `sandbox_check` or no flag gives the error's type as the answer, so the
    probe fails rather than the canary.

    Raises:
        OSError: libSystem cannot be loaded; the probe's outcome names it.
    """
    try:
        check = load_sandbox_check()
    except (AttributeError, ValueError) as error:
        return type(error).__name__
    return str(check.denies_lookup(os.getpid(), service))


def _lookup(name: str) -> None:
    """Ask the system's resolver for `name`."""
    socket.getaddrinfo(name, _HTTP_PORT, proto=socket.IPPROTO_TCP)


def _read(path: Path) -> None:
    """Read `path`."""
    path.read_bytes()


def _actions(
    loopback_port: int, canary_file: Path
) -> dict[str, Callable[[], str | None]]:
    """Each probe's action: the network ones aimed at their targets, the read at `canary_file`."""
    return {
        TCP_V4: lambda: _tcp(socket.AF_INET, _OFFSITE_V4, _HTTP_PORT),
        TCP_V6: lambda: _tcp(socket.AF_INET6, _OFFSITE_V6, _HTTP_PORT),
        UDP_V4: lambda: _udp(_OFFSITE_V4, _DNS_PORT),
        RESOLVER: lambda: _unix_connect(RESOLVER_SOCKET),
        RESOLVER_SERVICE: lambda: _check_own_lookup(DNSSD_SERVICE),
        LOOKUP: lambda: _lookup(_INVALID_NAME),
        LOOPBACK_TCP: lambda: _tcp(socket.AF_INET, LOOPBACK_V4, loopback_port),
        LOOPBACK_V6_TCP: lambda: _tcp(socket.AF_INET6, LOOPBACK_V6, loopback_port),
        CANARY_READ: lambda: _read(canary_file),
    }


def main(config_text: str) -> int:
    """Read the config, probe, write the report; then, as the proof's canary, run the child.

    The canary the proof started -- the one told of a child -- then waits to be checked:
    `sandbox_check` can answer only for a process still running under the profile.

    Raises:
        ValueError: the config is not a `CanaryConfig`; nothing was probed.
    """
    config = parse_config(config_text)
    results = run_probes(_actions(config["loopback_port"], Path(config["canary_file"])))
    report = Path(config["report"])
    write_report(report, {"pid": os.getpid(), "ppid": os.getppid(), "results": results})
    child_report = config["child_report"]
    if child_report is None:
        return 0
    if passed(results):
        # Its output not captured: the child writes its report to a file, and its streams
        # are this process's own, which the fixture's bounded runner reads.
        subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve())],
            input=json.dumps(child_config(config, child_report)),
            text=True,
            timeout=CHILD_TIMEOUT_SECONDS,
            check=False,
        )
    # Whether the check came is the proof's to judge: it records an answer it never got.
    await_check(checked_marker(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read()))
