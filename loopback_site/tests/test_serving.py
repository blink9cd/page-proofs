"""Grouped suite: the library -- serving a directory on loopback, and proving what it serves.

Covers `serve_directory` binding and stopping, `confirm_serves` against the tree on disk, the
Host check that refuses every origin but the server's own loopback port, and which handler
failures reach stderr. A page answered with a redirect has a suite of its own,
`test_redirects.py`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import http.client
import http.server
import inspect
import io
import os
import socket
import socketserver
import statistics
import struct
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import loopback_site  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import TIMEOUT, scratch_tree  # noqa: E402 - beside this module, via the loader's top_level_dir


#: A foreign name for the Host header. `.example` is reserved, so it can never name a real host.
_FOREIGN_HOST = "rebound.example"

#: How long leaving a `serve_directory` block may take. At socketserver's own poll of 0.5 s an
#: exit right after the loop starts a select waits nearly all of it (measured ~0.49 s); a
#: short poll exits in a few hundredths. 0.3 s sits clear of both, so load has room to slow a
#: prompt exit without passing a slow one.
_PROMPT_EXIT_SECONDS = 0.3

#: Exits timed per run; the median keeps one scheduling hiccup from deciding the verdict.
_EXIT_SAMPLES = 5

#: What a client hanging up mid-request raises on the server's side of the socket.
_CLIENT_HANG_UPS: tuple[type[OSError], ...] = (ConnectionResetError, BrokenPipeError)

#: Handler failures that are not a hang-up, so their traceback must still print.
#: ConnectionAbortedError shares the hang-ups' ConnectionError base, so it pins the quiet path
#: to the two named types rather than to that whole family.
_OTHER_FAILURES: tuple[type[Exception], ...] = (ValueError, ConnectionAbortedError)

#: A body far larger than both loopback socket buffers together, so the server is still
#: writing it when the client resets the connection. Written sparse, so it costs no disk.
_OVERSIZED_BODY_BYTES = 32 * 1024 * 1024

#: How much of the reply the resetting client reads: enough to hold the status line.
_STATUS_READ_BYTES = 64

#: SO_LINGER on, with a zero timeout, so close() sends an RST rather than a FIN. The layout is
#: POSIX's `struct linger`, two ints.
_RESET_ON_CLOSE = struct.pack("ii", 1, 0)


def _get(
    base_url: str, host: str | None, page: str = "/"
) -> tuple[int, bytes, http.client.HTTPMessage]:
    """GET `page` at `base_url` sending `host` as the Host header, or no Host at all for None."""
    connection = http.client.HTTPConnection(
        loopback_site.LOOPBACK_HOST, urlsplit(base_url).port, timeout=TIMEOUT
    )
    try:
        connection.putrequest("GET", page, skip_host=True)
        if host is not None:
            connection.putheader("Host", host)
        connection.endheaders()
        reply = connection.getresponse()
        return reply.status, reply.read(), reply.headers
    finally:
        connection.close()


def _stderr_while_the_body_write_raises(
    case: unittest.TestCase, tree: Path, error: Exception
) -> str:
    """Serve one request whose body write raises `error`; return everything stderr received.

    The body write is where a real hang-up lands. The client must see its reply cut short,
    which proves the handler failed, so the test cannot pass on a request that never did. The
    server reports before it closes the socket, and the cut-short read waits on that close, so
    the report is complete once the read fails.
    """
    captured = io.StringIO()
    with mock.patch.object(
        http.server.SimpleHTTPRequestHandler, "copyfile", side_effect=error
    ):
        with contextlib.redirect_stderr(captured):
            with loopback_site.serve_directory(tree) as base_url:
                with case.assertRaises(http.client.HTTPException):
                    _get(base_url, urlsplit(base_url).netloc)
    return captured.getvalue()


@dataclasses.dataclass
class _RequestRecord:
    """What the server's request raised, and a signal set once its handler thread is done."""

    raised: list[Exception] = dataclasses.field(default_factory=list)
    finished: threading.Event = dataclasses.field(default_factory=threading.Event)


