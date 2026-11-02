"""Read the audits that explain a run's numbers out of one Lighthouse report.

`lighthouse_report` reads the numbers a run is MEASURED by, and refuses a report missing any of
them: a missing metric is a failed run. This module reads what explains those numbers
(render-blocking requests, the request dependency chain, unused CSS, the LCP element, cache
lifetimes, the document's own latency) and refuses nothing. Each field is read on its own
and is `None` when Lighthouse did not write it, or wrote it in a shape this reader does not
know, so an explanation missing from one run never costs the run its numbers.

Every field is read from where Lighthouse 13.4.1 writes it; the audit ids are the constants
below. Three readings are not obvious from the field names:

- **Request keys.** A render-blocking request on the measured page's own origin is keyed by
  its path, with any query; one on another origin by its full url. So a loopback summary and
  a hosted one line up request for request, and a hosted origin is not repeated per request.
  The page's origin is the report's `finalDisplayedUrl`, else its `mainDocumentUrl`.
- **A blocking request's `wastedMs` is its simulated download time**, not a saving, and
  Lighthouse leaves it off a request under 50 ms, or one not in its first-paint graph. So a
  request recorded with `None` downloaded in under 50 ms (a bound, not an unknown), or
  its `wastedMs` was unreadable: present, but not a finite number. Real desktop runs leave
  it off most of their requests.
- **A text LCP has no discovery checklist**: no request fetched the element, so Lighthouse
  writes no `details` for the discovery insight, and `discovery` is `None` for "not
  applicable". Lighthouse also writes none when a run has no LCP event or no document
  request, or for an LCP image fetched without a network request, and a checklist this
  reader cannot read is `None` too; in practice it is text.

**Privacy.** The LCP element is recorded by its CSS selector alone, never by the
`nodeLabel` or `snippet` Lighthouse writes beside it: those carry the page's own text.

Entry point assumptions: none. These are pure functions over an already-parsed JSON value.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TypedDict
from urllib.parse import SplitResult, urlsplit

#: The audits read, by their Lighthouse ids.
RENDER_BLOCKING = "render-blocking-insight"
DEPENDENCY_TREE = "network-dependency-tree-insight"
UNUSED_CSS = "unused-css-rules"
LCP_BREAKDOWN = "lcp-breakdown-insight"
LCP_DISCOVERY = "lcp-discovery-insight"
CACHE = "cache-insight"
DOCUMENT_LATENCY = "document-latency-insight"

#: The LCP discovery checklist's flags, recorded under Lighthouse's own names.
DISCOVERY_FLAGS = ("priorityHinted", "requestDiscoverable", "eagerlyLoaded")

#: The `type` of the dependency-tree section holding the chains; its siblings are text.
_NETWORK_TREE = "network-tree"

#: The `type` of each item read from the LCP insights. Each is found by its type, never by
#: its position: Lighthouse lists the breakdown's table before its node, and the discovery
#: insight's checklist before its node, and neither order is a contract.
_CHECKLIST = "checklist"
_NODE = "node"
_TABLE = "table"


class RenderBlocking(TypedDict):
    """Lighthouse's saving from removing EVERY blocking request at once, and each request.

    A request maps to its simulated download time (`wastedMs`), None for one under 50 ms
    or unreadable.
    """

    fcp_saving_ms: float | None
    lcp_saving_ms: float | None
    requests: dict[str, float | None] | None


class DependencyChain(TypedDict):
    """The longest request chain's duration, and how deep the chains nest."""

    longest_ms: float | None
    depth: int | None


class Lcp(TypedDict):
    """The LCP element: its selector, its time by subpart, and how its request was found."""

    selector: str | None
    subparts: dict[str, float] | None
    discovery: dict[str, bool | None] | None


class Cache(TypedDict):
    """Bytes a longer cache lifetime would save a repeat visit: all, and the page's own."""

    wasted_bytes: float | None
    first_party_wasted_bytes: float | None


