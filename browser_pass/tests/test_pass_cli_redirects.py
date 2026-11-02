"""Grouped suite: the CLI with a meta-refresh page (followed when declared, refused when not).

The scratch project's `/archive/` becomes a 0-second meta refresh to its year page, as a
generated archive stub often is. These tests drive the CLI through `_cli_fixtures.FakeEngine`
against the real loopback server and the real meta-refresh reader: a page listed in `redirects`
is measured at its destination and the report names the pair; an undeclared refresh, a declared
page that is no refresh, one that waits, and one whose destination is not served each stop the
run with exit 2 before the engine is asked for anything. The decisions themselves are
`test_pass_redirects.py`'s.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's folder on
# sys.path, and the CLI it imports adds the tool's src/, for every import after it.
from _cli_fixtures import TARGETS, CliCase

import json
import unittest
from typing import cast

from pass_report import JSON_NAME, MARKDOWN_NAME
from pass_targets import Rendering

#: A year-archive stub, as a site generator writes one, with `{delay}` its wait.
_STUB = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta http-equiv="refresh" content="{delay}; url=2026/" />
<title>moved</title></head><body></body></html>
"""

#: The key, as a targets file writes it.
_REDIRECTS_KEY = 'redirects = ["/archive/"]'


class RedirectCliTests(CliCase):
    """`/archive/` a meta refresh to `/archive/2026/`, served, with and without `redirects`."""

    def setUp(self) -> None:
        """Make `/archive/` the stub, and give it a year page to send the browser to."""
        super().setUp()
        self.write_stub(0)
        (self.site / "archive" / "2026").mkdir()
        (self.site / "archive" / "2026" / "index.html").write_text(
            "<p>2026</p>\n", encoding="utf-8", newline="\n"
        )

    def write_stub(self, delay: int) -> None:
        """`/archive/` as a meta refresh to `2026/` after `delay` seconds."""
        (self.site / "archive" / "index.html").write_text(
            _STUB.format(delay=delay), encoding="utf-8", newline="\n"
        )

    def write_targets(self, pages: str, *lines: str) -> None:
        """The scratch targets with `pages` in place of its own, `lines` written above them."""
        text = TARGETS.replace('["/", "/archive/"]', pages)
        self.targets.write_text(
            "\n".join((*lines, text)), encoding="utf-8", newline="\n"
        )

    def run_served(self) -> tuple[int, str, str]:
        """The CLI over the served scratch tree, Chromium only."""
        return self.run_cli("--serve", str(self.site), "--engines", "chromium")

    def test_a_declared_redirect_is_measured_at_its_destination_and_reported(
        self,
    ) -> None:
        self.write_targets('["/", "/archive/"]', _REDIRECTS_KEY)
        code, _, err = self.run_served()
        self.assertEqual((code, err), (0, ""))
        (call,) = self.engine.calls
        pages = {r.page for r in cast("list[Rendering]", call["renderings"])}
        self.assertEqual(pages, {"/", "/archive/2026/"})
        run_dir = self.only_run_dir()
        document = json.loads((run_dir / JSON_NAME).read_text(encoding="utf-8"))
        self.assertEqual(
            document["facts"]["redirects"],
            [{"page": "/archive/", "destination": "/archive/2026/"}],
        )
        self.assertIn(
            "- Redirects followed: `/archive/` to `/archive/2026/`",
            (run_dir / MARKDOWN_NAME).read_text(encoding="utf-8"),
        )

    def test_a_destination_also_listed_is_measured_once(self) -> None:
        self.write_targets('["/archive/", "/archive/2026/"]', _REDIRECTS_KEY)
        code, _, _ = self.run_served()
        self.assertEqual(code, 0)
        (call,) = self.engine.calls
        renderings = cast("list[Rendering]", call["renderings"])
        # One page's worth (two orientations, two modes, scripts on and off), not two pages'.
        self.assertEqual(len(renderings), 2 * 2 * 2)
        self.assertEqual({r.page for r in renderings}, {"/archive/2026/"})

    def test_an_undeclared_refresh_stops_the_run_naming_the_page_and_the_key(
        self,
    ) -> None:
        code, _, err = self.run_served()
        self.assertEqual(code, 2)
        self.assertRegex(
            err,
            r"/archive/ is a meta refresh to http://127\.0\.0\.1:\d+/archive/2026/: list it in "
            r"the targets file's redirects",
        )
        self.assertEqual(self.engine.calls, [])

    def test_a_declared_page_that_is_no_refresh_stops_the_run(self) -> None:
        (self.site / "archive" / "index.html").write_text(
            "<p>then</p>\n", encoding="utf-8", newline="\n"
        )
        self.write_targets('["/", "/archive/"]', _REDIRECTS_KEY)
        code, _, err = self.run_served()
        self.assertEqual(code, 2)
        self.assertIn(
            "/archive/ is listed in redirects but declares no meta refresh", err
        )
        self.assertEqual(self.engine.calls, [])

    def test_a_declared_refresh_that_waits_stops_the_run(self) -> None:
        self.write_stub(3)
        self.write_targets('["/", "/archive/"]', _REDIRECTS_KEY)
        code, _, err = self.run_served()
        self.assertEqual(code, 2)
        self.assertIn("refreshes after 3 seconds", err)
        self.assertEqual(self.engine.calls, [])

    def test_a_destination_not_served_stops_the_run(self) -> None:
        (self.site / "archive" / "2026" / "index.html").unlink()
        self.write_targets('["/", "/archive/"]', _REDIRECTS_KEY)
        code, _, err = self.run_served()
        self.assertEqual(code, 2)
        self.assertIn(
            "/archive/ is a meta refresh to /archive/2026/, which is not served", err
        )
        self.assertEqual(self.engine.calls, [])

    def test_redirects_without_serve_is_refused(self) -> None:
        # Only a tree this run serves is read for a meta refresh before it is measured.
        self.write_targets(
            '["/", "/archive/"]', 'base_url = "http://127.0.0.1:8123"', _REDIRECTS_KEY
        )
        code, _, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("redirects needs --serve", err)
        self.assertEqual(self.engine.calls, [])


if __name__ == "__main__":
    unittest.main()
