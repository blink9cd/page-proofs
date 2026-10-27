"""Grouped suite: what the WebKit guard baseline expects on each platform, with no engine run.

WebKit names a refused request in its network stack's own words, which differ by platform, and
its WebRTC finishes gathering candidates only on Linux, so both expectations are chosen per
`sys.platform`. These pure selectors are tested here, fast, so neither wording is accepted on the
wrong platform and an unmeasured platform fails loudly rather than passing.
"""

from __future__ import annotations

# First: the fixture module puts the tool's src/ and the shared loopback server on sys.path.
import _live_fixtures  # noqa: F401 - imported for its sys.path side effect

import unittest

from _guard_fixtures import GATHERED, check_webkit_udp, webkit_refusal

#: A refusal as Linux WebKit (libsoup) words it, measured in CI.
_LINUX_FAILURE = (
    "failed http://192.0.2.1/off-loopback.png "
    "(Could not connect to proxy server 127.0.0.1: Connection refused)"
)

#: A refusal as macOS WebKit (CFNetwork) words it, measured locally.
_MAC_FAILURE = (
    "failed http://192.0.2.1/off-loopback.png (Could not connect to the server.)"
)

#: What the probe logs before any gathering line.
_PRESENT = ("webrtc: present",)


class WebkitRefusalTest(unittest.TestCase):
    """`webkit_refusal` picks the current platform's wording, and only that one."""

    def test_mac_wording_matches_mac_failure_only(self) -> None:
        refusal = webkit_refusal("darwin")
        self.assertIn(refusal, _MAC_FAILURE)
        self.assertNotIn(refusal, _LINUX_FAILURE)

    def test_linux_wording_matches_linux_failure_only(self) -> None:
        refusal = webkit_refusal("linux")
        self.assertIn(refusal, _LINUX_FAILURE)
        self.assertNotIn(refusal, _MAC_FAILURE)

    def test_unmeasured_platform_fails_loudly(self) -> None:
        with self.assertRaisesRegex(
            AssertionError, "no measured WebKit refusal for 'win32'"
        ):
            webkit_refusal("win32")


class CheckWebkitUdpTest(unittest.TestCase):
    """`check_webkit_udp` holds the open-UDP claim where it is measurable, and says when not."""

    def test_linux_gathered_with_datagrams_holds(self) -> None:
        self.assertIsNone(check_webkit_udp("linux", (*_PRESENT, GATHERED), 1))

    def test_linux_without_gathering_fails(self) -> None:
        with self.assertRaisesRegex(
            AssertionError, "never finished gathering on 'linux'"
        ):
            check_webkit_udp("linux", _PRESENT, 3)

    def test_gathered_without_datagrams_fails_on_either_platform(self) -> None:
        for platform in ("linux", "darwin"):
            with (
                self.subTest(platform=platform),
                self.assertRaisesRegex(AssertionError, "sent no STUN datagram"),
            ):
                check_webkit_udp(platform, (*_PRESENT, GATHERED), 0)

    def test_mac_gathered_with_datagrams_holds(self) -> None:
        self.assertIsNone(check_webkit_udp("darwin", (*_PRESENT, GATHERED), 2))

    def test_mac_without_gathering_returns_a_note_not_a_verdict(self) -> None:
        note = check_webkit_udp("darwin", _PRESENT, 0)
        self.assertIsNotNone(note)
        self.assertIn("not measured on 'darwin'", str(note))

    def test_mac_without_gathering_but_datagrams_holds(self) -> None:
        self.assertIsNone(check_webkit_udp("darwin", _PRESENT, 1))

    def test_unmeasured_platform_fails_loudly(self) -> None:
        with self.assertRaisesRegex(
            AssertionError, "no measured WebRTC claim for 'win32'"
        ):
            check_webkit_udp("win32", (*_PRESENT, GATHERED), 1)


if __name__ == "__main__":
    unittest.main()