class DocumentLatency(TypedDict):
    """The page document's own request: the server's answer, redirects, its size, compression."""

    server_response_ms: float | None
    redirect_ms: float | None
    uncompressed_bytes: float | None
    uses_compression: bool | None


class RunAudits(TypedDict):
    """What one run's report says about why its numbers are what they are."""

    render_blocking: RenderBlocking
    dependency_chain: DependencyChain
    unused_css_bytes: float | None
    lcp: Lcp
    cache: Cache
    document_latency: DocumentLatency


def _at(value: object, *path: str) -> object:
    """The value at `path` under `value`, or None where a step is missing or not an object."""
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _list(value: object) -> Sequence[object] | None:
    """`value` as a JSON array, or None."""
    return value if isinstance(value, list) else None


def _number(value: object) -> float | None:
    """`value` as a finite float, or None; never a bool, which JSON keeps apart."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _flag(value: object) -> bool | None:
    """`value` as a bool, or None."""
    return value if isinstance(value, bool) else None


def _text(value: object) -> str | None:
    """`value` as text, or None."""
    return value if isinstance(value, str) else None


def _first_item(items: object, *path: str, equals: str) -> object:
    """The first entry of the array `items` whose value at `path` is `equals`, or None."""
    for item in _list(items) or ():
        if _at(item, *path) == equals:
            return item
    return None


def _split(url: object) -> SplitResult | None:
    """`url` split into its parts, or None when it is not text urlsplit accepts."""
    if not isinstance(url, str):
        return None
    try:
        return urlsplit(url)
    except ValueError:
        # urlsplit refuses a malformed host, such as an unclosed IPv6 bracket.
        return None


def _origin(parts: SplitResult | None) -> str | None:
    """The `scheme://host[:port]` of a split url, or None when it names no host."""
    if parts is None or not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _page_origin(report: object) -> str | None:
    """The measured page's origin: its displayed url's, else its main document's."""
    displayed = _origin(_split(_at(report, "finalDisplayedUrl")))
    return displayed or _origin(_split(_at(report, "mainDocumentUrl")))


def _request_key(url: object, origin: str) -> str | None:
    """How a request is keyed: its path and query on `origin`, its full url elsewhere."""
    parts = _split(url)
    if parts is None:
        return None
    if _origin(parts) != origin:
        return parts.geturl()
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path


def _blocking_requests(
    rows: object, origin: str | None
) -> dict[str, float | None] | None:
    """Each render-blocking request's simulated download time, by request key.

    A request Lighthouse gave no `wastedMs` maps to None: it downloaded in under 50 ms, or
    is not in the first-paint graph. One whose `wastedMs` is unreadable, not a finite
    number, maps to None too. None as a whole when the page's origin is unknown or a row
    cannot be placed: a map missing a request would read as complete. A row listing the
    same url again keeps its last time.
    """
    listed = _list(rows)
    if origin is None or listed is None:
        return None
    requests: dict[str, float | None] = {}
    for row in listed:
        key = _request_key(_at(row, "url"), origin)
        if key is None:
            return None
        requests[key] = _number(_at(row, "wastedMs"))
    return requests


def _render_blocking(audit: object, origin: str | None) -> RenderBlocking:
    """The render-blocking insight's savings and requests."""
    return {
        "fcp_saving_ms": _number(_at(audit, "metricSavings", "FCP")),
        "lcp_saving_ms": _number(_at(audit, "metricSavings", "LCP")),
        "requests": _blocking_requests(_at(audit, "details", "items"), origin),
    }


def _chain_depth(chains: object) -> int | None:
    """How deep `chains` nest, the document itself counting as 1; None if a level is not a map.

    Walked with an explicit stack rather than by recursion: a report nested deeper than the
    interpreter's recursion limit would otherwise raise instead of reading as a number.
    """
    deepest = 0
    pending: list[tuple[object, int]] = [(chains, 1)]
    while pending:
        level, depth = pending.pop()
        if not isinstance(level, Mapping):
            return None
        for node in level.values():
            deepest = max(deepest, depth)
            pending.append((_at(node, "children"), depth + 1))
    return deepest


