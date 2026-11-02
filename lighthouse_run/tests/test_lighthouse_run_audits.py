"""Grouped suite: reading the audits behind a run's numbers out of real Lighthouse reports.

Every expectation here is read from the real reports under `fixtures/` (see
`_lighthouse_run_fixtures.AUDIT_REPORTS`), then each is broken one field at a time: a field the
reader cannot read is None, and nothing a report holds makes it raise.
"""

from __future__ import annotations

import copy
import itertools
import json
import math
import sys
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import lighthouse_audits  # noqa: E402 - the tool's private src/, via the insert above
from _lighthouse_run_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    AUDIT_ORIGIN,
    IMAGE_SELECTOR,
    NO_AUDITS,
    TEXT_SELECTOR,
    audit_report,
    recorded,
    report_details,
)

# The deterministic profile, registered at import as the report suite registers it; the
# reasons for each setting are written there.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")


def _read(report: object) -> dict[str, Any]:
    """The audits of `report`, as the summary records them."""
    found: dict[str, Any] = recorded(lighthouse_audits.extract_audits(report))
    return found


class RealReportTests(unittest.TestCase):
    """Every field, read from the real reports of an image LCP, a text LCP and the 404."""

    def test_render_blocking_savings_are_lighthouses_whole_estimate(self) -> None:
        blocking = _read(audit_report("home-desktop-run1"))["render_blocking"]
        self.assertEqual(blocking["fcp_saving_ms"], 150.0)
        self.assertEqual(blocking["lcp_saving_ms"], 0.0)

    def test_requests_on_the_pages_own_origin_are_keyed_by_path(self) -> None:
        report = audit_report("home-desktop-run1")
        requests = _read(report)["render_blocking"]["requests"]
        self.assertEqual(len(requests), 18)
        self.assertEqual(requests["/index.js"], 55.0)
        self.assertEqual(requests["/shared/style-07.css"], 95.0)

    def test_a_blocking_request_under_50_ms_is_kept_as_none(self) -> None:
        # Lighthouse writes `wastedMs`, a simulated download time, only at 50 ms or more.
        requests = _read(audit_report("404-desktop-run1"))["render_blocking"][
            "requests"
        ]
        under_50_ms = ("/shared/style-01.css", "/shared/style-02.css")
        timed = (
            "/shared/style-03.css /shared/style-04.css /shared/style-05.css "
            "/shared/style-06.css /shared/style-07.css /shared/style-08.css"
        ).split()
        self.assertEqual(
            requests,
            {path: None for path in under_50_ms} | {path: 84.0 for path in timed},
        )

    def test_the_dependency_chain_counts_the_document_as_one(self) -> None:
        for name, chain in (
            ("home-desktop-run1", {"longest_ms": 286.0, "depth": 5}),
            ("404-desktop-run1", {"longest_ms": 57.0, "depth": 4}),
        ):
            with self.subTest(name=name):
                self.assertEqual(_read(audit_report(name))["dependency_chain"], chain)

    def test_zero_unused_css_is_a_measurement_not_a_gap(self) -> None:
        unused = _read(audit_report("home-desktop-run1"))["unused_css_bytes"]
        self.assertEqual(unused, 0.0)

    def test_an_image_lcp_has_four_subparts_and_a_discovery_checklist(self) -> None:
        self.assertEqual(
            _read(audit_report("home-desktop-run1"))["lcp"],
            {
                "selector": IMAGE_SELECTOR,
                "subparts": {
                    "timeToFirstByte": 4.157,
                    "resourceLoadDelay": 24.177,
                    "resourceLoadDuration": 112.377,
                    "elementRenderDelay": 55.237,
                },
                "discovery": {
                    "priorityHinted": False,
                    "requestDiscoverable": True,
                    "eagerlyLoaded": True,
                },
            },
        )

    def test_a_text_lcp_has_two_subparts_and_no_discovery(self) -> None:
        self.assertEqual(
            _read(audit_report("home-mobile-run1"))["lcp"],
            {
                "selector": TEXT_SELECTOR,
                "subparts": {"timeToFirstByte": 5.314, "elementRenderDelay": 136.931},
                "discovery": None,
            },
        )

    def test_first_party_cache_waste_leaves_out_other_origins(self) -> None:
        for name, wasted, first_party in (
            ("home-desktop-run1", 311_274.0, 235_433.0),
            ("404-desktop-run1", 109_091.0, 109_091.0),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    _read(audit_report(name))["cache"],
                    {"wasted_bytes": wasted, "first_party_wasted_bytes": first_party},
                )

    def test_document_latency(self) -> None:
        self.assertEqual(
            _read(audit_report("home-desktop-run1"))["document_latency"],
            {
                "server_response_ms": 2.0,
                "redirect_ms": 0.0,
                "uncompressed_bytes": 22_759.0,
                "uses_compression": False,
            },
        )

    def test_the_lcp_element_is_recorded_by_selector_never_by_its_text(self) -> None:
        report = audit_report("home-desktop-run1")
        for audit in ("lcp-breakdown-insight", "lcp-discovery-insight"):
            for item in report_details(report, audit)["items"]:
                item.update(nodeLabel="Private words", snippet="<p>Private words</p>")
        self.assertNotIn("Private words", json.dumps(_read(report)))


