#!/usr/bin/env python3
"""Aggregator for print_pass's grouped suites: bare-runnable, 100% branch.

This file holds no tests of its own beyond its guards. The real suites live in the sibling
``tests/`` directory, grouped by the module each exercises, and this file is the
``test_<folder>.py`` aggregator that runs them. ``test_print_pass_live.py``,
``test_print_pass_live_sites.py``, ``test_print_pass_live_redirects.py`` and
``test_print_pass_live_chrome.py`` among them drive the real engine, so a run of this file
launches the pinned headless shell, and installed Google Chrome where it is present.
``test_print_pass_live_firefox.py`` launches installed Firefox through geckodriver, both
under ``sandbox-exec`` inside a network fence it proves first, where macOS, geckodriver and
Firefox are all present. And on every macOS host with ``sandbox-exec``, Firefox or not, the
fence's suites run its canaries under it (the proof on the real profile and on each
profile missing a rule) and a stand-in driver through the fenced shim.

Entry point assumptions: run bare, or under a coverage run from this folder scoped to
``print_pass`` and its ``src/``. Both put this directory on ``sys.path``; the insert below
makes that explicit rather than inherited.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SUITE_DIR = _HERE / "tests"
sys.path.insert(0, str(_HERE))

# What each grouped file must yield, floored SEPARATELY: an empty or half-failed discovery is
# otherwise a PASSING run, and one combined floor lets a whole small file vanish inside the
# growth of a large one. Restated, not derived: a floor computed from discovery would compare
# discovery against itself and pass for an empty suite.
_MIN_TESTS_BY_FILE: dict[str, int] = {
    "test_gecko_budget.py": 12,
    "test_gecko_client.py": 10,
    "test_gecko_client_process.py": 4,
    "test_gecko_client_session.py": 21,
    "test_gecko_client_transport.py": 10,
    "test_gecko_engine.py": 27,
    "test_gecko_engine_guard.py": 7,
    "test_gecko_launch.py": 30,
    "test_gecko_launch_driver.py": 24,
    "test_gecko_launch_fuzz.py": 4,
    "test_gecko_launch_helpers.py": 30,
    "test_gecko_launch_stop.py": 15,
    "test_gecko_live_canary.py": 29,
    "test_gecko_live_fence.py": 25,
    "test_gecko_live_proof.py": 11,
    "test_gecko_live_sandbox_check.py": 13,
    "test_gecko_live_watch.py": 10,
    "test_gecko_log.py": 12,
    "test_gecko_network.py": 28,
    "test_gecko_network_fuzz.py": 2,
    "test_gecko_page.py": 24,
    "test_gecko_page_fuzz.py": 1,
    "test_gecko_prefs.py": 35,
    "test_gecko_print.py": 10,
    "test_gecko_process.py": 6,
    "test_gecko_process_fuzz.py": 6,
    "test_gecko_process_listing.py": 33,
    "test_gecko_reply.py": 20,
    "test_gecko_reply_fuzz.py": 3,
    "test_gecko_scratch.py": 17,
    "test_gecko_state.py": 30,
    "test_gecko_state_actions.py": 26,
    "test_gecko_state_isolation.py": 9,
    "test_pdf_privacy.py": 30,
    "test_pdf_privacy_content.py": 26,
    "test_pdf_raster.py": 36,
    "test_pdf_raster_render.py": 20,
    "test_pdf_read.py": 21,
    "test_pdf_read_fuzz.py": 2,
    "test_pdf_read_isolation.py": 7,
    "test_pdf_read_worker.py": 13,
    "test_pdf_text.py": 22,
    "test_pdf_text_pypdf.py": 21,
    "test_pdf_tools.py": 35,
    "test_pdf_tools_fuzz.py": 3,
    "test_pdf_tools_run.py": 10,
    "test_print_actions.py": 17,
    "test_print_csp.py": 17,
    "test_print_engine.py": 28,
    "test_print_engine_blink.py": 1,
    "test_print_engine_failures.py": 6,
    "test_print_engine_firefox.py": 3,
    "test_print_engine_fixtures.py": 4,
    "test_print_engine_network.py": 9,
    "test_print_engine_state.py": 33,
    "test_print_judge.py": 17,
    "test_print_judge_emulation.py": 5,
    "test_print_modes.py": 16,
    "test_print_modes_firefox.py": 10,
    "test_print_modes_read.py": 8,
    "test_print_offsite.py": 23,
    "test_print_page_width.py": 15,
    "test_print_page_width_engines.py": 7,
    "test_print_pass_cli.py": 35,
    "test_print_pass_cli_firefox.py": 10,
    "test_print_pass_live.py": 6,
    "test_print_pass_live_chrome.py": 2,
    "test_print_pass_live_firefox.py": 11,
    "test_print_pass_live_redirects.py": 3,
    "test_print_pass_live_sites.py": 17,
    "test_print_plan.py": 5,
    "test_print_playwright.py": 26,
    "test_print_preflight.py": 23,
    "test_print_probe.py": 27,
    "test_print_redirects.py": 19,
    "test_print_redirects_runs.py": 7,
    "test_print_report.py": 25,
    "test_print_report_engines.py": 12,
    "test_print_report_output.py": 27,
    "test_print_report_pdf.py": 29,
    "test_print_report_pdf_budget.py": 9,
    "test_print_report_pdf_compare.py": 8,
    "test_print_report_pdf_untrusted.py": 3,
    "test_print_report_scrub.py": 29,
    "test_print_targets.py": 29,
    "test_print_targets_refusals.py": 69,
    "test_print_watchdog.py": 14,
}

# This module's OWN tests, counted exactly and apart from the floors, so no amount of grouped
# growth pays for a deleted guard.
_OWN_GUARD_TESTS = 2


def _grouped_suite() -> unittest.TestSuite:
    """Every ``test_*.py`` under ``tests/``, loaded as top-level modules.

    ``top_level_dir`` is the suite directory itself, so the modules need no ``__init__.py``;
    unittest puts that directory on ``sys.path`` while loading, which is how each suite reaches
    the shared ``_print_targets_fixtures`` module beside it.
    """
    loader = unittest.TestLoader()
    return loader.discover(str(_SUITE_DIR), top_level_dir=str(_SUITE_DIR))


def _tests_by_module(suite: unittest.TestSuite) -> dict[str, int]:
    """Flatten a (possibly nested) suite into a per-file test count."""
    counts: dict[str, int] = {}
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for module, count in _tests_by_module(item).items():
                counts[module] = counts.get(module, 0) + count
        else:
            filename = f"{type(item).__module__}.py"
            counts[filename] = counts.get(filename, 0) + 1
    return counts


class GroupedSuiteDiscoveryTest(unittest.TestCase):
    """Fail loudly when a grouped suite is lost, or when a guard in here is."""

    def test_module_load_includes_the_grouped_suites(self) -> None:
        """Guards the ``load_tests`` WIRING, which a discovery-based check cannot see.

        ``loadTestsFromModule`` is what a bare run and a coverage run actually use, and it
        honors ``load_tests``; a guard calling ``_grouped_suite()`` directly would stay green
        with that hook deleted.
        """
        loaded = _tests_by_module(
            unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        )
        own_found = loaded.pop(f"{__name__}.py", 0)
        self.assertEqual(own_found, _OWN_GUARD_TESTS)
        self.assertGreaterEqual(sum(loaded.values()), sum(_MIN_TESTS_BY_FILE.values()))

    def test_grouped_suite_meets_its_own_per_file_floor(self) -> None:
        """Fail when ONE grouped file has lost tests, or a new file arrives unfloored."""
        found_by_file = _tests_by_module(_grouped_suite())
        self.assertEqual(found_by_file.keys(), _MIN_TESTS_BY_FILE.keys())
        for filename, floor in _MIN_TESTS_BY_FILE.items():
            self.assertGreaterEqual(found_by_file[filename], floor, filename)


def load_tests(
    loader: unittest.TestLoader, standard_tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    """unittest's own hook: fold the grouped suites in beside this file's guards."""
    standard_tests.addTests(_grouped_suite())
    return standard_tests


if __name__ == "__main__":
    unittest.main()
