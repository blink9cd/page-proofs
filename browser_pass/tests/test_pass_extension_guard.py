"""Grouped suite: the egress guard -- what each engine is launched with, and what it refuses.

`--loopback-only` launches each engine with its own guard, so a request off the served origin
fails -- off 127.0.0.1, and in Chromium and Firefox to any other port on it, which a page's own
script could otherwise reach (a dev server, a local API). WebKit's proxy bypass names no port, so
WebKit still reaches every loopback port; the docs say so, and the live suite holds it.
`pass_guard.guard_options` says what that is: the launch arguments and prefs below, each one
pinned, because a guard that loses one of them still loads every page and fails no test but
this one. An extension that could route its requests around the guard -- one asking for the
`proxy` permission -- is refused before any browser opens. The live suites,
`test_pass_extension_live.py` for Chromium and `test_pass_loopback_live.py` for Firefox and
WebKit, show each engine's guard failing a real request.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pass_extension  # noqa: E402 - the tool's private src/, via the insert above
import pass_guard  # noqa: E402 - the tool's private src/, via the insert above
from _extension_fixtures import (  # noqa: E402 - beside this suite
    CHROMIUM_MANIFEST,
    FIREFOX_MANIFEST,
    unpacked,
    xpi,
)
from pass_extension import Extension, Isolation  # noqa: E402 - the tool's private src/

_ALL = ("chromium", "firefox", "webkit")
#: The served origin's port the guarded runs here are launched for.
_PORT = 8123
_GUARDED = Isolation(loopback_only=True).serving(f"http://127.0.0.1:{_PORT}")


class GuardOptionsTests(unittest.TestCase):
    """What each engine is launched with under `--loopback-only`, and nothing without it."""

    def test_nothing_is_added_without_the_guard(self) -> None:
        for engine in _ALL:
            with self.subTest(engine=engine):
                self.assertEqual(
                    pass_guard.guard_options(engine, pass_extension.NO_ISOLATION),
                    {},
                )

    def test_chromium_runs_the_full_build_resolves_nothing_and_sends_no_udp(
        self,
    ) -> None:
        self.assertEqual(
            pass_guard.guard_options("chromium", _GUARDED),
            {
                "channel": "chromium",
                "args": [
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                    # WebRTC's own sockets never ask the resolver: no UDP that no proxy
                    # carries, and there is none. Measured to stop a STUN request.
                    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--disable-quic",
                    # Every request to a proxy nothing listens on, but the served origin's:
                    # loopback included, since Chromium's implicit loopback bypass is removed
                    # first, and the bypass list is read last rule first.
                    "--proxy-server=http://127.0.0.1:9",
                    "--proxy-bypass-list=<-loopback>;127.0.0.1:8123",
                ],
            },
        )

    def test_firefox_proxies_everything_and_opens_no_udp_channel(self) -> None:
        self.assertEqual(
            pass_guard.guard_options("firefox", _GUARDED),
            {
                "firefox_user_prefs": {
                    "network.proxy.type": 1,
                    "network.proxy.http": "127.0.0.1",
                    "network.proxy.http_port": 9,
                    "network.proxy.ssl": "127.0.0.1",
                    "network.proxy.ssl_port": 9,
                    # Loopback goes to the proxy too, the served origin's port alone excepted;
                    # still a secure context, so a measured page keeps its secure-only APIs.
                    "network.proxy.no_proxies_on": "127.0.0.1:8123",
                    "network.proxy.allow_hijacking_localhost": True,
                    "network.proxy.testing_localhost_is_secure_when_hijacked": True,
                    "network.proxy.socks_remote_dns": True,
                    "network.proxy.allow_bypass": False,
                    "media.peerconnection.enabled": False,
                    "network.webtransport.enabled": False,
                    "network.http.http3.enable": False,
                    "network.dns.disablePrefetch": True,
                }
            },
        )

    def test_webkit_sends_everything_but_loopback_to_a_closed_port(self) -> None:
        # Not narrowed to the served port: WebKit's bypass list names hosts, not ports
        # (measured: `127.0.0.1:<port>` left the served origin itself unreachable).
        self.assertEqual(
            pass_guard.guard_options("webkit", _GUARDED),
            {"proxy": {"server": "http://127.0.0.1:9", "bypass": "127.0.0.1"}},
        )

    def test_each_call_hands_out_its_own_copy(self) -> None:
        for engine, key in (("chromium", "args"), ("firefox", "firefox_user_prefs")):
            with self.subTest(engine=engine):
                first = pass_guard.guard_options(engine, _GUARDED)
                pristine = pass_guard.guard_options(engine, _GUARDED)
                first[key].clear()
                self.assertEqual(pass_guard.guard_options(engine, _GUARDED), pristine)


class ServedOriginTests(unittest.TestCase):
    """The guard admits the served origin's port, read from the base URL the run serves."""

    def test_the_port_is_the_base_url_s_or_its_scheme_s_default(self) -> None:
        cases = {
            "http://127.0.0.1:8123": 8123,
            "http://127.0.0.1": 80,
            "https://127.0.0.1": 443,
            "https://127.0.0.1:8443/site": 8443,
        }
        for base_url, port in cases.items():
            with self.subTest(base_url=base_url):
                served = Isolation(loopback_only=True).serving(base_url)
                self.assertEqual(served.served_port, port)
                self.assertTrue(served.loopback_only)

    def test_a_url_that_names_no_http_port_is_refused(self) -> None:
        for base_url in ("ftp://127.0.0.1", "http://127.0.0.1:99999"):
            with self.subTest(base_url=base_url), self.assertRaises(ValueError):
                pass_guard.served_port(base_url)

    def test_serving_keeps_the_extensions_and_an_unguarded_run_still_adds_nothing(
        self,
    ) -> None:
        served = pass_extension.NO_ISOLATION.serving("http://127.0.0.1:8123")
        self.assertEqual(served.extensions, ())
        for engine in _ALL:
            with self.subTest(engine=engine):
                self.assertEqual(pass_guard.guard_options(engine, served), {})

    def test_a_guarded_launch_before_anything_is_served_is_a_defect(self) -> None:
        for engine in _ALL:
            with (
                self.subTest(engine=engine),
                self.assertRaisesRegex(ValueError, "no origin is served"),
            ):
                pass_guard.guard_options(engine, Isolation(loopback_only=True))