class RequestKeyTests(unittest.TestCase):
    """Own-origin requests by path and query, another origin's by its full url."""

    def requests(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """The request map `report` reads as; {} when it reads as unknown."""
        found: dict[str, Any] = _read(report)["render_blocking"]["requests"] or {}
        return found

    def test_a_query_stays_in_the_key_and_the_bare_origin_is_the_root(self) -> None:
        report = audit_report("404-desktop-run1")
        rows = report_details(report, "render-blocking-insight")["items"]
        rows[2]["url"] = f"{AUDIT_ORIGIN}/shared/page.css?v=2#top"
        rows[3]["url"] = AUDIT_ORIGIN
        requests = self.requests(report)
        self.assertEqual(requests["/shared/page.css?v=2"], 84.0)
        self.assertEqual(requests["/"], 84.0)

    def test_a_request_on_another_origin_is_keyed_by_its_full_url(self) -> None:
        report = audit_report("404-desktop-run1")
        other = "https://fonts.example.invalid/face.css?family=a"
        rows = report_details(report, "render-blocking-insight")["items"]
        rows.append({"url": other, "wastedMs": 60})
        self.assertEqual(self.requests(report)[other], 60.0)

    def test_an_https_page_origin_keys_its_own_requests_by_path(self) -> None:
        # A hosted copy's summary lines up with a loopback one request for request.
        hosted = "https://site.example.invalid"
        text = json.dumps(audit_report("home-desktop-run1"))
        report = json.loads(text.replace(AUDIT_ORIGIN, hosted))
        requests = self.requests(report)
        self.assertEqual(requests["/index.js"], 55.0)
        self.assertEqual(requests, self.requests(audit_report("home-desktop-run1")))
        self.assertNotIn(hosted, json.dumps(requests))

    def test_the_main_document_url_stands_in_for_the_displayed_one(self) -> None:
        report = audit_report("404-desktop-run1")
        del report["finalDisplayedUrl"]
        self.assertEqual(
            self.requests(report), self.requests(audit_report("404-desktop-run1"))
        )

    def test_the_displayed_url_outranks_the_main_document_url(self) -> None:
        # The two differ when a page changes its own address after it loads.
        other = "https://other.example.invalid/404.html"
        intact = self.requests(audit_report("404-desktop-run1"))
        moved_document = audit_report("404-desktop-run1")
        moved_document["mainDocumentUrl"] = other
        self.assertEqual(self.requests(moved_document), intact)
        moved_display = audit_report("404-desktop-run1")
        moved_display["finalDisplayedUrl"] = other
        self.assertEqual(
            self.requests(moved_display),
            {f"{AUDIT_ORIGIN}{path}": wasted for path, wasted in intact.items()},
        )

    def test_with_no_page_origin_only_the_origin_blind_fields_are_read(self) -> None:
        report = audit_report("home-desktop-run1")
        report["finalDisplayedUrl"] = "about:blank"
        del report["mainDocumentUrl"]
        audits = _read(report)
        self.assertIsNone(audits["render_blocking"]["requests"])
        self.assertEqual(
            audits["cache"],
            {"wasted_bytes": 311_274.0, "first_party_wasted_bytes": None},
        )


class ItemOrderTests(unittest.TestCase):
    """Each audit item is found by its type, in whatever order Lighthouse lists them.

    Every real report lists each audit's items in one order, so a reader taking an item by
    its position could pass the tests above: these read the items in every order.
    """

    def read_in_every_order(self, audit: str, field: str) -> list[dict[str, Any]]:
        """The group `field` of the image-LCP report, read with `audit`'s items in each order."""
        readings: list[dict[str, Any]] = []
        count = len(report_details(audit_report("home-desktop-run1"), audit)["items"])
        for order in itertools.permutations(range(count)):
            report = audit_report("home-desktop-run1")
            items = report_details(report, audit)["items"]
            items[:] = [items[index] for index in order]
            readings.append(_read(report)[field])
        return readings

    def test_the_network_tree_is_found_after_the_text_sections(self) -> None:
        readings = self.read_in_every_order(
            "network-dependency-tree-insight", "dependency_chain"
        )
        self.assertEqual(len(readings), 6)
        for reading in readings:
            self.assertEqual(reading, {"longest_ms": 286.0, "depth": 5})

    def test_the_lcp_node_and_subpart_table_are_found_in_either_order(self) -> None:
        intact = _read(audit_report("home-desktop-run1"))["lcp"]
        readings = self.read_in_every_order("lcp-breakdown-insight", "lcp")
        self.assertEqual(len(readings), 2)
        for reading in readings:
            self.assertEqual(reading, intact)
        self.assertEqual(intact["selector"], IMAGE_SELECTOR)
        self.assertEqual(len(intact["subparts"]), 4)

    def test_the_discovery_checklist_is_found_after_the_node(self) -> None:
        readings = self.read_in_every_order("lcp-discovery-insight", "lcp")
        self.assertEqual(len(readings), 2)
        for reading in readings:
            self.assertEqual(
                reading["discovery"],
                {
                    "priorityHinted": False,
                    "requestDiscoverable": True,
                    "eagerlyLoaded": True,
                },
            )


class MalformedTests(unittest.TestCase):
    """A field that cannot be read is None; the rest of the run's audits still are."""

    def assert_only(self, report: object, group: str) -> None:
        """Assert `report` reads as the intact image-LCP report but for `group`, unknown."""
        intact = _read(audit_report("home-desktop-run1"))
        self.assertEqual(_read(report), {**intact, group: NO_AUDITS[group]})

    def test_anything_but_a_report_reads_as_nothing(self) -> None:
        reports: tuple[object, ...] = ([], None, "report", {"audits": []})
        for report in reports:
            with self.subTest(report=report):
                self.assertEqual(_read(report), NO_AUDITS)

    def test_a_missing_audit_unsets_only_its_own_fields(self) -> None:
        report = audit_report("home-desktop-run1")
        del report["audits"]["render-blocking-insight"]
        self.assert_only(report, "render_blocking")

    def test_a_request_row_that_cannot_be_placed_unsets_the_whole_map(self) -> None:
        # A partial map would read as complete; None says the requests are unknown.
        for row in (5, {"wastedMs": 3}, {"url": "http://[::1/x", "wastedMs": 3}):
            with self.subTest(row=row):
                report = audit_report("home-desktop-run1")
                report_details(report, "render-blocking-insight")["items"].append(row)
                self.assertIsNone(_read(report)["render_blocking"]["requests"])

    def test_a_download_time_that_is_not_a_number_is_none(self) -> None:
        report = audit_report("home-desktop-run1")
        report_details(report, "render-blocking-insight")["items"][0]["wastedMs"] = "55"
        requests = _read(report)["render_blocking"]["requests"]
        self.assertIsNone(requests["/index.js"])

    def test_values_that_are_not_finite_numbers_are_none(self) -> None:
        for value in (True, float("nan"), float("inf"), 10**400, "150", None):
            with self.subTest(value=value):
                report = audit_report("home-desktop-run1")
                savings = report["audits"]["render-blocking-insight"]["metricSavings"]
                savings["FCP"] = value
                self.assertIsNone(_read(report)["render_blocking"]["fcp_saving_ms"])

    def test_a_tree_without_a_network_tree_section_reads_as_unknown(self) -> None:
        report = audit_report("home-desktop-run1")
        sections = report_details(report, "network-dependency-tree-insight")["items"]
        del sections[0]
        self.assert_only(report, "dependency_chain")

    def chains(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """The dependency tree's chains in `report`, to edit in place."""
        details = report_details(report, "network-dependency-tree-insight")
        found: dict[str, Any] = details["items"][0]["value"]["chains"]
        return found

    def test_a_chain_node_that_cannot_be_walked_unsets_only_the_depth(self) -> None:
        for broken in (7, {"url": f"{AUDIT_ORIGIN}/"}):
            with self.subTest(broken=broken):
                report = audit_report("404-desktop-run1")
                (document,) = self.chains(report).values()
                document["children"]["leaf"] = broken
                chain = _read(report)["dependency_chain"]
                self.assertEqual(chain, {"longest_ms": 57.0, "depth": None})

    def test_an_empty_tree_has_depth_zero(self) -> None:
        report = audit_report("404-desktop-run1")
        self.chains(report).clear()
        self.assertEqual(_read(report)["dependency_chain"]["depth"], 0)

    def test_a_chain_deeper_than_the_recursion_limit_is_still_counted(self) -> None:
        levels = sys.getrecursionlimit() + 100
        nested: dict[str, Any] = {}
        for _ in range(levels):
            nested = {"node": {"children": nested}}
        report = audit_report("404-desktop-run1")
        self.chains(report).clear()
        self.chains(report).update(nested)
        chain = lighthouse_audits.extract_audits(report)["dependency_chain"]
        self.assertEqual(chain["depth"], levels)

    def test_a_subpart_without_a_duration_unsets_the_subparts(self) -> None:
        report = audit_report("home-desktop-run1")
        table = report_details(report, "lcp-breakdown-insight")["items"][0]
        del table["items"][1]["duration"]
        lcp = _read(report)["lcp"]
        self.assertIsNone(lcp["subparts"])
        self.assertEqual(lcp["selector"], IMAGE_SELECTOR)

    def test_a_breakdown_without_a_node_has_no_selector(self) -> None:
        report = audit_report("home-desktop-run1")
        del report_details(report, "lcp-breakdown-insight")["items"][1]
        self.assertIsNone(_read(report)["lcp"]["selector"])

    def test_null_discovery_details_mean_not_applicable(self) -> None:
        report = audit_report("home-desktop-run1")
        report["audits"]["lcp-discovery-insight"]["details"] = None
        self.assertIsNone(_read(report)["lcp"]["discovery"])

    def test_a_checklist_flag_that_is_not_a_boolean_is_none(self) -> None:
        report = audit_report("home-desktop-run1")
        details = report_details(report, "lcp-discovery-insight")
        checklist = details["items"][0]["items"]
        checklist["priorityHinted"]["value"] = "false"
        del checklist["eagerlyLoaded"]
        self.assertEqual(
            _read(report)["lcp"]["discovery"],
            {
                "priorityHinted": None,
                "requestDiscoverable": True,
                "eagerlyLoaded": None,
            },
        )

    def test_a_cache_row_without_wasted_bytes_unsets_both_sums(self) -> None:
        report = audit_report("home-desktop-run1")
        del report_details(report, "cache-insight")["items"][3]["wastedBytes"]
        self.assert_only(report, "cache")

    def test_a_cache_row_naming_no_origin_counts_toward_the_total_only(self) -> None:
        report = audit_report("404-desktop-run1")
        rows = report_details(report, "cache-insight")["items"]
        rows[0]["url"] = None
        self.assertEqual(
            _read(report)["cache"],
            {
                "wasted_bytes": 109_091.0,
                "first_party_wasted_bytes": 109_091.0 - rows[0]["wastedBytes"],
            },
        )

    def test_a_compression_check_that_is_not_a_boolean_is_none(self) -> None:
        report = audit_report("home-desktop-run1")
        checklist = report_details(report, "document-latency-insight")["items"]
        checklist["usesCompression"] = {}
        self.assertIsNone(_read(report)["document_latency"]["uses_compression"])


#: Arbitrary JSON values: what any file on disk can parse to.
_JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=5),
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=5), children, max_size=3)
    ),
    max_leaves=10,
)

