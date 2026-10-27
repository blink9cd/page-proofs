"""Read the meta refresh a served page declares, before any browser opens it.

A page that is a `<meta http-equiv="refresh" content="0; url=2026/">` navigates away as it
loads, so a tool that loads it in a browser and then evaluates script races that navigation and
crashes. A tool calls `read_meta_refresh` first: it fetches the page over loopback, finds the
first refresh the document declares, and resolves its destination, so the tool can decide
whether the page is a declared redirect before the browser ever sees it.

The content is read by the HTML spec's "shared declarative refresh steps". A refresh with no
URL names the page itself, which is where a browser would go.

Private to `loopback_site`, which re-exports `MetaRefresh`, `parse_meta_refresh` and
`read_meta_refresh`, and the loopback names its server binds and answers to: they live here
because this module, imported by `loopback_site`, cannot import them back from it.

Entry point assumptions: imported by `loopback_site` alone. Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from loopbacksite_confirm import NO_REDIRECTS

#: The only interface the server listens on.
LOOPBACK_HOST = "127.0.0.1"

#: The other name a browser may use for the loopback interface.
LOOPBACK_NAME = "localhost"

#: Every host name `read_meta_refresh` will fetch from.
LOOPBACK_HOSTS = (LOOPBACK_HOST, LOOPBACK_NAME)

#: The only scheme the loopback server speaks.
_LOOPBACK_SCHEME = "http"

#: The charset a page is read in when its response names none, or one Python cannot decode.
_DEFAULT_CHARSET = "utf-8"

#: The `http-equiv` value that makes a meta a refresh, compared in ASCII lower case.
_REFRESH = "refresh"

#: HTML's ASCII whitespace.
_ASCII_WHITESPACE = frozenset("\t\n\f\r ")

#: The digits a refresh time is written in: ASCII only, unlike `str.isdigit`.
_ASCII_DIGITS = frozenset("0123456789")

#: What may follow the time.
_TIME_ENDERS = (";", ",")

#: The quotes a URL may open with.
_QUOTES = ("'", '"')

#: What the URL parser strips from both ends of a URL: C0 controls and space.
_URL_EDGE_CHARACTERS = "".join(map(chr, range(0x21)))

#: What the URL parser removes from anywhere in a URL: ASCII tab and newlines.
_URL_REMOVED = str.maketrans("", "", "\t\n\r")


@dataclass(frozen=True)
class MetaRefresh:
    """A page's first meta refresh: its delay in seconds and its absolute destination URL."""

    delay: float
    destination: str


def _skip_while(text: str, position: int, test: Callable[[str], bool]) -> int:
    """The first position at or after `position` whose character fails `test`."""
    while position < len(text) and test(text[position]):
        position += 1
    return position


def _is_whitespace(character: str) -> bool:
    """Whether `character` is HTML's ASCII whitespace."""
    return character in _ASCII_WHITESPACE


def _is_time_character(character: str) -> bool:
    """Whether `character` may continue a time: an ASCII digit or a dot."""
    return character == "." or character in _ASCII_DIGITS


def _unquoted(text: str) -> str:
    """`text`, cut at the matching quote when it opens with one; an unclosed one runs on."""
    if not text.startswith(_QUOTES):
        return text
    url = text[1:]
    close = url.find(text[0])
    return url if close == -1 else url[:close]


def _url_part(rest: str) -> str:
    """The URL in what follows the time, with any `url=` and quotes read off.

    The spec's quirk is kept: a remainder that starts like `url=` and then stops (`ur.html`,
    `url.html`) is the URL whole, quotes and all.
    """
    if rest[:1].lower() != "u":
        return _unquoted(rest)
    if rest[1:3].lower() != "rl":
        return rest
    equals = _skip_while(rest, len("url"), _is_whitespace)
    if rest[equals : equals + 1] != "=":
        return rest
    return _unquoted(rest[_skip_while(rest, equals + 1, _is_whitespace) :])


def _refresh_steps(content: str) -> tuple[float, str] | None:
    """The delay and raw URL a refresh content declares, or None when the steps reject it.

    The delay is the whole seconds the spec reads: the digits before any dot, none being 0.
    An empty URL means the page refreshes itself.
    """
    start = _skip_while(content, 0, _is_whitespace)
    digits_end = _skip_while(
        content, start, lambda character: character in _ASCII_DIGITS
    )
    if digits_end == start and content[start : start + 1] != ".":
        return None
    # float, not int: a time of hundreds of digits is legal, and float saturates to inf
    # where converting a huge int to float would raise.
    delay = float(content[start:digits_end] or "0")
    position = _skip_while(content, digits_end, _is_time_character)
    if position < len(content):
        if content[position] not in _TIME_ENDERS and not _is_whitespace(
            content[position]
        ):
            return None
        position = _skip_while(content, position, _is_whitespace)
        if content[position : position + 1] in _TIME_ENDERS:
            position = _skip_while(content, position + 1, _is_whitespace)
    url = _url_part(content[position:]) if position < len(content) else ""
    return delay, url


