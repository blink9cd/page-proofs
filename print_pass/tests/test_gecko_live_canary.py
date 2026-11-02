"""Grouped suite: the fence's canary by itself (its probes, its config, its report, its hold).

The canary runs inside the live Firefox suite's Seatbelt fence and probes, in a fixed order,
what the fence must refuse and what it must let through. Here every probe's action is a
stand-in, so nothing is reached: the order the probes run and stop in; the resolver's socket
looked at, then probed, before any lookup, and its mach service asked of a stand-in
`sandbox_check` about the canary itself; the config the canary reads on stdin and what it
refuses; the report it writes whole; and its wait for the proof's `sandbox_check`. The one
socket connected to here is a listener of the test's own, in a temporary folder. The probes
run for real only under a fence, in `test_gecko_live_proof.py`.

Entry point assumptions: discovered by the folder's aggregator, or run alone from this
folder, which puts the canary beside it on the path.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import socket
import sys
import tempfile
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Final
from unittest import mock

# Beside this module via the loader's top-level folder. The canary imports the standard
# library alone, so it needs no bootstrap of the tool's paths.
import _gecko_canary as canary

#: A stand-in folder and listener port for a canary's config: nothing is written or reached.
_SRV: Final = Path("/srv/fence")
_PORT: Final = 49152

#: A config every check accepts, as the proof writes it.
_GOOD: Final = {
    "loopback_port": _PORT,
    "canary_file": "/srv/fence/canary.txt",
    "report": "/srv/fence/canary.json",
    "child_report": "/srv/fence/child.json",
}


def _config_text(**changes: object) -> str:
    """`_GOOD` as JSON, each of `changes` put in place of its key's value."""
    return json.dumps({**_GOOD, **changes})


#: A stand-in for libSystem's flag that keeps a check out of the kernel's reports.
_NO_REPORT: Final = 0x40


def _denying(probe: str, ran: list[str], outcome: str) -> Callable[[], str | None]:
    """A stand-in action for `probe`: it notes that it ran, then gives `outcome`.

    An outcome no error gives is named by the action itself, as the service check names
    `sandbox_check`'s answer.
    """

    def action() -> str | None:
        ran.append(probe)
        if outcome == canary.DENIED:
            raise PermissionError(errno.EPERM, "denied")
        if outcome == canary.LOOKUP_FAILED:
            raise socket.gaierror(8, "no name")
        return None if outcome == canary.OK else outcome

    return action


class _CheckRecorder:
    """A stand-in `sandbox_check`: records each call, and gives one answer to every one."""

    def __init__(self, answer: int) -> None:
        """Keep the answer; no call yet."""
        self.answer = answer
        self.calls: list[tuple[object, ...]] = []

    def __call__(
        self, pid: int, operation: bytes | None, kind: int, *value: bytes
    ) -> int:
        """Record the call, and answer."""
        self.calls.append((pid, operation, kind, *value))
        return self.answer


