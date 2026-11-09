"""Grouped suite: the live boundary for an extension (loaded, acting, and held to loopback).

The committed fixture, `fixture_extension/`, is a tiny MV3 extension: its content script marks
the page's root and inserts a `<style>`, and its service worker opens a welcome tab off loopback
on install, as real extensions do, and a page of its own once the first page it marks is loading,
one breaking the extension's own policy while the pass measures. The guarded page
(`_guard_fixtures.py`) reads both insertions back from its own script and says what it found as
a console error, which the `console_error` detector reports: so the full Chromium build, launched
by `run_matrix` the way the CLI launches it, is shown to have loaded the extension and the
extension to have acted, in a page browser_pass measured, and the extension's own page's
violation never to be its finding. The same page asks for an image off loopback, which the
egress guard must fail with and without the extension, and another loopback port than the
served one, whose image, fetch and WebSocket it must fail too; starts a WebRTC connection to a STUN
server on a loopback UDP port, which no guarded Chromium may reach once its candidates are
gathered; and declares a policy its own markup breaks, which the CSP collector must report from
inside a persistent context. A state's stored value must reach the page's first script.

Firefox loads only a Mozilla-signed `.xpi`, which a repository fixture cannot be, so its live
extension test runs only when `BROWSER_PASS_SIGNED_XPI` names one, and otherwise skips; its every
branch is driven by fakes in `test_pass_extension_launch.py`. Its `--loopback-only` baseline,
and WebKit's, need no extension and are `test_pass_loopback_live.py`'s. `_live_fixtures.py` holds
the setup the live suites share.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ and the
# shared loopback server on sys.path for every import after it.
from _live_fixtures import (
    FULL_CHROMIUM,
    PLAYWRIGHT_PACKAGE,
    LiveSite,
    absent_builds,
    missing_builds,
)

import os
import unittest
from pathlib import Path

import pass_extension
import pass_facts
import pass_shift
from _extension_fixtures import FIXTURE_EXTENSION
from _guard_fixtures import (
    ABSENT,
    ACTED,
    GATHERED,
    OFF_LOOPBACK,
    measure,
    off_loopback_failures,
    other_port_findings,
)
from pass_extension import Extension, Isolation
from pass_report import RenderingResult
from pass_targets import Rendering

#: Names a Mozilla-signed `.xpi` for the Firefox live test, which skips without it.
SIGNED_XPI_ENV = "BROWSER_PASS_SIGNED_XPI"

#: Each `run_matrix` launches its engine once; a persistent context is not a launch it counts.
#: Chromium's three are the extension case, the loopback baseline and the slow-gathering case.
_LAUNCH_BUDGET = {"chromium": 3, "firefox": 1}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)

#: How Chromium names a request its guard's proxy refused.
_PROXY_REFUSAL = "net::ERR_PROXY_CONNECTION_FAILED"

#: How Chromium names the off-loopback image its guard refused: at the proxy, which every
#: request but the served origin's is sent to, before any name is resolved.
_NOT_RESOLVED = [f"failed {OFF_LOOPBACK} ({_PROXY_REFUSAL})"]

#: How late the slow-gathering case's verdict arrives: past the live suites' settle window
#: (`_live_fixtures.TEST_SETTLE_QUIET_SECONDS`) several times over, within the probe's bound.
_LATE_GATHER_MS = 1500


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the builds, then require the full Chromium build an extension needs."""
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)
    looked_in, missing = missing_builds(
        Path(os.environ[pass_facts.BROWSERS_ENV]), PLAYWRIGHT_PACKAGE, FULL_CHROMIUM
    )
    if missing:  # pragma: no cover - needs the build absent
        # Reached only where the headless builds are installed and the full one is not, which
        # a run measuring this suite's coverage never is; `absent_builds` is tested on its own.
        raise absent_builds(
            f"the full Chromium build is not installed in {looked_in}",
            "python -m playwright install chromium",
        )


