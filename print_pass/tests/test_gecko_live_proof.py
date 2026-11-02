"""Grouped suite: the fence's proof (the canaries' verdict, and the real proof under the profile).

Before the live Firefox suite launches anything, it runs the canaries of `_gecko_canary` under
its Seatbelt profile, in a process and in a child it starts, and asks libSystem's
`sandbox_check` about the canary from outside. Here the verdict is checked on stand-in
reports, and the real proof is run under `sandbox-exec`: on the real profile, which must
hold, and on profiles each missing one rule, which must fail where that rule is missing:
the canary folder's denial, the resolver socket's, and the resolver's mach service's. Every
profile here keeps the rules refusing TCP and UDP off loopback, so no probe reaches past it.
No name is ever looked up under a profile whose resolver rules are weakened: such a profile
is proven only once the canary is shown to stop before its lookup, at the socket, which it
connects to sending nothing, or at the service, which it asks `sandbox_check` about.

Entry point assumptions: discovered by the folder's aggregator, or run alone from this
folder; the fixtures' bootstrap puts the tool's folder and its private `src/` on the path.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Final

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
from _gecko_live_fixtures import SANDBOX_EXEC, Fence, make_fence, seatbelt_profile

import _gecko_canary as canary  # beside this module
import _gecko_sandbox_check as check  # as above
from _gecko_proof_fixtures import (  # as above
    FENCE_PROOF_SECONDS,
    Proof,
    canary_failures,
    prove_fence,
)

#: Whether this host can run a program under a Seatbelt profile: macOS, with its runner.
_HAS_SANDBOX_EXEC: Final = sys.platform == "darwin" and Path(SANDBOX_EXEC).is_file()

#: What each loopback listener receives from a canary and its child that both reach it.
_BOTH: Final = [canary.PAYLOAD] * 2

#: The verdict's line for a listener that received `got` rather than one payload a process.
_PAYLOAD_LINE: Final = "the {host} listener received {got!r}, not {payload!r} twice"

#: A mach service no process offers: what the resolver's service rule is renamed to.
_NO_SUCH_SERVICE: Final = "invalid.print-pass.no-such-service"


def _passing_report(pid: int, ppid: int) -> canary.CanaryReport:
    """A canary's report with every probe as the fence must answer it."""
    return {
        "pid": pid,
        "ppid": ppid,
        "results": [
            {"probe": probe, "outcome": outcome} for probe, outcome in canary.REQUIRED
        ],
    }


def _short(host: str, got: list[bytes]) -> str:
    """The verdict's line for the listener on `host` having received `got`."""
    return _PAYLOAD_LINE.format(host=host, got=got, payload=canary.PAYLOAD)


def _received() -> dict[str, list[bytes]]:
    """What both listeners receive from a canary and its child that both reach them."""
    return {canary.LOOPBACK_V4: _BOTH, canary.LOOPBACK_V6: _BOTH}


class ProofBoundTest(unittest.TestCase):
    """The canary's own bounds fit inside the proof's, with room left for its start."""

    def test_the_childs_bound_and_the_wait_to_be_checked_leave_the_proof_room(
        self,
    ) -> None:
        """Both, run in full, still leave the proof's bound time for the parent's start."""
        self.assertLess(
            canary.CHILD_TIMEOUT_SECONDS + canary.CHECK_HOLD_SECONDS,
            FENCE_PROOF_SECONDS,
        )


class CanaryVerdictTest(unittest.TestCase):
    """The canaries pass only when every probe, in both processes, gave what the fence must."""

    def test_every_probe_as_required_in_both_processes_passes(self) -> None:
        parent = _passing_report(pid=10, ppid=1)
        child = _passing_report(pid=11, ppid=10)
        self.assertEqual(canary_failures(parent, child, _received()), ())

    def test_a_probe_that_got_through_fails_naming_it(self) -> None:
        parent = _passing_report(pid=10, ppid=1)
        parent["results"][0]["outcome"] = "TimeoutError"
        child = _passing_report(pid=11, ppid=10)
        self.assertEqual(
            canary_failures(parent, child, _received()),
            (f"the canary: {canary.TCP_V4} gave TimeoutError, not {canary.DENIED}",),
        )

    def test_a_probe_that_never_ran_fails(self) -> None:
        parent = _passing_report(pid=10, ppid=1)
        del parent["results"][1:]
        child = _passing_report(pid=11, ppid=10)
        failures = canary_failures(parent, child, _received())
        self.assertEqual(len(failures), len(canary.REQUIRED) - 1)
        self.assertIn(f"the canary: {canary.CANARY_READ} did not run", failures)

    def test_a_missing_report_fails_for_each_process_and_each_listener(self) -> None:
        self.assertEqual(
            canary_failures(None, None, {}),
            (
                "the canary wrote no report",
                "its child wrote no report",
                _short(canary.LOOPBACK_V4, []),
                _short(canary.LOOPBACK_V6, []),
            ),
        )

    def test_a_child_another_process_started_fails(self) -> None:
        parent = _passing_report(pid=10, ppid=1)
        child = _passing_report(pid=11, ppid=99)
        self.assertEqual(
            canary_failures(parent, child, _received()),
            ("its child names parent 99, not the canary's pid 10",),
        )

    def test_a_listener_short_of_either_canary_fails_naming_its_address(self) -> None:
        parent = _passing_report(pid=10, ppid=1)
        child = _passing_report(pid=11, ppid=10)
        for host in (canary.LOOPBACK_V4, canary.LOOPBACK_V6):
            with self.subTest(host=host):
                received = {**_received(), host: [canary.PAYLOAD]}
                self.assertEqual(
                    canary_failures(parent, child, received),
                    (_short(host, [canary.PAYLOAD]),),
                )


