"""Grouped suite: a declared meta-refresh redirect (the `redirects` key, and following it).

A page that is a 0-second meta refresh navigates away as it loads, so a browser that measures it
measures a page being torn down. The targets file's `redirects` names each page that is one, and
the run measures the page it points at in its place. These tests hold the key's refusals, each
decision `follow` makes about a served page, with the server's two answers (the page's
refresh, and whether the destination is served) replaced by fakes, the renderings it retargets,
and the report line. The CLI drives the real reader in `test_pass_cli_redirects.py`, and the real
engines in `test_pass_redirects_live.py`.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's folder on
# sys.path, and the CLI it imports adds the tool's src/ and the shared loopback server.
import _cli_fixtures  # noqa: F401 - imported for its sys.path bootstrap alone

import unittest
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pass_redirects
import pass_targets
from loopback_site import MetaRefresh
from pass_redirects import Redirect, RedirectRefused
from pass_targets import Rendering

#: Where the fakes pretend the tree is served.
_BASE = "http://127.0.0.1:50000"

#: A minimal valid targets document, as `tomllib` would hand it over.
_VALID: dict[str, object] = {
    "pages": ["/", "/archive/"],
    "viewport": [{"width": 390, "height": 844}],
}

#: The folder the fakes pretend is served, and the bound each call is given.
_FOLDER = Path("site")
_TIMEOUT = 7.0

#: The stub every following test reads: at once, to its year page.
_STUB = MetaRefresh(0, f"{_BASE}/archive/2026/")


def _targets(**keys: object) -> pass_targets.Targets:
    """`_VALID` with `keys` set, parsed."""
    return pass_targets.parse_targets({**_VALID, **keys})


class _Site:
    """`loopback_site`, faked: each url's meta refresh, and the paths that are not served."""

    SiteServeError = RuntimeError

    def __init__(
        self,
        refreshes: Mapping[str, MetaRefresh],
        unserved: Mapping[str, Exception] = MappingProxyType({}),
    ) -> None:
        """Pages not in `refreshes` declare none; a page in `unserved` raises its error."""
        self.refreshes = dict(refreshes)
        self.unserved = unserved
        self.read_urls: list[str] = []

    def read_meta_refresh(self, page_url: str, *, timeout: float) -> MetaRefresh | None:
        """The meta refresh at `page_url`, recording that it was read."""
        assert timeout == _TIMEOUT
        self.read_urls.append(page_url)
        return self.refreshes.get(page_url)

    def confirm_serves(
        self, base_url: str, directory: Path, page: str, *, timeout: float
    ) -> None:
        """Raise as `confirm_serves` does for a page the server does not hold."""
        assert (base_url, directory, timeout) == (_BASE, _FOLDER, _TIMEOUT)
        if page in self.unserved:
            raise self.unserved[page]

    def follow(
        self, targets: pass_targets.Targets, missing: frozenset[str] = frozenset()
    ) -> tuple[Redirect, ...]:
        """`follow` over these answers."""
        served = pass_redirects.Served(self, _BASE, _FOLDER, _TIMEOUT)
        return pass_redirects.follow(targets, served, missing)


class RedirectsKeyTests(unittest.TestCase):
    """The targets file's `redirects`: pages it also lists, each once, never the not-found page."""

    def test_reads_the_declared_pages_and_their_absence(self) -> None:
        self.assertEqual(_targets(redirects=["/archive/"]).redirects, ("/archive/",))
        self.assertEqual(_targets().redirects, ())
        self.assertEqual(_targets(redirects=[]).redirects, ())

    def test_refuses_a_value_that_is_not_a_list_of_pages(self) -> None:
        for bad in ("/archive/", ["/archive/", 3], {"page": "/archive/"}):
            with (
                self.subTest(bad=bad),
                self.assertRaisesRegex(
                    pass_targets.TargetsError, "redirects must be a list of pages"
                ),
            ):
                _targets(redirects=bad)

    def test_refuses_a_page_that_pages_does_not_list(self) -> None:
        with self.assertRaisesRegex(
            pass_targets.TargetsError,
            "redirects holds '/news/', which pages does not list",
        ):
            _targets(redirects=["/news/"])

    def test_refuses_a_page_named_twice(self) -> None:
        with self.assertRaisesRegex(
            pass_targets.TargetsError, "redirects holds '/archive/' twice"
        ):
            _targets(redirects=["/archive/", "/archive/"])

    def test_refuses_the_not_found_page(self) -> None:
        with self.assertRaisesRegex(
            pass_targets.TargetsError, "redirects holds '/404.html', the not_found page"
        ):
            _targets(
                pages=["/", "/404.html"], not_found="/404.html", redirects=["/404.html"]
            )


