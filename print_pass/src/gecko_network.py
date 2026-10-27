"""A Firefox state's network record, read back from its Resource Timing rows and its log.

This module's premise: the engine has no listener on a Firefox page's requests, as the
Chromium engine has in Playwright's request listeners and its route, so a Firefox state's
record is read back once the page has settled: from the rows the page's own Resource Timing
holds, the document's own navigation row, and the lines
Firefox wrote to geckodriver's log while the state ran. `network_record` makes of them a
`NetworkRecord` that means what a Chromium state's means, entry for entry, so that a report
reads one way under either engine; a page error and the CSP violations alone read otherwise,
as below:

- a request off the served origin that got no status is `blocked`, as its whole URL: the
  engine's isolation refuses every one, another port on loopback included.
  `print_playwright.is_loopback` draws the line, so that a URL falls on the same side of it
  under either engine;
- a request on the served origin that got no status `failed`, as `<path> (no response)`,
  since Resource Timing gives no reason; one whose status is 400 or more is an HTTP error,
  `<status> <path>`, the document's own included. `print_playwright.request_label` names
  each, by its path and query and never the served port, so either engine names one alike;
- a `data:`, `blob:` or `about:` row is no network request, and is not recorded;
- a page error is a log line `JavaScript error: <url>, line <n>: <message>` whose script is
  the page's: on the served origin, or written into the page inline, in a `srcdoc` frame
  (`about:srcdoc`) or a `data:` URL. It is recorded as its message, which keeps its error's
  class name (`ReferenceError: boom is not defined`): Playwright's Chromium text leaves the
  name off, and no general mapping from one to the other exists. The browser's own scripts'
  lines (`resource://`, `chrome://`), an extension's (`moz-extension://`) and every other
  line are not the page's;
- `csp_violations` is always empty: the shared CSP collector is an init script reporting
  through a Playwright binding, and WebDriver's classic commands offer neither.

Only the status decides. A transfer size of 0 is also what a response from the cache reads,
so it says nothing the status does not, and no row carries it.

Two things the rows can show lie outside the record. `isolation_failures` lists how the page
left loopback -- a request answered off it, or a document off it -- and is the engine's
signal to raise `EngineError` (exit 2) before it prints: such a page measures nothing
honestly, and the record has no true name for it. A document at a local scheme (`about:`,
`data:`, `blob:`) gives no line there: the engine refuses a document that is not on the
served origin by a check of its own. `timing_problems` warns when the
rows are as many as the browser's buffer holds, which may have dropped some. What the record
cannot list at all is `CANNOT_OBSERVE`, for a report to state, so that a quiet record is
never read as a quiet page.

Entry point assumptions: the rows are the page's own, read back as `TimingEntry`s; the log
text is the state's slice of geckodriver's log; `base_url` is the served origin. Pure:
nothing here touches a browser, and nothing raises on any well-typed input.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Final, Literal, TypedDict
from urllib.parse import urlsplit

from print_judge import warning
from print_offsite import NETWORK_SCHEMES
from print_playwright import is_loopback, request_label
from print_record import NetworkRecord, Problem

#: How many rows the browser's Resource Timing buffer holds before it stops adding them:
#: the default the specification sets, which a page may raise for itself.
RESOURCE_TIMING_BUFFER: Final = 250

#: What a Firefox state's network record cannot list, one sentence each, for a report to
#: state beside the records, so that a quiet record is never read as a quiet page.
CANNOT_OBSERVE: Final[tuple[str, ...]] = (
    "A WebSocket the page opens is refused, but never listed: Resource Timing does not "
    "time one.",
    "The network record is read from Resource Timing, so a request the document never "
    "timed is not seen.",
    "The record is what the page's own Resource Timing still holds when it is read, so a "
    f"page that clears it, shrinks its buffer below {RESOURCE_TIMING_BUFFER}, or replaces "
    "window.performance decides what is listed, and a quiet record is not proof of a quiet "
    "page.",
    "A connection Firefox itself makes, outside any page, is refused, but never listed: "
    "no page timed it.",
    "Requests made while printing are not in the record, because Resource Timing is read "
    "before the print.",
    "A cross-origin no-cors response reads no status whether or not a server answered it "
    "(measured), so an answered one on loopback is listed as failing with no response, and "
    "one off loopback cannot be told from one refused.",
    "Firefox's crash helper is handed the reader's own per-user temporary folder, which "
    "TMPDIR does not redirect, and a crash may write there.",
    "Which addresses Firefox's non-local guard counts as local was not measured, and the "
    "guard covers direct TCP connections alone.",
    "No Content Security Policy violation is heard, so csp_violations is always empty: the "
    "collector is an init script reporting through a Playwright binding, and WebDriver's "
    "classic commands offer neither.",
)

#: Why a loopback request that got no status failed: Resource Timing gives no reason.
NO_RESPONSE: Final = "no response"

# The status a row reads when no response arrived.
_NO_STATUS: Final = 0

# The lowest HTTP status recorded as an error: every 4xx and 5xx, the client's and the
# server's alike.
_HTTP_ERROR_FLOOR: Final = 400

# The openings of an isolation failure's two kinds of line, each before `<status> <url>`.
_DOCUMENT_OFF_LOOPBACK: Final = "document off loopback"
_ANSWERED_OFF_LOOPBACK: Final = "request answered off loopback"

# A page error as Firefox writes it to standard output, which geckodriver logs: the
# script's URL, its line, then the error's message, the rest of the line. Matched from the
# start of a line, so a line that only quotes one -- the page's own console output, which
# Firefox writes behind a prefix of its own -- is not one.
_PAGE_ERROR: Final = re.compile(
    r"JavaScript error: (?P<url>.*?), line [0-9]+: (?P<message>.*)"
)

# An object URL's scheme: its origin is that of the URL after it.
_BLOB_SCHEME: Final = "blob"

# Where a script the page wrote into itself runs, beside its own origin: a frame made from
# its `srcdoc`, whose URL Firefox writes as this whole, and a `data:` URL. An eval inside
# either is named by that URL, then a space and where the eval ran.
_SRCDOC_URL: Final = "about:srcdoc"
_DATA_PREFIX: Final = "data:"

# Where a row's request went: the loopback address, off it, or nowhere, as a `data:` URL.
type _Reach = Literal["loopback", "off-loopback", "local"]
_LOOPBACK: Final[_Reach] = "loopback"
_OFF_LOOPBACK: Final[_Reach] = "off-loopback"
_LOCAL: Final[_Reach] = "local"

# A URL's origin as compared here: its scheme, host and port.
type _Origin = tuple[str, str, int | None]


class TimingEntry(TypedDict):
    """One Resource Timing row as the page reads it: the URL asked for, and its status.

    The status is 0 when no response arrived. The document's own navigation row has the
    same two fields, under the same names.
    """

    name: str
    responseStatus: int


# fuzz-target
def network_record(
    entries: Sequence[TimingEntry],
    document: TimingEntry,
    base_url: str,
    log_text: str,
) -> NetworkRecord:
    """The record of a state's network: what its page asked for and did not get, and threw.

    `entries` are the page's Resource Timing rows and `document` the document's own
    navigation row, judged first, since the document's response is the first a page gets.
    `base_url` is the served origin, and `log_text` the state's slice of geckodriver's log.
    Each list keeps the order its rows or lines came in.
    """
    rows = (document, *entries)
    loopback = _reaching(rows, _LOOPBACK, base_url)
    return NetworkRecord(
        failed=tuple(
            f"{request_label(row['name'], served=base_url)} ({NO_RESPONSE})"
            for row in loopback
            if row["responseStatus"] == _NO_STATUS
        ),
        http_errors=tuple(
            f"{row['responseStatus']} {request_label(row['name'], served=base_url)}"
            for row in loopback
            if row["responseStatus"] >= _HTTP_ERROR_FLOOR
        ),
        blocked=tuple(
            row["name"]
            for row in _reaching(rows, _OFF_LOOPBACK, base_url)
            if row["responseStatus"] == _NO_STATUS
        ),
        page_errors=_page_errors(log_text, base_url),
        # The CSP collector is an init script reporting through a Playwright binding, and
        # WebDriver's classic commands offer neither, so no violation is heard here.
        csp_violations=(),
    )


def isolation_failures(
    entries: Sequence[TimingEntry], document: TimingEntry, base_url: str
) -> tuple[str, ...]:
    """How the state's page left its origin, one line each: the signal to refuse the state.

    The document's own row comes first whenever it is off loopback, at any status: refused,
    the browser's own error page stands in the served page's place, and that is what would
    print. Then each request off the origin served at `base_url` that got a status: refused,
    it would read 0, so a server answered it. Each line is `<what>: <status> <url>`.

    Any line here is the engine's signal to raise `EngineError` (exit 2) before it prints:
    a page whose isolation did not hold measures nothing honestly, so the run refuses rather
    than report it. A document at a local scheme (`about:`, `data:`, `blob:`) gives no line
    here, since it reached nothing off loopback; the engine refuses a document that is not on
    the served origin by a check of its own.
    """
    strayed = _reaching((document,), _OFF_LOOPBACK, base_url)
    answered = [
        row
        for row in _reaching(entries, _OFF_LOOPBACK, base_url)
        if row["responseStatus"] != _NO_STATUS
    ]
    return (
        *(_failure(_DOCUMENT_OFF_LOOPBACK, row) for row in strayed),
        *(_failure(_ANSWERED_OFF_LOOPBACK, row) for row in answered),
    )


def timing_problems(entries: Sequence[TimingEntry]) -> tuple[Problem, ...]:
    """What the rows show beyond the record: a warning when they fill the browser's buffer.

    The buffer stops taking rows once full, so a request made after it filled is not listed.
    """
    if len(entries) < RESOURCE_TIMING_BUFFER:
        return ()
    return (
        warning(
            f"network record may be incomplete: Resource Timing listed {len(entries)} "
            f"requests, and the browser stops listing at {RESOURCE_TIMING_BUFFER}"
        ),
    )


def _failure(what: str, row: TimingEntry) -> str:
    """One isolation failure's line: what it is, then the row's status and whole URL.

    A status of 0 reads `(no response)`, as the record words a request that got none.
    """
    status = row["responseStatus"]
    worded = f"({NO_RESPONSE})" if status == _NO_STATUS else str(status)
    return f"{what}: {worded} {row['name']}"


def _reaching(
    rows: Iterable[TimingEntry], reach: _Reach, base_url: str
) -> list[TimingEntry]:
    """The rows whose request went where `reach` says, in order."""
    return [row for row in rows if _reach(row["name"], base_url) == reach]


def _reach(url: str, base_url: str) -> _Reach:
    """Where a request for `url` went: the served origin, off it, or nowhere."""
    if not is_loopback(url, served=base_url):
        return _OFF_LOOPBACK
    # `is_loopback` parsed `url` to say so, so this parse cannot raise.
    return _LOOPBACK if urlsplit(url).scheme in NETWORK_SCHEMES else _LOCAL


def _page_errors(log_text: str, base_url: str) -> tuple[str, ...]:
    """The message of each page error `log_text` holds from the page's scripts, in order."""
    served = _origin(base_url)
    matches = (_PAGE_ERROR.match(line) for line in log_text.splitlines())
    return tuple(
        match["message"]
        for match in matches
        if match is not None and _is_the_pages(match["url"], served)
    )


def _is_the_pages(url: str, served: _Origin | None) -> bool:
    """Whether a script at `url` is the page's own: written into it inline, or on `served`.

    An origin that cannot be read matches none: two unread origins are not one.
    """
    if url.startswith(_DATA_PREFIX) or url.partition(" ")[0] == _SRCDOC_URL:
        return True
    origin = _origin(url)
    return origin is not None and origin == served


def _origin(url: str) -> _Origin | None:
    """`url`'s scheme, host and port; None when it names no host, or does not parse.

    An object URL's origin is the one it was made on: the URL after its scheme.
    """
    try:
        parts = urlsplit(url)
        if parts.scheme == _BLOB_SCHEME:
            parts = urlsplit(parts.path)
        port = parts.port  # raises on a port that is not one
    except ValueError:
        return None
    if parts.hostname is None:
        return None
    return (parts.scheme, parts.hostname, port)
