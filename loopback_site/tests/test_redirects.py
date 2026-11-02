"""Grouped suite: the library, on a page answered with a redirect.

`confirm_serves` follows a redirect by default. With `follow_redirects=False` it refuses the page,
naming its status and never the address the redirect points at, and the not-found check reads a
redirect as the status it is. Neither reads a malformed `Location` before declining it.
"""

# gate-serves: none (serves only temporary trees built at test time)

from __future__ import annotations

import contextlib
import http.server
import sys
import threading
import unittest
import urllib.request
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import loopback_site  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import TIMEOUT, scratch_tree  # noqa: E402 - beside this module, via the loader's top_level_dir


#: The page a redirect leads to, byte-identical to the file the redirected page names.
_MOVED_BYTES = b"<!doctype html><title>moved</title>\n"

#: A redirect target the url parser refuses: a bracketed host that is no IP address. Its
#: placeholder name is what a message quoting the header would be caught by.
_MALFORMED_LOCATION = "http://[placeholder-target]/"

#: Every redirect status urllib's own handler follows, read from that handler, so a status it
#: learns to follow is tried here too. The declining handler needs an alias for each: a status
#: it has none for inherits the base handler's, which reads `Location` and follows.
_FOLLOWED_STATUSES = tuple(
    status.value
    for status in http.HTTPStatus
    if hasattr(urllib.request.HTTPRedirectHandler, f"http_error_{status.value}")
)

#: The statuses urllib followed when the declining handler was written. Restated, not
#: derived: the derivation above must still find each, or the tests below try too few.
_KNOWN_FOLLOWED_STATUSES = frozenset({301, 302, 303, 307, 308})

#: How often a redirecting server's loop looks for shutdown, which each test waits on.
_SHUTDOWN_POLL_SECONDS = 0.05


def _redirecting_handler(
    status: int, location: str
) -> type[http.server.BaseHTTPRequestHandler]:
    """A handler answering every page with a `status` redirect to `location`."""

    class _Redirecting(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            """Keep the access log off the suite's stderr."""

    return _Redirecting


@contextlib.contextmanager
def _redirect_server(status: int, location: str) -> Iterator[str]:
    """Serve every page as a `status` redirect to `location` for the block; yield its base url."""
    server = http.server.ThreadingHTTPServer(
        (loopback_site.LOOPBACK_HOST, 0), _redirecting_handler(status, location)
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


class RedirectTests(unittest.TestCase):
    """A redirect is followed by default; with `follow_redirects=False` it refuses the page.

    The host tree holds `moved/` as a directory, whose index holds exactly the bytes of the
    expected tree's FILE `moved`. A static server answers `/moved` with a 301 to `/moved/`, and
    this suite's redirecting server answers it with each status urllib follows, to the same
    place. Followed, the page confirms; refused, the same bytes cannot rescue it.
    """

    def setUp(self) -> None:
        self.host = scratch_tree(self, "host")
        (self.host / "moved").mkdir()
        (self.host / "moved" / "index.html").write_bytes(_MOVED_BYTES)
        self.expected = scratch_tree(self, "expected")
        (self.expected / "moved").write_bytes(_MOVED_BYTES)

    def confirm(self, base_url: str, page: str, *, follow_redirects: bool) -> None:
        """Confirm `page` at `base_url` against the expected tree."""
        loopback_site.confirm_serves(
            base_url,
            self.expected,
            page,
            timeout=TIMEOUT,
            follow_redirects=follow_redirects,
        )

    def refusal(self, status: int, location: str) -> str:
        """Why `/moved` is refused, unfollowed, when it answers `status` to `location`."""
        with (
            _redirect_server(status, location) as base_url,
            self.assertRaises(loopback_site.SiteServeError) as caught,
        ):
            self.confirm(base_url, "/moved", follow_redirects=False)
        return str(caught.exception)

    def test_every_status_urllib_follows_is_tried(self) -> None:
        self.assertLessEqual(_KNOWN_FOLLOWED_STATUSES, set(_FOLLOWED_STATUSES))

    def test_a_redirect_is_followed_by_default_and_when_asked(self) -> None:
        with loopback_site.serve_directory(self.host) as base_url:
            loopback_site.confirm_serves(
                base_url, self.expected, "/moved", timeout=TIMEOUT
            )
            self.confirm(base_url, "/moved", follow_redirects=True)
            # Followed, every status lands on the matching bytes, so in the test below only
            # declining a redirect can refuse the page.
            for status in _FOLLOWED_STATUSES:
                with (
                    self.subTest(status=status),
                    _redirect_server(status, f"{base_url}/moved/") as redirecting,
                ):
                    self.confirm(redirecting, "/moved", follow_redirects=True)

    def test_a_redirect_is_refused_naming_the_page_and_status_not_its_target(
        self,
    ) -> None:
        # Every status, each declined by an alias of its own: a status left without one is
        # followed to the matching bytes, and the page confirms.
        with loopback_site.serve_directory(self.host) as base_url:
            for status in _FOLLOWED_STATUSES:
                with self.subTest(status=status):
                    message = self.refusal(status, f"{base_url}/moved/")
                    self.assertIn("/moved", message)
                    self.assertIn(str(status), message)
                    # Where a redirect points is never echoed: its host or its path.
                    self.assertNotIn(base_url, message)
                    self.assertNotIn("/moved/", message)

    def test_a_page_answered_directly_confirms_without_following(self) -> None:
        with loopback_site.serve_directory(self.host) as base_url:
            self.confirm(base_url, "/archive/", follow_redirects=False)

    def test_an_error_status_stays_an_os_error_without_following(self) -> None:
        # The miss answers 404 with exactly the expected bytes, so a check that compared an
        # error status's body instead of refusing it would pass this page.
        (self.expected / "gone.html").write_bytes((self.host / "404.html").read_bytes())
        serving = loopback_site.serve_directory(self.host, not_found="/404.html")
        with serving as base_url:
            with self.assertRaises(OSError):
                self.confirm(base_url, "/gone.html", follow_redirects=False)

    def test_a_redirect_to_a_malformed_address_is_refused_without_quoting_it(
        self,
    ) -> None:
        # urllib parses a redirect's address before any handler may decline to follow it,
        # so a malformed one escaped as a ValueError quoting part of the header: as it
        # would again for a status left without an alias declining it.
        for status in _FOLLOWED_STATUSES:
            with self.subTest(status=status):
                message = self.refusal(status, _MALFORMED_LOCATION)
                self.assertIn(str(status), message)
                self.assertNotIn("placeholder-target", message)

    def test_a_malformed_redirect_is_a_status_where_a_404_is_due(self) -> None:
        # The not-found check reads every status, a redirect's included, and compares it.
        for status in _FOLLOWED_STATUSES:
            with (
                self.subTest(status=status),
                _redirect_server(status, _MALFORMED_LOCATION) as base_url,
            ):
                with self.assertRaises(loopback_site.SiteServeError) as caught:
                    loopback_site.confirm_serves(
                        base_url,
                        self.expected,
                        "/absent.html",
                        timeout=TIMEOUT,
                        not_found="/404.html",
                    )
                message = str(caught.exception)
                self.assertIn(f"(it answered {status})", message)
                self.assertNotIn("placeholder-target", message)
