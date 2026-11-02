"""Grouped suite: the CLI with a targets file's `not_found` (refused, served, and passed on).

`not_found` serves the tree the way GitHub Pages serves a site, a request with no file answered
with the site's own 404 page and status 404, so a page with no file can be measured. These tests
drive the CLI through `_cli_fixtures.FakeEngine`: the key is refused without `--serve`, and when
it names no file; with it, the served tree answers a missing page with that file, every page is
confirmed, and the engine is told which pages have no file. What the engine does with them is
`test_pass_not_found.py`'s.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's folder on
# sys.path, and the CLI it imports adds the tool's src/, for every import after it.
from _cli_fixtures import TARGETS, CliCase

import unittest
import urllib.error
import urllib.request
from typing import Any
from unittest import mock

import browser_pass

#: What the scratch project's not-found page holds.
_NOT_FOUND_PAGE = b"<p>not here</p>\n"

#: The key, as a targets file writes it.
_NOT_FOUND_KEY = 'not_found = "/404.html"'

#: Bound on each fetch a test makes of the served tree.
_FETCH_TIMEOUT_SECONDS = 10.0


def _fetch(url: str) -> tuple[int, bytes]:
    """GET `url`: its status and body, an error status's included."""
    try:
        with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_SECONDS) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()


class NotFoundCliTests(CliCase):
    """`not_found` with `--serve`: a page with no file is served, confirmed and named."""

    def setUp(self) -> None:
        """Give the scratch project's site its own not-found page."""
        super().setUp()
        (self.site / "404.html").write_bytes(_NOT_FOUND_PAGE)

    def write_targets(self, pages: str, *lines: str) -> None:
        """The scratch targets with `pages` in place of its own, `lines` written above them."""
        text = TARGETS.replace('["/", "/archive/"]', pages)
        self.targets.write_text(
            "\n".join((*lines, text)), encoding="utf-8", newline="\n"
        )

    def test_a_page_with_no_file_answers_the_not_found_page_and_is_named(
        self,
    ) -> None:
        self.write_targets('["/", "/future?from=test#top"]', _NOT_FOUND_KEY)
        answered: list[tuple[int, bytes]] = []
        run_matrix = self.engine.run_matrix

        def fetching(*args: Any, **options: Any) -> Any:
            answered.append(_fetch(f"{args[1]}/future"))
            return run_matrix(*args, **options)

        with mock.patch.object(self.engine, "run_matrix", fetching):
            code, _, err = self.run_cli(
                "--serve", str(self.site), "--engines", "chromium"
            )
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(answered, [(404, _NOT_FOUND_PAGE)])
        (call,) = self.engine.calls
        self.assertEqual(call["missing_pages"], frozenset({"/future"}))

    def test_a_state_s_page_with_no_file_is_named_too(self) -> None:
        state = '[[state]]\nname = "opened"\npage = "/gone/?from=state"\n'
        self.write_targets('["/"]', _NOT_FOUND_KEY)
        with self.targets.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(state)
        code, _, _ = self.run_cli("--serve", str(self.site), "--engines", "webkit")
        self.assertEqual(code, 0)
        self.assertEqual(self.engine.calls[0]["missing_pages"], frozenset({"/gone/"}))

    def test_without_not_found_no_page_is_named(self) -> None:
        code, _, _ = self.run_cli("--serve", str(self.site), "--engines", "webkit")
        self.assertEqual(code, 0)
        self.assertEqual(self.engine.calls[0]["missing_pages"], frozenset())

    def test_not_found_without_serve_is_refused(self) -> None:
        # A server this run does not start answers a missing page its own way.
        self.write_targets(
            '["/"]', 'base_url = "http://127.0.0.1:8123"', _NOT_FOUND_KEY
        )
        code, _, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("not_found needs --serve", err)
        self.assertEqual(self.engine.calls, [])

    def test_a_not_found_that_names_no_file_is_refused(self) -> None:
        (self.site / "404.html").unlink()
        self.write_targets('["/"]', _NOT_FOUND_KEY)
        code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertIn("cannot serve", err)
        self.assertIn("names no file", err)
        self.assertEqual(self.engine.calls, [])

    def test_a_not_found_that_is_not_a_server_path_is_refused(self) -> None:
        self.write_targets('["/"]', 'not_found = "404.html"')
        code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertIn("not_found must be a server path", err)
        self.assertEqual(self.engine.calls, [])

    def test_a_page_naming_a_file_outside_the_tree_is_refused_in_one_line(
        self,
    ) -> None:
        # The targets file refuses every dot segment first, so only a stand-in reaches here.
        self.write_targets('["/"]', _NOT_FOUND_KEY)
        refusal = browser_pass.loopback_site.SiteServeError("the page / names a file")
        with mock.patch.object(
            browser_pass.loopback_site, "page_file", side_effect=refusal
        ):
            code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertEqual(err.count("\n"), 1)
        self.assertIn("cannot serve", err)
        self.assertIn("names a file", err)
        self.assertEqual(self.engine.calls, [])


if __name__ == "__main__":
    unittest.main()
