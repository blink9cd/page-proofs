"""Grouped suite: the CSP violation detector, through a browser that records what is asked of it.

`pass_csp` wires the shared `csp_watch` collector into a rendering: installed on a scripts-on
context before its first page, each distinct violation one finding, the plant made LAST and judged
on the log, and with `--require-csp` a page load declaring no policy a finding of its own. What is
checked here is that wiring and its order, which a stand-in makes plain: `_csp_fixtures.py`'s
browser hands out contexts that log every call, and pages that deliver scripted violations
through the exposed binding, only where the listener's init script would run, so never with
scripts off. The report's cell and facts and the CLI's `--require-csp` are here too. The real
engines report through it in `test_pass_csp_live.py`.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's folder on
# sys.path, and the CLI it imports adds the tool's src/, for every import after it.
from _cli_fixtures import CliCase
from _csp_fixtures import (
    BASE_URL,
    CHROMIUM,
    EVAL,
    EVAL_LINE,
    NO_LAYOUT,
    NO_POLICY,
    OFF,
    ON,
    PLANTED,
    STYLE,
    STYLE_LINE,
    TIMEOUT,
    Script,
    facts,
    payload,
    render,
)

import contextlib
import json
import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

import csp_watch
import pass_checks
import pass_csp
import pass_engine
import pass_report
import pass_shift
from browser_pass import build_parser
from pass_late_load import LateOutcome
from pass_report import JSON_NAME
from pass_targets import State

_BINDING_CALL = f"expose_binding {csp_watch.BINDING}"

#: A refused URL on the measured origin, as the engine reports it, and the line naming it.
_MEDIA = payload("media-src", f"{BASE_URL}/clip.mp4")
_MEDIA_LINE = "media-src blocked /clip.mp4 on /csp.html"

#: A refused URL on another origin, which keeps its whole URL, as an HTTP error's does.
_ELSEWHERE = payload("img-src", "https://images.example/image.png")
_ELSEWHERE_LINE = "img-src blocked https://images.example/image.png on /csp.html"


class CollectorWiringTests(unittest.TestCase):
    """The collector goes onto each context that can report, before that context's first page."""

    def test_a_scripts_on_rendering_installs_it_before_its_page(self) -> None:
        _, browser = render(ON, Script())
        (context,) = browser.contexts
        page_opened = context.calls.index("new_page")
        self.assertLess(context.calls.index(_BINDING_CALL), page_opened)
        self.assertLess(context.calls.index("add_init_script"), page_opened)

    def test_a_scripts_off_rendering_installs_nothing(self) -> None:
        result, browser = render(OFF, Script(on_load=(STYLE,)))
        (context,) = browser.contexts
        self.assertNotIn(_BINDING_CALL, context.calls)
        self.assertNotIn("add_init_script", context.calls)
        self.assertEqual(result.findings, {})

    def test_the_late_script_load_installs_one_of_its_own(self) -> None:
        _, browser = render(CHROMIUM, Script(), Script())
        late = browser.contexts[1]
        self.assertLess(late.calls.index(_BINDING_CALL), late.calls.index("new_page"))


class FindingTests(unittest.TestCase):
    """Each distinct violation is one finding; with scripts off the detector is not measured."""

    def test_each_distinct_violation_is_one_finding_in_arrival_order(self) -> None:
        result, _ = render(ON, Script(on_load=(STYLE, EVAL, STYLE)))
        self.assertEqual(result.faults, ())
        self.assertEqual(result.findings, {"csp_violation": (STYLE_LINE, EVAL_LINE)})
        self.assertEqual(pass_report.exit_code([result]), 1)

    def test_a_page_that_breaks_no_policy_has_no_finding(self) -> None:
        result, _ = render(ON, Script())
        self.assertEqual(result.findings, {})

    def test_scripts_off_reads_not_measured_in_the_report_never_zero(self) -> None:
        off, _ = render(OFF, Script(on_load=(STYLE,)))
        on, _ = render(ON, Script())
        alone = pass_report.report_markdown([off], [], facts("webkit"))
        self.assertIn("| csp_violation | not measured |", alone)
        # Beside a rendering that was watched, a zero is a measurement.
        beside = pass_report.report_markdown([off, on], [], facts("webkit"))
        self.assertIn("| csp_violation | 0 |", beside)
        # And an engine with no renderings at all is never a zero either.
        empty = pass_report.report_markdown([on], [], facts("webkit", "firefox"))
        self.assertIn("| csp_violation | 0 | not measured |", empty)


