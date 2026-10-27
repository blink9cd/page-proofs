"""Grouped suite: the exit code, the cross-engine pairing, and the report in JSON and Markdown."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_report  # noqa: E402 - the tool's private src/, via the insert above
import pass_shift  # noqa: E402 - the tool's private src/, via the insert above
from pass_digest import Box  # noqa: E402 - the tool's private src/, via the insert above
from pass_motion import NO_MOTION, Motion  # noqa: E402 - the tool's private src/
from pass_targets import Rendering  # noqa: E402 - the tool's private src/, via the insert above

_HOME = Rendering("chromium", "/", 390, 844, "light", "on")

#: A late-script load that measured one shift of the home page's band.
_BAND_SHIFT = pass_shift.LateShift(
    pass_shift.MEASURED,
    shifts=(pass_shift.Shift(237.0, 0.0962, ("body>figure:1", "body>p:2")),),
)

_FACTS = pass_report.RunFacts(
    started="20260920T230000Z",
    commit="abc1234",
    tree_dirty=False,
    served="src",
    base_url="http://127.0.0.1:50000",
    playwright="1.63.0",
    engines={"chromium": "153.0.8010.12", "webkit": "26.6"},
    prove=False,
    require_csp=False,
)


def _result(
    rendering: Rendering = _HOME, **changes: object
) -> pass_report.RenderingResult:
    """A clean result for `rendering`, with `changes` applied."""
    clean = pass_report.RenderingResult(
        rendering=rendering,
        faults=(),
        findings={},
        verdicts={},
        digest={"body>main:1": Box(True, 0.0, 68.0, 390.0, 900.0, 16.0)},
        late_shift=pass_shift.LateShift(pass_shift.MEASURED),
        motion=NO_MOTION,
    )
    return replace(clean, **changes)  # type: ignore[arg-type] # changes name real fields


def _not_measured(rendering: Rendering) -> pass_report.RenderingResult:
    """A clean result from an engine or scripting state the late-shift load does not run in."""
    late = pass_shift.not_measured(rendering)
    assert late is not None
    return _result(rendering, late_shift=late)


class ExitCodeTests(unittest.TestCase):
    """0 all clean, 1 something found or a plant missed, 2 the measurement cannot be trusted."""

    def test_clean_is_zero(self) -> None:
        self.assertEqual(pass_report.exit_code([_result()]), 0)

    def test_a_finding_is_one(self) -> None:
        found = _result(findings={"small_target": ("body>a:1 20x20",)})
        self.assertEqual(pass_report.exit_code([_result(), found]), 1)

    def test_a_missed_plant_is_one(self) -> None:
        self.assertEqual(
            pass_report.exit_code([_result(verdicts={"page_error": "missed"})]), 1
        )

    def test_an_instrument_fault_is_two_even_beside_findings(self) -> None:
        faulted = _result(faults=("a classic scrollbar takes 15px",))
        found = _result(findings={"page_overflow": ("wider",)})
        self.assertEqual(pass_report.exit_code([found, faulted]), 2)

    def test_a_plant_that_did_not_take_is_two(self) -> None:
        self.assertEqual(
            pass_report.exit_code(
                [_result(verdicts={"hidden_rendered": "not planted"})]
            ),
            2,
        )

    def test_nothing_measured_is_two(self) -> None:
        self.assertEqual(pass_report.exit_code([]), 2)

    def test_a_late_shift_finding_is_one(self) -> None:
        found = _result(findings={"late_shift": ("0.0962 from body>figure:1",)})
        self.assertEqual(pass_report.exit_code([found]), 1)

    def test_motion_stilled_or_left_running_changes_nothing(self) -> None:
        # Motion is how a page was brought to rest before it was measured, not a finding.
        moving = _result(motion=Motion(finished=3, paused=1, running=2))
        self.assertEqual(pass_report.exit_code([moving]), 0)

    def test_a_plant_in_an_engine_without_layout_shifts_is_neither(self) -> None:
        # Not a miss (nothing could catch it there) and not a fault (the engine was asked
        # nothing it cannot answer): the report says NOT MEASURED instead.
        unmeasured = _result(verdicts={"late_shift": "not measured"})
        self.assertEqual(pass_report.exit_code([unmeasured]), 0)


class CrossEngineTests(unittest.TestCase):
    """Pairing each engine's rendering with the reference engine's rendering of the same key."""

    def test_compares_each_other_engine_with_the_reference(self) -> None:
        webkit = _result(
            replace(_HOME, engine="webkit"),
            digest={"body>main:1": Box(True, 0.0, 70.92, 390.0, 900.0, 16.0)},
        )
        comparisons = pass_report.cross_engine(
            [_result(), webkit], reference="chromium"
        )
        self.assertEqual(len(comparisons), 1)
        self.assertEqual(comparisons[0].other, "webkit")
        self.assertEqual(comparisons[0].summary["vertical"].first_path, "body>main:1")

    def test_skips_a_rendering_with_no_reference_or_a_fault(self) -> None:
        lone = _result(replace(_HOME, engine="webkit", page="/archive/"))
        faulted = _result(
            replace(_HOME, engine="webkit"), faults=("scripting still on",)
        )
        self.assertEqual(
            pass_report.cross_engine([_result(), lone, faulted], reference="chromium"),
            [],
        )

    def test_a_faulted_reference_compares_nothing(self) -> None:
        reference = _result(faults=("viewport 375px wide",))
        webkit = _result(replace(_HOME, engine="webkit"))
        self.assertEqual(
            pass_report.cross_engine([reference, webkit], reference="chromium"), []
        )


class DocumentTests(unittest.TestCase):
    """The JSON record: every rendering, its verdicts, the facts, and the exit code."""

    def test_records_the_facts_the_renderings_and_the_exit_code(self) -> None:
        found = _result(findings={"small_target": ("body>a:1 20x20",)})
        document = pass_report.report_document([found], [], _FACTS)
        self.assertEqual(document["exit_code"], 1)
        self.assertEqual(document["facts"]["commit"], "abc1234")  # type: ignore[index] # a dict
        rendering = document["renderings"][0]  # type: ignore[index] # a list
        self.assertEqual(rendering["orientation"], "portrait")
        self.assertEqual(rendering["findings"], {"small_target": ["body>a:1 20x20"]})
        json.dumps(document)  # must serialize as it stands

    def test_records_each_rendering_s_late_shift_score_and_windows(self) -> None:
        measured = _result(late_shift=_BAND_SHIFT)
        unmeasured = _not_measured(replace(_HOME, engine="webkit"))
        document = pass_report.report_document([measured, unmeasured], [], _FACTS)
        records = [r["late_shift"] for r in document["renderings"]]  # type: ignore[attr-defined,index] # a list of dicts
        self.assertEqual(
            records[0],
            {
                "status": "measured",
                "reason": "",
                "score": 0.0962,
                "windows": [
                    {
                        "start_ms": 237.0,
                        "score": 0.0962,
                        "sources": ["body>figure:1", "body>p:2"],
                    }
                ],
            },
        )
        self.assertEqual(records[1]["status"], "not measured")
        self.assertIn("webkit", records[1]["reason"])
        json.dumps(document)

    def test_records_the_motion_each_rendering_s_settle_stilled(self) -> None:
        moving = _result(motion=Motion(finished=2, paused=1, running=0))
        document = pass_report.report_document([moving, _result()], [], _FACTS)
        records = [r["motion"] for r in document["renderings"]]  # type: ignore[attr-defined,index] # a list of dicts
        self.assertEqual(
            records,
            [
                {"finished": 2, "paused": 1, "running": 0},
                {"finished": 0, "paused": 0, "running": 0},
            ],
        )
        json.dumps(document)

    def test_records_the_late_script_load_s_motion_apart_from_the_page_load_s(
        self,
    ) -> None:
        # The same animations are stilled by both loads, so each keeps its own counts.
        moving = _result(motion=Motion(1, 1, 0), late_motion=Motion(0, 1, 1))
        document = pass_report.report_document([moving, _result()], [], _FACTS)
        renderings = document["renderings"]
        assert isinstance(renderings, list)
        self.assertEqual(
            [(r["motion"], r["late_motion"]) for r in renderings],
            [
                (
                    {"finished": 1, "paused": 1, "running": 0},
                    {"finished": 0, "paused": 1, "running": 1},
                ),
                (
                    {"finished": 0, "paused": 0, "running": 0},
                    {"finished": 0, "paused": 0, "running": 0},
                ),
            ],
        )
        json.dumps(document)

    def test_write_report_writes_both_files(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        run_dir = Path(scratch.name) / "run"
        document = pass_report.report_document([_result()], [], _FACTS)
        pass_report.write_report(run_dir, document, "# report\n")
        written = json.loads(
            (run_dir / pass_report.JSON_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(written["exit_code"], 0)
        self.assertEqual(
            (run_dir / pass_report.MARKDOWN_NAME).read_text(encoding="utf-8"),
            "# report\n",
        )


class MarkdownTests(unittest.TestCase):
    """The page a person reads: verdict first, then only the sections with something in them."""

    def _markdown(
        self, results: list[pass_report.RenderingResult], *, prove: bool = False
    ) -> str:
        facts = replace(_FACTS, prove=prove)
        comparisons = pass_report.cross_engine(results, reference="chromium")
        return pass_report.report_markdown(results, comparisons, facts)

    def test_a_clean_run_says_so_and_counts_zero_for_every_detector(self) -> None:
        text = self._markdown([_result(), _result(replace(_HOME, engine="webkit"))])
        self.assertIn("**Verdict: CLEAN**", text)
        self.assertIn("| page_overflow | 0 | 0 |", text)
        self.assertIn("No detector found anything.", text)
        self.assertIn("No cross-engine differences", text)
        self.assertNotIn("## Instrument faults", text)
        self.assertNotIn("## Proof", text)
        self.assertNotIn("## Motion", text)

    def test_a_run_that_stilled_motion_has_a_section_on_it_grouped_by_page(
        self,
    ) -> None:
        results = [
            _result(motion=Motion(finished=2, paused=1, running=0)),
            _result(replace(_HOME, engine="webkit"), motion=Motion(0, 0, 1)),
        ]
        text = self._markdown(results)
        self.assertIn("**Verdict: CLEAN**", text)
        self.assertIn("## Motion", text)
        self.assertIn(
            "- `/`, 2 renderings: 2 finished, 1 paused, 1 still running when the settle ended, "
            "in 1 rendering: webkit / 390x844 light scripts-on",
            text,
        )

    def test_the_late_load_s_motion_has_lines_of_its_own_in_the_motion_section(
        self,
    ) -> None:
        results = [
            _result(motion=Motion(1, 1, 0), late_motion=Motion(0, 1, 0)),
            _result(replace(_HOME, mode="dark"), motion=Motion(1, 1, 0)),
        ]
        text = self._markdown(results)
        self.assertIn("**Verdict: CLEAN**", text)
        section = text[text.index("## Motion") :]
        self.assertIn(
            "- `/`, 2 renderings: 2 finished, 2 paused, 0 still running", section
        )
        late = section[section.index("**The late-script load**") :]
        self.assertIn("- `/`, 1 rendering: 0 finished, 1 paused, 0 still running", late)

    def test_groups_a_finding_repeated_across_renderings(self) -> None:
        item = ("body>a:1 20x20",)
        results = [
            _result(findings={"small_target": item}),
            _result(replace(_HOME, mode="dark"), findings={"small_target": item}),
        ]
        text = self._markdown(results)
        self.assertIn("**Verdict: FINDINGS**", text)
        self.assertIn("| small_target | 2 | 0 |", text)
        self.assertIn("- `body>a:1 20x20` in 2 renderings:", text)

    def test_lists_at_most_a_handful_of_renderings_per_finding(self) -> None:
        item = ("body>a:1 20x20",)
        results = [
            _result(replace(_HOME, width=width), findings={"small_target": item})
            for width in range(300, 310)
        ]
        text = self._markdown(results)
        self.assertIn("in 10 renderings:", text)
        self.assertIn("and 5 more", text)

    def test_lists_instrument_faults(self) -> None:
        text = self._markdown([_result(faults=("a classic scrollbar takes 15px",))])
        self.assertIn("**Verdict: INSTRUMENT FAULT**", text)
        self.assertIn("## Instrument faults", text)
        self.assertIn("a classic scrollbar takes 15px", text)

    def test_the_proof_table_counts_each_verdict_per_detector_and_engine(self) -> None:
        verdicts = {
            "page_overflow": "caught",
            "page_error": "n/a",
            "console_error": "missed",
        }
        text = self._markdown([_result(verdicts=verdicts)], prove=True)
        self.assertIn("## Proof", text)
        self.assertIn("| page_overflow | caught 1 |", text)
        self.assertIn("| console_error | missed 1 |", text)
        self.assertIn("| page_error | n/a 1 |", text)

    def test_aggregates_cross_engine_differences_per_page_engine_and_axis(self) -> None:
        shifted = Box(True, 0.0, 70.92, 390.0, 900.0, 16.0)
        results = [
            _result(),
            _result(replace(_HOME, engine="webkit"), digest={"body>main:1": shifted}),
            _result(replace(_HOME, mode="dark")),
            _result(
                replace(_HOME, engine="webkit", mode="dark"),
                digest={"body>main:1": shifted},
            ),
        ]
        text = self._markdown(results)
        self.assertIn(
            "| / | webkit | vertical | 2 | 2.92 | `body>main:1` | `body>main:1` |", text
        )

    def test_names_where_the_largest_is_apart_from_where_the_engines_diverge(
        self,
    ) -> None:
        # The reported maximum and the first divergence are usually different elements. A row
        # pairing the maximum with the first divergence alone reads as that element moving by it.
        reference = {
            "body>header:1": Box(True, 0.0, 0.0, 390.0, 68.0, 16.0),
            "body>main:1": Box(True, 0.0, 68.0, 390.0, 900.0, 16.0),
        }
        other = {
            "body>header:1": Box(True, 0.0, 0.0, 390.0, 68.92, 16.0),
            "body>main:1": Box(True, 0.0, 68.92, 390.0, 905.0, 16.0),
        }
        results = [
            _result(digest=reference),
            _result(replace(_HOME, engine="webkit"), digest=other),
        ]
        text = self._markdown(results)
        self.assertIn("| Max Δ px | Largest at | First divergence |", text)
        self.assertIn(
            "| / | webkit | vertical | 1 | 5 | `body>main:1` | `body>header:1` |", text
        )

    def test_the_largest_across_renderings_names_its_own_element(self) -> None:
        # Two renderings of one page: the row's largest comes from the SECOND, so the element
        # named beside it must be that rendering's, not the first rendering's.
        small = {"body>main:1": Box(True, 0.0, 70.0, 390.0, 900.0, 16.0)}
        large = {
            "body>main:1": Box(True, 0.0, 70.0, 390.0, 900.0, 16.0),
            "body>footer:1": Box(True, 0.0, 1000.0, 390.0, 80.0, 16.0),
        }
        base = {
            "body>main:1": Box(True, 0.0, 68.0, 390.0, 900.0, 16.0),
            "body>footer:1": Box(True, 0.0, 968.0, 390.0, 80.0, 16.0),
        }
        dark = replace(_HOME, mode="dark")
        results = [
            _result(digest={"body>main:1": base["body>main:1"]}),
            _result(replace(_HOME, engine="webkit"), digest=small),
            _result(dark, digest=base),
            _result(replace(dark, engine="webkit"), digest=large),
        ]
        text = self._markdown(results)
        self.assertIn(
            "| / | webkit | vertical | 2 | 32 | `body>footer:1` | `body>main:1` |", text
        )

    def test_the_detector_table_never_counts_zero_where_late_shifts_were_not_measured(
        self,
    ) -> None:
        results = [
            _result(
                late_shift=_BAND_SHIFT,
                findings={"late_shift": ("0.0962 from body>figure:1, body>p:2",)},
            ),
            _not_measured(replace(_HOME, engine="webkit")),
        ]
        text = self._markdown(results)
        self.assertIn("| late_shift | 1 | not measured |", text)
        self.assertIn("### late_shift", text)
        self.assertIn("- `0.0962 from body>figure:1, body>p:2` in 1 rendering:", text)

    def test_scripts_off_alone_reads_not_applicable(self) -> None:
        # And the engine with no renderings at all reads `not measured`, never 0.
        text = self._markdown([_not_measured(replace(_HOME, scripts="off"))])
        self.assertIn("| late_shift | n/a | not measured |", text)

    def test_the_late_shift_section_summarizes_each_engine(self) -> None:
        quiet = pass_shift.LateShift(
            pass_shift.MEASURED,
            shifts=(pass_shift.Shift(300.0, 0.0004, ("body>p:1",)),),
        )
        results = [
            _result(late_shift=_BAND_SHIFT),
            _result(replace(_HOME, mode="dark"), late_shift=quiet),
            _not_measured(replace(_HOME, scripts="off")),
            _not_measured(replace(_HOME, engine="webkit")),
        ]
        text = self._markdown(results)
        self.assertIn("## Late-script layout shifts", text)
        self.assertIn(f"above {pass_shift.THRESHOLD:g}", text)
        self.assertIn(
            "| chromium | 2 | 1 | 0.0962 | chromium / 390x844 light scripts-on |", text
        )
        self.assertIn(
            "| webkit | not measured: webkit does not report layout shifts", text
        )

    def test_a_run_with_no_late_shift_measured_says_so(self) -> None:
        webkit_only = replace(_FACTS, engines={"webkit": "26.6"})
        results = [_not_measured(replace(_HOME, engine="webkit"))]
        text = pass_report.report_markdown(results, [], webkit_only)
        self.assertIn("No rendering in this run was measured for late-script", text)
        self.assertIn("| late_shift | not measured |", text)

    def test_the_proof_table_says_not_measured_rather_than_caught_or_missed(
        self,
    ) -> None:
        results = [
            _result(verdicts={"late_shift": "caught"}),
            _result(
                replace(_HOME, engine="webkit"),
                verdicts={"late_shift": "not measured"},
            ),
        ]
        text = self._markdown(results, prove=True)
        self.assertIn("| late_shift | caught 1 | not measured 1 |", text)

    def test_names_a_dirty_tree_and_an_unknown_commit(self) -> None:
        facts = replace(_FACTS, commit=None, tree_dirty=None, served=None)
        text = pass_report.report_markdown([_result()], [], facts)
        self.assertIn("Commit: unknown", text)
        self.assertIn("not served by this run", text)
        dirty = replace(_FACTS, tree_dirty=True)
        self.assertIn(
            "uncommitted edits", pass_report.report_markdown([_result()], [], dirty)
        )


if __name__ == "__main__":
    unittest.main()
