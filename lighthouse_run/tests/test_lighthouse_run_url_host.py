"""Grouped suite: the host `--url` names, judged at parse time as the request layer judges it.

urllib percent-decodes the host, writes the `Host` header itself from the decoded text as
Latin-1, and the resolver IDNA-encodes it before any lookup. So a host holding a percent-escape
was not the host the parse-time checks saw: `%3a` became a port, `%40` slipped past the
credentials check, and `%2e%2e` was a doubled dot. A non-ASCII host went out as raw bytes or
raised before connecting, and a name IDNA cannot encode -- an empty label, one over 63
characters -- raised at the resolver. Each is refused here with exit 2, the url never echoed: a
non-ASCII name with its `xn--` form asked for, and any percent-escape with a plain host asked
for. A browser decodes an escape too and refuses many (a decoded colon, at sign, space, a
scoped IPv6 zone), so every `%` is refused and the host typed is the host each check judged.
Every host the request layer and a browser can both use, typed plainly, is kept: an
ASCII name, `localhost`, and an IP literal.

No test here opens a socket or resolves a name: every one stops at parsing. Names are under
`example.invalid`, which RFC 2606 reserves so that no resolver answers them, and addresses are
loopback or documentation ones.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lighthouse_run  # noqa: E402 - import follows the sys.path bootstrap above

#: The longest label a domain name may hold (RFC 1035: 63 octets), and one a character over.
_LONGEST_LABEL = "a" * 63
_OVERLONG_LABEL = "a" * 64

#: What a refusal of a host IDNA cannot encode says, naming no part of the url.
_REFUSAL = "the url's host is not a valid domain name"

#: What a refusal of a non-ASCII host says: it asks for the xn-- form.
_ASCII_REFUSAL = "the url's host must be ASCII"

#: What a refusal of a host holding a percent-escape says.
_ESCAPE_REFUSAL = "the url's host holds a percent-escape"


class UrlHostTests(unittest.TestCase):
    """A host the request layer cannot use exits 2 unechoed; every one it can use is kept."""

    def assert_refused_unechoed(
        self, base: str, *parts: str, expected: str = _REFUSAL
    ) -> None:
        """Assert `main` refuses `--url base` with exit 2 saying `expected`, echoing no `parts`."""
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as caught,
        ):
            lighthouse_run.main(["--root", ".", "--url", base, "--expect", "package"])
        self.assertEqual(caught.exception.code, 2)
        written = out.getvalue() + err.getvalue()
        # Flattened: argparse wraps its message to the terminal width.
        self.assertIn(expected, " ".join(err.getvalue().split()))
        # Never echoed: a hosted address may be private, and a url may carry credentials.
        for echoed in (base, *parts):
            self.assertNotIn(echoed, written)

    def parsed_base(self, base: str) -> str:
        """The base url the parser keeps for `--url base`."""
        args = lighthouse_run.build_parser().parse_args(
            ["--root", ".", "--url", base, "--expect", "package"]
        )
        kept: str = args.url
        return kept

    def test_a_label_over_63_characters_is_refused(self) -> None:
        for host in (
            f"{_OVERLONG_LABEL}.example.invalid",
            f"site.{_OVERLONG_LABEL}.invalid",
        ):
            with self.subTest(host=host):
                self.assert_refused_unechoed(f"https://{host}", _OVERLONG_LABEL)

    def test_an_empty_label_is_refused(self) -> None:
        # A doubled dot, or a leading one: a trailing dot is a complete name, kept below.
        for host in ("site..example.invalid", ".site.example.invalid"):
            with self.subTest(host=host):
                self.assert_refused_unechoed(f"https://{host}:8443/under", host)

    def test_a_non_ascii_host_is_refused_with_its_xn_form_asked_for(self) -> None:
        # urllib sends the Host header as Latin-1: the first would go out as raw bytes, not its
        # xn-- form, and the others raise before connecting. The last two IDNA rejects as well.
        for label in (
            "b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher",
            "\N{CYRILLIC SMALL LETTER PE}\N{CYRILLIC SMALL LETTER ER}\N{CYRILLIC SMALL LETTER I}",
            "\N{CJK UNIFIED IDEOGRAPH-4E2D}\N{CJK UNIFIED IDEOGRAPH-6587}",
            "caf\N{REPLACEMENT CHARACTER}",
            "\N{LATIN SMALL LETTER U WITH DIAERESIS}" * 60,
        ):
            with self.subTest(label=label):
                self.assert_refused_unechoed(
                    f"https://{label}.example.invalid", label, expected=_ASCII_REFUSAL
                )

    def test_a_host_holding_a_percent_escape_is_refused(self) -> None:
        # Each decodes to a different host than the one typed: a doubled dot, a character no
        # host holds, a port, credentials, whitespace, a non-ASCII name, a scoped IPv6 zone.
        for base, decoded in (
            ("https://a%2e%2eb.example.invalid", "a..b"),
            ("https://%ff.example.invalid", "\N{REPLACEMENT CHARACTER}"),
            ("https://site.example.invalid%3a8443", "invalid:8443"),
            ("https://user%40site.example.invalid", "user@site"),
            ("https://site%20two.example.invalid", "site two"),
            (
                "https://b%C3%BCcher.example.invalid",
                "b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher",
            ),
            ("http://[fe80::1%25en0]:8443", "fe80::1%en0"),
        ):
            with self.subTest(base=base):
                # Neither the host as typed nor as decoded is echoed.
                typed = urlsplit(base).hostname
                self.assertIsNotNone(typed)
                self.assert_refused_unechoed(
                    base, str(typed), decoded, expected=_ESCAPE_REFUSAL
                )

    def test_an_ascii_host_idna_encodes_is_kept(self) -> None:
        for base, kept in (
            (
                f"https://{_LONGEST_LABEL}.example.invalid",
                f"https://{_LONGEST_LABEL}.example.invalid",
            ),
            (
                "https://XN--BCHER-KVA.example.invalid/",
                "https://xn--bcher-kva.example.invalid",
            ),
            ("https://site.example.invalid.", "https://site.example.invalid."),
        ):
            with self.subTest(base=base):
                self.assertEqual(self.parsed_base(base), kept)

    def test_loopback_and_ip_literal_hosts_are_kept_as_before(self) -> None:
        for base, kept in (
            ("http://127.0.0.1:8443/", "http://127.0.0.1:8443"),
            ("http://LOCALHOST:8443", "http://localhost:8443"),
            ("http://[::1]:8443/site/", "http://[::1]:8443/site"),
            ("https://192.0.2.10", "https://192.0.2.10"),
            ("https://[2001:DB8::10]:8443", "https://[2001:db8::10]:8443"),
        ):
            with self.subTest(base=base):
                self.assertEqual(self.parsed_base(base), kept)


if __name__ == "__main__":
    unittest.main()
