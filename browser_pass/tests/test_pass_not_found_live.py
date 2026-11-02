"""Grouped suite: the LIVE boundary for a page with no file, answered with the site's own 404 page.

A scratch site is served the way GitHub Pages serves one, a request with no file answered with
its `404.html` and status 404, and a page with no file is loaded in each pinned engine. Its own
document's 404 is no finding there, scripts on and off: neither its response nor the console line
Chromium and WebKit echo it as. The same load measured as a page that has a file reports both,
which shows it is the exemption that takes them away. A subresource such a page cannot have is
still a finding, its echo with it, and with `--prove` every plant is caught in Chromium, the
missing-path one among them. `_live_fixtures.py` holds the setup the live suites share.
"""

# gate-serves: none (temporary trees, and its own samples/site, gated with its tool)

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ and the
# shared loopback server on sys.path for every import after it.
from _live_fixtures import LiveSite, SharedEngines, scratch

import unittest
from collections.abc import Iterator
from contextlib import contextmanager

import loopback_site
from pass_targets import ENGINES, SCRIPTS, Rendering

#: The engine launches this module may make (`LiveSite` fails it past them): one browser per
#: engine, shared by every test here.
_LAUNCH_BUDGET = {"chromium": 1, "firefox": 1, "webkit": 1}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)

#: The scratch site's own 404 page, clean, with `{extra}` at the end of its main content.
_NOT_FOUND_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>browser_pass sample: 404: Page not found</title>
  </head>
  <body>
    <main>
      <h1>404: Page not found</h1>
      <p>There is nothing at this address.</p>{extra}
    </main>
  </body>
</html>
"""

#: A subresource the scratch site does not hold, sized so its broken box moves nothing.
_GONE_IMAGE = '\n      <img src="/gone.png" alt="" width="20" height="20" />'

#: The page with no file every test loads.
_MISSING = "/future"

#: The engines that echo a 404 response into the console, a document's own included, and the
#: line they echo it as. Firefox logs none. Measured in the pinned builds, and spelled here
#: rather than taken from the tool, so the suite holds the tool's copy to the engines' words.
_ECHOING_ENGINES = frozenset({"chromium", "webkit"})
_ECHO = "Failed to load resource: the server responded with a status of 404 (Not Found)"


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the pinned builds for the whole module.

    `LiveSite` also serves the sample site, which nothing here loads: each test serves a
    scratch site of its own, answering a miss with its 404 page.
    """
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)


@contextmanager
def _served(case: unittest.TestCase, extra: str = "") -> Iterator[str]:
    """A scratch site holding only its 404 page, served answering every miss with it."""
    site = scratch(case)
    (site / "404.html").write_text(
        _NOT_FOUND_PAGE.format(extra=extra), encoding="utf-8", newline="\n"
    )
    with loopback_site.serve_directory(site, not_found="/404.html") as base_url:
        yield base_url


def _reported(engine: str, path: str) -> dict[str, tuple[str, ...]]:
    """What `engine` reports for one 404 response at `path`: the response, and any echo."""
    found: dict[str, tuple[str, ...]] = {"http_error": (f"404 {path}",)}
    if engine in _ECHOING_ENGINES:
        found["console_error"] = (_ECHO,)
    return found


class MissingPageLiveTests(unittest.TestCase):
    """A page with no file in every engine: its own 404 excused, and nothing else."""

    engines = SharedEngines()

    @classmethod
    def setUpClass(cls) -> None:
        """Start the class's driver; `setUpModule` has already pointed it at the builds."""
        cls.engines.start()
        cls.addClassCleanup(cls.engines.stop)

    def test_its_own_404_is_no_finding_in_any_engine(self) -> None:
        with _served(self) as base_url:
            for engine in ENGINES:
                for scripts in SCRIPTS:
                    rendering = Rendering(engine, _MISSING, 390, 844, "light", scripts)
                    with self.subTest(rendering.label):
                        result = self.engines.render(rendering, base_url, missing=True)
                        self.assertEqual((result.faults, result.findings), ((), {}))

    def test_measured_as_a_page_with_a_file_its_404_is_a_finding(self) -> None:
        # The control: what the exemption takes away is there to be taken.
        with _served(self) as base_url:
            for engine in ENGINES:
                rendering = Rendering(engine, _MISSING, 390, 844, "light", "off")
                with self.subTest(engine=engine):
                    result = self.engines.render(rendering, base_url)
                    self.assertEqual(result.faults, ())
                    self.assertEqual(result.findings, _reported(engine, _MISSING))

    def test_a_subresource_it_cannot_have_is_still_a_finding(self) -> None:
        with _served(self, _GONE_IMAGE) as base_url:
            for engine in ENGINES:
                rendering = Rendering(engine, _MISSING, 390, 844, "light", "on")
                with self.subTest(engine=engine):
                    result = self.engines.render(rendering, base_url, missing=True)
                    self.assertEqual(result.faults, ())
                    self.assertEqual(result.findings, _reported(engine, "/gone.png"))

    def test_with_prove_every_plant_is_caught_the_missing_path_among_them(
        self,
    ) -> None:
        with _served(self) as base_url:
            result = self.engines.render(
                Rendering("chromium", _MISSING, 390, 844, "light", "on"),
                base_url,
                prove=True,
                missing=True,
            )
        self.assertEqual((result.faults, result.findings), ((), {}))
        self.assertEqual(result.verdicts["http_error"], "caught")
        self.assertEqual(set(result.verdicts.values()), {"caught"})


if __name__ == "__main__":
    unittest.main()
