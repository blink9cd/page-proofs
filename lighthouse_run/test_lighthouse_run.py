#!/usr/bin/env python3
"""Aggregator for lighthouse_run's grouped suites: bare-runnable, 100% branch.

This file holds no tests of its own beyond its guards. The real suites live in the sibling
``tests/`` directory, grouped by the module each exercises, and this file is the
``test_<folder>.py`` aggregator that runs them.

Entry point assumptions: run bare, or under a coverage runner from this folder, with coverage
scoped to ``lighthouse_run`` and its ``src/``. Both put this directory on ``sys.path``;
the insert below makes that explicit rather than inherited.
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
    "test_lighthouse_run_audit_summary.py": 18,
    "test_lighthouse_run_audits.py": 36,
    "test_lighthouse_run_cli.py": 40,
    "test_lighthouse_run_facts.py": 7,
    "test_lighthouse_run_report.py": 23,
    "test_lighthouse_run_serve.py": 8,
    "test_lighthouse_run_url.py": 36,
    "test_lighthouse_run_url_host.py": 6,
}

# This module's OWN tests, counted exactly and apart from the floors, so no amount of grouped
# growth pays for a deleted guard.
_OWN_GUARD_TESTS = 2


def _grouped_suite() -> unittest.TestSuite:
    """Every ``test_*.py`` under ``tests/``, loaded as top-level modules.

    ``top_level_dir`` is the suite directory itself, so the modules need no ``__init__.py``;
    unittest puts that directory on ``sys.path`` while loading, which is how each suite reaches
    the shared ``_lighthouse_run_fixtures`` module beside it.
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