class CanaryProbesTest(unittest.TestCase):
    """The probes run in order and stop at the first wrong outcome; every action is a stand-in."""

    def test_an_outcome_names_the_error_a_probe_raised(self) -> None:
        def raises(error: BaseException) -> Callable[[], str | None]:
            def action() -> str | None:
                raise error

            return action

        cases: dict[str, Callable[[], str | None]] = {
            "EPERM": raises(PermissionError(errno.EPERM, "denied")),
            canary.LOOKUP_FAILED: raises(socket.gaierror(8, "no name")),
            "TimeoutError": raises(TimeoutError()),
            "ECONNREFUSED": raises(ConnectionRefusedError(errno.ECONNREFUSED, "no")),
            "OSError": raises(OSError("dlopen: no such library")),
            canary.OK: lambda: None,
            # An action that names its own outcome, as the service check names its answer.
            "0": lambda: "0",
        }
        for expected, action in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(canary.outcome_of(action), expected)

    def test_the_probes_stop_at_the_first_outcome_the_fence_must_not_give(self) -> None:
        ran: list[str] = []
        # The second probe, an off-site TCP connect, is let through: nothing after it runs.
        actions = {
            probe: _denying(probe, ran, canary.DENIED) for probe, _ in canary.REQUIRED
        }
        actions[canary.TCP_V6] = _denying(canary.TCP_V6, ran, canary.OK)
        results = canary.run_probes(actions)
        self.assertEqual(ran, [canary.TCP_V4, canary.TCP_V6])
        self.assertEqual(
            results,
            [
                {"probe": canary.TCP_V4, "outcome": canary.DENIED},
                {"probe": canary.TCP_V6, "outcome": canary.OK},
            ],
        )
        self.assertFalse(canary.passed(results))

    def test_probes_that_all_answer_as_required_pass(self) -> None:
        ran: list[str] = []
        results = canary.run_probes(
            {probe: _denying(probe, ran, outcome) for probe, outcome in canary.REQUIRED}
        )
        self.assertTrue(canary.passed(results))
        self.assertEqual(ran, [probe for probe, _ in canary.REQUIRED])

    def test_the_resolvers_socket_is_probed_before_the_lookup_and_denied(
        self,
    ) -> None:
        probes = [probe for probe, _ in canary.REQUIRED]
        self.assertLess(probes.index(canary.RESOLVER), probes.index(canary.LOOKUP))
        self.assertEqual(dict(canary.REQUIRED)[canary.RESOLVER], canary.DENIED)
        self.assertIn(canary.RESOLVER_SOCKET, canary.RESOLVER)

    def test_a_resolver_socket_that_connects_stops_the_canary_before_its_lookup(
        self,
    ) -> None:
        """Unfenced, the socket connects: the canary stops there, the lookup never made."""
        ran: list[str] = []
        actions = {
            probe: _denying(probe, ran, outcome) for probe, outcome in canary.REQUIRED
        }
        actions[canary.RESOLVER] = _denying(canary.RESOLVER, ran, canary.OK)
        results = canary.run_probes(actions)
        self.assertNotIn(canary.LOOKUP, ran)
        self.assertEqual(ran[-1], canary.RESOLVER)
        self.assertEqual(results[-1], {"probe": canary.RESOLVER, "outcome": canary.OK})

    def test_a_listener_on_each_loopback_address_must_be_reached(self) -> None:
        required = dict(canary.REQUIRED)
        self.assertEqual(required[canary.LOOPBACK_TCP], canary.OK)
        self.assertEqual(required[canary.LOOPBACK_V6_TCP], canary.OK)
        self.assertIn(canary.LOOPBACK_V4, canary.LOOPBACK_TCP)
        self.assertIn(f"[{canary.LOOPBACK_V6}]", canary.LOOPBACK_V6_TCP)


