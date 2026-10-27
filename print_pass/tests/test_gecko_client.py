"""`gecko_client.GeckoClient`: the client that opens a session, and what it refuses.

Every case runs over `_gecko_client_fixtures`' recording transport and bound stand-in, so the
log shows each request made inside a bound of its own. The session's own commands are
`test_gecko_client_session.py`; `urllib_transport` against a real loopback server is
`test_gecko_client_transport.py`; the error, and the decoding and parsing of every reply, are
`test_gecko_reply.py`.

Entry point assumptions: standalone, or discovered by `unittest discover`. The fixtures
module beside this one puts the tool's private `src/` on `sys.path`.
"""

from __future__ import annotations

import math
import sys
import unittest
import urllib.error
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The fixtures first: importing them puts the tool's private src/ on sys.path.
from _gecko_client_fixtures import (  # noqa: E402 - beside this module, via the insert above
    BASE_URL,
    BROWSER_VERSION,
    SESSION_ID,
    TIMEOUT_S,
    Entry,
    FakeBounder,
    exchange,
    failure,
    make_client,
    ok,
    session_reply,
)

import gecko_client  # noqa: E402 - the tool's private src/, via the fixtures' insert
from gecko_client import WebDriverError  # noqa: E402 - as above

#: The capabilities each case asks a session for.
_CAPABILITIES = {"alwaysMatch": {"browserName": "firefox"}}

#: The error code a malformed reply is reported under.
_INVALID = "invalid response"


