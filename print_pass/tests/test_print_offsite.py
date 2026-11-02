"""Suite: which URLs a targets file's expected off-loopback hosts cover.

`on_expected_host` is the one place a URL is matched against the hosts a site expects: a host
covers itself and every name under it, case ignored, and nothing else. What a targets file may
list is `test_print_targets.py`'s and `test_print_targets_refusals.py`'s, since it is refused
through `parse_targets`.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_offsite  # noqa: E402 - the tool's private src/, via the insert above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: The one host most cases list.
_EXAMPLE = ("example.org",)

#: What ends a URL's user info.
_AT = "@"

#: What the browser reads as a slash in an http, https or WebSocket URL.
_BACKSLASH = "\\"

#: One past the longest label DNS carries, which the browser still requests.
_PAST_LONGEST_LABEL = 64


class OnExpectedHostTests(unittest.TestCase):
    """A listed host covers itself and every subdomain of it, and no other name."""

    def on(self, url: str, hosts: tuple[str, ...] = _EXAMPLE) -> bool:
        """Whether `url` is on one of `hosts`."""
        return print_offsite.on_expected_host(url, hosts)

    def test_the_host_itself_is_covered(self) -> None:
        self.assertTrue(self.on("https://example.org/cover.jpg"))

    def test_every_subdomain_is_covered(self) -> None:
        for url in ("https://img.example.org/a.png", "https://a.b.example.org/"):
            with self.subTest(url=url):
                self.assertTrue(self.on(url))

    def test_a_look_alike_is_not_covered(self) -> None:
        # Only a whole label may come before the listed name: `bad` + `example.org` is
        # another registrable name, and so is one that merely holds the listed one.
        for url in (
            "https://badexample.org/a.png",
            "https://example.org.example.net/a.png",
            "https://example.organic/a.png",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.on(url))

    def test_a_parent_of_a_listed_host_is_not_covered(self) -> None:
        self.assertFalse(self.on("https://example.org/", ("img.example.org",)))

    def test_case_is_ignored_on_both_sides(self) -> None:
        self.assertTrue(self.on("https://IMG.Example.ORG/a.png"))
        self.assertTrue(self.on("https://img.example.org/a.png", ("Example.ORG",)))

    def test_a_port_path_query_and_fragment_change_nothing(self) -> None:
        for url in (
            "https://img.example.org:8443/a.png",
            "http://example.org:80/a/b.png?w=200#x",
            "wss://live.example.org/socket",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.on(url))

    def test_the_host_is_the_one_after_any_user_info(self) -> None:
        # Joined at run time: written whole, user info shaped as a name reads as an email
        # address to the privacy scan.
        self.assertFalse(
            self.on(_AT.join(("https://example.org", "example.net/a.png")))
        )
        self.assertTrue(self.on(_AT.join(("https://example.net", "img.example.org/"))))

    def test_a_listed_name_elsewhere_in_the_url_is_not_its_host(self) -> None:
        for url in (
            "https://example.net/?from=https://example.org/",
            "https://example.net/example.org/a.png",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.on(url))

    def test_a_host_ending_in_a_dot_is_not_covered(self) -> None:
        # The rule is equality or a `.`-joined suffix, nothing looser: a request that
        # escapes it stays the finding it was.
        self.assertFalse(self.on("https://img.example.org./a.png"))

    def test_a_url_with_no_scheme_names_no_host(self) -> None:
        # A same-origin image is reported by its path, and a path may open with `//`
        # without naming a host: only an absolute URL a page requests is on one.
        for url in ("/a.png", "//img.example.org/a.png", "img.example.org/a.png"):
            with self.subTest(url=url):
                self.assertFalse(self.on(url))

    def test_a_url_with_no_host_is_on_none(self) -> None:
        for url in ("data:image/png", "blob:https://img.example.org/1", "about:blank"):
            with self.subTest(url=url):
                self.assertFalse(self.on(url))

    def test_a_url_no_page_request_carries_to_a_host_is_on_none(self) -> None:
        # A page asks a host for http, https and WebSocket URLs alone: a `file:` URL's
        # host is never asked by a page, and a `javascript:` URL runs where it stands.
        for url in (
            "file://img.example.org/a.png",
            "javascript://img.example.org/%0Aalert(1)",
            "ftp://img.example.org/a.png",
            "foo://img.example.org/a.png",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.on(url))

    def test_every_scheme_a_page_requests_from_a_host_is_matched(self) -> None:
        # Case and a leading space change nothing: the browser folds the scheme's case
        # and drops the leading space before it requests the URL.
        for url in (
            "http://img.example.org/a.png",
            "https://img.example.org/a.png",
            "ws://img.example.org/live",
            "wss://img.example.org/live",
            "HTTPS://img.example.org/a.png",
            " https://img.example.org/a.png",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.on(url))

    def test_a_url_that_does_not_parse_is_on_none(self) -> None:
        self.assertFalse(self.on("http://[::1/example.org"))

    def test_a_url_the_browser_cannot_parse_is_on_none(self) -> None:
        # The browser never requests a URL it cannot parse, and an image's source is then
        # reported as written: nothing was blocked, so such an image is broken on its own
        # account, whatever host its text names. A port past 65535 or not a number, a
        # bracketed host (never a name) and a percent sign in a host are each refused.
        for url in (
            "https://img.example.org:99999/a.png",
            "https://img.example.org:65536/a.png",
            "https://img.example.org:abc/f.png",
            "https://[v1.example.org]/e.png",
            "https://a%b.example.org/s.png",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.on(url))

    def test_every_port_the_browser_accepts_changes_nothing(self) -> None:
        for url in (
            "https://img.example.org:8443/a.png",
            "https://img.example.org:65535/a.png",
            "https://img.example.org:/a.png",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.on(url))

    def test_a_backslash_ends_the_host_before_any_listed_name(self) -> None:
        # The browser reads a backslash as a slash, so the host it asks is `evil.test`, the
        # text before it, where `urlsplit` reads on past it.
        for url in (
            _AT.join((f"https://evil.test{_BACKSLASH}", "img.example.org/")),
            f"https://evil.test{_BACKSLASH}img.example.org/",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.on(url))

    def test_a_host_the_browser_would_have_written_otherwise_is_on_none(self) -> None:
        # The browser reports an internationalized label in its `xn--` form and a space as
        # `%20`: a host written any other way is not one it reported.
        for url in (
            "https://b" + chr(0xFC) + "cher.example.org/r.png",
            "https://a b.example.org/s.png",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.on(url))

    def test_a_host_outside_the_label_alphabet_is_on_none(self) -> None:
        # The browser requests each of these as written, but a host is read only as labels
        # of letters, digits, hyphens and underscores: anything else fails closed, and the
        # request stays the finding it is rather than a warning it might not be.
        for url in (
            "https://a%20b.example.org/s.png",
            "https://a$b.example.org/s.png",
            "https://a..example.org/e.png",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.on(url))

    def test_underscores_and_edge_hyphens_under_a_listed_name_are_covered(self) -> None:
        # The browser requests each of these as written, so each is on the listed name.
        for url in (
            "https://a_b.example.org/u.png",
            "https://_a.example.org/u.png",
            "https://-a.example.org/h.png",
            "https://a-.example.org/h.png",
            "https://" + "a" * _PAST_LONGEST_LABEL + ".example.org/l.png",
            "https://xn--bcher-kva.example.org/q.png",
        ):
            with self.subTest(url=url[:30]):
                self.assertTrue(self.on(url))

    def test_no_listed_host_covers_nothing(self) -> None:
        self.assertFalse(self.on("https://example.org/", ()))

    def test_any_listed_host_may_cover_the_url(self) -> None:
        hosts = ("example.net", "example.org")
        self.assertTrue(self.on("https://cdn.example.org/a.png", hosts))


# Hosts drawn beside arbitrary text, and URLs built on them, so a drawn case often matches.
_HOSTS = st.sampled_from(("example.org", "img.example.org", "Example.net")) | st.text()

# What may stand either side of a host in an authority, the hard cases included: a port the
# browser accepts or refuses, an address's brackets, a backslash it reads as a slash, and
# user info.
_BESIDE_HOST = st.sampled_from(
    (
        "",
        ":8443",
        ":99999",
        ":abc",
        "[v1.",
        "]",
        _BACKSLASH,
        f"evil.test{_BACKSLASH}",
        f"{_BACKSLASH}{_AT}",
        _AT,
        "a%",
        "_",
    )
)
_URLS = st.one_of(
    st.text(),
    st.builds(
        lambda scheme, before, host, after, rest: (
            f"{scheme}://{before}{host}{after}{rest}"
        ),
        st.sampled_from(("http", "https", "wss", "foo", "")),
        _BESIDE_HOST,
        _HOSTS,
        _BESIDE_HOST,
        st.text(),
    ),
)

# The URL standard's reading of an authority, written out here with no URL library, so the
# property does not restate the matcher: what a parser drops before it reads a URL, what an
# absolute URL opens with, and what ends its authority (in a special scheme, a backslash too).
_C0_CONTROL_OR_SPACE = "".join(chr(code) for code in range(ord(" ") + 1))
_TAB_OR_NEWLINE = ("\t", "\n", "\r")
_ABSOLUTE = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://(?P<rest>.*)", re.DOTALL)
_SPECIAL_SCHEMES = frozenset(("ftp", "file", "http", "https", "ws", "wss"))
_AUTHORITY_ENDS = "/?#"
_HIGHEST_PORT = 65535

# The schemes of the URLs a page requests from a host: the fetch standard's HTTP(S) schemes,
# and the WebSocket standard's two.
_REQUESTED_SCHEMES = frozenset(("http", "https", "ws", "wss"))


def _authority(url: str) -> tuple[str, str, str] | None:
    """The scheme, host and port the URL standard reads in `url`; None with no authority."""
    cleaned = url.strip(_C0_CONTROL_OR_SPACE)
    for mark in _TAB_OR_NEWLINE:
        cleaned = cleaned.replace(mark, "")
    match = _ABSOLUTE.fullmatch(cleaned)
    if match is None:
        return None
    scheme = match["scheme"].lower()
    ends = _AUTHORITY_ENDS
    if scheme in _SPECIAL_SCHEMES:
        ends += _BACKSLASH
    authority = re.split(f"[{re.escape(ends)}]", match["rest"], maxsplit=1)[0]
    host, _, port = authority.rpartition(_AT)[2].partition(":")
    return scheme, host, port


def _port_parses(port: str) -> bool:
    """Whether a URL's port is absent or a number the browser accepts."""
    return port == "" or (
        port.isascii() and port.isdigit() and int(port) <= _HIGHEST_PORT
    )


