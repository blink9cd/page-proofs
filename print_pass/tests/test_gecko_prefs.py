"""Suite: what a Firefox session asks for (the isolation and state prefs, the capabilities),
and the dead port the isolation prefs point the proxy at.

The prefs and the capabilities are pure values. `DeadPort` is proven on real loopback sockets,
since what it promises (every connection ended at once, a port no rival can bind) is the
kernel's behavior, not this module's; its accept loop's retry is proven over a stand-in
listener, since a failed accept cannot be made to happen on demand. Each name is also checked
to be the very object `gecko_launch` gives, so whichever module a caller imports, it gets the
one definition.

Entry point assumptions: standalone, or discovered by `unittest discover`. The insert below
reaches the subject's private `src/` beside this folder, never the working directory.
"""

from __future__ import annotations

import errno
import itertools
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import NoReturn
from unittest import mock
from urllib.parse import quote

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_launch  # noqa: E402 - the tool's private src/, via the insert above
import gecko_prefs  # noqa: E402 - as above
from print_record import EngineError  # noqa: E402 - as above
from print_targets import COLOR_SCHEMES, REDUCED_MOTIONS  # noqa: E402 - as above

#: The one address everything here binds and connects to.
_LOOPBACK = "127.0.0.1"

#: The served origin the isolation cases name, and its proxy auto-config script with a dead
#: port of 4242: its pages and WebSockets go direct, every other request to the dead port.
_SERVED = "http://127.0.0.1:8123"
#: The script is held in a file the node suite parses and runs (test_gecko_proxy_script.mjs).
_PAC = (Path(__file__).parent / "proxy_script.pac").read_text(encoding="utf-8")
_PAC = _PAC.removesuffix("\n")

#: `_PAC` as the pref names it: a `data:` URL, all but a letter, digit or `-_.~` encoded.
_PAC_URL = "data:application/x-ns-proxy-autoconfig," + quote(_PAC, safe="")

#: Bound on every connection and wait here: far longer than each takes.
_TIMEOUT_S = 30.0

#: The largest integer WebDriver carries exactly, which bounds every timeout it takes.
_MAX_SAFE_INTEGER = 2**53 - 1

#: Firefox's lazy-load margin on each side, a million CSS pixels: a decimal string, since
#: Firefox ignores an integer given for it (measured).
_LAZY_MARGIN = "1000000"

#: SO_LINGER on with a zero linger: a close that resets the connection.
_RESET_ON_CLOSE = struct.pack("ii", 1, 0)

#: Failed accepts in a row a back-off case makes: enough for any escalation to reach its cap.
_BACKOFF_WAITS = 20

#: What accept raises once the process is out of file descriptors, every time, at once.
_OUT_OF_DESCRIPTORS = OSError(errno.EMFILE, "Too many open files")

#: Failed accepts after which a stand-in listener sets the stop itself: a loop that never
#: waits then ends, and its case fails on what it waited instead of spinning forever.
_SPIN_GUARD = 1000


def _dict(value: object) -> dict[object, object]:
    """`value`, checked to be a dictionary."""
    assert isinstance(value, dict), value
    return value


def _member(value: object, key: str) -> object:
    """`value[key]`, `value` checked to be a dictionary first."""
    return _dict(value)[key]


class InterfaceTests(unittest.TestCase):
    """Each of these is the very object `gecko_launch`, an engine's one import, gives."""

    def test_each_is_the_same_object_there(self) -> None:
        for name in ("isolation_prefs", "state_prefs", "capabilities", "DeadPort"):
            with self.subTest(name=name):
                self.assertIs(getattr(gecko_launch, name), getattr(gecko_prefs, name))


