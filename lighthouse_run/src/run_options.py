"""Parse and check the command line's values, before anything is measured.

Each option's value parser lives here, and so does the one check no single parser can make:
whether the target options, taken together, name exactly one tree. The one exception is
`--pages`: every page is confirmed through the shared `loopback_site`, so its parser,
`loopback_site.page_path`, lives there with the rule it enforces. Every refusal surfaces as
argparse's own error, exit 2, so a run that could not be honest never starts.

Entry point assumptions: pure functions over the text the operator typed; nothing here touches
the disk or the network.
"""

from __future__ import annotations

import argparse
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

#: The fewest runs a median is taken over. A measured site's score has swung 74-96 between
#: identical runs, so one run is not evidence of anything.
MIN_RUNS = 3

#: The schemes a hosted copy may be fetched over.
URL_SCHEMES = ("http", "https")

#: The Unicode category of a control character, such as a NUL or a DEL.
_CONTROL_CATEGORY = "Cc"


def run_count(text: str) -> int:
    """Parse `--runs`, refusing fewer than a median needs."""
    try:
        count = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if count < MIN_RUNS:
        raise argparse.ArgumentTypeError(
            f"{count} is below {MIN_RUNS}: a median of fewer runs is not evidence here"
        )
    return count


def _holds_whitespace_or_control(text: str) -> bool:
    """Whether any character of `text` is whitespace or a control character."""
    return any(
        char.isspace() or unicodedata.category(char) == _CONTROL_CATEGORY
        for char in text
    )


def _host_problem(hostname: str) -> str | None:
    """Why the request layer or a browser cannot use `hostname`, or `None` when both can.

    urllib percent-decodes a host before using it, so an escaped host is not the host the
    checks on the typed text saw (`%3a` becomes a port, `%40` slips past the credentials
    check). A browser decodes one too, and refuses what decodes to a colon, an at sign, a space
    or a byte that is not ASCII, and any scoped IPv6 zone. Every `%` is refused, so the host as
    typed is the host each check judged and the host used. urllib writes the `Host` header itself as Latin-1, so a
    non-ASCII host would go out as raw bytes or raise before connecting: only ASCII is sent
    intact, a non-ASCII name in its `xn--` form. The resolver IDNA-encodes every host before
    any lookup; on ASCII, IDNA refuses only an empty label and one over 63 characters, raising
    a `UnicodeError` no handler past parsing expects. No message quotes the host.
    """
    if "%" in hostname:
        return (
            "the url's host holds a percent-escape: type it plainly, a non-ASCII name in its "
            "xn-- form (a scoped IPv6 address cannot be measured: a browser refuses its zone)"
        )
    if not hostname.isascii():
        return "the url's host must be ASCII: give a non-ASCII domain name in its xn-- form"
    try:
        hostname.encode("idna")
    except UnicodeError:
        # Dropped, not chained: its message quotes the characters of the host it refused.
        return "the url's host is not a valid domain name: a label is empty or over 63 characters"
    return None


def _base_url_problem(text: str) -> str | None:
    """Why `text` is not a base url a hosted copy can be measured at, or `None` when it is.

    No message echoes `text`: a refused url may carry credentials.
    """
    # Tested on the text, before it is split: the splitter silently drops a tab or a newline,
    # so a host typed with one would be measured as another host.
    if _holds_whitespace_or_control(text):
        return "the url holds whitespace or a control character"
    try:
        parts = urlsplit(text)
    except ValueError:
        # Not chained or quoted: the splitter's message can quote part of the url.
        return "the url is not well formed"
    if parts.scheme not in URL_SCHEMES:
        return f"the scheme must be {' or '.join(URL_SCHEMES)}"
    if "@" in parts.netloc:
        return "credentials (user@) are not accepted in the url"
    if not parts.hostname:
        return "the url has no host"
    host_problem = _host_problem(parts.hostname)
    if host_problem:
        return host_problem
    try:
        # Read only for the ValueError a malformed or out-of-range port raises.
        _ = parts.port
    except ValueError:
        return "the url's port is not a number in range"
    # Tested on the text, not the parts: a bare "?" or "#" leaves both parts empty.
    if "?" in text:
        return "a base url takes no query"
    if "#" in text:
        return "a base url takes no fragment"
    # Whitespace and control characters are refused above, so all a request line cannot
    # carry that is left is a non-ASCII character, and a host holding one is refused above.
    # Encoding is right for the base's path, as it is not for a page: only a page is mapped
    # to a file on disk.
    if not parts.path.isascii():
        return "the url's path holds a non-ASCII character: percent-encode it"
    return None


def base_url(text: str) -> str:
    """Parse `--url`: an http(s) base with a host and nothing after its path.

    The scheme and host are lowercased, as neither is case-sensitive, so one host is recorded
    one way however it was typed; the path is kept as typed, since a server may treat its case
    as significant. A trailing slash is stripped, since every page starts with one:
    `https://<host>/` and `https://<host>` measure the same urls.
    """
    problem = _base_url_problem(text)
    if problem is not None:
        raise argparse.ArgumentTypeError(problem)
    parts = urlsplit(text)
    # The whole netloc: it holds no credentials, which are refused, so it is the host and any
    # port, and a port's digits have no case.
    normalized = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, "", ""))
    return normalized.rstrip("/")


def option_conflict(
    *, url: str | None, expect: Path | None, serve: Path | None
) -> str | None:
    """Why the target options do not name exactly one tree, or `None` when they do."""
    if url is None:
        if expect is None:
            return None
        return "--expect needs --url: it names the bytes a hosted copy must serve"
    if expect is None:
        return (
            "--url needs --expect: the folder holding the bytes the host must serve, "
            "so the tree measured is the tree named"
        )
    if serve is not None:
        return "--url and --serve exclude each other: measure one tree per run"
    return None