def _destination(url: str, page_url: str) -> str | None:
    """`url` cleaned as the URL parser cleans it, resolved against `page_url`; None if invalid."""
    cleaned = url.strip(_URL_EDGE_CHARACTERS).translate(_URL_REMOVED)
    try:
        return urljoin(page_url, cleaned)
    except ValueError:
        # urllib refuses what a browser's URL parser would too (an unclosed IPv6 bracket),
        # and a refresh whose URL does not parse is one the browser never follows.
        return None


def _first_attribute(attrs: Sequence[tuple[str, str | None]], name: str) -> str | None:
    """The first value of attribute `name`, as HTML keeps the first of a repeated attribute."""
    return next((value for key, value in attrs if key == name), None)


def _meta_refresh(
    attrs: Sequence[tuple[str, str | None]], page_url: str
) -> MetaRefresh | None:
    """The refresh one `<meta>`'s attributes declare, or None when it declares none."""
    equiv = _first_attribute(attrs, "http-equiv") or ""
    content = _first_attribute(attrs, "content")
    if not (equiv.isascii() and equiv.lower() == _REFRESH) or content is None:
        return None
    steps = _refresh_steps(content)
    if steps is None:
        return None
    delay, url = steps
    destination = _destination(url, page_url)
    return None if destination is None else MetaRefresh(delay, destination)


class _RefreshFinder(HTMLParser):
    """Keeps the first `<meta>` that declares a refresh the steps accept.

    The first that PARSES, not the first written: the spec sets the document's "will
    declaratively refresh" flag only once a refresh is accepted, so a browser ignores a
    rejected one and goes on to the next.
    """

    def __init__(self, page_url: str) -> None:
        """Find refreshes resolved against `page_url`; character references are decoded."""
        super().__init__(convert_charrefs=True)
        self._page_url = page_url
        self.found: MetaRefresh | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Read a `<meta>` while no refresh has been found yet."""
        if self.found is None and tag == "meta":
            self.found = _meta_refresh(attrs, self._page_url)


# fuzz-target
def parse_meta_refresh(html: str, page_url: str) -> MetaRefresh | None:
    """Pure: the document's first `<meta http-equiv="refresh">` (case-insensitive), or None.

    `destination` is resolved against `page_url`. A content the refresh steps reject, or a URL
    that does not parse, is no refresh, as a browser would ignore it.
    """
    finder = _RefreshFinder(page_url)
    finder.feed(html)
    finder.close()
    return finder.found


def _require_loopback(page_url: str) -> None:
    """Refuse, before any request, a URL that is not plain http to a loopback name.

    Raises:
        ValueError: the scheme is not http or the host is not a loopback name.
    """
    parts = urlsplit(page_url)
    if parts.scheme != _LOOPBACK_SCHEME or parts.hostname not in LOOPBACK_HOSTS:
        raise ValueError(
            f"{page_url!r} is not an http url on loopback "
            f"({' or '.join(LOOPBACK_HOSTS)}); refusing to fetch it"
        )


def _decode(body: bytes, charset: str | None) -> str:
    """`body` in its response's charset, or UTF-8; a byte that does not decode is replaced."""
    try:
        return body.decode(charset or _DEFAULT_CHARSET, errors="replace")
    except LookupError:
        # An unknown name, or a codec that is no text encoding: a browser falls back too.
        return body.decode(_DEFAULT_CHARSET, errors="replace")


def read_meta_refresh(page_url: str, *, timeout: float) -> MetaRefresh | None:
    """GET page_url (loopback only), decode it, and parse_meta_refresh it.

    No HTTP redirect is followed, so nothing off loopback is ever fetched: a redirect is
    raised as the `HTTPError` it is, like any error status, for the caller to report.

    Raises:
        ValueError: `page_url` is not http on loopback; nothing was requested.
        urllib.error.HTTPError: the server answered an error or redirect status.
        OSError: the server could not be reached.
    """
    _require_loopback(page_url)
    with NO_REDIRECTS.open(page_url, timeout=timeout) as reply:
        body: bytes = reply.read()
        charset = reply.headers.get_content_charset()
    return parse_meta_refresh(_decode(body, charset), page_url)