class AttributionTests(unittest.TestCase):
    """Only a page the pass itself opened is measured: another page's reports are not kept."""

    def test_another_page_s_violation_is_not_the_measured_page_s(self) -> None:
        # A tab an extension opens shares the context, and with it the collector.
        result, _ = render(ON, Script(on_load=(STYLE,), from_elsewhere=(EVAL,)))
        self.assertEqual(result.findings, {"csp_violation": (STYLE_LINE,)})

    def test_a_frame_an_extension_injected_into_the_page_is_not_the_page_s(
        self,
    ) -> None:
        # Its document is the extension's, under the extension's policy, though its page is
        # the measured one.
        injected = (
            ("chrome-extension://fblbloeflbleaellfeefliannmdofjap/panel.html", EVAL),
            ("moz-extension://0b1c2d3e-4f50-4617-8293-a4b5c6d7e8f9/panel.html", EVAL),
            ("https://images.example/embed.html", EVAL),
        )
        result, _ = render(ON, Script(on_load=(STYLE,), from_frames=injected))
        self.assertEqual(result.findings, {"csp_violation": (STYLE_LINE,)})

    def test_the_page_s_own_frames_on_its_origin_are_kept(self) -> None:
        # A frame on the measured origin, and documents that take their origin and policy
        # from the page that made them: srcdoc and blank frames, and a blob url's.
        own = (
            (f"{BASE_URL}/frame.html", EVAL),
            ("about:srcdoc", _MEDIA),
            ("about:blank", _ELSEWHERE),
            (f"blob:{BASE_URL}/0b1c2d3e-4f50-4617-8293-a4b5c6d7e8f9", STYLE),
        )
        result, _ = render(ON, Script(from_frames=own))
        lines = (EVAL_LINE, _MEDIA_LINE, _ELSEWHERE_LINE, STYLE_LINE)
        self.assertEqual(result.findings, {"csp_violation": lines})

    def test_the_measured_origin_is_the_main_frame_s_not_the_base_url_s(self) -> None:
        # A base url that redirects (http to https, a bare host to www) lands the page on
        # another origin: its own violations, and its own frames', are still the page's, and
        # a frame on the base url's origin is now another origin's.
        landed = "https://www.example.test"
        frames = (
            (f"{landed}/frame.html", EVAL),
            (f"blob:{landed}/0b1c2d3e-4f50-4617-8293-a4b5c6d7e8f9", _MEDIA),
            (f"{BASE_URL}/frame.html", _ELSEWHERE),
        )
        script = Script(
            on_load=(STYLE,), from_frames=frames, main_frame_url=f"{landed}/csp.html"
        )
        result, _ = render(ON, script)
        lines = (STYLE_LINE, EVAL_LINE, _MEDIA_LINE)
        self.assertEqual(result.findings, {"csp_violation": lines})

    def test_a_caller_naming_no_frame_url_is_not_kept(self) -> None:
        pages: list[Any] = [object()]
        for frame in (None, object()):
            with self.subTest(frame=frame):
                source = {"page": pages[0], "frame": frame}
                self.assertFalse(pass_csp._opened(source, pages))
        # Nor one that is no caller Playwright hands a binding at all.
        self.assertFalse(pass_csp._opened("a caller", pages))

    def test_nor_in_the_late_script_load(self) -> None:
        result, _ = render(CHROMIUM, Script(), Script(from_elsewhere=(EVAL, {})))
        self.assertEqual(result.faults, ())
        self.assertEqual(result.findings, {})

    def test_a_page_opened_with_nothing_watched_is_still_opened(self) -> None:
        _, browser = render(OFF, Script())
        context: Any = browser.contexts[0]  # a logging stand-in for the context
        self.assertIsNotNone(pass_csp.open_page(context, pass_csp.UNWATCHED))
        # Nothing watched, so nothing recorded: the shared UNWATCHED stays empty.
        self.assertEqual(pass_csp.UNWATCHED.pages, [])