def _dependency_chain(audit: object) -> DependencyChain:
    """The dependency tree's longest chain and depth, from its network-tree section."""
    sections = _at(audit, "details", "items")
    tree = _at(_first_item(sections, "value", "type", equals=_NETWORK_TREE), "value")
    return {
        "longest_ms": _number(_at(tree, "longestChain", "duration")),
        "depth": _chain_depth(_at(tree, "chains")),
    }


def _subparts(rows: object) -> dict[str, float] | None:
    """Each LCP subpart's duration by name; None when any row cannot be read."""
    listed = _list(rows)
    if listed is None:
        return None
    subparts: dict[str, float] = {}
    for row in listed:
        name, duration = _text(_at(row, "subpart")), _number(_at(row, "duration"))
        if name is None or duration is None:
            return None
        subparts[name] = duration
    return subparts


def _discovery(audit: object) -> dict[str, bool | None] | None:
    """The LCP request's discovery checklist, flag by flag; None when there is none."""
    items = _at(audit, "details", "items")
    checklist = _at(_first_item(items, "type", equals=_CHECKLIST), "items")
    if not isinstance(checklist, Mapping):
        return None
    return {flag: _flag(_at(checklist, flag, "value")) for flag in DISCOVERY_FLAGS}


def _lcp(breakdown: object, discovery: object) -> Lcp:
    """The LCP element by selector, its subparts, and its discovery checklist."""
    items = _at(breakdown, "details", "items")
    return {
        "selector": _text(_at(_first_item(items, "type", equals=_NODE), "selector")),
        "subparts": _subparts(_at(_first_item(items, "type", equals=_TABLE), "items")),
        "discovery": _discovery(discovery),
    }


def _cache(audit: object, origin: str | None) -> Cache:
    """Cache-lifetime waste summed over every request, and over the page's own.

    Both are None when a row's bytes cannot be read, since a partial sum would read as the
    whole. A row whose url names no origin counts toward the total only.
    """
    unknown: Cache = {"wasted_bytes": None, "first_party_wasted_bytes": None}
    rows = _list(_at(audit, "details", "items"))
    if rows is None:
        return unknown
    total, first_party = 0.0, 0.0
    for row in rows:
        wasted = _number(_at(row, "wastedBytes"))
        if wasted is None:
            return unknown
        total += wasted
        if origin is not None and _origin(_split(_at(row, "url"))) == origin:
            first_party += wasted
    return {
        "wasted_bytes": total,
        "first_party_wasted_bytes": None if origin is None else first_party,
    }


def _document_latency(audit: object) -> DocumentLatency:
    """The document request's server time, redirect time, size and compression."""
    debug = _at(audit, "details", "debugData")
    return {
        "server_response_ms": _number(_at(debug, "serverResponseTime")),
        "redirect_ms": _number(_at(debug, "redirectDuration")),
        "uncompressed_bytes": _number(_at(debug, "uncompressedResponseBytes")),
        "uses_compression": _flag(
            _at(audit, "details", "items", "usesCompression", "value")
        ),
    }


# fuzz-target
def extract_audits(report: object) -> RunAudits:
    """Read every recorded audit field from one parsed report; never raises.

    A field Lighthouse did not write, or wrote in another shape, is None; so is every field
    of a value that is not a report at all.
    """
    audits = _at(report, "audits")
    origin = _page_origin(report)
    return {
        "render_blocking": _render_blocking(_at(audits, RENDER_BLOCKING), origin),
        "dependency_chain": _dependency_chain(_at(audits, DEPENDENCY_TREE)),
        "unused_css_bytes": _number(
            _at(audits, UNUSED_CSS, "details", "overallSavingsBytes")
        ),
        "lcp": _lcp(_at(audits, LCP_BREAKDOWN), _at(audits, LCP_DISCOVERY)),
        "cache": _cache(_at(audits, CACHE), origin),
        "document_latency": _document_latency(_at(audits, DOCUMENT_LATENCY)),
    }
