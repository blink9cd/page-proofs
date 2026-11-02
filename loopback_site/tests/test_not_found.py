"""Grouped suite: answering a miss with a named 404 file, the way GitHub Pages serves one.

Covers `serve_directory(..., not_found=...)` and what it answers, its refusal of a file that is
not in the tree, `confirm_serves(..., not_found=...)`, the `--not-found` option, and the pin on
the default: without `not_found`, a miss is still Python's own script-free error page, which
a caller may depend on.
"""

# gate-serves: none (serves only temporary trees built at test time)

from __future__ import annotations

import contextlib
import functools
import http.client
import http.server
import io
import os
import signal
import socket
import sys
import threading
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import loopback_site  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    TIMEOUT,
    restore_stop_dispositions,
    scratch_tree,
)

#: The server path every test names as the not-found page: the generic tree's own 404 file.
_NOT_FOUND = "/404.html"

#: The Content-Type a custom 404 is answered with: a site's HTML 404 page, written in UTF-8,
#: named as both rather than leaving the charset to sniffing.
_HTML = "text/html; charset=utf-8"

#: Paths the generic tree has no file for, in each shape a miss takes: a file, an extensionless
#: name, a directory path that does not exist, another letter case, a miss below a directory
#: that does exist, and a file asked for with a trailing slash.
_MISSES = (
    "/missing.html",
    "/future",
    "/future/",
    "/FUTURE",
    "/archive/missing.html",
    "/404.html/",
)

#: A foreign name for the Host header. `.example` is reserved, so it can never name a real host.
_FOREIGN_HOST = "rebound.example"

#: How much one read of a raw socket asks for; the whole answer is read to the close anyway.
_READ_BYTES = 65536

#: How often the comparison server's loop looks for its shutdown request.
_STOCK_POLL_SECONDS = 0.05


def _ask(
    base_url: str, page: str, *, host: str | None = None
) -> tuple[int, bytes, http.client.HTTPMessage]:
    """GET `page`, following no redirect; return status, body and headers.

    `host` replaces the Host header http.client would send for the server's own address.
    """
    connection = http.client.HTTPConnection(
        loopback_site.LOOPBACK_HOST, urlsplit(base_url).port, timeout=TIMEOUT
    )
    try:
        connection.request("GET", page, headers={} if host is None else {"Host": host})
        reply = connection.getresponse()
        return reply.status, reply.read(), reply.headers
    finally:
        connection.close()


def _head_on_the_wire(base_url: str, page: str) -> bytes:
    """Send a HEAD for `page` over a bare socket; return every byte the server sends back.

    http.client hands back an empty body for any HEAD, whatever followed the headers, so it
    cannot see a body sent by mistake. The handler speaks HTTP/1.0 and closes after one
    answer, so everything up to the close is the whole answer.
    """
    port = urlsplit(base_url).port
    request = (
        f"HEAD {page} HTTP/1.0\r\nHost: {loopback_site.LOOPBACK_HOST}:{port}\r\n\r\n"
    )
    received = bytearray()
    with socket.create_connection(
        (loopback_site.LOOPBACK_HOST, port), timeout=TIMEOUT
    ) as connection:
        connection.sendall(request.encode("ascii"))
        while chunk := connection.recv(_READ_BYTES):
            received += chunk
    return bytes(received)


def _answer(base_url: str, page: str) -> tuple[int, str | None, bytes]:
    """The status, Content-Type and body a GET of `page` is answered with."""
    status, body, headers = _ask(base_url, page)
    return status, headers.get("Content-Type"), body


class _StockHandler(http.server.SimpleHTTPRequestHandler):
    """Python's own static-file handler, unchanged except that it keeps its access log quiet."""

    def log_message(self, format: str, *args: object) -> None:
        """Drop the access-log line, so a comparison run leaves stderr as it found it."""


@contextlib.contextmanager
def _stock_server(directory: Path) -> Iterator[str]:
    """Serve `directory` with Python's own handler on loopback for the block; yield its url.

    The reference for "unchanged": what the loopback server answered before `not_found`
    existed is what the standard library answers, so the pin compares the two directly rather
    than restating the error page's text here, where it would go stale with the stdlib.
    """
    handler = functools.partial(_StockHandler, directory=str(directory))
    with http.server.ThreadingHTTPServer(
        (loopback_site.LOOPBACK_HOST, 0), handler
    ) as server:
        worker = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": _STOCK_POLL_SECONDS},
            daemon=True,
        )
        worker.start()
        try:
            yield f"http://{loopback_site.LOOPBACK_HOST}:{server.server_port}"
        finally:
            server.shutdown()
            worker.join(timeout=TIMEOUT)