class BlockedUrlTests(unittest.TestCase):
    """A refused URL on the measured origin is named by its path, as an HTTP error's is."""

    def test_the_page_load_names_one_by_its_path_and_another_origin_s_whole(
        self,
    ) -> None:
        result, _ = render(ON, Script(on_load=(_MEDIA, _ELSEWHERE)))
        self.assertEqual(
            result.findings, {"csp_violation": (_MEDIA_LINE, _ELSEWHERE_LINE)}
        )

    def test_the_late_script_load_names_one_by_its_path(self) -> None:
        result, _ = render(CHROMIUM, Script(), Script(on_load=(_MEDIA,)))
        self.assertEqual(result.findings, {"csp_violation": (_MEDIA_LINE,)})

    def test_both_loads_refusing_it_is_one_finding(self) -> None:
        result, _ = render(
            CHROMIUM, Script(on_load=(_MEDIA,)), Script(on_load=(_MEDIA,))
        )
        self.assertEqual(result.findings, {"csp_violation": (_MEDIA_LINE,)})


class PlantTests(unittest.TestCase):
    """Findings come from the log before any plant, the verdict from after the CSP plant."""

    def test_findings_are_taken_before_the_plants_and_the_verdict_after(self) -> None:
        script = Script(
            on_load=(STYLE,), on_probe_plant=(EVAL,), on_csp_plant=(PLANTED,)
        )
        result, _ = render(ON, script, prove=True)
        self.assertEqual(result.findings, {"csp_violation": (STYLE_LINE,)})
        self.assertEqual(result.verdicts["csp_violation"], "caught")

    def test_the_csp_plant_is_made_last(self) -> None:
        _, browser = render(ON, Script(on_csp_plant=(PLANTED,)), prove=True)
        calls = browser.contexts[0].calls
        self.assertLess(calls.index("plant"), calls.index("csp plant"))
        self.assertEqual(calls[-2:], ["csp plant", "close"])

    def test_a_plant_the_log_never_reports_is_missed(self) -> None:
        result, _ = render(ON, Script(), prove=True)
        self.assertEqual(result.verdicts["csp_violation"], "missed")

    def test_a_violation_without_the_planted_sample_does_not_count(self) -> None:
        result, _ = render(ON, Script(on_csp_plant=(STYLE,)), prove=True)
        self.assertEqual(result.verdicts["csp_violation"], "missed")

    def test_the_plant_is_n_a_with_scripts_off_and_never_made(self) -> None:
        result, browser = render(OFF, Script(on_csp_plant=(PLANTED,)), prove=True)
        self.assertEqual(result.verdicts["csp_violation"], "n/a")
        self.assertNotIn("csp plant", browser.contexts[0].calls)

    def test_the_verdict_is_the_log_s_whatever_the_probe_says_it_planted(self) -> None:
        clean = pass_checks.observation_from(
            NO_LAYOUT,
            console_errors=[],
            page_errors=[],
            http_errors=[],
            csp_violations=[],
        )
        nothing_took = dict.fromkeys(pass_checks.DETECTORS, False)
        for caught, verdict in ((True, "caught"), (False, "missed")):
            verdicts = pass_checks.plant_verdicts(
                clean, clean, nothing_took, scripts_on=True, csp_caught=caught
            )
            self.assertEqual(verdicts["csp_violation"], verdict)
        verdicts = pass_checks.plant_verdicts(
            clean, clean, nothing_took, scripts_on=False, csp_caught=True
        )
        self.assertEqual(verdicts["csp_violation"], "n/a")