class IsolationPrefsTests(unittest.TestCase):
    """Every request but the served origin's goes to a dead proxy; nothing reaches out alone."""

    def test_the_whole_set(self) -> None:
        self.assertEqual(
            gecko_prefs.isolation_prefs(4242, served=_SERVED),
            {
                "network.proxy.type": 2,
                "network.proxy.autoconfig_url": _PAC_URL,
                "network.proxy.allow_hijacking_localhost": True,
                "network.proxy.failover_direct": False,
                "network.proxy.allow_bypass": False,
                "network.dns.forceResolve": _LOOPBACK,
                "media.peerconnection.enabled": False,
                "network.http.http3.enable": False,
                "network.webtransport.enabled": False,
                "network.dns.disablePrefetch": True,
                "network.dns.disablePrefetchFromHTTPS": True,
                "network.predictor.enabled": False,
                "network.prefetch-next": False,
                "network.http.speculative-parallel-limit": 0,
                "network.trr.mode": 5,
                "network.captive-portal-service.enabled": False,
                "network.connectivity-service.enabled": False,
                "dom.serviceWorkers.enabled": False,
                "devtools.console.stdout.content": True,
            },
        )

    def test_the_script_sends_only_the_served_origin_direct(self) -> None:
        script = gecko_prefs.proxy_script(served_port=8123, dead_port=4242)
        self.assertEqual(script, _PAC)
        # Its URL holds nothing a URL parser could read as its end or a fragment.
        self.assertNotRegex(_PAC_URL.partition(",")[2], r"[\s#?\"']")

    def test_each_call_is_a_fresh_dictionary(self) -> None:
        first = gecko_prefs.isolation_prefs(4242, served=_SERVED)
        first["network.proxy.type"] = 0
        second = gecko_prefs.isolation_prefs(4242, served=_SERVED)
        self.assertEqual(second["network.proxy.type"], 2)

    def test_a_port_no_socket_can_have_is_refused(self) -> None:
        for port in (0, -1, 65536):
            with self.subTest(port=port), self.assertRaises(ValueError):
                gecko_prefs.isolation_prefs(port, served=_SERVED)
            with self.subTest(served=port), self.assertRaises(ValueError):
                gecko_prefs.proxy_script(served_port=port, dead_port=4242)

    def test_a_served_url_off_the_loopback_address_or_portless_is_refused(self) -> None:
        local, portless = "http://localhost:8123", "http://127.0.0.1"
        for served in (local, portless, "https://127.0.0.1:8123", "http://127.0.0.1:x"):
            with self.subTest(served=served), self.assertRaises(ValueError):
                gecko_prefs.isolation_prefs(4242, served=served)


class StatePrefsTests(unittest.TestCase):
    """A state's color scheme and motion become Firefox prefs; `native` sets none."""

    def prefs(self, scheme: str, motion: str = "native") -> dict[str, object]:
        """The prefs for one state."""
        return gecko_prefs.state_prefs(color_scheme=scheme, reduced_motion=motion)

    def test_native_sets_nothing(self) -> None:
        self.assertEqual(self.prefs("native"), {})

    def test_light_and_dark_override_the_content_scheme(self) -> None:
        override = "layout.css.prefers-color-scheme.content-override"
        self.assertEqual(self.prefs("light"), {override: 1})
        self.assertEqual(self.prefs("dark"), {override: 0})

    def test_native_dark_darkens_the_system_theme_itself(self) -> None:
        self.assertEqual(self.prefs("native-dark"), {"ui.systemUsesDarkTheme": 1})

    def test_reduce_sets_reduced_motion_beside_the_scheme(self) -> None:
        self.assertEqual(
            self.prefs("dark", "reduce"),
            {
                "layout.css.prefers-color-scheme.content-override": 0,
                "ui.prefersReducedMotion": 1,
            },
        )

    def test_every_value_a_targets_file_may_hold_has_prefs(self) -> None:
        for scheme in COLOR_SCHEMES:
            for motion in REDUCED_MOTIONS:
                with self.subTest(scheme=scheme, motion=motion):
                    self.assertIsInstance(self.prefs(scheme, motion), dict)

    def test_an_unknown_value_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "'sepia'"):
            self.prefs("sepia")
        with self.assertRaisesRegex(ValueError, "'slow'"):
            self.prefs("native", "slow")

    def test_each_call_is_a_fresh_dictionary(self) -> None:
        self.prefs("dark", "reduce").clear()
        self.assertEqual(len(self.prefs("dark", "reduce")), 2)