_TREE = ("audits", "network-dependency-tree-insight", "details", "items", 0, "value")

#: Where the real image-LCP report is broken, so the property reaches every reader.
_REPORT_PATHS: tuple[tuple[str | int, ...], ...] = (
    ("finalDisplayedUrl",),
    ("audits",),
    ("audits", "render-blocking-insight", "metricSavings"),
    ("audits", "render-blocking-insight", "details", "items", 0),
    ("audits", "render-blocking-insight", "details", "items", 0, "url"),
    _TREE,
    (*_TREE, "chains"),
    ("audits", "unused-css-rules", "details"),
    ("audits", "lcp-breakdown-insight", "details", "items", 0),
    ("audits", "lcp-breakdown-insight", "details", "items", 1, "selector"),
    ("audits", "lcp-discovery-insight", "details", "items", 0, "items"),
    ("audits", "cache-insight", "details", "items", 1),
    ("audits", "cache-insight", "details", "items", 1, "url"),
    ("audits", "document-latency-insight", "details"),
)

_BASE_REPORT = audit_report("home-desktop-run1")


def _broken_report(path: tuple[str | int, ...], value: object) -> object:
    """The real image-LCP report with the value at `path` replaced."""
    report: Any = copy.deepcopy(_BASE_REPORT)
    parent = report
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return report


