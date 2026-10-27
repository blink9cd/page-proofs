"""Grouped suite: the library -- reading a served page's meta refresh.

A page that is a 0-second `<meta http-equiv="refresh">` navigates away as it loads, so a tool
that evaluates script in it races the navigation. `parse_meta_refresh` reads the first refresh
the document declares, the way a browser's "shared declarative refresh steps" read it, and
`read_meta_refresh` fetches the page over loopback first, so a caller can decide before any
browser opens it.
"""

from __future__ import annotations

import contextlib
import http.server
import sys
import threading
import unittest
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import loopback_site  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import TIMEOUT, scratch_tree  # noqa: E402 - beside this module, via the loader's top_level_dir

# The determinism convention: the coverage run executes this property too, so a randomized
# profile would make it flaky. deadline=None: a wall-clock deadline
# measures machine load, not correctness. too_slow is suppressed because a cold cache's first
# text draw pays Hypothesis's one-time setup.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: The address every pure case is read as, as a served page's would be.
_PAGE = "http://127.0.0.1:8000/archive/"

#: How often the charset server's loop looks for shutdown.
_SHUTDOWN_POLL_SECONDS = 0.05


def _page(content: str, *, extra: str = "") -> str:
    """A document whose head holds one refresh meta with `content`, after `extra`."""
    return (
        "<!doctype html><html><head>"
        f'{extra}<meta http-equiv="refresh" content="{content}">'
        "<title>t</title></head><body></body></html>"
    )


def _parse(content: str) -> loopback_site.MetaRefresh | None:
    """The refresh a page holding one meta with `content` declares, read as `_PAGE`."""
    return loopback_site.parse_meta_refresh(_page(content), _PAGE)


