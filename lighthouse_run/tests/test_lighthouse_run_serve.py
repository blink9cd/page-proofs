"""Grouped suite: serving `src/` on loopback, and confirming what the server answers with.

The server itself is the shared `loopback_site/`, which carries its own suite. These tests stay
because they pin lighthouse_run's contract with that dependency, reached at the same fixed
sibling path the runner's bootstrap uses.
"""

# gate-serves: none (serves only temporary trees built at test time)

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

# The shared loopback server, at the fixed sibling path the runner's bootstrap names, put just
# after the entry at the front, ahead of every installed distribution, so none of the same name
# can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "loopback_site"))

import loopback_site
from _lighthouse_run_fixtures import PAGE_FILES, write_pages

_TIMEOUT = 5.0


class ServeTests(unittest.TestCase):
    """The server binds loopback only, serves the named directory, and stops."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.src = Path(scratch.name) / "src"
        write_pages(self.src)

    def test_binds_the_loopback_interface_only(self) -> None:
        with loopback_site.serve_directory(self.src) as base_url:
            self.assertRegex(base_url, r"^http://127\.0\.0\.1:[1-9][0-9]*$")

    def test_serves_the_named_directory(self) -> None:
        with loopback_site.serve_directory(self.src) as base_url:
            with urllib.request.urlopen(
                base_url + "/archive/", timeout=_TIMEOUT
            ) as reply:
                served = reply.read()
        self.assertEqual(served, (self.src / "archive" / "index.html").read_bytes())

    def test_keeps_its_access_log_off_stderr(self) -> None:
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with loopback_site.serve_directory(self.src) as base_url:
                with urllib.request.urlopen(base_url + "/", timeout=_TIMEOUT) as reply:
                    reply.read()
        self.assertEqual(captured.getvalue(), "")

    def test_stops_serving_when_the_block_ends(self) -> None:
        with loopback_site.serve_directory(self.src) as base_url:
            pass
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(base_url + "/", timeout=_TIMEOUT)


class ConfirmTests(unittest.TestCase):
    """A page is confirmed only when the server answers with the file on disk."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.src = self.root / "src"
        write_pages(self.src)

    def test_page_paths_map_to_the_files_the_server_resolves(self) -> None:
        self.assertEqual(
            loopback_site.page_file(self.src, "/"), self.src / "index.html"
        )
        self.assertEqual(
            loopback_site.page_file(self.src, "/archive/"),
            self.src / "archive" / "index.html",
        )
        self.assertEqual(
            loopback_site.page_file(self.src, "/404.html"), self.src / "404.html"
        )

    def test_confirms_every_default_page(self) -> None:
        with loopback_site.serve_directory(self.src) as base_url:
            for page in ("/", "/archive/", "/404.html"):
                loopback_site.confirm_serves(base_url, self.src, page, timeout=_TIMEOUT)
        self.assertEqual(len(PAGE_FILES), 3)

    def test_refuses_a_server_answering_from_another_tree(self) -> None:
        other = self.root / "other"
        write_pages(other)
        (other / "index.html").write_text(
            "<!doctype html><title>stale</title>\n", encoding="utf-8", newline="\n"
        )
        with loopback_site.serve_directory(other) as base_url:
            with self.assertRaises(loopback_site.SiteServeError) as caught:
                loopback_site.confirm_serves(base_url, self.src, "/", timeout=_TIMEOUT)
        self.assertIn("index.html", str(caught.exception))

    def test_a_page_missing_from_disk_is_an_os_error(self) -> None:
        with loopback_site.serve_directory(self.src) as base_url:
            with self.assertRaises(OSError):
                loopback_site.confirm_serves(
                    base_url, self.src, "/missing.html", timeout=_TIMEOUT
                )


if __name__ == "__main__":
    unittest.main()
