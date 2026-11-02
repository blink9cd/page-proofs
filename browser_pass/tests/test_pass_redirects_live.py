"""Grouped suite: the live boundary for a meta-refresh page (the whole CLI, in real Chromium).

A scratch site whose `archive/index.html` is a 0-second meta refresh to `2026/`, a year-archive
stub as a site generator writes one, beside a real `archive/2026/index.html`. Loaded in the
browser, the stub navigates away while script is evaluated in it, which fails with `Execution
context was destroyed`. So with `/archive/` listed in `redirects`, the run exits 0 having
measured the year page in its place, and the report names the pair; without it, the
run stops with exit 2 naming the page and the key, before any browser is launched.
`_live_fixtures.py` holds the setup the live suites share.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ and the
# shared loopback server on sys.path for every import after it.
from _live_fixtures import TOOL_DIR, LiveSite, scratch

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path

import pass_facts
from pass_report import JSON_NAME, MARKDOWN_NAME

sys.path.insert(0, str(TOOL_DIR))

import browser_pass  # noqa: E402 - the CLI, via the insert above

#: The engine launches this module may make (`LiveSite` fails it past them): the one run that
#: measures launches Chromium once, and the refused run launches nothing.
_LAUNCH_BUDGET = {"chromium": 1}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)

#: A year-archive stub, as a site generator writes one.
_STUB = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="refresh" content="0; url=2026/" />
    <title>browser_pass sample: moved</title>
  </head>
  <body></body>
</html>
"""

#: The year page the stub sends the browser to: clean, so a run that measures it exits 0.
_YEAR_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>browser_pass sample: 2026</title>
  </head>
  <body>
    <main>
      <h1>2026</h1>
      <p>What happened this year.</p>
    </main>
  </body>
</html>
"""

#: One page, one phone size, one rendering of each kind the run makes in Chromium.
_TARGETS = """pages = ["/archive/"]
{redirects}
[[viewport]]
width = 390
height = 844
"""


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the pinned builds for the whole module."""
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)


class RedirectLiveTests(unittest.TestCase):
    """The stub measured through the CLI: followed when declared, refused when not."""

    def setUp(self) -> None:
        """Write the scratch site: the stub, and the year page it points at."""
        self.root = scratch(self)
        self.site = self.root / "site"
        (self.site / "archive" / "2026").mkdir(parents=True)
        (self.site / "archive" / "index.html").write_text(
            _STUB, encoding="utf-8", newline="\n"
        )
        (self.site / "archive" / "2026" / "index.html").write_text(
            _YEAR_PAGE, encoding="utf-8", newline="\n"
        )

    def run_cli(self, redirects: str) -> tuple[int, str, Path]:
        """The CLI over the scratch site, Chromium light scripts-on; exit, stderr, results."""
        targets = self.root / "targets.toml"
        targets.write_text(
            _TARGETS.format(redirects=redirects), encoding="utf-8", newline="\n"
        )
        out = self.root / "runs"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = browser_pass.main(
                [
                    *("--targets", str(targets), "--serve", str(self.site)),
                    *("--engines", "chromium", "--modes", "light", "--scripts", "on"),
                    *("--browsers", os.environ[pass_facts.BROWSERS_ENV]),
                    *("--out", str(out)),
                ]
            )
        return code, err.getvalue(), out

    def test_declared_the_year_page_is_measured_and_reported(self) -> None:
        code, err, out = self.run_cli('redirects = ["/archive/"]')
        self.assertEqual(code, 0, err)
        (run_dir,) = out.iterdir()
        document = json.loads((run_dir / JSON_NAME).read_text(encoding="utf-8"))
        self.assertEqual(
            [r["page"] for r in document["renderings"]], ["/archive/2026/"]
        )
        self.assertEqual(
            document["facts"]["redirects"],
            [{"page": "/archive/", "destination": "/archive/2026/"}],
        )
        self.assertIn(
            "- Redirects followed: `/archive/` to `/archive/2026/`",
            (run_dir / MARKDOWN_NAME).read_text(encoding="utf-8"),
        )

    def test_undeclared_the_run_stops_naming_the_page_and_the_key(self) -> None:
        code, err, out = self.run_cli("")
        self.assertEqual(code, 2)
        self.assertNotIn("Execution context was destroyed", err)
        self.assertRegex(
            err,
            r"/archive/ is a meta refresh to http://127\.0\.0\.1:\d+/archive/2026/: list it in "
            r"the targets file's redirects",
        )
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
