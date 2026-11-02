"""Grouped suite: a target's `follows_redirect`, and how each target's meta refresh is resolved.

`follows_redirect` is parsed like every other key: a boolean, false when left out, refused at
its path otherwise. `resolve_redirects` reads each target's page over a real loopback server
of a temp folder, and decides: a declared refresh at once to a served page on the same origin
is followed, its target printed at the destination; any other declared one is refused,
naming the target and why; an undeclared refresh is recorded, and its target left out.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and `print_redirects` finds
the shared loopback server itself.
"""

# gate-serves: none (serves only temporary trees built at test time)

from __future__ import annotations

import sys
import tempfile
import unittest
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Final

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_redirects  # noqa: E402 - the tool's private src/, via the insert above
from _print_targets_fixtures import doc  # noqa: E402 - beside this module
from print_redirects import (  # noqa: E402 - the tool's private src/
    Redirect,
    RedirectError,
    resolve_redirects,
    server_path,
)
from print_targets import (  # noqa: E402 - as above
    TargetsError,
    TargetsFile,
    adhoc_targets,
    parse_targets,
)

import loopback_site  # noqa: E402 - via print_redirects' own insert

#: A served origin no case's server holds: only `server_path` is handed it.
_ORIGIN: Final = "http://127.0.0.1:8000"

#: The archive stub's shape: a refresh at once to the year page beside it.
_STUB: Final = '<!doctype html><meta http-equiv="refresh" content="0; url=2026/">\n'

#: A plain page, with no refresh.
_PLAIN: Final = "<!doctype html><title>plain</title><p>plain</p>\n"


def _refresh(content: str) -> str:
    """A page whose one meta refresh carries `content`."""
    return f'<!doctype html><meta http-equiv="refresh" content="{content}">\n'


def _targets(page: str, *, follows: bool | None) -> TargetsFile:
    """One target `archive` at `page`; `follows_redirect` as given, or left out for None."""
    keys: dict[str, object] = {"name": "archive", "page": page}
    if follows is not None:
        keys["follows_redirect"] = follows
    return parse_targets({"target": [keys]})


class FollowsRedirectKeyTest(unittest.TestCase):
    """The key parses as a boolean, false when left out, refused at its path otherwise."""

    def test_left_out_it_is_false(self) -> None:
        (target,) = parse_targets(doc()).targets
        self.assertIs(target.follows_redirect, False)

    def test_true_and_false_parse(self) -> None:
        for value in (True, False):
            with self.subTest(value=value):
                (target,) = parse_targets(doc(follows_redirect=value)).targets
                self.assertIs(target.follows_redirect, value)

    def test_a_non_boolean_is_refused_at_its_path(self) -> None:
        second = {"name": "two", "page": "/two/", "follows_redirect": "yes"}
        data = {"target": [{"name": "one", "page": "/"}, second]}
        with self.assertRaisesRegex(
            TargetsError, r"^target\[1\]\.follows_redirect: must be true or false$"
        ):
            parse_targets(data)

    def test_pages_given_on_the_command_line_follow_nothing(self) -> None:
        (target,) = adhoc_targets(["/"]).targets
        self.assertIs(target.follows_redirect, False)


class ServerPathTest(unittest.TestCase):
    """A destination on the served origin is its path and query; off it, None."""

    def test_a_destination_on_the_origin_is_its_path_and_query(self) -> None:
        cases = {
            f"{_ORIGIN}/archive/2026/": "/archive/2026/",
            f"{_ORIGIN}/archive/2026/?a=1#top": "/archive/2026/?a=1",
            _ORIGIN: "/",
        }
        for destination, expected in cases.items():
            with self.subTest(destination=destination):
                self.assertEqual(server_path(destination, _ORIGIN), expected)

    def test_another_origin_is_none(self) -> None:
        for destination in (
            "http://192.0.2.1:8000/archive/",
            "http://localhost:8000/archive/",
            "https://127.0.0.1:8000/archive/",
            "http://127.0.0.1:8001/archive/",
        ):
            with self.subTest(destination=destination):
                self.assertIsNone(server_path(destination, _ORIGIN))


class _ServedCase(unittest.TestCase):
    """A temp site, served on loopback for a case's resolution."""

    def setUp(self) -> None:
        """Make the empty site folder."""
        holder = tempfile.TemporaryDirectory(prefix="print_pass-redirects-")
        self.addCleanup(holder.cleanup)
        self.site = Path(holder.name)

    def write(self, pages: Mapping[str, str]) -> None:
        """Write each of `pages`, a path under the site to its HTML."""
        for name, html in pages.items():
            path = self.site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8", newline="\n")

    @contextmanager
    def served(self) -> Iterator[str]:
        """The site served, for the length of the block; its base url."""
        with loopback_site.serve_directory(self.site) as base_url:
            yield base_url

    def resolve(self, targets: TargetsFile) -> print_redirects.Resolution:
        """`targets` resolved against the site served."""
        with self.served() as base_url:
            return resolve_redirects(targets, base_url, self.site)

    def refused(self, targets: TargetsFile) -> str:
        """The message `targets` is refused with."""
        with self.assertRaises(RedirectError) as caught:
            self.resolve(targets)
        return str(caught.exception)


