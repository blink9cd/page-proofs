"""Grouped suite: the LIVE `--loopback-only` baseline, with no extension, in Firefox and WebKit.

The guarded page (`_guard_fixtures.py`) is measured once in each engine, alone: each must
refuse its image off loopback, at its guard's proxy and in its own words, while loopback still
loads. Firefox refuses every request to another loopback port than the served one, naming it,
while WebKit's bypass, which names no port, lets them all through, as `browser_pass.md` says.
Firefox has no WebRTC at all, and WebKit's WebRTC still sends its STUN request over UDP,
as the guard's table in `browser_pass.md` says. WebKit's wording and whether its gathering
completes differ by platform, so both are chosen per `sys.platform` (`_guard_fixtures.py`). Chromium's baseline is measured beside its
extension run, in `test_pass_extension_live.py`, whose setup requires its full build.
`_live_fixtures.py` holds the setup the live suites share.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ and the
# shared loopback server on sys.path for every import after it.
from _live_fixtures import LiveSite

import sys
import unittest

from _guard_fixtures import (
    ABSENT,
    Measured,
    check_webkit_udp,
    measure,
    off_loopback_failures,
    other_port_findings,
    webkit_refusal,
)
from pass_extension import Isolation
from pass_report import RenderingResult
from pass_targets import Rendering

#: The one `run_matrix` here launches each engine once.
_LAUNCH_BUDGET = {"firefox": 1, "webkit": 1}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)

#: How Firefox names a request its guard's proxy refused.
_FIREFOX_REFUSAL = "NS_ERROR_PROXY_CONNECTION_REFUSED"


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the builds and serve the sample site."""
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)


class LoopbackOnlyBaselineLiveTests(unittest.TestCase):
    """`--loopback-only` with no extension, in the engines other than Chromium."""

    def test_firefox_refuses_off_loopback_and_every_other_loopback_port(self) -> None:
        measured = self.measure_alone("firefox")
        (firefox,) = measured.results
        self.assert_refused(firefox, _FIREFOX_REFUSAL)
        # Another loopback port: its image and its fetch refused at the proxy, each named with
        # its port, and its WebSocket's failure named too; nothing reached it.
        named = other_port_findings(firefox)
        http = named.get("http_error", [])
        self.assertTrue(any("/other-port.png" in f for f in http), named)
        self.assertTrue(any("/other-port-fetch" in f for f in http), named)
        for failure in http:
            self.assertIn(_FIREFOX_REFUSAL, failure)
        sockets = [f for f in named.get("console_error", []) if "ws://127.0.0.1:" in f]
        self.assertTrue(sockets, named)
        self.assertEqual(
            measured.connections, 0, "the guard let another port be reached"
        )
        self.assertIn("webrtc: absent", firefox.findings["console_error"])
        # No WebRTC at all, so no STUN request was ever made to count.
        self.assertEqual(measured.datagrams, 0)

    def test_webkit_refuses_off_loopback_and_leaves_loopback_open(self) -> None:
        measured = self.measure_alone("webkit")
        (webkit,) = measured.results
        self.assert_refused(webkit, webkit_refusal(sys.platform))
        # What browser_pass.md says WebKit's guard leaves open: every loopback port, since its
        # bypass names a host and no port. Held here, so the doc is told when that changes.
        self.assertGreater(
            measured.connections, 0, "WebKit's guard is documented open to other ports"
        )
        # And WebRTC's UDP. Held here where it is measurable, likewise.
        console = webkit.findings["console_error"]
        self.assertIn("webrtc: present", console)
        note = check_webkit_udp(sys.platform, console, measured.datagrams)
        if note is not None:
            # Visible in the run's output: the claim went unmeasured here, it did not pass.
            print(f"\nNOTE: {note}", file=sys.stderr)

    def measure_alone(self, engine: str) -> Measured:
        """The guarded page under `--loopback-only` in `engine` alone, so what reached the
        other servers is that engine's."""
        rendering = Rendering(engine, "/index.html", 390, 844, "light", "on")
        return measure(self, _SITE, Isolation(loopback_only=True), [rendering])

    def assert_refused(self, result: RenderingResult, refusal: str) -> None:
        """`result` loaded from loopback, and its image off loopback failed with `refusal`."""
        with self.subTest(engine=result.rendering.engine):
            self.assertEqual(result.faults, ())
            # Loopback loads: the page's own script ran, and found no extension.
            self.assertIn(ABSENT, result.findings["console_error"])
            failures = off_loopback_failures(result)
            self.assertTrue(failures, result.findings)
            for failure in failures:
                self.assertIn(refusal, failure)


if __name__ == "__main__":
    unittest.main()