class _ServingTestCase(unittest.TestCase):
    """A scratch tree, and a helper serving it for the rest of the test."""

    def setUp(self) -> None:
        self.tree = scratch_tree(self)
        self.not_found_bytes = (self.tree / "404.html").read_bytes()

    def _serve(self, directory: Path, not_found: str | None = _NOT_FOUND) -> str:
        """Serve `directory` until the test ends; return the base url."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        return stack.enter_context(
            loopback_site.serve_directory(directory, not_found=not_found)
        )


class NotFoundAnswerTests(_ServingTestCase):
    """With `not_found`, a path with no file is answered 404 with that file's bytes, as HTML."""

    def test_a_miss_answers_404_with_the_named_file_as_html(self) -> None:
        base_url = self._serve(self.tree)
        for page in _MISSES:
            with self.subTest(page=page):
                self.assertEqual(
                    _answer(base_url, page), (404, _HTML, self.not_found_bytes)
                )

    def test_a_directory_without_an_index_answers_the_named_file_not_a_listing(
        self,
    ) -> None:
        # GitHub Pages lists no directory, so a local server that did would show a page the
        # hosted site never could.
        (self.tree / "empty").mkdir()
        base_url = self._serve(self.tree)
        self.assertEqual(
            _answer(base_url, "/empty/"), (404, _HTML, self.not_found_bytes)
        )

    def test_a_directory_without_its_slash_keeps_the_redirect(self) -> None:
        base_url = self._serve(self.tree)
        status, _, headers = _ask(base_url, "/archive")
        self.assertEqual((status, headers.get("Location")), (301, "/archive/"))

    def test_a_file_that_exists_is_served_as_before(self) -> None:
        base_url = self._serve(self.tree)
        for page in ("/", "/archive/", _NOT_FOUND):
            with self.subTest(page=page):
                status, body, _ = _ask(base_url, page)
                expected = loopback_site.page_file(self.tree, page).read_bytes()
                self.assertEqual((status, body), (200, expected))

    def test_a_head_miss_sends_the_length_without_the_body(self) -> None:
        base_url = self._serve(self.tree)
        answer = _head_on_the_wire(base_url, "/missing.html")
        head, blank_line, after = answer.partition(b"\r\n\r\n")
        status_line, _, header_lines = head.partition(b"\r\n")
        headers = http.client.parse_headers(io.BytesIO(header_lines + b"\r\n\r\n"))
        self.assertEqual(status_line.split()[1:2], [b"404"])
        self.assertEqual(
            (headers.get("Content-Length"), blank_line, after),
            (str(len(self.not_found_bytes)), b"\r\n\r\n", b""),
        )

    def test_a_foreign_host_is_still_refused_before_any_file(self) -> None:
        base_url = self._serve(self.tree)
        status, body, _ = _ask(base_url, "/missing.html", host=_FOREIGN_HOST)
        self.assertEqual(status, 403)
        self.assertNotIn(self.not_found_bytes, body)

    def test_answers_with_the_file_as_it_is_when_asked(self) -> None:
        # Read per request, like every other file, so an edit shows on the next load.
        base_url = self._serve(self.tree)
        edited = b"<!doctype html><title>edited</title>\n"
        (self.tree / "404.html").write_bytes(edited)
        self.assertEqual(_answer(base_url, "/missing.html"), (404, _HTML, edited))

    def test_a_named_file_removed_while_serving_falls_back_to_pythons_page(
        self,
    ) -> None:
        base_url = self._serve(self.tree)
        (self.tree / "404.html").unlink()
        with _stock_server(self.tree) as stock_url:
            expected = _answer(stock_url, "/missing.html")
        self.assertEqual(expected[0], 404)
        self.assertEqual(_answer(base_url, "/missing.html"), expected)