@contextlib.contextmanager
def _recording_requests() -> Iterator[_RequestRecord]:
    """Record what a request raised, and signal once its handler thread has fully finished.

    Leaving `serve_directory` is no such signal: `ThreadingHTTPServer` handles on daemon
    threads, and `server_close()` joins only non-daemon ones. `process_request_thread` returns
    only after `handle_error` and the socket close, so its return is the signal. Built for one
    request: the first handler thread to return sets it.
    """
    record = _RequestRecord()
    finish = socketserver.BaseServer.finish_request
    process = socketserver.ThreadingMixIn.process_request_thread

    def finish_recording(
        server: socketserver.BaseServer,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        try:
            finish(server, request, client_address)
        except Exception as error:
            record.raised.append(error)
            raise

    def process_signaling(
        server: socketserver.ThreadingMixIn,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        try:
            process(server, request, client_address)
        finally:
            record.finished.set()

    with (
        mock.patch.object(socketserver.BaseServer, "finish_request", finish_recording),
        mock.patch.object(
            socketserver.ThreadingMixIn, "process_request_thread", process_signaling
        ),
    ):
        yield record


def _reset_mid_reply(base_url: str, page: str) -> bytes:
    """Request `page`, read the start of the reply, then hang up with an RST; return what was read."""
    address = urlsplit(base_url)
    assert address.hostname is not None and address.port is not None, base_url
    with socket.create_connection(
        (address.hostname, address.port), timeout=TIMEOUT
    ) as client:
        request = f"GET {page} HTTP/1.1\r\nHost: {address.netloc}\r\n\r\n"
        client.sendall(request.encode("ascii"))
        start = client.recv(_STATUS_READ_BYTES)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, _RESET_ON_CLOSE)
    return start


class ServeTests(unittest.TestCase):
    """The server binds loopback only, serves the named directory, and stops."""

    def setUp(self) -> None:
        self.tree = scratch_tree(self)

    def test_binds_the_loopback_interface_only(self) -> None:
        with loopback_site.serve_directory(self.tree) as base_url:
            self.assertRegex(base_url, r"^http://127\.0\.0\.1:[1-9][0-9]*$")

    def test_serves_the_named_directory(self) -> None:
        with loopback_site.serve_directory(self.tree) as base_url:
            with urllib.request.urlopen(
                base_url + "/archive/", timeout=TIMEOUT
            ) as reply:
                served = reply.read()
        self.assertEqual(served, (self.tree / "archive" / "index.html").read_bytes())

    def test_keeps_its_access_log_off_stderr(self) -> None:
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with loopback_site.serve_directory(self.tree) as base_url:
                with urllib.request.urlopen(base_url + "/", timeout=TIMEOUT) as reply:
                    reply.read()
                _get(base_url, _FOREIGN_HOST)
        self.assertEqual(captured.getvalue(), "")

    def test_stops_serving_when_the_block_ends(self) -> None:
        with loopback_site.serve_directory(self.tree) as base_url:
            pass
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(base_url + "/", timeout=TIMEOUT)

    def test_stops_promptly_once_the_block_ends(self) -> None:
        # Every test that serves pays this exit, so a slow one is paid many times per run.
        exits: list[float] = []
        for _ in range(_EXIT_SAMPLES):
            with loopback_site.serve_directory(self.tree):
                block_ended = time.monotonic()
            exits.append(time.monotonic() - block_ended)
        self.assertLess(
            statistics.median(exits),
            _PROMPT_EXIT_SECONDS,
            f"leaving serve_directory took {[round(s, 3) for s in exits]} s",
        )

    def test_polls_for_shutdown_faster_than_a_prompt_exit_allows(self) -> None:
        # The deterministic half of the timing test above: the interval the serving loop was
        # actually handed, read by binding the real call against the stdlib signature, so the
        # default a bare call would get is derived rather than restated here.
        serve = socketserver.BaseServer.serve_forever
        with mock.patch.object(
            socketserver.BaseServer, "serve_forever", autospec=True, side_effect=serve
        ) as serving:
            with loopback_site.serve_directory(self.tree):
                pass
        self.assertEqual(serving.call_count, 1)
        call = inspect.signature(serve).bind(
            *serving.call_args.args, **serving.call_args.kwargs
        )
        call.apply_defaults()
        self.assertLess(call.arguments["poll_interval"], _PROMPT_EXIT_SECONDS)


class ConfirmTests(unittest.TestCase):
    """A page is confirmed only when the server answers with the file on disk."""

    def setUp(self) -> None:
        self.tree = scratch_tree(self)

    def test_page_paths_map_to_the_files_a_static_server_resolves(self) -> None:
        self.assertEqual(
            loopback_site.page_file(self.tree, "/"), self.tree / "index.html"
        )
        self.assertEqual(
            loopback_site.page_file(self.tree, "/archive/"),
            self.tree / "archive" / "index.html",
        )
        self.assertEqual(
            loopback_site.page_file(self.tree, "/404.html"), self.tree / "404.html"
        )

    def test_a_page_climbing_out_of_the_tree_is_refused_before_any_read(self) -> None:
        # The same lexical containment `not_found` has, with a real file just outside the
        # tree, so an escape cannot pass for want of a file.
        (self.tree.parent / "outside.html").write_bytes(b"outside\n")
        with loopback_site.serve_directory(self.tree) as base_url:
            for page in ("/../outside.html", "/x/../../outside.html"):
                with self.subTest(page=page):
                    with self.assertRaisesRegex(loopback_site.SiteServeError, page):
                        loopback_site.page_file(self.tree, page)
                    with self.assertRaises(loopback_site.SiteServeError):
                        loopback_site.confirm_serves(
                            base_url, self.tree, page, timeout=TIMEOUT
                        )

    def test_a_page_that_normalizes_into_the_tree_is_its_file(self) -> None:
        self.assertEqual(
            loopback_site.page_file(self.tree, "/x/../index.html"),
            self.tree / "x" / ".." / "index.html",
        )

    def test_confirms_every_page_of_the_tree(self) -> None:
        with loopback_site.serve_directory(self.tree) as base_url:
            for page in ("/", "/archive/", "/404.html"):
                loopback_site.confirm_serves(base_url, self.tree, page, timeout=TIMEOUT)

    def test_refuses_a_server_answering_from_another_tree(self) -> None:
        other = scratch_tree(self, "other")
        (other / "index.html").write_text(
            "<!doctype html><title>stale</title>\n", encoding="utf-8", newline="\n"
        )
        with loopback_site.serve_directory(other) as base_url:
            with self.assertRaises(loopback_site.SiteServeError) as caught:
                loopback_site.confirm_serves(base_url, self.tree, "/", timeout=TIMEOUT)
        self.assertIn("index.html", str(caught.exception))

    def test_a_page_missing_from_disk_is_an_os_error(self) -> None:
        with loopback_site.serve_directory(self.tree) as base_url:
            with self.assertRaises(OSError):
                loopback_site.confirm_serves(
                    base_url, self.tree, "/missing.html", timeout=TIMEOUT
                )


class HostTests(unittest.TestCase):
    """Only a Host naming this server's own loopback port is answered; nothing is shared cross-origin."""

    def setUp(self) -> None:
        self.tree = scratch_tree(self)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.base_url = stack.enter_context(loopback_site.serve_directory(self.tree))
        self.port = urlsplit(self.base_url).port
        self.index = (self.tree / "index.html").read_bytes()

    def test_answers_the_loopback_address_with_its_port(self) -> None:
        status, body, _ = _get(self.base_url, f"127.0.0.1:{self.port}")
        self.assertEqual((status, body), (200, self.index))

    def test_answers_localhost_with_its_port(self) -> None:
        status, body, _ = _get(self.base_url, f"localhost:{self.port}")
        self.assertEqual((status, body), (200, self.index))

    def test_refuses_a_foreign_host_without_sending_the_file(self) -> None:
        status, body, _ = _get(self.base_url, f"{_FOREIGN_HOST}:{self.port}")
        self.assertEqual(status, 403)
        self.assertNotIn(self.index, body)

    def test_refuses_a_loopback_name_without_this_port(self) -> None:
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host=host):
                status, body, _ = _get(self.base_url, host)
                self.assertEqual(status, 403)
                self.assertNotIn(self.index, body)

    def test_refuses_a_request_with_no_host(self) -> None:
        status, body, _ = _get(self.base_url, None)
        self.assertEqual(status, 403)
        self.assertNotIn(self.index, body)

    def test_sends_no_cors_header_on_an_answer_or_a_refusal(self) -> None:
        for host in (f"127.0.0.1:{self.port}", _FOREIGN_HOST):
            with self.subTest(host=host):
                _, _, headers = _get(self.base_url, host)
                cors = [
                    name
                    for name in headers.keys()
                    if name.lower().startswith("access-control-")
                ]
                self.assertEqual(cors, [])