class PropertyTests(unittest.TestCase):
    """Whatever a report holds, the reader answers, and answers in the recorded shape."""

    def assert_number(self, value: object) -> None:
        """Assert `value` is None or a finite float, never a bool, an int or a NaN."""
        finite = isinstance(value, float) and math.isfinite(value)
        self.assertTrue(value is None or finite, repr(value))

    def assert_numbers(self, values: Mapping[str, float | None] | None) -> None:
        """Assert `values` is None or a text-keyed map of numbers or None."""
        if values is not None:
            self.assertIsInstance(values, dict)
            for key, value in values.items():
                self.assertIsInstance(key, str)
                self.assert_number(value)

    def assert_well_typed(self, audits: lighthouse_audits.RunAudits) -> None:
        """Assert every field of `audits` has its recorded type."""
        self.assertEqual(audits.keys(), NO_AUDITS.keys())
        blocking, chain = audits["render_blocking"], audits["dependency_chain"]
        lcp, latency = audits["lcp"], audits["document_latency"]
        for number in (
            blocking["fcp_saving_ms"],
            blocking["lcp_saving_ms"],
            chain["longest_ms"],
            audits["unused_css_bytes"],
            *audits["cache"].values(),
            latency["server_response_ms"],
            latency["redirect_ms"],
            latency["uncompressed_bytes"],
        ):
            self.assert_number(number)
        self.assert_numbers(blocking["requests"])
        self.assert_numbers(lcp["subparts"])
        self.assertIn(type(chain["depth"]), (int, type(None)))
        self.assertIn(type(lcp["selector"]), (str, type(None)))
        self.assertIn(type(latency["uses_compression"]), (bool, type(None)))
        if lcp["discovery"] is not None:
            self.assertEqual(tuple(lcp["discovery"]), lighthouse_audits.DISCOVERY_FLAGS)
            for flag in lcp["discovery"].values():
                self.assertIn(type(flag), (bool, type(None)))

    @given(st.one_of(_JSON, st.tuples(st.sampled_from(_REPORT_PATHS), _JSON)))
    def test_fuzz_extract_audits(self, drawn: object) -> None:
        report = _broken_report(*drawn) if isinstance(drawn, tuple) else drawn
        self.assert_well_typed(lighthouse_audits.extract_audits(report))


if __name__ == "__main__":
    unittest.main()
