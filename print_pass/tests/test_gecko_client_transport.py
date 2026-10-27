"""`gecko_client.urllib_transport` against a real HTTP server on 127.0.0.1.

The server is the standard library's, bound to loopback on a port the kernel picks, and it
records each request it is sent: its method, path, Content-Type and body. A reply with an
error status must come back as a status rather than raise, a redirect must come back
unfollowed, and every failure of the socket or of the reply's HTTP must raise `OSError`, the
one error the client turns into its transport error. A proxy named in the environment must
never carry a loopback request: the default opener would send one there.

Nothing here waits on a clock but the one silent-server case, whose short timeout is the
behavior under test. Nothing leaves 127.0.0.1.

Entry point assumptions: standalone, or discovered by `unittest discover`. The fixtures
module beside this one puts the tool's private `src/` on `sys.path`.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Final, NamedTuple
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _gecko_client_fixtures  # noqa: E402, F401 - puts the tool's private src/ on sys.path
import gecko_client  # noqa: E402 - the tool's private src/, via the fixtures' insert

#: The Content-Type every request with a body must carry.
_JSON_TYPE: Final = "application/json; charset=utf-8"

#: The timeout each request here is given: generous, since a healthy one answers at once.
_TIMEOUT_S: Final = 10.0

#: The timeout a request to the silent server is given, which it must run out.
_SILENT_TIMEOUT_S: Final = 0.2

#: How long the silent-server case waits for its call to end: far past the call's timeout,
#: so only a call that was never given one is still running when it is reached.
_SILENT_DEADLINE_S: Final = 5.0

#: How long a test waits for its server's thread to end once the server is shut down.
_JOIN_S: Final = 10.0

#: How often the server's loop looks for its shutdown: short, so each case ends promptly
#: rather than waiting out the standard library's half-second default.
_POLL_S: Final = 0.05

#: Each path the server answers, and its reply: a status, then a body.
_SUCCESS_BODY: Final = b'{"value": {"ok": true}}'
_ERROR_BODY: Final = b'{"value": {"error": "unknown command", "message": "no route"}}'
_REDIRECT_BODY: Final = b'{"value": "moved"}'

#: Every status a redirect can carry. A default opener follows some of them for a POST,
#: re-sent as a GET, and which ones is urllib's choice rather than this client's, so each
#: is checked.
_REDIRECT_STATUSES: Final = (301, 302, 303, 307, 308)


def _moved(status: int) -> str:
    """The path the server answers with a redirect carrying `status`."""
    return f"/moved-{status}"


# Records are NamedTuples, not dataclasses: under postponed annotations a dataclass looks
# its module up in sys.modules, and a NamedTuple does not, so this file imports by path.
class _Seen(NamedTuple):
    """One request the server was sent."""

    method: str
    path: str
    content_type: str | None
    body: bytes


class _Server(NamedTuple):
    """The running server's base URL and every request it has been sent, in order."""

    base_url: str
    seen: list[_Seen]


class _Handler(BaseHTTPRequestHandler):
    """Records each request, then replies as its path says."""

    server: _RecordingServer

    def do_POST(self) -> None:
        """Record and reply."""
        self._record_and_reply()

    def do_DELETE(self) -> None:
        """Record and reply."""
        self._record_and_reply()

    def log_message(self, format: str, *args: object) -> None:
        """Write no access log: each request is recorded instead, and stderr stays the test's."""

    def _record_and_reply(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.server.seen.append(
            _Seen(
                self.command,
                self.path,
                self.headers.get("Content-Type"),
                self.rfile.read(length),
            )
        )
        if self.path == "/garbage":
            # No status line at all: the client cannot read this as HTTP.
            self.wfile.write(b"garbage\r\n\r\n")
            return
        if self.path == "/short":
            # A length the body never reaches before the connection closes.
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"ten bytes!")
            return
        status, body, location = _REPLIES.get(self.path, (404, _ERROR_BODY, None))
        self.send_response(status)
        self.send_header("Content-Type", _JSON_TYPE)
        self.send_header("Content-Length", str(len(body)))
        if location is not None:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)


#: The plain replies by path; any other path is a 404 with an error body. Each redirect
#: points at a path the server answers, so one followed would reach it: re-sent as a GET,
#: which the server does not answer (501), or as a POST, which it records.
_REPLIES: Final[dict[str, tuple[int, bytes, str | None]]] = {
    "/session": (200, _SUCCESS_BODY, None),
    "/session/abc": (200, b'{"value": null}', None),
    **{
        _moved(status): (status, _REDIRECT_BODY, "/session")
        for status in _REDIRECT_STATUSES
    },
}


class _RecordingServer(HTTPServer):
    """The standard library's server, keeping the requests its handler records."""

    def __init__(self) -> None:
        """Bind 127.0.0.1 on a port the kernel picks."""
        super().__init__(("127.0.0.1", 0), _Handler)
        self.seen: list[_Seen] = []


@contextmanager
def _serving() -> Iterator[_Server]:
    """Run a recording server on loopback for the block, then shut it down."""
    server = _RecordingServer()
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": _POLL_S}, daemon=True
    )
    thread.start()
    try:
        port = server.server_address[1]
        state = _Server(base_url=f"http://127.0.0.1:{port}", seen=server.seen)
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=_JOIN_S)


