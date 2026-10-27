"""Grouped suite: a page and preset's audits reduced to medians and tallies, printed, recorded.

The runs reduced here are read from the real reports under `fixtures/` -- three desktop rounds
of the home page among them. Lighthouse writes a blocking request's `wastedMs`, its simulated
download time, only at 50 ms or more: all eighteen requests reached that in the first round,
and only six did in each of the next two.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import audit_summary  # noqa: E402 - the tool's private src/, via the insert above
import lighthouse_audits  # noqa: E402 - the tool's private src/, via the insert above
import lighthouse_run  # noqa: E402 - the tool folder, on every grouped suite's sys.path
from _lighthouse_run_case import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    ProjectCase,
)
from _lighthouse_run_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    HOME_DESKTOP_ROUNDS,
    IMAGE_SELECTOR,
    NO_AUDITS,
    TEXT_SELECTOR,
    audit_report,
    fake_report,
    recorded,
)

#: The printed audit table's header, and the rule beneath it.
_HEADER = (
    "| page | preset | blocking FCP saving ms | chain ms | chain depth "
    "| unused CSS bytes | LCP element | LCP discovery | first-party cache waste bytes "
    "| server ms | compressed |"
)
_RULE = "| " + " | ".join(["---"] * 11) + " |"

#: The home page's desktop row, as its three real rounds reduce.
_HOME_DESKTOP_ROW = (
    "| / | desktop | 250 | 121 | 5 | 0 | img.slide "
    "| hinted n, discoverable y, eager y | 235433 | 1 | no |"
)


def _runs(*names: str) -> list[lighthouse_audits.RunAudits]:
    """The audits of each named fixture report, in order."""
    return [lighthouse_audits.extract_audits(audit_report(name)) for name in names]


def _medians(*names: str) -> dict[str, Any]:
    """The audit medians of the named fixture reports, as the summary records them."""
    found: dict[str, Any] = recorded(audit_summary.audit_medians(_runs(*names)))
    return found


class MedianTests(unittest.TestCase):
    """Numbers reduce to medians over the runs that have them; categories to tallies."""

    def test_numbers_are_medians_over_the_rounds(self) -> None:
        medians = _medians(*HOME_DESKTOP_ROUNDS)
        self.assertEqual(medians["render_blocking"]["fcp_saving_ms"], 250.0)
        self.assertEqual(medians["render_blocking"]["lcp_saving_ms"], 0.0)
        self.assertEqual(
            medians["dependency_chain"], {"longest_ms": 121.0, "depth": 5.0}
        )
        self.assertEqual(medians["unused_css_bytes"], 0.0)
        self.assertEqual(
            medians["cache"],
            {"wasted_bytes": 311_275.0, "first_party_wasted_bytes": 235_433.0},
        )
        self.assertEqual(
            medians["document_latency"],
            {
                "server_response_ms": 1.0,
                "redirect_ms": 0.0,
                "uncompressed_bytes": 22_759.0,
                "uses_compression": {"false": 3},
            },
        )

    def test_each_request_is_a_median_over_its_runs_at_50_ms_kept_with_their_count(
        self,
    ) -> None:
        requests = _medians(*HOME_DESKTOP_ROUNDS)["render_blocking"]["requests"]
        self.assertEqual(len(requests), 18)
        # At 50 ms or more in the first round only: one slow run of three, which a bare
        # median of 55 would pass off as the request's typical download time.
        self.assertEqual(requests["/index.js"], {"median_ms": 55.0, "runs": 1})
        # 95, 85 and 83 across the rounds.
        self.assertEqual(
            requests["/shared/style-04.css"], {"median_ms": 85.0, "runs": 3}
        )

    def test_a_request_under_50_ms_in_every_run_has_no_median_and_no_runs(
        self,
    ) -> None:
        requests = _medians("404-desktop-run1")["render_blocking"]["requests"]
        self.assertEqual(
            requests["/shared/style-01.css"], {"median_ms": None, "runs": 0}
        )
        self.assertEqual(
            requests["/shared/style-05.css"], {"median_ms": 84.0, "runs": 1}
        )

    def test_each_subpart_is_its_own_median(self) -> None:
        self.assertEqual(
            _medians(*HOME_DESKTOP_ROUNDS)["lcp"]["subparts"],
            {
                "timeToFirstByte": 2.207,
                "resourceLoadDelay": 10.25,
                "resourceLoadDuration": 28.996,
                "elementRenderDelay": 48.341,
            },
        )

    def test_categories_are_tallied_by_run(self) -> None:
        lcp = _medians(*HOME_DESKTOP_ROUNDS)["lcp"]
        self.assertEqual(lcp["selector"], {IMAGE_SELECTOR: 3})
        self.assertEqual(
            lcp["discovery"],
            {
                "priorityHinted": {"false": 3},
                "requestDiscoverable": {"true": 3},
                "eagerlyLoaded": {"true": 3},
            },
        )

    def test_a_split_tally_lists_the_majority_first_and_counts_null(self) -> None:
        lcp = _medians("home-desktop-run1", "home-mobile-run1", "home-mobile-run1")[
            "lcp"
        ]
        self.assertEqual(
            list(lcp["selector"].items()), [(TEXT_SELECTOR, 2), (IMAGE_SELECTOR, 1)]
        )
        self.assertEqual(
            list(lcp["discovery"]["priorityHinted"].items()),
            [("null", 2), ("false", 1)],
        )
        # The image's loading subparts exist in its one run only, so are its numbers.
        self.assertEqual(lcp["subparts"]["resourceLoadDelay"], 24.177)
        self.assertEqual(lcp["subparts"]["timeToFirstByte"], 5.314)

    def test_no_measured_run_has_no_medians(self) -> None:
        self.assertIsNone(audit_summary.audit_medians([]))

    def test_fields_no_run_could_read_have_no_median(self) -> None:
        unread = recorded(
            audit_summary.audit_medians([lighthouse_audits.extract_audits([])] * 3)
        )
        self.assertIsNone(unread["render_blocking"]["requests"])
        self.assertIsNone(unread["dependency_chain"]["depth"])
        self.assertIsNone(unread["lcp"]["subparts"])
        self.assertEqual(unread["lcp"]["selector"], {"null": 3})
        self.assertEqual(unread["lcp"]["discovery"]["eagerlyLoaded"], {"null": 3})


class TableTests(unittest.TestCase):
    """One row per page and preset; a category shows its majority, marked when runs split."""

    def row(self, runs: list[lighthouse_audits.RunAudits]) -> str:
        """The one row `runs`, measured as `/` on desktop, print as."""
        medians = audit_summary.audit_medians(runs)
        lines = audit_summary.format_table([("/", "desktop", medians)])
        self.assertEqual(lines[:2], [_HEADER, _RULE])
        (row,) = lines[2:]
        return row

    def test_a_row_shows_the_medians_and_the_lcp_elements_last_compound(self) -> None:
        self.assertEqual(self.row(_runs(*HOME_DESKTOP_ROUNDS)), _HOME_DESKTOP_ROW)

    def test_a_text_lcp_has_no_discovery(self) -> None:
        self.assertIn("| p | n/a |", self.row(_runs("home-mobile-run1")))

    def test_runs_that_disagree_are_marked_with_the_majoritys_share(self) -> None:
        runs = _runs("home-desktop-run1", "home-mobile-run1", "home-mobile-run1")
        self.assertIn("| p (2/3) | n/a (2/3) |", self.row(runs))

    def test_a_split_flag_is_marked_on_its_own(self) -> None:
        runs = _runs("home-desktop-run1", "home-desktop-run1")
        discovery = runs[1]["lcp"]["discovery"] or {}
        discovery["priorityHinted"] = True
        self.assertIn("| hinted n (1/2), discoverable y, eager y |", self.row(runs))

    def test_a_page_with_no_measured_run_reads_n_a(self) -> None:
        lines = audit_summary.format_table([("/archive/", "mobile", None)])
        self.assertEqual(lines[2:], ["| /archive/ | mobile |" + " n/a |" * 9])

    def test_a_pipe_in_a_selector_cannot_split_the_row(self) -> None:
        runs = _runs("home-desktop-run1")
        runs[0]["lcp"]["selector"] = "div > a[lang|=en]"
        row = self.row(runs)
        self.assertIn(r"| a[lang\|=en] |", row)
        self.assertEqual(row.replace(r"\|", "").count("|"), 12)


class RecordTests(ProjectCase):
    """Whole runs: each run's audits and each result's medians recorded, a second table."""

    def test_every_run_records_its_audits_and_every_result_their_medians(self) -> None:
        real = audit_report("home-desktop-run1")
        report: dict[str, Any] = fake_report()
        report["audits"].update(real["audits"])
        report["finalDisplayedUrl"] = real["finalDisplayedUrl"]
        Path(os.environ["FAKE_LH_REPORT"]).write_text(
            json.dumps(report), encoding="utf-8", newline="\n"
        )
        code, out, err = self.run_main("--pages", "/", "--presets", "desktop")
        self.assertEqual(code, 0, err)
        result = self.result("/", "desktop")
        expected = recorded(lighthouse_audits.extract_audits(real))
        self.assertEqual([run["audits"] for run in result["runs"]], [expected] * 3)
        self.assertEqual(
            result["audit_medians"]["lcp"]["selector"], {IMAGE_SELECTOR: 3}
        )
        # A request's run count reads against the result's own count of measured runs.
        self.assertEqual(result["measured_runs"], 3)
        self.assertEqual(
            result["audit_medians"]["render_blocking"]["requests"]["/index.js"],
            {"median_ms": 55.0, "runs": 3},
        )
        row = (
            "| / | desktop | 150 | 286 | 5 | 0 | img.slide "
            "| hinted n, discoverable y, eager y | 235433 | 2 | no |"
        )
        # Printed after the metrics table, a blank line apart so the two never merge.
        self.assertIn(f"|\n\n{_HEADER}\n{_RULE}\n{row}\n", out)

    def test_a_report_without_these_audits_still_measures(self) -> None:
        code, out, err = self.run_main("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0, err)
        result = self.result("/", "mobile")
        self.assertEqual(result["measured_runs"], 3)
        self.assertEqual([run["audits"] for run in result["runs"]], [NO_AUDITS] * 3)
        self.assertIn("| / | mobile |" + " n/a |" * 9, out)

    def test_a_failed_run_records_no_audits(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_LH_MODE": "fail-desktop"}):
            code, _, _ = self.run_main("--pages", "/")
        self.assertEqual(code, 1)
        desktop = self.result("/", "desktop")
        self.assertIsNone(desktop["runs"][0]["audits"])
        self.assertIsNone(desktop["audit_medians"])
        self.assertIsNotNone(self.result("/", "mobile")["audit_medians"])

    def test_an_outcome_holding_audits_can_still_be_hashed(self) -> None:
        # Reached through the CLI, whose bootstrap makes run_summary's imports findable.
        summary = lighthouse_run.run_summary
        job = summary.Job(1, "/", "desktop")
        audits = lighthouse_audits.extract_audits(audit_report("404-desktop-run1"))
        self.assertEqual(
            hash(summary.Outcome(job, audits=audits)), hash(summary.Outcome(job))
        )


if __name__ == "__main__":
    unittest.main()
