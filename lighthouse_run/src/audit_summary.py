"""Reduce one page and preset's run audits to medians and tallies, and print them as a table.

What a run records about WHY its numbers are what they are (`lighthouse_audits`) varies from
run to run as the numbers do, so it is reduced the same way: every number to its median over
the runs that have it, each LCP subpart on its own. Each render-blocking request keeps its run
count beside its median: Lighthouse writes a request's simulated download time only at 50 ms
or more, so the median is over the runs that reached 50 ms alone, and the count -- out of the
result's measured runs -- says how many did. A request that crossed 50 ms in one run of five
then reads as one slow run, not as a typical one. A run whose time for the request, or whose
whole request map, could not be read adds to no count either, so a count below the measured
runs can also mean an unreadable run. A category -- the LCP element's selector, each discovery
flag, whether the document was compressed -- is tallied instead, as `{value: run count}`,
majority first, with an unknown or not-applicable run counted under `null` rather than
dropped, so a split between runs shows.

The printed table shows each category's majority, followed by its share of the runs, as
`(2/3)`, when the runs did not all agree.

Entry point assumptions: pure functions over audits `lighthouse_audits` already read.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import NamedTuple, TypedDict, TypeVar

import lighthouse_audits

#: Runs per value of a category, keyed by the value as JSON writes it (`null`, `true`, text).
Tally = dict[str, int]

#: How a tally spells an unknown or not-applicable run: None, as JSON writes it.
_NULL = json.dumps(None)

#: What one key's values over the runs reduce to: a median, or a median with its run count.
_Reduced = TypeVar("_Reduced")


class RequestMedian(TypedDict):
    """One render-blocking request over the runs that measured.

    `runs` counts the runs that gave the request a readable simulated download time, which
    Lighthouse writes only at 50 ms or more, out of the result's `measured_runs`, which also
    counts a run whose whole request map could not be read; `median_ms` is over those `runs`
    alone, and None when there were none.
    """

    median_ms: float | None
    runs: int


class RenderBlockingMedians(TypedDict):
    """Medians of the render-blocking savings, and each request's download time and count."""

    fcp_saving_ms: float | None
    lcp_saving_ms: float | None
    requests: dict[str, RequestMedian] | None


class DependencyChainMedians(TypedDict):
    """Medians of the longest chain and of the nesting depth."""

    longest_ms: float | None
    depth: float | None


class LcpMedians(TypedDict):
    """The LCP element's selectors and discovery flags tallied, its subparts' medians."""

    selector: Tally
    subparts: dict[str, float | None] | None
    discovery: dict[str, Tally]


class CacheMedians(TypedDict):
    """Medians of the cache-lifetime waste, in all and on the page's own origin."""

    wasted_bytes: float | None
    first_party_wasted_bytes: float | None


class DocumentLatencyMedians(TypedDict):
    """Medians of the document request's timings and size; its compression tallied."""

    server_response_ms: float | None
    redirect_ms: float | None
    uncompressed_bytes: float | None
    uses_compression: Tally


class AuditMedians(TypedDict):
    """One page and preset's audits, reduced over the runs that measured."""

    render_blocking: RenderBlockingMedians
    dependency_chain: DependencyChainMedians
    unused_css_bytes: float | None
    lcp: LcpMedians
    cache: CacheMedians
    document_latency: DocumentLatencyMedians


#: The printed table's columns after page and preset.
_HEADINGS = (
    "blocking FCP saving ms",
    "chain ms",
    "chain depth",
    "unused CSS bytes",
    "LCP element",
    "LCP discovery",
    "first-party cache waste bytes",
    "server ms",
    "compressed",
)

#: What a cell shows when nothing could be read.
_NOT_AVAILABLE = "n/a"

#: How the table names each discovery flag, in `DISCOVERY_FLAGS` order.
_FLAG_NAMES = ("hinted", "discoverable", "eager")

#: A flag's tallied value, as its cell shows it.
_FLAG_TEXT = {"true": "y", "false": "n"}
_COMPRESSION_TEXT = {"true": "yes", "false": "no"}

#: CSS combinators, and the whitespace around them, between a selector's compounds.
_COMBINATOR = re.compile(r"\s*[>+~]\s*|\s+")


def _median(values: Iterable[float | None]) -> float | None:
    """The median of the values that are numbers; None when none is."""
    numbers = [value for value in values if value is not None]
    return float(statistics.median(numbers)) if numbers else None


def _per_key(
    maps: Sequence[Mapping[str, float | None] | None],
    reduce: Callable[[list[float | None]], _Reduced],
) -> dict[str, _Reduced] | None:
    """Each key's values over the maps present, reduced, in first-seen order.

    A present map lacking a key gives None for it; None as a whole when no map is present.
    """
    present = [found for found in maps if found is not None]
    if not present:
        return None
    keys = dict.fromkeys(key for found in present for key in found)
    return {key: reduce([found.get(key) for found in present]) for key in keys}


def _request_median(download_ms: list[float | None]) -> RequestMedian:
    """One request's download times over the runs: their median, and how many runs had one."""
    reported = [ms for ms in download_ms if ms is not None]
    return {"median_ms": _median(reported), "runs": len(reported)}