class NotFoundRefusalTests(unittest.TestCase):
    """A `not_found` naming no file inside the tree is refused before a server starts."""

    def setUp(self) -> None:
        self.tree = scratch_tree(self)
        # A real file just outside the tree, so an escape cannot pass for want of a file.
        (self.tree.parent / "outside.html").write_bytes(b"outside\n")

    def test_names_no_file_in_the_tree_raises_before_serving(self) -> None:
        for not_found in (
            "/absent.html",
            "/archive",
            "/archive/missing/",
            "/../outside.html",
            "/archive/../../outside.html",
        ):
            with self.subTest(not_found=not_found):
                with mock.patch.object(loopback_site, "_DeepQueueServer") as server:
                    with self.assertRaises(loopback_site.SiteServeError) as caught:
                        with loopback_site.serve_directory(
                            self.tree, not_found=not_found
                        ):
                            pass
                server.assert_not_called()
                self.assertIn(not_found, str(caught.exception))

    def test_a_path_that_normalizes_into_the_tree_is_accepted(self) -> None:
        with loopback_site.serve_directory(
            self.tree, not_found="/archive/../404.html"
        ) as base_url:
            status, body, _ = _ask(base_url, "/missing.html")
        self.assertEqual((status, body), (404, (self.tree / "404.html").read_bytes()))

    def test_a_climb_through_a_directory_that_does_not_exist_is_accepted(
        self,
    ) -> None:
        # The handler normalizes a request path without asking the disk, so /x/../404.html
        # names /404.html whether or not an x/ exists, and so does the not-found path.
        with loopback_site.serve_directory(
            self.tree, not_found="/x/../404.html"
        ) as base_url:
            status, body, _ = _ask(base_url, "/missing.html")
        self.assertEqual((status, body), (404, (self.tree / "404.html").read_bytes()))

    @unittest.skipUnless(
        os.name == "posix", "creating a symlink needs a privilege Windows withholds"
    )
    def test_a_climb_through_a_symlink_serves_the_trees_own_file(self) -> None:
        # link/ points at a directory outside the tree whose parent holds a decoy 404.html,
        # so the OS, following link/ and then .., reaches the decoy. Normalized first, the
        # path is the tree's own /404.html: the file a request for the same path is answered
        # with, and the one file the server checks, serves and confirms against.
        outside = self.tree.parent / "elsewhere"
        (outside / "deep").mkdir(parents=True)
        (outside / "404.html").write_bytes(b"decoy outside the tree\n")
        (self.tree / "link").symlink_to(outside / "deep", target_is_directory=True)
        own = (self.tree / "404.html").read_bytes()
        not_found = "/link/../404.html"
        with loopback_site.serve_directory(self.tree, not_found=not_found) as base_url:
            self.assertEqual(_ask(base_url, not_found)[:2], (200, own))
            self.assertEqual(_ask(base_url, "/missing.html")[:2], (404, own))
            loopback_site.confirm_serves(
                base_url,
                self.tree,
                "/missing.html",
                timeout=TIMEOUT,
                not_found=not_found,
            )


class DefaultMissTests(unittest.TestCase):
    """Without `not_found`, misses and listings are exactly the standard library's own.

    A caller may load a missing path and require the page it lands on to run no script,
    which Python's own error page guarantees and a site's 404 page need not.
    """

    def setUp(self) -> None:
        self.tree = scratch_tree(self)

    def test_a_miss_keeps_pythons_own_script_free_error_page(self) -> None:
        with (
            loopback_site.serve_directory(self.tree) as base_url,
            _stock_server(self.tree) as stock_url,
        ):
            for page in ("/missing.html", "/.print-pass-storage-seed"):
                with self.subTest(page=page):
                    served = _answer(base_url, page)
                    self.assertEqual(served, _answer(stock_url, page))
                    self.assertEqual(served[0], 404)
                    self.assertNotIn(b"<script", served[2].lower())

    def test_a_directory_without_an_index_is_still_listed(self) -> None:
        (self.tree / "empty").mkdir()
        with (
            loopback_site.serve_directory(self.tree) as base_url,
            _stock_server(self.tree) as stock_url,
        ):
            served = _answer(base_url, "/empty/")
            self.assertEqual(served, _answer(stock_url, "/empty/"))
        self.assertEqual(served[0], 200)


