"""Grouped suite: reading one Lighthouse report (the metrics, the font join, the refusals)."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import lighthouse_report  # noqa: E402 - the tool's private src/, via the insert above
from _lighthouse_run_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    FONT_BYTES_BEFORE_FCP,
    NETWORK_START_TS,
    fake_report,
    font_request,
    other_request,
)

# The determinism convention: a profile registered at import, never an `@settings`
# decorator, which a fuzzing run applying its own settings could not override.
# deadline=None: a per-example wall-clock deadline measures machine load, not
# correctness, so a busy machine would report a passing property as flaky.
# too_slow is suppressed for the same reason: on a cold .hypothesis cache the
# first draw also builds Hypothesis's unicode tables and scans the local modules
# for constants, which under load outlasts the limit that check allows even
# without a deadline.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")


def _audits(report: dict[str, Any]) -> dict[str, Any]:
    """The audits table of a fake report, typed loosely so a test can break it."""
    audits: dict[str, Any] = report["audits"]
    return audits


def _metrics(**changes: Any) -> lighthouse_report.RunMetrics:
    """Extract from a default fake report with `changes` applied."""
    return lighthouse_report.extract_metrics(fake_report(**changes))


class ExtractTests(unittest.TestCase):
    """The tracked numbers, read from where Lighthouse 13 writes them."""

    def test_extracts_every_tracked_metric(self) -> None:
        metrics = _metrics()
        self.assertEqual(metrics.score, 0.9)
        self.assertEqual(metrics.fcp_ms, 2710.0)
        self.assertEqual(metrics.lcp_ms, 2860.0)
        self.assertEqual(metrics.tbt_ms, 0.0)
        self.assertEqual(metrics.cls, 0.0001)
        self.assertEqual(metrics.total_bytes, 884_981.0)
        self.assertEqual(metrics.requests, 4)
        self.assertEqual(metrics.font_bytes_before_fcp, FONT_BYTES_BEFORE_FCP)

    def test_an_integer_metric_is_accepted(self) -> None:
        self.assertEqual(_metrics(total_bytes=900).total_bytes, 900.0)


class FontJoinTests(unittest.TestCase):
    """Font bytes count when they had finished arriving by first contentful paint."""

    def test_a_font_finishing_exactly_at_first_paint_counts(self) -> None:
        requests = [font_request(100, 60.0), font_request(7, 60.001)]
        self.assertEqual(_metrics(requests=requests).font_bytes_before_fcp, 100.0)

    def test_requests_that_are_not_fonts_never_count(self) -> None:
        metrics = _metrics(requests=[other_request(500, 1.0)])
        self.assertEqual(metrics.font_bytes_before_fcp, 0.0)
        self.assertEqual(metrics.requests, 1)

    def test_the_join_places_request_times_on_the_network_clock(self) -> None:
        # The same relative finish lands after paint once the network clock starts 10ms
        # later, so a join comparing the relative number against paint directly fails.
        report = fake_report(requests=[font_request(100, 55.0)])
        network = _audits(report)["network-requests"]["details"]
        network["debugData"]["networkStartTimeTs"] = NETWORK_START_TS + 10_000
        self.assertEqual(
            lighthouse_report.extract_metrics(report).font_bytes_before_fcp, 0.0
        )


class RefusalTests(unittest.TestCase):
    """Anything missing or mistyped is refused, never read as a zero."""

    def assert_refused(self, report: object, fragment: str) -> None:
        """Assert extraction refuses `report` with a message naming `fragment`."""
        with self.assertRaises(lighthouse_report.ReportError) as caught:
            lighthouse_report.extract_metrics(report)
        self.assertIn(fragment, str(caught.exception))

    def test_a_runtime_error_is_refused(self) -> None:
        report = fake_report()
        report["runtimeError"] = {"code": "NO_FCP", "message": "no paint"}
        self.assert_refused(report, "NO_FCP")

    def test_a_report_that_is_not_an_object_is_refused(self) -> None:
        self.assert_refused([], "report")

    def test_a_missing_audit_is_refused(self) -> None:
        report = fake_report()
        del _audits(report)["largest-contentful-paint"]
        self.assert_refused(report, "largest-contentful-paint")

    def test_a_null_score_is_refused(self) -> None:
        self.assert_refused(fake_report(score=None), "score")

    def test_a_boolean_is_not_a_measurement(self) -> None:
        self.assert_refused(fake_report(cls=True), "cumulative-layout-shift")

    def test_a_non_finite_number_is_refused(self) -> None:
        self.assert_refused(fake_report(fcp_ms=float("nan")), "first-contentful-paint")

    def test_an_integer_too_large_for_a_float_is_refused(self) -> None:
        self.assert_refused(fake_report(total_bytes=10**400), "total-byte-weight")

    def test_request_items_must_be_a_list(self) -> None:
        report = fake_report()
        _audits(report)["network-requests"]["details"]["items"] = {}
        self.assert_refused(report, "network-requests")

    def test_a_request_row_must_be_an_object(self) -> None:
        self.assert_refused(fake_report(requests=[5]), "items[0]")  # type: ignore[list-item]

    def test_the_paint_row_must_exist(self) -> None:
        report = fake_report()
        _audits(report)["metrics"]["details"]["items"] = []
        self.assert_refused(report, "metrics")


class VersionTests(unittest.TestCase):
    """The Lighthouse version a report was written by."""

    def test_reads_the_version(self) -> None:
        self.assertEqual(lighthouse_report.lighthouse_version(fake_report()), "13.4.1")

    def test_a_version_that_is_not_text_is_refused(self) -> None:
        with self.assertRaises(lighthouse_report.ReportError):
            lighthouse_report.lighthouse_version(fake_report(version=13))

    def test_a_report_that_is_not_an_object_is_refused(self) -> None:
        with self.assertRaises(lighthouse_report.ReportError):
            lighthouse_report.lighthouse_version("13.4.1")


class MedianTests(unittest.TestCase):
    """Medians are taken per metric, over the runs that measured."""

    def test_an_odd_count_takes_the_middle_run(self) -> None:
        runs = [_metrics(score=score) for score in (1.0, 0.8, 0.9)]
        self.assertEqual(lighthouse_report.median_metrics(runs)["score"], 0.9)

    def test_an_even_count_averages_the_middle_pair(self) -> None:
        runs = [_metrics(tbt_ms=10.0), _metrics(tbt_ms=20.0)]
        self.assertEqual(lighthouse_report.median_metrics(runs)["tbt_ms"], 15.0)

    def test_every_tracked_metric_has_a_median(self) -> None:
        medians = lighthouse_report.median_metrics([_metrics()])
        self.assertEqual(tuple(medians), lighthouse_report.METRIC_FIELDS)


#: Arbitrary JSON values: what any file on disk can parse to.
_JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=5),
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=5), children, max_size=3)
    ),
    max_leaves=10,
)

#: Where a valid report is broken, so the property reaches the deep refusals too.
_REPORT_PATHS: tuple[tuple[str | int, ...], ...] = (
    ("categories", "performance", "score"),
    ("audits", "first-contentful-paint", "numericValue"),
    ("audits", "metrics", "details", "items"),
    ("audits", "metrics", "details", "items", 0),
    ("audits", "network-requests", "details"),
    ("audits", "network-requests", "details", "items"),
    ("audits", "network-requests", "details", "items", 1),
    ("audits", "network-requests", "details", "items", 1, "networkEndTime"),
    ("audits", "network-requests", "details", "items", 1, "transferSize"),
    ("audits", "network-requests", "details", "debugData", "networkStartTimeTs"),
)


def _broken_report(path: tuple[str | int, ...], value: object) -> object:
    """A default report with the value at `path` replaced."""
    report: Any = copy.deepcopy(fake_report())
    parent = report
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return report


class PropertyTests(unittest.TestCase):
    """Invariants over the input space, not just the examples above."""

    @given(st.one_of(_JSON, st.tuples(st.sampled_from(_REPORT_PATHS), _JSON)))
    def test_fuzz_extract_metrics(self, drawn: object) -> None:
        report = _broken_report(*drawn) if isinstance(drawn, tuple) else drawn
        try:
            metrics = lighthouse_report.extract_metrics(report)
        except lighthouse_report.ReportError:
            return
        self.assertIsInstance(metrics, lighthouse_report.RunMetrics)

    @given(
        st.lists(
            st.tuples(st.integers(0, 10**6), st.integers(0, 200), st.booleans()),
            max_size=8,
        )
    )
    def test_font_bytes_are_the_fonts_finished_by_paint(
        self, rows: list[tuple[int, int, bool]]
    ) -> None:
        requests = [
            font_request(size, end) if is_font else other_request(size, end)
            for size, end, is_font in rows
        ]
        expected = sum(size for size, end, is_font in rows if is_font and end <= 60)
        self.assertEqual(_metrics(requests=requests).font_bytes_before_fcp, expected)


if __name__ == "__main__":
    unittest.main()