def _session_prefs(given: dict[str, object] | None = None) -> dict[object, object]:
    """The prefs a new-session body asks for, given `given` (none by default)."""
    body = gecko_prefs.capabilities(prefs=given or {}, timeout_ms=1)
    options = _member(_member(body, "alwaysMatch"), "moz:firefoxOptions")
    return _dict(_member(options, "prefs"))


class CapabilitiesTests(unittest.TestCase):
    """The new-session body: headless Firefox, its prefs, and the timeouts, nothing else."""

    def test_the_whole_body(self) -> None:
        self.assertEqual(
            gecko_prefs.capabilities(prefs={"a.b": 1}, timeout_ms=30000),
            {
                "alwaysMatch": {
                    "browserName": "firefox",
                    "acceptInsecureCerts": False,
                    "pageLoadStrategy": "normal",
                    "timeouts": {"script": 30000, "pageLoad": 30000, "implicit": 0},
                    "moz:firefoxOptions": {
                        "args": ["-headless"],
                        "prefs": {
                            "browser.chrome.site_icons": False,
                            "layout.css.visited_links_enabled": False,
                            "dom.lazy-loading.margin.top": _LAZY_MARGIN,
                            "dom.lazy-loading.margin.bottom": _LAZY_MARGIN,
                            "dom.lazy-loading.margin.left": _LAZY_MARGIN,
                            "dom.lazy-loading.margin.right": _LAZY_MARGIN,
                            "a.b": 1,
                        },
                    },
                }
            },
        )

    def test_no_binary_is_named(self) -> None:
        # geckodriver finds the installed Firefox itself, so no path is ever written here.
        body = gecko_prefs.capabilities(prefs={}, timeout_ms=1)
        options = _member(_member(body, "alwaysMatch"), "moz:firefoxOptions")
        self.assertNotIn("binary", _dict(options))

    def test_the_prefs_are_copied(self) -> None:
        prefs: dict[str, object] = {"a.b": 1}
        body = gecko_prefs.capabilities(prefs=prefs, timeout_ms=1)
        prefs["a.b"] = 2
        options = _member(_member(body, "alwaysMatch"), "moz:firefoxOptions")
        self.assertEqual(_member(_member(options, "prefs"), "a.b"), 1)

    def test_a_timeout_webdriver_cannot_carry_is_refused(self) -> None:
        for timeout_ms in (0, -1, _MAX_SAFE_INTEGER + 1):
            with self.subTest(timeout_ms=timeout_ms), self.assertRaises(ValueError):
                gecko_prefs.capabilities(prefs={}, timeout_ms=timeout_ms)

    def test_the_largest_timeout_webdriver_carries(self) -> None:
        body = gecko_prefs.capabilities(prefs={}, timeout_ms=_MAX_SAFE_INTEGER)
        timeouts = _member(_member(body, "alwaysMatch"), "timeouts")
        self.assertEqual(_member(timeouts, "script"), _MAX_SAFE_INTEGER)


class PageOwnPrefsTests(unittest.TestCase):
    """Every session turns off behaviors of Firefox's own that show in a page's record as if
    they were the page's, so what a state reports is the page's own."""

    def test_firefox_fetches_no_site_icon(self) -> None:
        # Not a linked icon, nor the `/favicon.ico` Firefox guesses for a page with none.
        self.assertIs(_session_prefs()["browser.chrome.site_icons"], False)

    def test_no_link_prints_visited(self) -> None:
        self.assertIs(_session_prefs()["layout.css.visited_links_enabled"], False)

    def test_a_lazy_image_within_a_million_pixels_on_any_side_loads(self) -> None:
        prefs = _session_prefs()
        for side in ("top", "bottom", "left", "right"):
            with self.subTest(side=side):
                margin = prefs[f"dom.lazy-loading.margin.{side}"]
                # A string, and only a string: Firefox ignored the same value as an integer.
                self.assertIsInstance(margin, str)
                self.assertEqual(margin, _LAZY_MARGIN)

    def test_a_pref_the_caller_names_wins(self) -> None:
        given: dict[str, object] = {"browser.chrome.site_icons": True}
        self.assertIs(_session_prefs(given)["browser.chrome.site_icons"], True)