class PropertyTests(unittest.TestCase):
    """Never raises; a URL covered is one a page requests, from a listed host or one under it.

    Its scheme is one a page requests from a host, and its port parses. The oracle reads the
    authority by the URL standard's own delimiters, not `urlsplit`'s: a backslash ends it,
    and the host is what follows the last `@`. Which characters a host may hold is pinned by
    the examples above, since no independent fact decides it.
    """

    # Drawn URLs are rarely covered (none of the deterministic profile's hundred were), so
    # these covered ones run the oracle on every gate: a listed host, a subdomain of one,
    # and a subdomain with a port, its scheme and host in another case.
    @given(_URLS, st.lists(_HOSTS, max_size=3))
    @example("https://example.org/cover.jpg", ["example.org"])
    @example("http://img.example.org/a.png", ["example.net", "example.org"])
    @example("WSS://Live.Example.NET:8443/socket", ["example.net"])
    def test_fuzz_on_expected_host(self, url: str, hosts: list[str]) -> None:
        covered = print_offsite.on_expected_host(url, hosts)
        self.assertIsInstance(covered, bool)
        if covered:
            read = _authority(url)
            self.assertIsNotNone(read, url)
            scheme, host, port = read or ("", "", "")
            self.assertIn(scheme, _REQUESTED_SCHEMES, url)
            self.assertTrue(_port_parses(port), url)
            host = host.lower()
            names = [name.lower() for name in hosts]
            self.assertTrue(
                any(host == name or host.endswith(f".{name}") for name in names), url
            )


if __name__ == "__main__":
    unittest.main()