class FollowTests(unittest.TestCase):
    """What `follow` decides for each served page."""

    def test_a_declared_refresh_at_once_is_followed_to_its_served_destination(
        self,
    ) -> None:
        site = _Site({f"{_BASE}/archive/": _STUB})
        followed = site.follow(_targets(redirects=["/archive/"]))
        self.assertEqual(followed, (Redirect("/archive/", "/archive/2026/"),))
        # Every page is read, and the destination once more: a chain is not followed.
        self.assertEqual(
            site.read_urls, [f"{_BASE}/", f"{_BASE}/archive/", f"{_BASE}/archive/2026/"]
        )

    def test_a_destination_keeps_its_query_and_fragment(self) -> None:
        site = _Site(
            {f"{_BASE}/archive/": MetaRefresh(0, f"{_BASE}/archive/2026/?a=1#top")}
        )
        followed = site.follow(_targets(redirects=["/archive/"]))
        self.assertEqual(followed, (Redirect("/archive/", "/archive/2026/?a=1#top"),))
        self.assertEqual(site.read_urls[-1], f"{_BASE}/archive/2026/")

    def test_an_anchored_page_is_read_without_its_fragment(self) -> None:
        site = _Site({f"{_BASE}/archive/": _STUB})
        targets = _targets(pages=["/archive/#top"], redirects=["/archive/#top"])
        self.assertEqual(
            site.follow(targets), (Redirect("/archive/", "/archive/2026/"),)
        )
        self.assertEqual(site.read_urls[0], f"{_BASE}/archive/")

    def test_no_refresh_and_none_declared_follows_nothing(self) -> None:
        self.assertEqual(_Site({}).follow(_targets()), ())

    def test_an_undeclared_refresh_is_refused_naming_the_key(self) -> None:
        site = _Site({f"{_BASE}/archive/": _STUB})
        with self.assertRaises(RedirectRefused) as caught:
            site.follow(_targets())
        self.assertEqual(
            str(caught.exception),
            f"/archive/ is a meta refresh to {_BASE}/archive/2026/: list it in the targets "
            "file's redirects to measure the page it points at",
        )

    def test_a_declared_page_that_is_no_refresh_is_refused(self) -> None:
        with self.assertRaisesRegex(
            RedirectRefused,
            r"^/archive/ is listed in redirects but declares no meta refresh$",
        ):
            _Site({}).follow(_targets(redirects=["/archive/"]))

    def test_a_declared_refresh_that_waits_is_refused(self) -> None:
        site = _Site({f"{_BASE}/archive/": MetaRefresh(5, f"{_BASE}/archive/2026/")})
        with self.assertRaisesRegex(
            RedirectRefused,
            "^/archive/ is listed in redirects but refreshes after 5 seconds",
        ):
            site.follow(_targets(redirects=["/archive/"]))

    def test_a_destination_off_the_served_site_is_refused(self) -> None:
        for elsewhere in (
            "http://localhost:50000/archive/2026/",
            "https://example.com/",
        ):
            site = _Site({f"{_BASE}/archive/": MetaRefresh(0, elsewhere)})
            with (
                self.subTest(elsewhere=elsewhere),
                self.assertRaisesRegex(RedirectRefused, "off the served site"),
            ):
                site.follow(_targets(redirects=["/archive/"]))

    def test_a_destination_not_served_is_refused_with_why(self) -> None:
        # Both ways confirm_serves refuses: no file, and another tree's bytes.
        for error in (OSError("no file"), RuntimeError("other bytes")):
            site = _Site(
                {f"{_BASE}/archive/": _STUB}, unserved={"/archive/2026/": error}
            )
            with (
                self.subTest(error=error),
                self.assertRaisesRegex(
                    RedirectRefused,
                    "^/archive/ is a meta refresh to /archive/2026/, which is not served: "
                    f"{error}$",
                ),
            ):
                site.follow(_targets(redirects=["/archive/"]))

    def test_a_destination_that_refreshes_again_is_refused(self) -> None:
        site = _Site({f"{_BASE}/archive/": _STUB, f"{_BASE}/archive/2026/": _STUB})
        with self.assertRaisesRegex(RedirectRefused, "itself a meta refresh"):
            site.follow(_targets(redirects=["/archive/"]))

    def test_a_missing_page_is_never_read(self) -> None:
        site = _Site({})
        targets = _targets(pages=["/", "/future"], not_found="/404.html")
        self.assertEqual(site.follow(targets, frozenset({"/future"})), ())
        self.assertEqual(site.read_urls, [f"{_BASE}/"])

    def test_a_declared_missing_page_is_refused_unread(self) -> None:
        site = _Site({})
        targets = _targets(
            pages=["/", "/future"], not_found="/404.html", redirects=["/future"]
        )
        with self.assertRaisesRegex(RedirectRefused, "^/future .* has no file"):
            site.follow(targets, frozenset({"/future"}))
        self.assertEqual(site.read_urls, [f"{_BASE}/"])

    def test_a_state_s_page_that_refreshes_undeclared_is_refused(self) -> None:
        site = _Site({f"{_BASE}/old/": _STUB})
        targets = _targets(state=[{"name": "opened", "page": "/old/"}])
        with self.assertRaisesRegex(RedirectRefused, "^/old/ is a meta refresh"):
            site.follow(targets)