class ParseTests(unittest.TestCase):
    """The first refresh meta, read as the HTML spec reads it, resolved against the page."""

    def test_a_page_with_no_meta_declares_no_refresh(self) -> None:
        self.assertIsNone(
            loopback_site.parse_meta_refresh("<!doctype html><title>x</title>", _PAGE)
        )

    def test_a_meta_that_is_no_refresh_is_ignored(self) -> None:
        html = (
            '<meta charset="utf-8"><meta name="refresh" content="0; url=x">'
            '<meta http-equiv="content-type" content="text/html">'
            '<meta http-equiv="refresh">'
        )
        self.assertIsNone(loopback_site.parse_meta_refresh(html, _PAGE))

    def test_a_relative_url_resolves_against_the_page(self) -> None:
        self.assertEqual(
            _parse("0; url=2026/"),
            loopback_site.MetaRefresh(
                delay=0.0, destination="http://127.0.0.1:8000/archive/2026/"
            ),
        )

    def test_the_http_equiv_name_is_read_in_any_case(self) -> None:
        html = '<META HTTP-EQUIV="ReFresh" CONTENT="0; url=2026/">'
        refresh = loopback_site.parse_meta_refresh(html, _PAGE)
        self.assertIsNotNone(refresh)

    def test_a_quoted_url_ends_at_its_closing_quote(self) -> None:
        for content in ("0; url='2026/'trailing", '0; url="2026/"'):
            with self.subTest(content=content):
                refresh = loopback_site.parse_meta_refresh(
                    f"<meta http-equiv=refresh content={content!r}>"
                    if '"' in content
                    else _page(content),
                    _PAGE,
                )
                assert refresh is not None
                self.assertEqual(
                    refresh.destination, "http://127.0.0.1:8000/archive/2026/"
                )

    def test_an_unclosed_quote_runs_to_the_end(self) -> None:
        refresh = _parse("0; url='2026/")
        assert refresh is not None
        self.assertEqual(refresh.destination, "http://127.0.0.1:8000/archive/2026/")

    def test_url_is_read_in_any_case_with_space_around_the_equals(self) -> None:
        for content in ("0; URL=2026/", "0;Url = 2026/", "0,url=2026/", "0 url=2026/"):
            with self.subTest(content=content):
                refresh = _parse(content)
                assert refresh is not None
                self.assertEqual(
                    refresh.destination, "http://127.0.0.1:8000/archive/2026/"
                )

    def test_the_url_may_come_without_url_equals(self) -> None:
        refresh = _parse("0; /elsewhere/")
        assert refresh is not None
        self.assertEqual(refresh.destination, "http://127.0.0.1:8000/elsewhere/")

    def test_a_remainder_that_only_starts_like_url_is_the_url_whole(self) -> None:
        for name in ("ur.html", "url.html", "u.html"):
            with self.subTest(name=name):
                refresh = _parse(f"0; {name}")
                assert refresh is not None
                self.assertEqual(
                    refresh.destination, f"http://127.0.0.1:8000/archive/{name}"
                )

    def test_the_delay_is_the_whole_seconds(self) -> None:
        for content, delay in (
            ("5;url=x", 5.0),
            ("2.75; url=x", 2.0),
            (".5;url=x", 0.0),
        ):
            with self.subTest(content=content):
                refresh = _parse(content)
                assert refresh is not None
                self.assertEqual(refresh.delay, delay)
                self.assertEqual(refresh.destination, "http://127.0.0.1:8000/archive/x")

    def test_a_time_alone_refreshes_the_page_itself(self) -> None:
        for content in ("0", " 3 ", "0;", "0; url="):
            with self.subTest(content=content):
                refresh = _parse(content)
                assert refresh is not None
                self.assertEqual(refresh.destination, _PAGE)

    def test_a_content_the_steps_reject_is_no_refresh(self) -> None:
        for content in ("", "x; url=y", "5x; url=y", "; url=y", "url=y"):
            with self.subTest(content=content):
                self.assertIsNone(_parse(content))

    def test_a_url_the_url_parser_rejects_is_no_refresh(self) -> None:
        self.assertIsNone(_parse("0; url=http://[unclosed/"))

    def test_space_and_controls_around_the_url_are_dropped(self) -> None:
        refresh = loopback_site.parse_meta_refresh(
            '<meta http-equiv="refresh" content="0; url=20&#9;26/ &#1;">', _PAGE
        )
        assert refresh is not None
        self.assertEqual(refresh.destination, "http://127.0.0.1:8000/archive/2026/")

    def test_only_the_first_refresh_counts(self) -> None:
        html = _page(
            "0; url=second/",
            extra='<meta http-equiv="refresh" content="1; url=first/">',
        )
        self.assertEqual(
            loopback_site.parse_meta_refresh(html, _PAGE),
            loopback_site.MetaRefresh(
                delay=1.0, destination="http://127.0.0.1:8000/archive/first/"
            ),
        )

    def test_a_rejected_refresh_does_not_stop_a_later_one(self) -> None:
        # The spec sets the document's "will declaratively refresh" flag only when a refresh
        # parses, so a browser goes on to the next refresh meta after one it ignored.
        html = _page(
            "0; url=second/", extra='<meta http-equiv="refresh" content="soon">'
        )
        refresh = loopback_site.parse_meta_refresh(html, _PAGE)
        assert refresh is not None
        self.assertEqual(refresh.destination, "http://127.0.0.1:8000/archive/second/")

    def test_a_meta_inside_a_script_is_not_one(self) -> None:
        html = '<script>"<meta http-equiv=refresh content=0>"</script>'
        self.assertIsNone(loopback_site.parse_meta_refresh(html, _PAGE))

    @given(
        before=st.text(max_size=80),
        content=st.text(max_size=80),
        after=st.text(max_size=80),
    )
    def test_fuzz_parse_meta_refresh(
        self, before: str, content: str, after: str
    ) -> None:
        """Never raises; whatever it returns names an absolute destination."""
        escaped = content.replace("&", "&amp;").replace('"', "&quot;")
        for html in (
            f'{before}<meta http-equiv="refresh" content="{escaped}">{after}',
            before + content + after,
        ):
            refresh = loopback_site.parse_meta_refresh(html, _PAGE)
            if refresh is not None:
                self.assertTrue(urlsplit(refresh.destination).scheme)
                self.assertGreaterEqual(refresh.delay, 0.0)


def _charset_handler(
    content_type: str, body: bytes
) -> type[http.server.BaseHTTPRequestHandler]:
    """A handler answering every page with `body`, labeled `content_type`."""

    class _Labeled(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Keep the access log off the suite's stderr."""

    return _Labeled


@contextlib.contextmanager
def _labeled_server(content_type: str, body: bytes) -> Iterator[str]:
    """Serve `body` as `content_type` on loopback for the block; yield its base url."""
    server = http.server.ThreadingHTTPServer(
        (loopback_site.LOOPBACK_HOST, 0), _charset_handler(content_type, body)
    )
    worker = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": _SHUTDOWN_POLL_SECONDS},
        daemon=True,
    )
    worker.start()
    try:
        yield f"http://{loopback_site.LOOPBACK_HOST}:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=TIMEOUT)