class ChromiumExtensionLiveTests(unittest.TestCase):
    """The committed fixture in the full Chromium build."""

    def test_the_extension_loads_acts_and_nothing_leaves_loopback(self) -> None:
        extension = pass_extension.read_unpacked(FIXTURE_EXTENSION)
        assert isinstance(extension, Extension), extension
        loaded = Rendering("chromium", "/index.html", 390, 844, "light", "on")
        stored = Rendering("chromium", "/index.html", 390, 844, "light", "on", "stored")
        measured = measure(
            self,
            _SITE,
            Isolation(loopback_only=True, extensions=(extension,)),
            [loaded, stored],
        )
        (page, state), datagrams = measured.results, measured.datagrams
        for result in (page, state):
            with self.subTest(rendering=result.rendering.label):
                self.assertEqual(result.faults, ())
                console = result.findings.get("console_error", ())
                self.assertIn(ACTED, console)
                self.assertEqual(off_loopback_failures(result), _NOT_RESOLVED)
                self.assert_other_port_refused(result)
                # The page's own inline style breaks its policy: the collector still reports
                # from inside a persistent context.
                self.assertTrue(result.findings.get("csp_violation"), result.findings)
                # Neither welcome tab is measured: not the one off loopback the extension
                # opened on install, nor its own page breaking its own policy as this loaded.
                everything = [f for lines in result.findings.values() for f in lines]
                self.assertEqual([f for f in everything if "welcome" in f], [])
                # Gathering finished, so a STUN request would have been sent by now.
                self.assertIn(GATHERED, console)
        self.assertIn("first script read mode=null", page.findings["console_error"])
        self.assertIn("first script read mode=dark", state.findings["console_error"])
        # The late-script load ran in a persistent context of its own, with the extension too.
        self.assertEqual(page.late_shift.status, pass_shift.MEASURED)
        self.assertEqual(datagrams, 0, "the guard let a STUN request out over UDP")
        self.assertEqual(
            measured.connections, 0, "the guard let another port be reached"
        )

    def test_a_loopback_only_baseline_is_guarded_without_the_extension(self) -> None:
        loaded = Rendering("chromium", "/index.html", 390, 844, "light", "on")
        measured = measure(self, _SITE, Isolation(loopback_only=True), [loaded])
        (result,), datagrams = measured.results, measured.datagrams
        self.assertEqual(result.faults, ())
        self.assert_other_port_refused(result)
        self.assertEqual(
            measured.connections, 0, "the guard let another port be reached"
        )
        console = result.findings["console_error"]
        self.assertIn(ABSENT, console)
        self.assertEqual(off_loopback_failures(result), _NOT_RESOLVED)
        # WebRTC is there, its candidates are gathered, and its STUN request never left.
        self.assertIn("webrtc: present", console)
        self.assertIn(GATHERED, console)
        self.assertEqual(datagrams, 0, "the guard let a STUN request out over UDP")

    def test_a_gathering_slower_than_the_settle_window_is_still_read(self) -> None:
        # Regression: a gathering that outlasted the settle window was read before its verdict,
        # so GATHERED went missing on a loaded machine (two nightly sweeps). The delay stands in
        # for that slow gathering, several times the live suites' quiet window.
        loaded = Rendering("chromium", "/index.html", 390, 844, "light", "on")
        measured = measure(
            self,
            _SITE,
            Isolation(loopback_only=True),
            [loaded],
            gather_delay_ms=_LATE_GATHER_MS,
        )
        (result,) = measured.results
        self.assertEqual(result.faults, ())
        self.assertIn(GATHERED, result.findings["console_error"])

    def assert_other_port_refused(self, result: RenderingResult) -> None:
        """`result`'s requests to the other loopback port failed at the proxy, each named."""
        named = other_port_findings(result)
        http = named.get("http_error", [])
        self.assertTrue(
            any("/other-port.png" in f for f in http)
            and any("/other-port-fetch" in f for f in http),
            named,
        )
        for failure in http:
            self.assertIn(_PROXY_REFUSAL, failure)
        socket_errors = [f for f in named.get("console_error", []) if "ws://" in f]
        self.assertTrue(socket_errors, named)
        for failure in socket_errors:
            self.assertIn(_PROXY_REFUSAL, failure)


@unittest.skipUnless(
    os.environ.get(SIGNED_XPI_ENV),
    f"{SIGNED_XPI_ENV} names no signed .xpi; Firefox loads no unsigned one",
)
class FirefoxExtensionLiveTests(unittest.TestCase):
    """A signed `.xpi`, named by the environment, in the pinned Firefox."""

    def test_the_signed_add_on_loads_and_nothing_leaves_loopback(self) -> None:
        extension = pass_extension.read_xpi(Path(os.environ[SIGNED_XPI_ENV]))
        assert isinstance(extension, Extension), extension
        loaded = Rendering("firefox", "/index.html", 390, 844, "light", "on")
        measured = measure(
            self,
            _SITE,
            Isolation(loopback_only=True, extensions=(extension,)),
            [loaded],
        )
        (result,), datagrams = measured.results, measured.datagrams
        self.assertEqual(result.faults, ())
        # Firefox asks for the refused image twice (measured with a signed uBlock Origin, Firefox
        # 155), and http_error keeps repeats, so the test holds every failure to the proxy's
        # refusal rather than counting them.
        failures = off_loopback_failures(result)
        self.assertTrue(failures, result.findings)
        for failure in failures:
            self.assertIn("NS_ERROR_PROXY_CONNECTION_REFUSED", failure)
        # No WebRTC at all, so no STUN request was ever made to count.
        self.assertIn("webrtc: absent", result.findings["console_error"])
        self.assertEqual(datagrams, 0)


if __name__ == "__main__":
    unittest.main()
