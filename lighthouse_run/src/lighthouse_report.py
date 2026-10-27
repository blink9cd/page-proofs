"""Read the numbers this project tracks out of one Lighthouse report.

A report is the JSON `lighthouse --output=json` writes. Every value is read by name from where
Lighthouse 13 puts it, and anything missing or mistyped is a `ReportError` rather than a zero:
a zero is a plausible measurement, and it would drag a median without anyone noticing.

**Font bytes before first paint is a join, not a field.** The network-requests audit gives
each request's finish time in milliseconds after the earliest request, whose own absolute start
is `debugData.networkStartTimeTs` in microseconds; the metrics audit gives first contentful
paint as the absolute `observedFirstContentfulPaintTs`, also in microseconds. A font counts
once its bytes had finished arriving by that paint. Both are OBSERVED clocks on purpose: the
simulated FCP Lighthouse scores is a model of a slower network, and no request ever finished
at a simulated time.

Entry point assumptions: none -- pure functions over an already-parsed JSON value.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields

#: The `resourceType` Lighthouse gives a webfont request.
_FONT_RESOURCE_TYPE = "Font"

#: Request times are milliseconds; trace timestamps are microseconds.
_MICROSECONDS_PER_MILLISECOND = 1000.0

#: Where the network-requests rows sit, for messages.
_REQUESTS_WHERE = "audits.network-requests.details.items"

#: Where the paint-timing row sits, for messages.
_PAINT_WHERE = "audits.metrics.details.items"


class ReportError(ValueError):
    """A report that cannot be measured: Lighthouse failed, or its shape moved."""


@dataclass(frozen=True)
class RunMetrics:
    """The tracked numbers from one Lighthouse run."""

    score: float
    fcp_ms: float
    lcp_ms: float
    tbt_ms: float
    cls: float
    total_bytes: float
    requests: int
    font_bytes_before_fcp: float


#: Every tracked metric's field name, in declaration order.
METRIC_FIELDS: tuple[str, ...] = tuple(field.name for field in fields(RunMetrics))


def _mapping(value: object, where: str) -> Mapping[str, object]:
    """Return `value` as a JSON object, or refuse it by its location in the report."""
    if not isinstance(value, Mapping):
        raise ReportError(f"{where} is missing or not an object")
    return value


def _items(value: object, where: str) -> Sequence[object]:
    """Return `value` as a JSON array, or refuse it."""
    if not isinstance(value, list):
        raise ReportError(f"{where} is missing or not a list")
    return value


def _number(value: object, where: str) -> float:
    """Return `value` as a finite float, or refuse it."""
    # bool is an int subclass, and `true` is not a measurement.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReportError(f"{where} is missing or not a number")
    try:
        number = float(value)
    except OverflowError:
        raise ReportError(f"{where} is too large to measure") from None
    if not math.isfinite(number):
        raise ReportError(f"{where} is not a finite number")
    return number


def _numeric_audit(audits: Mapping[str, object], name: str) -> float:
    """The `numericValue` of one audit."""
    audit = _mapping(audits.get(name), f"audits.{name}")
    return _number(audit.get("numericValue"), f"audits.{name}.numericValue")


def _audit_details(audits: Mapping[str, object], name: str) -> Mapping[str, object]:
    """The `details` object of one audit."""
    audit = _mapping(audits.get(name), f"audits.{name}")
    return _mapping(audit.get("details"), f"audits.{name}.details")


def _first_paint_us(audits: Mapping[str, object]) -> float:
    """Observed first contentful paint, as an absolute trace timestamp in microseconds."""
    rows = _items(_audit_details(audits, "metrics").get("items"), _PAINT_WHERE)
    if not rows:
        raise ReportError(f"{_PAINT_WHERE} is empty")
    row = _mapping(rows[0], f"{_PAINT_WHERE}[0]")
    return _number(
        row.get("observedFirstContentfulPaintTs"),
        f"{_PAINT_WHERE}[0].observedFirstContentfulPaintTs",
    )


def _font_bytes_by(
    requests: Sequence[object], network_start_us: float, paint_us: float
) -> float:
    """Sum the transfer size of every font request that had finished by `paint_us`."""
    total = 0.0
    for index, raw in enumerate(requests):
        where = f"{_REQUESTS_WHERE}[{index}]"
        request = _mapping(raw, where)
        if request.get("resourceType") != _FONT_RESOURCE_TYPE:
            continue
        end_ms = _number(request.get("networkEndTime"), f"{where}.networkEndTime")
        if network_start_us + end_ms * _MICROSECONDS_PER_MILLISECOND <= paint_us:
            total += _number(request.get("transferSize"), f"{where}.transferSize")
    return total


# fuzz-target
def extract_metrics(report: object) -> RunMetrics:
    """Read the tracked metrics from one parsed report.

    Raises:
        ReportError: Lighthouse recorded a `runtimeError`, or a value is missing or mistyped.
    """
    document = _mapping(report, "report")
    if "runtimeError" in document:
        # Present only when the run failed, and its scores are then zeros, not measurements.
        raise ReportError(
            f"Lighthouse recorded a runtimeError: {document['runtimeError']!r}"
        )
    audits = _mapping(document.get("audits"), "audits")
    categories = _mapping(document.get("categories"), "categories")
    performance = _mapping(categories.get("performance"), "categories.performance")
    network = _audit_details(audits, "network-requests")
    requests = _items(network.get("items"), _REQUESTS_WHERE)
    debug = _mapping(
        network.get("debugData"), "audits.network-requests.details.debugData"
    )
    network_start_us = _number(
        debug.get("networkStartTimeTs"),
        "audits.network-requests.details.debugData.networkStartTimeTs",
    )
    return RunMetrics(
        score=_number(performance.get("score"), "categories.performance.score"),
        fcp_ms=_numeric_audit(audits, "first-contentful-paint"),
        lcp_ms=_numeric_audit(audits, "largest-contentful-paint"),
        tbt_ms=_numeric_audit(audits, "total-blocking-time"),
        cls=_numeric_audit(audits, "cumulative-layout-shift"),
        total_bytes=_numeric_audit(audits, "total-byte-weight"),
        requests=len(requests),
        font_bytes_before_fcp=_font_bytes_by(
            requests, network_start_us, _first_paint_us(audits)
        ),
    )


def lighthouse_version(report: object) -> str:
    """The version of Lighthouse that wrote `report`."""
    version = _mapping(report, "report").get("lighthouseVersion")
    if not isinstance(version, str):
        raise ReportError("lighthouseVersion is missing or not text")
    return version


def median_metrics(runs: Sequence[RunMetrics]) -> dict[str, float]:
    """Each tracked metric's median over `runs`, which must not be empty.

    Per metric rather than the median RUN: the run with the median score need not hold the
    median CLS, and each number is compared on its own.
    """
    return {
        name: float(statistics.median(getattr(run, name) for run in runs))
        for name in METRIC_FIELDS
    }