class FollowedTest(_ServedCase):
    """A declared refresh at once to a served page on the origin is followed."""

    def test_the_target_is_printed_at_its_destination(self) -> None:
        self.write({"archive/index.html": _STUB, "archive/2026/index.html": _PLAIN})
        resolution = self.resolve(_targets("/archive/", follows=True))
        (target,) = resolution.targets.targets
        self.assertEqual(target.page, "/archive/2026/")
        self.assertEqual(target.name, "archive")
        self.assertEqual(
            resolution.redirects,
            (Redirect("archive", "/archive/", "/archive/2026/", followed=True),),
        )

    def test_a_page_with_a_query_is_read_as_named(self) -> None:
        self.write(
            {
                "index.html": _refresh("0; url=archive/?a=1"),
                "archive/index.html": _PLAIN,
            }
        )
        resolution = self.resolve(_targets("/?puzzle=0", follows=True))
        (target,) = resolution.targets.targets
        self.assertEqual(target.page, "/archive/?a=1")

    def test_a_page_with_no_refresh_and_no_declaration_is_left_alone(self) -> None:
        self.write({"index.html": _PLAIN})
        targets = _targets("/", follows=None)
        resolution = self.resolve(targets)
        self.assertEqual(resolution.targets, targets)
        self.assertEqual(resolution.redirects, ())

    def test_an_unreadable_undeclared_page_is_left_to_the_engine(self) -> None:
        """A page that answers an error is the engine's to report as it loads it."""
        targets = _targets("/missing/", follows=None)
        resolution = self.resolve(targets)
        self.assertEqual(resolution.targets, targets)


class UndeclaredTest(_ServedCase):
    """An undeclared refresh is recorded, unfollowed, and its target left out."""

    def test_the_target_is_left_out_and_the_redirect_recorded(self) -> None:
        self.write(
            {
                "archive/index.html": _STUB,
                "archive/2026/index.html": _PLAIN,
                "index.html": _PLAIN,
            }
        )
        data = {
            "target": [
                {"name": "home", "page": "/"},
                {"name": "archive", "page": "/archive/"},
            ]
        }
        resolution = self.resolve(parse_targets(data))
        self.assertEqual([t.name for t in resolution.targets.targets], ["home"])
        self.assertEqual(
            resolution.redirects,
            (Redirect("archive", "/archive/", "/archive/2026/", followed=False),),
        )

    def test_a_destination_off_the_origin_is_recorded_whole(self) -> None:
        self.write({"index.html": _refresh("0; url=http://192.0.2.1/x")})
        resolution = self.resolve(_targets("/", follows=None))
        self.assertEqual(resolution.targets.targets, ())
        (redirect,) = resolution.redirects
        self.assertEqual(redirect.destination, "http://192.0.2.1/x")


class RefusedTest(_ServedCase):
    """A declared redirect that cannot be followed honestly is refused, naming why."""

    def assert_refused(self, message: str, *reasons: str) -> None:
        """`message` names the target, its key's path and every one of `reasons`."""
        for part in ("target[0].follows_redirect", "'archive'", *reasons):
            self.assertIn(part, message)

    def test_a_page_with_no_refresh(self) -> None:
        self.write({"archive/index.html": _PLAIN})
        message = self.refused(_targets("/archive/", follows=True))
        self.assert_refused(message, "/archive/ is not a meta refresh")

    def test_a_refresh_that_waits(self) -> None:
        self.write({"archive/index.html": _refresh("3; url=2026/")})
        message = self.refused(_targets("/archive/", follows=True))
        self.assert_refused(message, "after 3 s")

    def test_a_destination_off_the_origin(self) -> None:
        self.write({"archive/index.html": _refresh("0; url=http://192.0.2.1/x")})
        message = self.refused(_targets("/archive/", follows=True))
        self.assert_refused(message, "http://192.0.2.1/x", "off the served origin")

    def test_a_destination_not_served(self) -> None:
        self.write({"archive/index.html": _STUB})
        message = self.refused(_targets("/archive/", follows=True))
        self.assert_refused(message, "/archive/2026/", "is not served")

    def test_a_destination_that_refreshes_again(self) -> None:
        self.write(
            {
                "archive/index.html": _STUB,
                "archive/2026/index.html": _refresh("0; url=../"),
            }
        )
        message = self.refused(_targets("/archive/", follows=True))
        self.assert_refused(message, "/archive/2026/ is itself a meta refresh")

    def test_a_page_that_cannot_be_read(self) -> None:
        message = self.refused(_targets("/archive/", follows=True))
        self.assert_refused(message, "/archive/ could not be read")

    def test_a_server_that_no_longer_answers(self) -> None:
        """A refused connection holds no reply to close, and is refused the same way."""
        with self.served() as base_url:
            pass
        with self.assertRaises(RedirectError) as caught:
            resolve_redirects(
                _targets("/archive/", follows=True), base_url, self.site, timeout=1.0
            )
        self.assert_refused(str(caught.exception), "/archive/ could not be read")


if __name__ == "__main__":
    unittest.main()