def _closed_port() -> int:
    """A loopback port nothing listens on: bound by the kernel's choice, then released.

    A connection to it is refused at once. A socket bound but never listening is not used
    instead: on some kernels a connection to one is dropped rather than refused, and waits
    out its whole timeout.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


class UrllibTransportTests(unittest.TestCase):
    def test_a_post_sends_json_and_returns_the_status_and_body(self) -> None:
        with _serving() as server:
            reply = gecko_client.urllib_transport(
                "POST", f"{server.base_url}/session", b'{"a": 1}', _TIMEOUT_S
            )
        self.assertEqual(reply, (200, _SUCCESS_BODY))
        self.assertEqual(
            server.seen, [_Seen("POST", "/session", _JSON_TYPE, b'{"a": 1}')]
        )

    def test_a_delete_without_a_body_sends_none(self) -> None:
        with _serving() as server:
            reply = gecko_client.urllib_transport(
                "DELETE", f"{server.base_url}/session/abc", None, _TIMEOUT_S
            )
        self.assertEqual(reply, (200, b'{"value": null}'))
        self.assertEqual([seen.method for seen in server.seen], ["DELETE"])
        self.assertEqual(server.seen[0].body, b"")

    def test_an_error_status_is_returned_not_raised(self) -> None:
        with _serving() as server:
            reply = gecko_client.urllib_transport(
                "POST", f"{server.base_url}/no-such-route", b"{}", _TIMEOUT_S
            )
        self.assertEqual(reply, (404, _ERROR_BODY))

    def test_every_redirect_is_returned_not_followed(self) -> None:
        for status in _REDIRECT_STATUSES:
            with self.subTest(status=status):
                with _serving() as server:
                    reply = gecko_client.urllib_transport(
                        "POST", f"{server.base_url}{_moved(status)}", b"{}", _TIMEOUT_S
                    )
                self.assertEqual(reply, (status, _REDIRECT_BODY))
                self.assertEqual([seen.path for seen in server.seen], [_moved(status)])

    def test_no_more_than_one_byte_past_the_cap_is_read(self) -> None:
        # The caller refuses what is past the cap; the transport never holds more of it.
        for path, status, body in (
            ("/session", 200, _SUCCESS_BODY),
            ("/no-such-route", 404, _ERROR_BODY),
        ):
            with self.subTest(path=path):
                with (
                    mock.patch.object(gecko_client, "MAX_REPLY_BYTES", 5),
                    _serving() as server,
                ):
                    reply = gecko_client.urllib_transport(
                        "POST", f"{server.base_url}{path}", b"{}", _TIMEOUT_S
                    )
                self.assertEqual(reply, (status, body[:6]))

    def test_a_refused_connection_raises_oserror(self) -> None:
        url = f"http://127.0.0.1:{_closed_port()}/session"
        with self.assertRaises(OSError):
            gecko_client.urllib_transport("POST", url, b"{}", _TIMEOUT_S)

    def test_a_server_that_never_answers_raises_oserror_at_the_timeout(self) -> None:
        # Listening but never accepting: the kernel completes the connection, and the
        # reply never comes. urllib has no timeout of its own, so the call runs on a
        # thread of its own: one never handed the timeout fails here rather than hanging.
        raised: list[OSError] = []

        def send(url: str) -> None:
            try:
                gecko_client.urllib_transport("POST", url, b"{}", _SILENT_TIMEOUT_S)
            except OSError as error:
                raised.append(error)

        with socket.create_server(("127.0.0.1", 0)) as silent:
            url = f"http://127.0.0.1:{silent.getsockname()[1]}/session"
            # A daemon, so a call still running at the deadline cannot hold the run open.
            caller = threading.Thread(target=send, args=(url,), daemon=True)
            caller.start()
            caller.join(timeout=_SILENT_DEADLINE_S)
            self.assertFalse(
                caller.is_alive(), f"the call outlived {_SILENT_DEADLINE_S} s"
            )
        self.assertEqual(len(raised), 1)

    def test_a_reply_that_is_not_http_raises_connection_error(self) -> None:
        for path in ("/garbage", "/short"):
            with self.subTest(path=path):
                with _serving() as server:
                    with self.assertRaises(ConnectionError) as caught:
                        gecko_client.urllib_transport(
                            "POST", f"{server.base_url}{path}", b"{}", _TIMEOUT_S
                        )
                self.assertIn("malformed HTTP reply", str(caught.exception))


class EnvironmentProxyTests(unittest.TestCase):
    """A proxy named in the environment never carries a loopback request."""

    def setUp(self) -> None:
        # Route plain HTTP to a dead proxy, as a reader's environment could, with nothing
        # exempting loopback from it.
        proxy = f"http://127.0.0.1:{_closed_port()}"
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.lower() not in {"no_proxy", "http_proxy", "request_method"}
        }
        environment.update(http_proxy=proxy, HTTP_PROXY=proxy)
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # urlopen's opener is built once and kept: drop it now and after, so a transport
        # that fell back to urlopen would build one under this environment and be caught.
        urllib.request.install_opener(None)
        self.addCleanup(urllib.request.install_opener, None)

    def test_the_default_opener_would_send_a_loopback_request_to_the_proxy(
        self,
    ) -> None:
        # The control: without it, a pass below could mean the environment was never read.
        with _serving() as server:
            with self.assertRaises(urllib.error.URLError):
                urllib.request.build_opener().open(
                    f"{server.base_url}/session", b"{}", _TIMEOUT_S
                )
        self.assertEqual(server.seen, [])

    def test_the_transport_reaches_the_loopback_server_directly(self) -> None:
        with _serving() as server:
            reply = gecko_client.urllib_transport(
                "POST", f"{server.base_url}/session", b"{}", _TIMEOUT_S
            )
        self.assertEqual(reply, (200, _SUCCESS_BODY))
        self.assertEqual([seen.path for seen in server.seen], ["/session"])


if __name__ == "__main__":
    unittest.main()
