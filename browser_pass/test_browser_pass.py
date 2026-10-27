#!/usr/bin/env python3
"""Aggregator for browser_pass's grouped suites -- bare-runnable, 100% branch.

This file holds no tests of its own beyond its guards. The real suites live in the sibling
``tests/`` directory, grouped by the module each exercises, and this file is the
``test_<folder>.py`` aggregator that runs them. ``test_pass_live.py`` among them drives the real
engine builds, so a run of this file launches Chromium, Firefox and WebKit, where they are
installed.

Entry point assumptions: run bare, or under a coverage runner from this folder, with coverage
scoped to ``browser_pass`` and its ``src/``. Both put this directory on ``sys.path``; the insert
below makes that explicit rather than inherited. A bare run loads everything. This file declares
``SPLIT_SUITE_BY_FILE``, so a runner that splits suites may instead run each grouped file as its
own task, and these guards as one more, through the selection ``suite_select`` owns, then
combine their coverage.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The shared suite-selection library, a folder beside this tool's own, named rather than
# searched for, so a same-named folder above the clone is never imported in its place, and put
# just after this folder, ahead of every installed distribution, so none of the same name can
# shadow it. Only sys.path statements precede the import, which keeps it lint-clean without a
# suppression.
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "suite_select"))

import suite_select

_HERE = Path(__file__).resolve().parent
_SUITE_DIR = _HERE / "tests"

# Opt in to a splitting runner running each grouped file as its own task: the live files mostly
# wait on engines, so one serial run of them all is the slowest part of this folder's tests.
# Read by AST (suite_select owns the name), never by import.
SPLIT_SUITE_BY_FILE = True

# What each grouped file must yield, floored SEPARATELY: an empty or half-failed discovery is
# otherwise a PASSING run, and one combined floor lets a whole small file vanish inside the
# growth of a large one. Restated, not derived: a floor computed from discovery would compare
# discovery against itself and pass for an empty suite.
_MIN_TESTS_BY_FILE: dict[str, int] = {
    "test_pass_checks.py": 33,
    "test_pass_cli.py": 31,
    "test_pass_cli_extension.py": 9,
    "test_pass_cli_not_found.py": 6,
    "test_pass_cli_redirects.py": 7,
    "test_pass_cli_states.py": 6,
    "test_pass_csp.py": 40,
    "test_pass_csp_live.py": 5,
    "test_pass_digest.py": 13,
    "test_pass_engine_isolation.py": 9,
    "test_pass_engine_launch.py": 7,
    "test_pass_engine_states.py": 6,
    "test_pass_extension.py": 29,
    "test_pass_extension_guard.py": 11,
    "test_pass_extension_launch.py": 15,
    "test_pass_extension_live.py": 3,
    "test_pass_extension_tabs.py": 18,
    "test_pass_facts.py": 7,
    "test_pass_inert.py": 15,
    "test_pass_late_faults.py": 5,
    "test_pass_late_live.py": 13,
    "test_pass_late_load.py": 10,
    "test_pass_late_motion_live.py": 11,
    "test_guard_expectations.py": 10,
    "test_live_fixtures.py": 22,
    "test_pass_live.py": 10,
    "test_pass_loopback_live.py": 2,
    "test_pass_markdown.py": 15,
    "test_pass_motion.py": 18,
    "test_pass_not_found.py": 22,
    "test_pass_not_found_live.py": 4,
    "test_pass_probe.py": 3,
    "test_pass_progress_serve.py": 7,
    "test_pass_redirects.py": 22,
    "test_pass_redirects_live.py": 2,
    "test_pass_report.py": 33,
    "test_pass_shadow_boxes_live.py": 5,
    "test_pass_shadow_motion_live.py": 6,
    "test_pass_shift.py": 39,
    "test_pass_states.py": 20,
    "test_pass_states_live.py": 3,
    "test_pass_targets.py": 31,
    "test_pass_targets_states.py": 18,
    "test_pass_watchdog.py": 6,
}

# This module's OWN tests, counted exactly and apart from the floors, so no amount of grouped
# growth pays for a deleted guard.
_OWN_GUARD_TESTS = 3


def _grouped_suite() -> unittest.TestSuite:
    """Every ``test_*.py`` under ``tests/``, loaded as top-level modules.

    ``top_level_dir`` is the suite directory itself, so the modules need no ``__init__.py``.
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


def _load_this_module() -> dict[str, int]:
    """Load this module as a bare run and a splitting runner do, through ``load_tests``."""
    return _tests_by_module(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    )


class GroupedSuiteDiscoveryTest(unittest.TestCase):
    """Fail loudly when a grouped suite is lost -- or when a guard in here is."""

    def test_module_load_includes_the_grouped_suites(self) -> None:
        """Guards the ``load_tests`` WIRING, which a discovery-based check cannot see.

        ``loadTestsFromModule`` is what a bare run and a splitting runner actually use, and it
        honors ``load_tests``; a guard calling ``_grouped_suite()`` directly would stay green
        with that hook deleted.
        """
        # Run inside a splitting runner's guard task, the selection would hide the fold-in this
        # guard exists to see, so it is cleared for this check alone.
        with mock.patch.dict(os.environ):
            os.environ.pop(suite_select.SELECT_ENV, None)
            loaded = _load_this_module()
        own_found = loaded.pop(f"{__name__}.py", 0)
        self.assertEqual(own_found, _OWN_GUARD_TESTS)
        self.assertGreaterEqual(sum(loaded.values()), sum(_MIN_TESTS_BY_FILE.values()))

    def test_split_is_declared_and_load_tests_honors_the_selection(self) -> None:
        """Guard the per-file split: the marker is set and each task loads only its share.

        A ``load_tests`` that ignored the selection would run the whole suite in every task,
        and one that dropped the guards would leave the guard task empty.
        """
        source = Path(__file__).read_text(encoding="utf-8")
        self.assertTrue(suite_select.declares_split(source))
        one_file = "test_pass_checks.py"
        with mock.patch.dict(os.environ, {suite_select.SELECT_ENV: one_file}):
            self.assertEqual(_load_this_module().keys(), {one_file})
        guards = {suite_select.SELECT_ENV: suite_select.GUARDS_ONLY}
        with mock.patch.dict(os.environ, guards):
            self.assertEqual(_load_this_module(), {f"{__name__}.py": _OWN_GUARD_TESTS})

    def test_grouped_suite_meets_its_own_per_file_floor(self) -> None:
        """Fail when ONE grouped file has lost tests, or a new file arrives unfloored."""
        found_by_file = _tests_by_module(_grouped_suite())
        self.assertEqual(found_by_file.keys(), _MIN_TESTS_BY_FILE.keys())
        for filename, floor in _MIN_TESTS_BY_FILE.items():
            self.assertGreaterEqual(found_by_file[filename], floor, filename)


def load_tests(
    loader: unittest.TestLoader, standard_tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    """unittest's own hook: fold in the grouped suites this task selected.

    Unset (a bare run), that is every grouped suite beside this file's guards; under
    a splitting runner's selection it is one grouped file, or the guards alone.
    """
    return suite_select.fold_selected(standard_tests, loader, _SUITE_DIR, os.environ)


if __name__ == "__main__":
    unittest.main()