class GeckoClientTests(unittest.TestCase):
    def test_refuses_a_base_url_that_is_not_loopback_http_with_a_port(self) -> None:
        refused = (
            "http://localhost:4444",
            "https://127.0.0.1:4444",
            "http://192.0.2.1:4444",
            "http://127.0.0.1",
            "http://127.0.0.1:",
            "http://127.0.0.1:0",
            "http://127.0.0.1:04444",
            "http://127.0.0.1:65536",
            "http://127.0.0.1:4444/",
            "http://127.0.0.1:4444/session",
            "http://127.0.0.1:4444\n",
            "http://user@127.0.0.1:4444",
            "http://127.0.0.1:٤٤٤٤",
        )
        for base_url in refused:
            with self.subTest(base_url=base_url):
                with self.assertRaises(ValueError):
                    make_client(base_url=base_url)

    def test_accepts_the_lowest_port_the_highest_and_one_between(self) -> None:
        for port in (1, 4444, 65535):
            with self.subTest(port=port):
                client, log = make_client(
                    session_reply(), base_url=f"http://127.0.0.1:{port}"
                )
                client.new_session(_CAPABILITIES)
                self.assertEqual(log[1][2], f"http://127.0.0.1:{port}/session")

    def test_refuses_a_timeout_whose_bound_the_watchdog_could_not_wait_out(
        self,
    ) -> None:
        for timeout_s in (0.0, -1.0, math.nan, math.inf):
            with self.subTest(timeout_s=timeout_s):
                with self.assertRaises(ValueError):
                    make_client(timeout_s=timeout_s)

    def test_new_session_posts_the_capabilities_inside_its_bound(self) -> None:
        client, log = make_client(session_reply())
        session = client.new_session(_CAPABILITIES)
        self.assertEqual(
            log,
            exchange(
                "new session",
                "POST",
                f"{BASE_URL}/session",
                {"capabilities": _CAPABILITIES},
            ),
        )
        self.assertEqual(session.session_id, SESSION_ID)
        self.assertEqual(session.browser_version, BROWSER_VERSION)

    def test_new_session_raises_the_error_geckodriver_replies_with(self) -> None:
        client, _ = make_client(
            failure(500, "session not created", "Process unexpectedly closed\nstatus 0")
        )
        with self.assertRaises(WebDriverError) as caught:
            client.new_session(_CAPABILITIES)
        self.assertEqual(
            str(caught.exception),
            "new session: session not created: Process unexpectedly closed",
        )

    def test_new_session_refuses_a_value_without_an_id_and_a_version(self) -> None:
        capabilities = {"browserVersion": BROWSER_VERSION}
        values: tuple[object, ...] = (
            None,
            [SESSION_ID],
            {"capabilities": capabilities},
            {"sessionId": 7, "capabilities": capabilities},
            {"sessionId": "", "capabilities": capabilities},
            {"sessionId": "a/b", "capabilities": capabilities},
            {"sessionId": "../x", "capabilities": capabilities},
            {"sessionId": SESSION_ID},
            {"sessionId": SESSION_ID, "capabilities": [BROWSER_VERSION]},
            {"sessionId": SESSION_ID, "capabilities": {}},
            {"sessionId": SESSION_ID, "capabilities": {"browserVersion": 157}},
        )
        for value in values:
            with self.subTest(value=value):
                client, _ = make_client(ok(value))
                with self.assertRaises(WebDriverError) as caught:
                    client.new_session(_CAPABILITIES)
                self.assertEqual(caught.exception.command, "new session")
                self.assertEqual(caught.exception.error, _INVALID)

    def test_a_reply_over_the_cap_is_refused_unread(self) -> None:
        # A page can make a reply as large as it likes (an Execute returning its own
        # string), so a reply past the cap is the command's failure, not a read.
        status, body = session_reply()
        cap = len(body) - 1
        with mock.patch.object(gecko_client, "MAX_REPLY_BYTES", cap):
            client, _ = make_client((status, body))
            with self.assertRaises(WebDriverError) as caught:
                client.new_session(_CAPABILITIES)
        self.assertEqual(caught.exception.error, _INVALID)
        self.assertEqual(
            str(caught.exception),
            f"new session: {_INVALID}: the reply is over {cap:,} bytes",
        )

    def test_a_reply_of_exactly_the_cap_is_read(self) -> None:
        status, body = session_reply()
        with mock.patch.object(gecko_client, "MAX_REPLY_BYTES", len(body)):
            client, _ = make_client((status, body))
            self.assertEqual(client.new_session(_CAPABILITIES).session_id, SESSION_ID)

    def test_a_transport_failure_is_a_transport_error_naming_the_command(
        self,
    ) -> None:
        failures: tuple[tuple[OSError, str], ...] = (
            (TimeoutError("timed out"), "TimeoutError: timed out"),
            (
                ConnectionRefusedError(61, "Connection refused"),
                "ConnectionRefusedError: [Errno 61] Connection refused",
            ),
            (
                urllib.error.URLError("refused\nsecond line"),
                "URLError: <urlopen error refused",
            ),
            (OSError(), "OSError: "),
        )
        for raised, text in failures:
            with self.subTest(raised=raised):
                client, log = make_client(raised)
                with self.assertRaises(WebDriverError) as caught:
                    client.new_session(_CAPABILITIES)
                self.assertEqual(caught.exception.command, "new session")
                self.assertEqual(caught.exception.error, "transport")
                self.assertEqual(
                    str(caught.exception), f"new session: transport: {text}"
                )
                self.assertIs(caught.exception.__cause__, raised)
                self.assertEqual(log[-1], ("unbound", "webdriver new session"))

    def test_an_error_the_transport_does_not_promise_is_not_rewritten(self) -> None:
        log: list[Entry] = []
        client = gecko_client.GeckoClient(
            BASE_URL,
            transport=_raise_lookup_error,
            bounder=FakeBounder(log),
            timeout_s=TIMEOUT_S,
        )
        with self.assertRaises(LookupError):
            client.new_session(_CAPABILITIES)
        self.assertEqual(log[-1], ("unbound", "webdriver new session"))


def _raise_lookup_error(
    method: str, url: str, body: bytes | None, timeout_s: float
) -> tuple[int, bytes]:
    """A transport failing with an error that is not a socket's."""
    raise LookupError(f"{method} {url} {timeout_s}")


if __name__ == "__main__":
    unittest.main()