class ResolverSocketTest(unittest.TestCase):
    """The resolver's socket is looked at before it is connected to, so a moved one fails.

    A fence's answer to a connect at a path holding no socket is not what the probe rests
    on: nothing there gives ENOENT, and something other than a socket ENOTSOCK, before any
    socket is made. Here every socket the probe would make is refused, but for the one
    case that connects, to a listener of the test's own.
    """

    def setUp(self) -> None:
        """A folder for the paths the probe is pointed at."""
        folder = tempfile.TemporaryDirectory(prefix="pp-sock-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def outcome_making_no_socket(self, path: Path) -> str:
        """The probe's outcome at `path`, every socket it would make refused as a failure."""
        made = AssertionError("the probe made a socket")
        with mock.patch.object(socket, "socket", side_effect=made):
            return canary.outcome_of(lambda: canary._unix_connect(str(path)))

    def test_a_path_holding_nothing_gives_enoent_before_any_socket_is_made(
        self,
    ) -> None:
        self.assertEqual(self.outcome_making_no_socket(self.root / "moved"), "ENOENT")

    def test_a_path_holding_no_socket_gives_enotsock_before_any_socket_is_made(
        self,
    ) -> None:
        file = self.root / "not-a-socket"
        file.write_text("canary\n", encoding="utf-8", newline="\n")
        self.assertEqual(self.outcome_making_no_socket(file), "ENOTSOCK")

    def test_a_socket_that_is_there_is_connected_to(self) -> None:
        if sys.platform == "win32":
            self.skipTest("no unix sockets here")
        path = self.root / "s"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            listener.listen(1)
            outcome = canary.outcome_of(lambda: canary._unix_connect(str(path)))
        self.assertEqual(outcome, canary.OK)


class ResolverServiceTest(unittest.TestCase):
    """The resolver's mach service: the canary asks `sandbox_check` about itself, before any lookup.

    The check is a stand-in here, so the order, the question and what each answer does are
    shown on any host; the real library answers under a fence, in `test_gecko_live_proof.py`.
    """

    @contextlib.contextmanager
    def loaded(self, recorder: _CheckRecorder) -> Iterator[None]:
        """Within: libSystem's `sandbox_check` stood in for by `recorder`."""
        check = canary.SandboxCheck(recorder, _NO_REPORT)
        with mock.patch.object(canary, "load_sandbox_check", return_value=check):
            yield

    def assert_stops_at_the_service(self, outcome: str) -> None:
        """The probes, the service's check the canary's own: they stop there, as `outcome`."""
        ran: list[str] = []
        actions = {
            probe: _denying(probe, ran, required) for probe, required in canary.REQUIRED
        }
        actions[canary.RESOLVER_SERVICE] = lambda: canary._check_own_lookup(
            canary.DNSSD_SERVICE
        )
        results = canary.run_probes(actions)
        self.assertNotIn(canary.LOOKUP, ran)
        self.assertEqual(
            results[-1], {"probe": canary.RESOLVER_SERVICE, "outcome": outcome}
        )

    def test_the_service_is_asked_after_the_socket_and_just_before_the_lookup(
        self,
    ) -> None:
        probes = [probe for probe, _ in canary.REQUIRED]
        at = probes.index(canary.RESOLVER_SERVICE)
        self.assertEqual(
            probes[at - 1 : at + 2],
            [canary.RESOLVER, canary.RESOLVER_SERVICE, canary.LOOKUP],
        )
        self.assertEqual(
            dict(canary.REQUIRED)[canary.RESOLVER_SERVICE], canary.CHECK_DENIED
        )
        self.assertIn(canary.DNSSD_SERVICE, canary.RESOLVER_SERVICE)

    def test_the_canary_asks_about_its_own_lookup_of_the_service_unreported(
        self,
    ) -> None:
        recorder = _CheckRecorder(1)
        with self.loaded(recorder):
            outcome = canary.outcome_of(
                lambda: canary._check_own_lookup(canary.DNSSD_SERVICE)
            )
        self.assertEqual(outcome, canary.CHECK_DENIED)
        self.assertEqual(
            recorder.calls,
            [(os.getpid(), b"mach-lookup", 2 | _NO_REPORT, b"com.apple.dnssd.service")],
        )

    def test_any_answer_but_denied_stops_the_canary_before_its_lookup(self) -> None:
        for answer in (0, -1):
            with self.subTest(answer=answer), self.loaded(_CheckRecorder(answer)):
                self.assert_stops_at_the_service(str(answer))

    def test_a_check_that_cannot_be_made_stops_the_canary_before_its_lookup(
        self,
    ) -> None:
        """No library, no function in it, or no flag: each is an outcome, never a pass."""
        for error in (
            OSError("dlopen: no such library"),
            AttributeError("no sandbox_check"),
            ValueError("no SANDBOX_CHECK_NO_REPORT"),
        ):
            with (
                self.subTest(error=type(error).__name__),
                mock.patch.object(canary, "load_sandbox_check", side_effect=error),
            ):
                self.assert_stops_at_the_service(type(error).__name__)


class CanaryConfigTest(unittest.TestCase):
    """What a canary reads on stdin: one JSON object, written by the proof or by its parent."""

    def test_the_config_names_the_listener_the_canary_file_and_both_reports(
        self,
    ) -> None:
        text = canary.encode_config(
            loopback_port=_PORT,
            canary_file=_SRV / "canary.txt",
            report=_SRV / "canary.json",
            child_report=_SRV / "child.json",
        )
        self.assertEqual(json.loads(text), _GOOD)

    def test_a_childs_config_keeps_the_targets_and_starts_no_child(self) -> None:
        parent = canary.parse_config(_config_text())
        self.assertEqual(
            canary.child_config(parent, "/srv/fence/child.json"),
            {**_GOOD, "report": "/srv/fence/child.json", "child_report": None},
        )

    def test_a_config_the_proof_wrote_reads_back_whole(self) -> None:
        self.assertEqual(canary.parse_config(_config_text()), _GOOD)

    def test_a_config_with_no_child_report_reads_back_with_none(self) -> None:
        config = canary.parse_config(_config_text(child_report=None))
        self.assertIsNone(config["child_report"])

    def test_malformed_json_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "not JSON"):
            canary.parse_config('{"loopback_port": ')

    def test_empty_input_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "not JSON"):
            canary.parse_config("")

    def test_json_that_is_not_an_object_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a JSON object"):
            canary.parse_config(json.dumps(list(_GOOD)))

    def test_a_missing_key_is_refused_naming_it(self) -> None:
        partial = {key: value for key, value in _GOOD.items() if key != "report"}
        with self.assertRaisesRegex(ValueError, "'report'"):
            canary.parse_config(json.dumps(partial))

    def test_an_extra_key_is_refused_naming_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "'spare'"):
            canary.parse_config(_config_text(spare=1))

    def test_a_wrongly_typed_value_is_refused_naming_its_key(self) -> None:
        cases = (
            ("loopback_port", True),
            ("loopback_port", "49152"),
            ("loopback_port", 49152.0),
            ("loopback_port", 0),
            ("loopback_port", 65536),
            ("canary_file", "relative/canary.txt"),
            ("canary_file", ""),
            ("canary_file", 7),
            ("report", None),
            ("report", "canary.json"),
            ("child_report", 7),
            ("child_report", "child.json"),
            ("child_report", ""),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(ValueError, key):
                    canary.parse_config(_config_text(**{key: value}))

    def test_a_port_at_either_end_of_its_range_is_kept(self) -> None:
        for port in (1, 65535):
            with self.subTest(port=port):
                config = canary.parse_config(_config_text(loopback_port=port))
                self.assertEqual(config["loopback_port"], port)

    def test_main_refuses_a_config_before_it_probes_anything(self) -> None:
        """Read first: a malformed config never reaches a probe, which unfenced would connect."""
        probed = AssertionError("a probe ran")
        with (
            mock.patch.object(canary, "run_probes", side_effect=probed),
            mock.patch.object(canary, "_actions", side_effect=probed),
        ):
            with self.assertRaisesRegex(ValueError, "loopback_port"):
                canary.main(_config_text(loopback_port=True))


class CanaryReportTest(unittest.TestCase):
    """The report is written whole, and the canary the proof started waits to be checked."""

    def setUp(self) -> None:
        """A folder for the report and its marker."""
        folder = tempfile.TemporaryDirectory(prefix="print_pass-canary-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_the_report_is_written_whole_with_no_staged_copy_left(self) -> None:
        report: canary.CanaryReport = {
            "pid": 10,
            "ppid": 1,
            "results": [{"probe": canary.TCP_V4, "outcome": canary.DENIED}],
        }
        path = self.root / "canary.json"
        canary.write_report(path, report)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)
        self.assertEqual(sorted(self.root.iterdir()), [path])

    def test_the_checked_marker_sits_beside_the_report(self) -> None:
        report = self.root / "canary.json"
        marker = canary.checked_marker(report)
        self.assertEqual(marker.parent, self.root)
        self.assertNotEqual(marker, report)

    def test_the_hold_ends_once_the_marker_is_there(self) -> None:
        marker = canary.checked_marker(self.root / "canary.json")
        marker.touch()
        self.assertTrue(canary.await_check(marker, bound_seconds=0.0))

    def test_the_hold_gives_up_at_its_bound(self) -> None:
        marker = canary.checked_marker(self.root / "canary.json")
        self.assertFalse(canary.await_check(marker, bound_seconds=0.0))


if __name__ == "__main__":
    unittest.main()