class LateLoadTests(unittest.TestCase):
    """The late-script load's violations join the page load's, taken before its own plant."""

    def test_its_violations_merge_with_the_page_load_s(self) -> None:
        result, _ = render(CHROMIUM, Script(on_load=(STYLE,)), Script(on_load=(EVAL,)))
        self.assertEqual(result.findings["csp_violation"], (STYLE_LINE, EVAL_LINE))

    def test_a_detector_both_loads_found_is_combined_not_overwritten(self) -> None:
        result, _ = render(ON, Script())
        found = replace(
            result, findings={"csp_violation": ("a",), "small_target": ("x",)}
        )
        late = pass_shift.LateShift(
            pass_shift.MEASURED,
            shifts=(pass_shift.Shift(20.0, 0.5, ("body>p:1",)),),
        )
        merged = pass_engine.with_late_shift(found, LateOutcome(late, csp=("b", "a")))
        self.assertEqual(
            merged.findings,
            {
                "csp_violation": ("a", "b"),
                "small_target": ("x",),
                "late_shift": ("0.5000 from body>p:1",),
            },
        )

    def test_only_csp_violations_are_merged_every_other_repeat_is_kept(self) -> None:
        result, _ = render(ON, Script())
        found = replace(
            result, findings={"console_error": ("x", "x"), "csp_violation": ("a", "a")}
        )
        late = pass_shift.LateShift(pass_shift.MEASURED)
        merged = pass_engine.with_late_shift(found, LateOutcome(late, csp=("a",)))
        self.assertEqual(
            merged.findings, {"console_error": ("x", "x"), "csp_violation": ("a",)}
        )

    def test_it_takes_its_findings_before_its_plant(self) -> None:
        late_script = Script(on_load=(STYLE,), on_shift_plant=(EVAL,))
        result, _ = render(CHROMIUM, Script(), late_script, prove=True)
        self.assertEqual(result.findings["csp_violation"], (STYLE_LINE,))


class RequireCspTests(unittest.TestCase):
    """With `--require-csp`, a page load whose markup declares no policy is a finding."""

    def test_the_finding_names_what_is_missing(self) -> None:
        self.assertEqual(NO_POLICY, "page carries no Content-Security-Policy meta")

    def test_a_page_load_without_one_is_a_finding_scripts_on_and_off(self) -> None:
        for rendering in (ON, OFF):
            with self.subTest(scripts=rendering.scripts):
                result, _ = render(rendering, Script(), require_csp=True)
                self.assertEqual(result.findings, {"csp_violation": (NO_POLICY,)})

    def test_without_the_option_the_policy_is_not_even_read(self) -> None:
        result, browser = render(ON, Script(), require_csp=False)
        self.assertEqual(result.findings, {})
        self.assertNotIn("policy_of", browser.contexts[0].calls)

    def test_a_page_declaring_one_meets_it(self) -> None:
        declared = Script(policy="default-src 'self'")
        result, _ = render(ON, declared, require_csp=True)
        self.assertEqual(result.findings, {})

    def test_it_follows_the_page_s_violations(self) -> None:
        result, _ = render(ON, Script(on_load=(STYLE,)), require_csp=True)
        self.assertEqual(result.findings["csp_violation"], (STYLE_LINE, NO_POLICY))

    def test_a_state_is_not_held_to_it(self) -> None:
        state = State(name="opened", page="/csp.html", storage=(), actions=())
        rendering = replace(ON, state="opened")
        result, browser = render(rendering, Script(), require_csp=True, state=state)
        self.assertEqual(result.findings, {})
        self.assertNotIn("policy_of", browser.contexts[0].calls)


class MatrixWiringTests(unittest.TestCase):
    """`run_matrix` hands `--require-csp` to every rendering it renders."""

    def test_each_rendering_is_told_whether_a_policy_is_required(self) -> None:
        told: list[bool] = []

        def recording_render(
            *_args: object, require_csp: bool, **_options: object
        ) -> object:
            told.append(require_csp)
            return mock.Mock()

        with (
            mock.patch.object(
                pass_engine,
                "sync_playwright",
                lambda: contextlib.nullcontext(mock.Mock()),
            ),
            mock.patch.object(pass_engine, "launch", return_value=mock.Mock()),
            mock.patch.object(pass_engine, "render", recording_render),
        ):
            for required in (True, False):
                pass_engine.run_matrix(
                    [ON, OFF],
                    BASE_URL,
                    timeout=TIMEOUT,
                    prove=False,
                    scratch=Path("unused"),
                    progress=lambda _result: None,
                    require_csp=required,
                )
        self.assertEqual(told, [True, True, False, False])


