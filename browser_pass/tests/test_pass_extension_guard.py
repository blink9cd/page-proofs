"""Grouped suite: the egress guard -- what each engine is launched with, and what it refuses.

`--loopback-only` launches each engine with its own guard, so a request off 127.0.0.1 fails.
`pass_extension.guard_options` says what that is: the launch arguments and prefs below, each one
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
from _extension_fixtures import (  # noqa: E402 - beside this suite
    CHROMIUM_MANIFEST,
    FIREFOX_MANIFEST,
    unpacked,
    xpi,
)
from pass_extension import Extension, Isolation  # noqa: E402 - the tool's private src/

_ALL = ("chromium", "firefox", "webkit")
_GUARDED = Isolation(loopback_only=True)


class GuardOptionsTests(unittest.TestCase):
    """What each engine is launched with under `--loopback-only`, and nothing without it."""

    def test_nothing_is_added_without_the_guard(self) -> None:
        for engine in _ALL:
            with self.subTest(engine=engine):
                self.assertEqual(
                    pass_extension.guard_options(engine, pass_extension.NO_ISOLATION),
                    {},
                )

    def test_chromium_runs_the_full_build_resolves_nothing_and_sends_no_udp(
        self,
    ) -> None:
        self.assertEqual(
            pass_extension.guard_options("chromium", _GUARDED),
            {
                "channel": "chromium",
                "args": [
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                    # WebRTC's own sockets never ask the resolver: no UDP that no proxy
                    # carries, and there is none. Measured to stop a STUN request.
                    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--disable-quic",
                ],
            },
        )

    def test_firefox_proxies_everything_and_opens_no_udp_channel(self) -> None:
        self.assertEqual(
            pass_extension.guard_options("firefox", _GUARDED),
            {
                "firefox_user_prefs": {
                    "network.proxy.type": 1,
                    "network.proxy.http": "127.0.0.1",
                    "network.proxy.http_port": 9,
                    "network.proxy.ssl": "127.0.0.1",
                    "network.proxy.ssl_port": 9,
                    "network.proxy.no_proxies_on": "",
                    "network.proxy.allow_hijacking_localhost": False,
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
        self.assertEqual(
            pass_extension.guard_options("webkit", _GUARDED),
            {"proxy": {"server": "http://127.0.0.1:9", "bypass": "127.0.0.1"}},
        )

    def test_each_call_hands_out_its_own_copy(self) -> None:
        for engine, key in (("chromium", "args"), ("firefox", "firefox_user_prefs")):
            with self.subTest(engine=engine):
                first = pass_extension.guard_options(engine, _GUARDED)
                pristine = pass_extension.guard_options(engine, _GUARDED)
                first[key].clear()
                self.assertEqual(
                    pass_extension.guard_options(engine, _GUARDED), pristine
                )


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