def _tally(values: Iterable[str | bool | None]) -> Tally:
    """Runs per value, majority first and ties in first-seen order; None counted as `null`.

    Text is kept as it is and anything else written as JSON writes it, so the keys are what
    `summary.json` shows.
    """
    counts = Counter(
        value if isinstance(value, str) else json.dumps(value) for value in values
    )
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def _lcp_medians(lcps: Sequence[lighthouse_audits.Lcp]) -> LcpMedians:
    """The LCP element over the runs: selector and flags tallied, subparts' medians."""
    return {
        "selector": _tally(lcp["selector"] for lcp in lcps),
        "subparts": _per_key([lcp["subparts"] for lcp in lcps], _median),
        "discovery": {
            flag: _tally(
                None if lcp["discovery"] is None else lcp["discovery"][flag]
                for lcp in lcps
            )
            for flag in lighthouse_audits.DISCOVERY_FLAGS
        },
    }


def _latency_medians(
    latencies: Sequence[lighthouse_audits.DocumentLatency],
) -> DocumentLatencyMedians:
    """The document request over the runs: timings' and size's medians, compression tallied."""
    return {
        "server_response_ms": _median(d["server_response_ms"] for d in latencies),
        "redirect_ms": _median(d["redirect_ms"] for d in latencies),
        "uncompressed_bytes": _median(d["uncompressed_bytes"] for d in latencies),
        "uses_compression": _tally(d["uses_compression"] for d in latencies),
    }


def audit_medians(runs: Sequence[lighthouse_audits.RunAudits]) -> AuditMedians | None:
    """Every audit field reduced over `runs`; None when no run measured."""
    if not runs:
        return None
    blocking = [run["render_blocking"] for run in runs]
    chains = [run["dependency_chain"] for run in runs]
    caches = [run["cache"] for run in runs]
    return {
        "render_blocking": {
            "fcp_saving_ms": _median(b["fcp_saving_ms"] for b in blocking),
            "lcp_saving_ms": _median(b["lcp_saving_ms"] for b in blocking),
            "requests": _per_key([b["requests"] for b in blocking], _request_median),
        },
        "dependency_chain": {
            "longest_ms": _median(c["longest_ms"] for c in chains),
            "depth": _median(c["depth"] for c in chains),
        },
        "unused_css_bytes": _median(run["unused_css_bytes"] for run in runs),
        "lcp": _lcp_medians([run["lcp"] for run in runs]),
        "cache": {
            "wasted_bytes": _median(c["wasted_bytes"] for c in caches),
            "first_party_wasted_bytes": _median(
                c["first_party_wasted_bytes"] for c in caches
            ),
        },
        "document_latency": _latency_medians([run["document_latency"] for run in runs]),
    }


class _Majority(NamedTuple):
    """A tally's most common value, how many runs had it, and how many runs there were."""

    value: str
    agreeing: int
    runs: int


def _majority(tally: Tally) -> _Majority:
    """The majority of a tally, which must not be empty; a tie goes to the first listed."""
    value, agreeing = max(tally.items(), key=lambda item: item[1])
    return _Majority(value, agreeing, sum(tally.values()))


def _marked(text: str, majority: _Majority) -> str:
    """`text`, followed by the majority's share of the runs when they did not all agree."""
    if majority.agreeing == majority.runs:
        return text
    return f"{text} ({majority.agreeing}/{majority.runs})"


def _number_cell(value: float | None, form: str = "{:.0f}") -> str:
    """A median as its cell shows it."""
    return _NOT_AVAILABLE if value is None else form.format(value)


def _element_cell(selectors: Tally) -> str:
    """The majority LCP element by its selector's last compound, such as `img.photo`."""
    majority = _majority(selectors)
    if majority.value == _NULL:
        return _marked(_NOT_AVAILABLE, majority)
    last = _COMBINATOR.split(majority.value.strip())[-1]
    # A selector comes from the report; a pipe in it would split the Markdown row.
    return _marked(last.replace("|", "\\|"), majority)


def _discovery_cell(discovery: Mapping[str, Tally]) -> str:
    """The discovery flags compactly, or `n/a` when every flag's majority is null.

    In practice that is a text LCP element, which no request fetched to be discovered.
    """
    majorities = [_majority(discovery[f]) for f in lighthouse_audits.DISCOVERY_FLAGS]
    if all(majority.value == _NULL for majority in majorities):
        return _marked(_NOT_AVAILABLE, min(majorities, key=lambda m: m.agreeing))
    return ", ".join(
        _marked(f"{name} {_FLAG_TEXT.get(majority.value, '?')}", majority)
        for name, majority in zip(_FLAG_NAMES, majorities, strict=True)
    )


def _cells(medians: AuditMedians | None) -> list[str]:
    """One table row's cells after page and preset."""
    if medians is None:
        return [_NOT_AVAILABLE] * len(_HEADINGS)
    compression = _majority(medians["document_latency"]["uses_compression"])
    return [
        _number_cell(medians["render_blocking"]["fcp_saving_ms"]),
        _number_cell(medians["dependency_chain"]["longest_ms"]),
        _number_cell(medians["dependency_chain"]["depth"], "{:g}"),
        _number_cell(medians["unused_css_bytes"]),
        _element_cell(medians["lcp"]["selector"]),
        _discovery_cell(medians["lcp"]["discovery"]),
        _number_cell(medians["cache"]["first_party_wasted_bytes"]),
        _number_cell(medians["document_latency"]["server_response_ms"]),
        _marked(_COMPRESSION_TEXT.get(compression.value, _NOT_AVAILABLE), compression),
    ]


def format_table(rows: Sequence[tuple[str, str, AuditMedians | None]]) -> list[str]:
    """The audit medians as a Markdown table, one row per (page, preset, medians)."""
    header = ["page", "preset", *_HEADINGS]
    lines = [header, ["---"] * len(header)]
    lines += [[page, preset, *_cells(medians)] for page, preset, medians in rows]
    return ["| " + " | ".join(line) + " |" for line in lines]