class ConfirmNotFoundTests(_ServingTestCase):
    """`confirm_serves` with `not_found` requires a page with no file to answer that file, 404."""

    def _confirm(self, base_url: str, page: str) -> None:
        loopback_site.confirm_serves(
            base_url, self.tree, page, timeout=TIMEOUT, not_found=_NOT_FOUND
        )

    def test_confirms_a_missing_page_answered_with_the_named_file(self) -> None:
        base_url = self._serve(self.tree)
        for page in ("/future", "/future/", "/missing.html"):
            with self.subTest(page=page):
                self._confirm(base_url, page)

    def test_confirms_a_page_with_a_file_as_before(self) -> None:
        base_url = self._serve(self.tree)
        for page in ("/", "/archive/", _NOT_FOUND):
            with self.subTest(page=page):
                self._confirm(base_url, page)

    def test_refuses_a_missing_page_answered_with_pythons_page(self) -> None:
        base_url = self._serve(self.tree, not_found=None)
        with self.assertRaises(loopback_site.SiteServeError) as caught:
            self._confirm(base_url, "/future")
        self.assertIn("404.html", str(caught.exception))

    def test_refuses_a_missing_page_answered_with_another_trees_404(self) -> None:
        other = scratch_tree(self, "other")
        (other / "404.html").write_bytes(b"<!doctype html><title>stale</title>\n")
        base_url = self._serve(other)
        with self.assertRaises(loopback_site.SiteServeError):
            self._confirm(base_url, "/future")

    def test_refuses_a_missing_page_the_server_answers_with_a_page(self) -> None:
        other = scratch_tree(self, "other")
        (other / "future").mkdir()
        (other / "future" / "index.html").write_bytes(b"a page\n")
        base_url = self._serve(other)
        with self.assertRaises(loopback_site.SiteServeError) as caught:
            self._confirm(base_url, "/future/")
        self.assertIn("200", str(caught.exception))

    def test_refuses_the_named_files_bytes_answered_200(self) -> None:
        # The bytes match exactly, so only the status can tell this page from a miss: a
        # confirmation that compared the bytes alone would pass it.
        other = scratch_tree(self, "other")
        (other / "future").mkdir()
        (other / "future" / "index.html").write_bytes(self.not_found_bytes)
        base_url = self._serve(other)
        with self.assertRaises(loopback_site.SiteServeError) as caught:
            self._confirm(base_url, "/future/")
        self.assertIn("it answered 200", str(caught.exception))

    def test_refuses_a_directory_without_its_slash_as_the_redirect_it_answers(
        self,
    ) -> None:
        # Followed, /archive's 301 would be reported as /archive/'s 200, and /empty's would pass as
        # /empty/'s 404 although the 404 page then reads another address than the one named.
        (self.tree / "empty").mkdir()
        base_url = self._serve(self.tree)
        for page in ("/archive", "/empty"):
            with self.subTest(page=page):
                with self.assertRaises(loopback_site.SiteServeError) as caught:
                    self._confirm(base_url, page)
                self.assertIn("it answered 301", str(caught.exception))

    def test_a_named_file_missing_from_disk_is_an_os_error(self) -> None:
        base_url = self._serve(self.tree)
        with self.assertRaises(OSError):
            loopback_site.confirm_serves(
                base_url,
                self.tree,
                "/future",
                timeout=TIMEOUT,
                not_found="/absent.html",
            )


class NotFoundCliTests(unittest.TestCase):
    """`--not-found` is opt-in, reaches the server and every confirmation, and exits 2 if absent."""

    def setUp(self) -> None:
        restore_stop_dispositions(self)
        self.tree = scratch_tree(self)

    def _run(self, *extra: str) -> tuple[int, str, str]:
        """Run `main` over the scratch tree, stopping as soon as it announces; return the result."""
        stop = loopback_site.StopFlag()
        stop.request(signal.SIGTERM, None)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = loopback_site.main(
                ["--directory", str(self.tree), *extra], stop=stop
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_not_found_is_off_by_default(self) -> None:
        args = loopback_site.build_parser().parse_args(["--directory", "site"])
        self.assertIsNone(args.not_found)

    def test_takes_a_server_path(self) -> None:
        args = loopback_site.build_parser().parse_args(
            ["--directory", "site", "--not-found", _NOT_FOUND]
        )
        self.assertEqual(args.not_found, _NOT_FOUND)

    def test_refuses_a_not_found_without_a_leading_slash(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                loopback_site.build_parser().parse_args(
                    ["--directory", "site", "--not-found", "404.html"]
                )
        self.assertEqual(caught.exception.code, 2)
        # The server-path refusal itself, not argparse's "unrecognized arguments", which
        # would also name the value.
        self.assertIn("'404.html' is not a server path", stderr.getvalue())

    def test_a_not_found_naming_no_file_exits_two_before_announcing(self) -> None:
        code, stdout, stderr = self._run("--not-found", "/absent.html")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("/absent.html", stderr)

    def test_confirms_missing_pages_against_the_named_file(self) -> None:
        # /future passes only if the server answers it with 404.html AND the confirmation
        # expects that: without either half, the page has no file and is not confirmed.
        code, stdout, stderr = self._run(
            "--not-found", _NOT_FOUND, "--pages", "/", "/future"
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(len(stdout.splitlines()), 1)

    def test_without_it_a_missing_page_is_still_not_confirmed(self) -> None:
        code, stdout, stderr = self._run("--pages", "/future")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("/future", stderr)


if __name__ == "__main__":
    unittest.main()