class ProxyPermissionTests(unittest.TestCase):
    """An extension asking for the `proxy` permission could route around the guard: refused.

    Chromium hands a request to an extension's proxy without resolving its host, so the
    resolver rule never sees it; measured, an extension setting a proxy on 127.0.0.1 had the
    browser's own requests sent there by name. Firefox's proxy API lets an add-on answer each
    request with a proxy of its own (`proxy.onRequest`), over the prefs the guard sets.
    """

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_either_engine_s_extension_asking_for_it_is_refused(self) -> None:
        for key in ("permissions", "optional_permissions"):
            asks = {key: ["storage", "proxy"]}
            with self.subTest(engine="chromium", key=key):
                folder = unpacked(self.root / key, {**CHROMIUM_MANIFEST, **asks})
                self.assertEqual(
                    pass_extension.read_unpacked(folder),
                    f'--chromium-extension {folder}: asks for the "proxy" permission, '
                    "with which it could route requests around --loopback-only",
                )
            with self.subTest(engine="firefox", key=key):
                path = xpi(self.root / f"{key}.xpi", {**FIREFOX_MANIFEST, **asks})
                self.assertEqual(
                    pass_extension.read_xpi(path),
                    f'--firefox-extension {path}: asks for the "proxy" permission, '
                    "with which it could route requests around --loopback-only",
                )

    def test_other_permissions_and_odd_shapes_are_not_refused(self) -> None:
        cases: dict[str, dict[str, object]] = {
            "others": {"permissions": ["storage", "tabs", "<all_urls>"]},
            "not a list": {"permissions": "proxy"},
            "not text": {"optional_permissions": [{"proxy": True}]},
        }
        for label, asks in cases.items():
            with self.subTest(label):
                folder = unpacked(self.root / label, {**CHROMIUM_MANIFEST, **asks})
                self.assertIsInstance(pass_extension.read_unpacked(folder), Extension)


if __name__ == "__main__":
    unittest.main()