class ReadTests(unittest.TestCase):
    """The page is fetched over loopback, decoded, and parsed; nothing else is fetched."""

    def setUp(self) -> None:
        self.tree = scratch_tree(self)
        stub = self.tree / "archive" / "index.html"
        stub.write_text(_page("0; url=2026/"), encoding="utf-8", newline="\n")

    def test_a_served_stub_names_its_destination(self) -> None:
        with loopback_site.serve_directory(self.tree) as base_url:
            refresh = loopback_site.read_meta_refresh(
                f"{base_url}/archive/", timeout=TIMEOUT
            )
            plain = loopback_site.read_meta_refresh(f"{base_url}/", timeout=TIMEOUT)
        self.assertEqual(
            refresh,
            loopback_site.MetaRefresh(
                delay=0.0, destination=f"{base_url}/archive/2026/"
            ),
        )
        self.assertIsNone(plain)

    def test_the_localhost_name_is_loopback_too(self) -> None:
        with loopback_site.serve_directory(self.tree) as base_url:
            port = urlsplit(base_url).port
            refresh = loopback_site.read_meta_refresh(
                f"http://localhost:{port}/archive/", timeout=TIMEOUT
            )
        assert refresh is not None
        self.assertEqual(refresh.destination, f"http://localhost:{port}/archive/2026/")

    def test_an_error_status_is_raised_unchanged(self) -> None:
        with (
            loopback_site.serve_directory(self.tree) as base_url,
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            loopback_site.read_meta_refresh(f"{base_url}/missing/", timeout=TIMEOUT)
        with caught.exception:
            self.assertEqual(caught.exception.code, 404)

    def test_a_redirect_is_not_followed(self) -> None:
        # Followed, an HTTP redirect could lead off loopback; declined, it is raised as the
        # status it is, for the caller's own confirmation to report.
        with (
            loopback_site.serve_directory(self.tree) as base_url,
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            loopback_site.read_meta_refresh(f"{base_url}/archive", timeout=TIMEOUT)
        with caught.exception:
            self.assertEqual(caught.exception.code, 301)

    def test_the_response_charset_decodes_the_page(self) -> None:
        body = _page("0; url=café/").encode("latin-1")
        with _labeled_server("text/html; charset=ISO-8859-1", body) as base_url:
            refresh = loopback_site.read_meta_refresh(f"{base_url}/", timeout=TIMEOUT)
        assert refresh is not None
        self.assertEqual(refresh.destination, f"{base_url}/café/")

    def test_an_unknown_or_missing_charset_decodes_as_utf8(self) -> None:
        body = _page("0; url=café/").encode("utf-8")
        for content_type in (
            "text/html; charset=no-such-codec",
            "text/html; charset=rot13",
            "text/html",
        ):
            with (
                self.subTest(content_type=content_type),
                _labeled_server(content_type, body) as base_url,
            ):
                refresh = loopback_site.read_meta_refresh(
                    f"{base_url}/", timeout=TIMEOUT
                )
                assert refresh is not None
                self.assertEqual(refresh.destination, f"{base_url}/café/")

    def test_undecodable_bytes_do_not_raise(self) -> None:
        body = b'<meta http-equiv="refresh" content="0; url=x/">\xff\xfe'
        with _labeled_server("text/html; charset=utf-8", body) as base_url:
            refresh = loopback_site.read_meta_refresh(f"{base_url}/", timeout=TIMEOUT)
        assert refresh is not None
        self.assertEqual(refresh.destination, f"{base_url}/x/")

    def test_a_url_off_loopback_is_refused_before_any_request(self) -> None:
        refused = (
            "http://192.0.2.1/archive/",
            "http://example.invalid/",
            "https://127.0.0.1.example.invalid/",
            "http://127.0.0.1@evil.example/",
            "https://127.0.0.1/",
            "file:///archive/index.html",
            "ftp://127.0.0.1/archive/",
            "/archive/",
        )
        with mock.patch("urllib.request.OpenerDirector.open") as opened:
            for url in refused:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    loopback_site.read_meta_refresh(url, timeout=TIMEOUT)
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