class RetargetTests(unittest.TestCase):
    """The renderings measured in a followed page's place."""

    def test_a_followed_page_is_measured_at_its_destination(self) -> None:
        renderings = [
            Rendering("chromium", page, 390, 844, "light", "on")
            for page in ("/", "/archive/", "/archive/#top")
        ]
        moved = pass_redirects.retarget(
            renderings, (Redirect("/archive/", "/archive/2026/"),)
        )
        self.assertEqual([r.page for r in moved], ["/", "/archive/2026/"])

    def test_a_destination_the_targets_also_list_is_measured_once(self) -> None:
        renderings = [
            Rendering(engine, page, 390, 844, "light", "on")
            for engine in ("chromium", "webkit")
            for page in ("/archive/", "/archive/2026/")
        ]
        moved = pass_redirects.retarget(
            renderings, (Redirect("/archive/", "/archive/2026/"),)
        )
        self.assertEqual(
            [(r.engine, r.page) for r in moved],
            [("chromium", "/archive/2026/"), ("webkit", "/archive/2026/")],
        )

    def test_nothing_followed_changes_nothing(self) -> None:
        renderings = (Rendering("webkit", "/", 390, 844, "dark", "off"),)
        self.assertEqual(pass_redirects.retarget(renderings, ()), renderings)


class ReportLineTests(unittest.TestCase):
    """The report's line naming each redirect followed."""

    def test_names_each_redirect_and_is_absent_without_one(self) -> None:
        followed = (Redirect("/archive/", "/archive/2026/"), Redirect("/old/", "/new/"))
        self.assertEqual(
            pass_redirects.report_lines(followed),
            [
                "- Redirects followed: `/archive/` to `/archive/2026/`; `/old/` to `/new/`"
            ],
        )
        self.assertEqual(pass_redirects.report_lines(()), [])


if __name__ == "__main__":
    unittest.main()
