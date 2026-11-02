"""Grouped suite: the LIVE boundary for the CSP detector, in the pinned Chromium, Firefox and WebKit.

`samples/site/csp.html` declares a policy and carries one style attribute that policy refuses.
With scripts on, every engine reports that refusal through the collector as ONE `csp_violation`
finding (in Chromium the late-script load reports it again, and the two loads' findings merge
into that one), and also logs it as a console error, so the refusal is reported twice. With
scripts off the collector's init script never runs: the detector is not measured, never zero, and
the console error is what still reports the refusal. On the clean page the plant is caught with
scripts on and is `n/a` with them off. The policy page refuses style attributes, and with `--prove`
every other plant that is made still takes there and is caught, Chromium's late-shift plant
included, while the rest read `n/a` or `not measured` as on the main sample: the run is a finding
rather than an instrument fault. With `--require-csp`, the clean page,
which declares no policy, is a finding scripts on and off, and the policy page is not.
`_live_fixtures.py` holds the setup the live suites share.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ and the
# shared loopback server on sys.path for every import after it.
from _live_fixtures import LiveSite, SharedEngines

import re
import unittest

import pass_csp
import pass_report
import pass_shift
from pass_checks import CSP_VIOLATION, DETECTORS
from pass_report import RenderingResult
from pass_targets import ENGINES, SCRIPTS, Rendering

#: The engine launches this module may make (`LiveSite` fails it past them): one browser per
#: engine, shared by every test here.
_LAUNCH_BUDGET = {"chromium": 1, "firefox": 1, "webkit": 1}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)

#: The policy page's one refusal, as the detector names it.
_REFUSED = "style-src-attr blocked inline on /csp.html"

#: How each engine's console names the refusal, measured in the pinned builds: Chromium and
#: WebKit write "Content Security Policy", Firefox "Content-Security-Policy".
_NAMES_THE_POLICY = re.compile(r"content.security.policy", re.IGNORECASE)

#: The plants only a script in the page can report: `n/a` with scripts off.
_SCRIPTED_PLANTS = ("page_error", CSP_VIOLATION)

#: The exit code of a run that found something and whose instrument is not in doubt.
_EXIT_FOUND = 1


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the pinned builds and serve the sample site for the whole module."""
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)


def _every_plant_caught(rendering: Rendering) -> dict[str, str]:
    """The proof `rendering` should read: every plant caught, but where it cannot be reported.

    A scripted plant is `n/a` with scripts off, and the late-shift plant reads whatever status
    the late-script load gives a rendering it does not measure.
    """
    off = rendering.scripts == "off"
    skipped = pass_shift.not_measured(rendering)
    return {
        **{
            detector: "n/a" if off and detector in _SCRIPTED_PLANTS else "caught"
            for detector in DETECTORS
        },
        pass_shift.LATE_SHIFT: skipped.status if skipped else "caught",
    }


def _console_reports_the_policy(result: RenderingResult) -> bool:
    """Whether a console error `result` found names the Content Security Policy."""
    errors = result.findings.get("console_error", ())
    return any(_NAMES_THE_POLICY.search(error) for error in errors)


class CspLiveTests(unittest.TestCase):
    """In each engine: the refusal found, not measured where it cannot be, and the plant caught."""

    engines = SharedEngines()

    @classmethod
    def setUpClass(cls) -> None:
        """Start the class's driver; `setUpModule` has already pointed it at the builds."""
        cls.engines.start()
        cls.addClassCleanup(cls.engines.stop)

    def _render(
        self,
        engine: str,
        page: str,
        scripts: str,
        *,
        prove: bool = False,
        require_csp: bool = False,
    ) -> RenderingResult:
        """One phone rendering of `page`, which must be measured rather than refused."""
        rendering = Rendering(engine, page, 390, 844, "light", scripts)
        result = self.engines.render(
            rendering, _SITE.base_url, prove=prove, require_csp=require_csp
        )
        self.assertEqual(result.faults, (), rendering.label)
        return result

    def test_a_refused_style_attribute_is_one_finding_and_a_console_error(
        self,
    ) -> None:
        for engine in ENGINES:
            with self.subTest(engine=engine):
                result = self._render(engine, "/csp.html", "on")
                self.assertEqual(result.findings["csp_violation"], (_REFUSED,))
                self.assertTrue(_console_reports_the_policy(result))

    def test_with_scripts_off_it_is_not_measured_and_the_console_reports_it(
        self,
    ) -> None:
        for engine in ENGINES:
            with self.subTest(engine=engine):
                result = self._render(engine, "/csp.html", "off")
                self.assertNotIn("csp_violation", result.findings)
                self.assertTrue(_console_reports_the_policy(result))
                facts = pass_report.RunFacts(
                    started="20261006T000000Z",
                    commit=None,
                    tree_dirty=None,
                    served=None,
                    base_url=_SITE.base_url,
                    playwright="",
                    engines={engine: ""},
                    prove=False,
                    require_csp=False,
                )
                text = pass_report.report_markdown([result], [], facts)
                self.assertIn("| csp_violation | not measured |", text)

    def test_the_plant_is_caught_with_scripts_on_and_n_a_off(self) -> None:
        for engine in ENGINES:
            with self.subTest(engine=engine):
                on = self._render(engine, "/clean.html", "on", prove=True)
                off = self._render(engine, "/clean.html", "off", prove=True)
                self.assertEqual(on.verdicts["csp_violation"], "caught")
                self.assertEqual(off.verdicts["csp_violation"], "n/a")
                # The plant's own violation came after the findings were taken.
                self.assertEqual(on.findings, {})

    def test_on_the_policy_page_every_plant_takes_and_is_caught(self) -> None:
        # The page refuses style attributes, so no plant may be drawn with one.
        results: list[RenderingResult] = []
        for engine in ENGINES:
            for scripts in SCRIPTS:
                with self.subTest(engine=engine, scripts=scripts):
                    result = self._render(engine, "/csp.html", scripts, prove=True)
                    results.append(result)
                    self.assertEqual(
                        result.verdicts, _every_plant_caught(result.rendering)
                    )
        # The page's own refusal is a finding; nothing about the instrument is in doubt.
        self.assertEqual(pass_report.exit_code(results), _EXIT_FOUND)

    def test_a_required_policy_is_read_scripts_on_and_off(self) -> None:
        for engine in ENGINES:
            for scripts in SCRIPTS:
                with self.subTest(engine=engine, scripts=scripts):
                    bare = self._render(
                        engine, "/clean.html", scripts, require_csp=True
                    )
                    declared = self._render(
                        engine, "/csp.html", scripts, require_csp=True
                    )
                    self.assertEqual(
                        bare.findings, {"csp_violation": (pass_csp.NO_POLICY,)}
                    )
                    self.assertNotIn(
                        pass_csp.NO_POLICY, declared.findings.get("csp_violation", ())
                    )


if __name__ == "__main__":
    unittest.main()