class InstrumentFaultTests(unittest.TestCase):
    """A payload the listener never sends is a fault naming the rendering, never a finding."""

    def test_in_the_page_load_it_stops_the_rendering(self) -> None:
        result, _ = render(ON, Script(on_load=({"document": "/csp.html"},)))
        (fault,) = result.faults
        self.assertTrue(fault.startswith("csp_violation: "), fault)
        self.assertIn("lacks", fault)
        self.assertEqual(result.findings, {})
        self.assertEqual(pass_report.exit_code([result]), 2)

    def test_in_the_late_script_load_it_is_one_too(self) -> None:
        result, _ = render(CHROMIUM, Script(), Script(on_load=("not one",)))
        self.assertEqual(
            [fault for fault in result.faults if fault.startswith("csp_violation: ")],
            [
                "csp_violation: the collector was sent what its listener never "
                "sends: the listener sent str, not a violation"
            ],
        )
        self.assertEqual(result.late_shift.status, pass_shift.NOT_MEASURED)
        self.assertEqual(pass_report.exit_code([result]), 2)

    def test_a_policy_read_it_cannot_use_names_the_read_not_the_listener(
        self,
    ) -> None:
        result, _ = render(ON, Script(policy=7), require_csp=True)
        self.assertEqual(
            result.faults,
            ("csp_violation: the policy read answered int, not text or nothing",),
        )
        self.assertEqual(pass_report.exit_code([result]), 2)


class ReportTests(unittest.TestCase):
    """The report records whether a policy was required, and counts what that found."""

    def test_the_facts_record_whether_a_policy_was_required(self) -> None:
        result, _ = render(ON, Script())
        for required, said in ((True, "yes"), (False, "no")):
            with self.subTest(required=required):
                run = facts("webkit", require_csp=required)
                # Read back as the JSON report holds it.
                written = json.loads(
                    json.dumps(pass_report.report_document([result], [], run))
                )
                self.assertIs(written["facts"]["require_csp"], required)
                text = pass_report.report_markdown([result], [], run)
                self.assertIn(f"- Content-Security-Policy meta required: {said}", text)

    def test_a_missing_policy_found_with_scripts_off_is_counted(self) -> None:
        off, _ = render(OFF, Script(), require_csp=True)
        text = pass_report.report_markdown([off], [], facts("webkit"))
        self.assertIn("| csp_violation | 1 |", text)


class RequireCspCliTests(CliCase):
    """`--require-csp`: off by default, handed to the engine, and recorded in the report."""

    def _facts_written(self) -> Mapping[str, object]:
        record = json.loads((self.only_run_dir() / JSON_NAME).read_text("utf-8"))
        facts: Mapping[str, object] = record["facts"]
        return facts

    def test_it_is_off_by_default(self) -> None:
        self.assertFalse(build_parser().parse_args(["--targets", "t.toml"]).require_csp)
        code, _, _ = self.run_cli("--serve", str(self.site), "--engines", "webkit")
        self.assertEqual(code, 0)
        self.assertIs(self.engine.calls[0]["require_csp"], False)
        self.assertIs(self._facts_written()["require_csp"], False)

    def test_it_is_handed_to_the_engine_and_recorded(self) -> None:
        code, _, _ = self.run_cli(
            "--serve", str(self.site), "--engines", "webkit", "--require-csp"
        )
        self.assertEqual(code, 0)
        self.assertIs(self.engine.calls[0]["require_csp"], True)
        self.assertIs(self._facts_written()["require_csp"], True)

    def test_help_says_what_it_does(self) -> None:
        self.assertIn("--require-csp", build_parser().format_help())


if __name__ == "__main__":
    unittest.main()