class HangUpTests(unittest.TestCase):
    """A client hanging up mid-request is dropped quietly; every other handler failure still prints."""

    def setUp(self) -> None:
        self.tree = scratch_tree(self)

    def test_drops_a_client_hang_up_without_a_traceback(self) -> None:
        for hang_up in _CLIENT_HANG_UPS:
            with self.subTest(hang_up=hang_up.__name__):
                stderr = _stderr_while_the_body_write_raises(
                    self, self.tree, hang_up("the client hung up")
                )
                self.assertEqual(stderr, "")

    def test_still_reports_every_other_handler_failure(self) -> None:
        for failure in _OTHER_FAILURES:
            with self.subTest(failure=failure.__name__):
                stderr = _stderr_while_the_body_write_raises(
                    self, self.tree, failure("not a hang-up")
                )
                self.assertIn("Traceback", stderr)
                self.assertIn(f"{failure.__name__}: not a hang-up", stderr)

    @unittest.skipUnless(
        os.name == "posix", "the RST is forced through POSIX's struct linger"
    )
    def test_a_real_reset_mid_reply_leaves_stderr_silent(self) -> None:
        # The reset is real, not a raised stand-in: the client reads the start of the reply, so
        # the handler is known to be writing a body larger than the socket buffers, then closes
        # with an RST. Recording what the request raised means the test cannot pass because the
        # reset never reached the server, or reached it as some other error.
        with (self.tree / "big.bin").open("wb") as body:
            body.truncate(_OVERSIZED_BODY_BYTES)
        captured = io.StringIO()
        with _recording_requests() as record, contextlib.redirect_stderr(captured):
            with loopback_site.serve_directory(self.tree) as base_url:
                start = _reset_mid_reply(base_url, "/big.bin")
                # Inside the redirect, so a report the handler is still writing lands here.
                self.assertTrue(
                    record.finished.wait(TIMEOUT), "the handler never finished"
                )
        self.assertRegex(start, rb"^HTTP/1\.[01] 200 ")
        self.assertEqual(len(record.raised), 1, record.raised)
        self.assertIsInstance(record.raised[0], _CLIENT_HANG_UPS)
        self.assertEqual(captured.getvalue(), "")