def _reset_seconds(port: int) -> float:
    """Connect to `port` and read; the seconds until the reset. Fails the case on anything else."""
    started = time.monotonic()
    # The reset can land before the connect has returned, or after: both are a reset.
    try:
        with socket.create_connection((_LOOPBACK, port), timeout=_TIMEOUT_S) as client:
            data = client.recv(1)
    except ConnectionResetError:
        return time.monotonic() - started
    raise AssertionError(f"the connection was not reset; it read {data!r}")


class DeadPortTests(unittest.TestCase):
    """A loopback port held for the run: every connection ended at once, no rival bound."""

    def test_a_connection_is_reset_at_once(self) -> None:
        with gecko_prefs.DeadPort() as port:
            # A connection the kernel drops fails only at the timeout; a reset is immediate.
            self.assertLess(_reset_seconds(port), _TIMEOUT_S / 2)

    def test_connection_after_connection_is_reset(self) -> None:
        with gecko_prefs.DeadPort() as port:
            for attempt in range(3):
                with self.subTest(attempt=attempt):
                    self.assertLess(_reset_seconds(port), _TIMEOUT_S / 2)

    def test_no_rival_can_bind_it_even_reusing_addresses(self) -> None:
        with gecko_prefs.DeadPort() as port, socket.socket() as rival:
            rival.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError) as raised:
                rival.bind((_LOOPBACK, port))
            self.assertEqual(raised.exception.errno, errno.EADDRINUSE)

    def test_leaving_the_block_ends_its_thread_and_frees_the_port(self) -> None:
        before = set(threading.enumerate())
        with gecko_prefs.DeadPort() as port:
            started = set(threading.enumerate()) - before
        self.assertEqual(len(started), 1)
        self.assertFalse(started.pop().is_alive())
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection((_LOOPBACK, port), timeout=_TIMEOUT_S).close()

    def test_it_is_a_port_a_proxy_pref_takes(self) -> None:
        with gecko_prefs.DeadPort() as port:
            script = gecko_prefs.proxy_script(served_port=8123, dead_port=port)
            self.assertIn(f"PROXY {_LOOPBACK}:{port}", script)

    def test_leaving_one_never_entered_is_harmless(self) -> None:
        gecko_prefs.DeadPort().__exit__(None, None, None)

    def test_entering_one_already_held_is_refused(self) -> None:
        # A second listener and thread would leak: the first stop would end only the second.
        dead = gecko_prefs.DeadPort()
        port = dead.__enter__()
        self.addCleanup(dead.__exit__, None, None, None)
        threads = set(threading.enumerate())
        with self.assertRaisesRegex(
            RuntimeError, f"^the dead port {port} is already held$"
        ):
            dead.__enter__()
        self.assertEqual(set(threading.enumerate()), threads)
        self.assertLess(_reset_seconds(port), _TIMEOUT_S / 2)
        dead.__exit__(None, None, None)
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection((_LOOPBACK, port), timeout=_TIMEOUT_S).close()


def _unheld_port() -> int:
    """A loopback port nothing holds: bound, then closed, so a connection to it is refused."""
    with socket.socket() as probe:
        probe.bind((_LOOPBACK, 0))
        port: int = probe.getsockname()[1]
    return port


class StopResettingTests(unittest.TestCase):
    """A stop whose thread does not end within its bound says so, instead of going on."""

    def test_a_thread_that_outlives_its_join_is_reported(self) -> None:
        thread = mock.Mock(spec=threading.Thread)
        thread.is_alive.return_value = True
        stop = threading.Event()
        with self.assertRaisesRegex(
            EngineError, "^the dead port's thread outlived its stop by 10 s$"
        ):
            gecko_prefs._stop_resetting(thread, stop, _unheld_port())
        self.assertTrue(stop.is_set())
        thread.join.assert_called_once_with(timeout=gecko_prefs._SOCKET_TIMEOUT_S)


def _always_out_of_descriptors(stop: threading.Event) -> mock.Mock:
    """A stand-in listener whose accept fails at once, every time, until `_SPIN_GUARD`."""
    listener = mock.Mock()

    def accept() -> NoReturn:
        if listener.accept.call_count >= _SPIN_GUARD:
            stop.set()
        raise _OUT_OF_DESCRIPTORS

    listener.accept.side_effect = accept
    return listener