@unittest.skipUnless(
    _HAS_SANDBOX_EXEC, "sandbox-exec is macOS's, and this host lacks it"
)
class FenceProofTest(unittest.TestCase):
    """The real proof, under `sandbox-exec`: it passes the real profile, and fails one missing a rule."""

    def setUp(self) -> None:
        """A fence of the suite's own making, under a temporary folder; nothing launched."""
        folder = tempfile.TemporaryDirectory(prefix="print_pass-proof-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.fence = make_fence(self.root, ())

    def leaky(self, text: str) -> Fence:
        """The fence, its profile replaced by `text`."""
        leaky = self.root / "leaky.sb"
        leaky.write_text(text, encoding="utf-8", newline="\n")
        return dataclasses.replace(self.fence, profile=leaky)

    def prove_without_resolver_rule(
        self, marker: str, replacement: str | None, *, stops_at: str
    ) -> Proof:
        """The proof under the real profile, its one line naming `marker` weakened.

        The line has `marker` put in place by `replacement`, or is dropped when that is
        None. A resolver rule is weakened here, so the proof is run only once `stops_at`,
        where such a profile must stop the canary, is shown to come before the lookup: no
        name is looked up under a weakened resolver rule.
        """
        probes = [probe for probe, _ in canary.REQUIRED]
        self.assertLess(
            probes.index(stops_at),
            probes.index(canary.LOOKUP),
            "a weakened resolver rule is proven only by a probe before the lookup",
        )
        lines = self.fence.profile.read_text(encoding="utf-8").splitlines(keepends=True)
        hits = [index for index, line in enumerate(lines) if marker in line]
        self.assertEqual(len(hits), 1, f"the real profile names {marker} once")
        if replacement is None:
            del lines[hits[0]]
        else:
            lines[hits[0]] = lines[hits[0]].replace(marker, replacement)
        return prove_fence(self.leaky("".join(lines)))

    def assert_stopped_before_the_lookup(
        self, proof: Proof, stopped: tuple[str, str], checked: tuple[str, ...]
    ) -> None:
        """The canary ran every probe as required up to `stopped`, its probe and outcome.

        Its report ends there, so the lookup, after it, was never made; it started no child.
        `checked` is what `sandbox_check`, asked from outside, failed.
        """
        assert proof.parent is not None, proof.failures  # and narrowed, for the checker
        ran = [(item["probe"], item["outcome"]) for item in proof.parent["results"]]
        probes = [probe for probe, _ in canary.REQUIRED]
        at = probes.index(stopped[0])
        self.assertEqual(ran, [*canary.REQUIRED[:at], stopped])
        self.assertNotIn(canary.LOOKUP, [probe for probe, _ in ran])
        self.assertIsNone(proof.child)
        required = dict(canary.REQUIRED)[stopped[0]]
        self.assertEqual(
            proof.failures,
            (
                f"the canary: {stopped[0]} gave {stopped[1]}, not {required}",
                *(f"the canary: {probe} did not run" for probe in probes[at + 1 :]),
                "its child wrote no report",
                _short(canary.LOOPBACK_V4, []),
                _short(canary.LOOPBACK_V6, []),
                *checked,
            ),
        )

    def test_the_real_profile_holds_in_the_canaries_and_in_sandbox_check(self) -> None:
        proof = prove_fence(self.fence)
        self.assertEqual(proof.failures, ())
        self.assertEqual(proof.checks, dict(check.REQUIRED_ANSWERS))

    def test_a_fence_that_lets_the_canary_file_be_read_fails_the_proof(self) -> None:
        """Only the canary folder's denial is left out: the read, last, gets through."""
        proof = prove_fence(self.leaky(seatbelt_profile((self.root / "elsewhere",))))
        self.assertEqual(
            proof.failures,
            (
                f"the canary: {canary.CANARY_READ} gave {canary.OK}, not {canary.DENIED}",
                "its child wrote no report",
                _short(canary.LOOPBACK_V4, [canary.PAYLOAD]),
                _short(canary.LOOPBACK_V6, [canary.PAYLOAD]),
                f"sandbox_check: {check.READ_DENIED} answered 0, not 1",
            ),
        )

    def test_a_fence_that_lets_the_resolvers_socket_connect_fails_before_any_lookup(
        self,
    ) -> None:
        """The real profile, its resolver-socket rule removed: the canary stops at that probe.

        Its report ends at the socket's probe, connected, so the lookup was never made.
        The service's rule still holds, so `sandbox_check` from outside fails nothing.
        """
        proof = self.prove_without_resolver_rule(
            f'(path-literal "{canary.RESOLVER_SOCKET}")', None, stops_at=canary.RESOLVER
        )
        self.assert_stopped_before_the_lookup(proof, (canary.RESOLVER, canary.OK), ())

    def test_a_fence_whose_resolver_service_rule_matches_nothing_fails_before_any_lookup(
        self,
    ) -> None:
        """The real profile, its dnssd rule renamed to match nothing: the canary stops at it.

        The canary asks `sandbox_check` about its own lookup of the service, which answers
        0, so its report ends there and the lookup was never made; asked from outside, the
        same question fails too.
        """
        proof = self.prove_without_resolver_rule(
            f'(global-name "{canary.DNSSD_SERVICE}")',
            f'(global-name "{_NO_SUCH_SERVICE}")',
            stops_at=canary.RESOLVER_SERVICE,
        )
        self.assert_stopped_before_the_lookup(
            proof,
            (canary.RESOLVER_SERVICE, "0"),
            (f"sandbox_check: {check.DNSSD_DENIED} answered 0, not 1",),
        )


if __name__ == "__main__":
    unittest.main()