class UnopenablePathTests(unittest.TestCase):
    """A request whose decoded path no file can have is a miss: a status, never a reset.

    `%00` decodes to NUL, and `%ED%A0%80` to a lone surrogate (the standard library decodes
    with `surrogatepass`); `open()` refuses both with a ValueError rather than the OSError the
    standard library turns into its 404, so without a guard the handler died, printed a
    traceback, and the client saw its connection reset.
    """

    _PAGES = ("/%00", "/x%00.html", "/%ED%A0%80", "/x%ED%A0%80.html")

    def setUp(self) -> None:
        self.tree = scratch_tree(self)

    def _answers_and_stderr(
        self, not_found: str | None
    ) -> tuple[list[tuple[int, bytes]], str]:
        """GET each unopenable page with the server's own Host; return the answers and all of stderr."""
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with loopback_site.serve_directory(
                self.tree, not_found=not_found
            ) as base_url:
                host = urlsplit(base_url).netloc
                answers = [_get(base_url, host, page)[:2] for page in self._PAGES]
        return answers, captured.getvalue()

    def test_an_unopenable_path_answers_404_without_a_traceback(self) -> None:
        answers, stderr = self._answers_and_stderr(None)
        self.assertEqual([status for status, _ in answers], [404] * len(self._PAGES))
        self.assertEqual(stderr, "")

    def test_an_unopenable_path_answers_the_named_not_found_file(self) -> None:
        answers, stderr = self._answers_and_stderr("/404.html")
        own = (self.tree / "404.html").read_bytes()
        self.assertEqual(answers, [(404, own)] * len(self._PAGES))
        self.assertEqual(stderr, "")