class _RecordingStop(threading.Event):
    """A stop flag that records each bounded wait on it, and sets itself at the `last`th."""

    def __init__(self, last: int) -> None:
        """Unset, nothing waited for yet."""
        super().__init__()
        self.waits: list[float] = []
        self._last = last

    def wait(self, timeout: float | None = None) -> bool:
        """Record `timeout` instead of waiting it out; whether the flag is now set."""
        assert timeout is not None, "the accept loop waited without a bound"
        self.waits.append(timeout)
        if len(self.waits) >= self._last:
            self.set()
        return self.is_set()


class ResetLoopTests(unittest.TestCase):
    """The loop behind `DeadPort`: each connection reset, a failed accept tried again."""

    def test_a_failed_accept_is_tried_again_and_a_connection_reset(self) -> None:
        # A timeout is the poll interval itself, and an aborted connection needs nothing:
        # neither is waited out.
        stop = _RecordingStop(last=1)
        connection = mock.Mock()
        connection.close.side_effect = stop.set
        listener = mock.Mock()
        listener.accept.side_effect = [
            TimeoutError(),
            ConnectionAbortedError(),
            (connection, (_LOOPBACK, 1)),
        ]
        gecko_prefs._reset_each_connection(listener, stop)
        self.assertEqual(listener.accept.call_count, 3)
        self.assertEqual(
            connection.mock_calls,
            [
                mock.call.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, _RESET_ON_CLOSE
                ),
                mock.call.close(),
            ],
        )
        self.assertEqual(stop.waits, [])

    def test_a_persistent_accept_error_backs_off_until_the_stop(self) -> None:
        # Out of descriptors, accept fails at once every time: retried with no wait, the
        # loop would spin a core until the stop.
        stop = _RecordingStop(last=_BACKOFF_WAITS)
        listener = _always_out_of_descriptors(stop)
        gecko_prefs._reset_each_connection(listener, stop)
        self.assertEqual(listener.accept.call_count, _BACKOFF_WAITS)
        cap = gecko_prefs._ACCEPT_POLL_S
        waits = stop.waits
        self.assertGreater(waits[0], 0)
        for before, after in itertools.pairwise(waits):
            with self.subTest(before=before, after=after):
                self.assertTrue(before < after or before == after == cap)
        self.assertEqual(waits[-1], cap)

    def test_an_accepted_connection_starts_the_back_off_over(self) -> None:
        stop = _RecordingStop(last=3)
        connection = mock.Mock()
        listener = mock.Mock()
        listener.accept.side_effect = [
            _OUT_OF_DESCRIPTORS,
            _OUT_OF_DESCRIPTORS,
            (connection, (_LOOPBACK, 1)),
            _OUT_OF_DESCRIPTORS,
        ]
        gecko_prefs._reset_each_connection(listener, stop)
        first, second, third = stop.waits
        self.assertLess(first, second)
        self.assertEqual(third, first)
        connection.close.assert_called_once_with()

    def test_a_reset_that_fails_still_closes_and_the_next_is_reset(self) -> None:
        # The client already gone: its error must not end the loop, which would leave every
        # later connection queued unanswered.
        stop = threading.Event()
        gone = mock.Mock()
        gone.setsockopt.side_effect = OSError(errno.EINVAL, "Invalid argument")
        following = mock.Mock()
        following.close.side_effect = stop.set
        listener = mock.Mock()
        listener.accept.side_effect = [
            (gone, (_LOOPBACK, 1)),
            (following, (_LOOPBACK, 2)),
        ]
        gecko_prefs._reset_each_connection(listener, stop)
        gone.close.assert_called_once_with()
        self.assertEqual(
            following.mock_calls,
            [
                mock.call.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, _RESET_ON_CLOSE
                ),
                mock.call.close(),
            ],
        )

    def test_a_stopped_loop_accepts_nothing(self) -> None:
        stop = threading.Event()
        stop.set()
        listener = mock.Mock()
        gecko_prefs._reset_each_connection(listener, stop)
        listener.accept.assert_not_called()


if __name__ == "__main__":
    unittest.main()
