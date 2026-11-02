#!/usr/bin/env python3
"""csp_watch's contract, against stand-ins for a Playwright context and page; and the aggregator.

The stand-ins record every call made on them, so a test reads what `install`, `policy_of` and
`plant` asked of the engine, in order, without one running. Each is handed over as it is, with no
cast, so mypy checks it against the protocols the collector types its context and page with, as
it checks Playwright's own classes below; and a fresh interpreter shows importing the collector
imports no Playwright of its own. What an engine then reports is the live suite's job:
``tests/test_csp_watch_live.py`` runs the same calls in Chromium, Firefox and WebKit. This file
is also the ``test_<folder>.py`` aggregator beside a ``tests/`` folder, so it folds that suite in
and floors it, beside ``tests/test_csp_watch_live_setup.py``, the check of that suite's setup
that needs no engine.

Entry point assumptions: run bare, or under a coverage runner from this folder, with coverage
scoped to ``csp_watch``. Both put this directory on ``sys.path``; the insert below makes that
explicit rather than inherited. The second insert is the one discovery makes for ``tests/``
anyway, made early so the live suite's module can be imported here by the same name. The shared
``bounded_run`` is the folder beside this one. Playwright and Hypothesis are importable (the
repository's ``test`` extra).
"""

from __future__ import annotations

import json
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Final, cast

from hypothesis import given
from hypothesis import strategies as st
from playwright.sync_api import BrowserContext, Page

_HERE = Path(__file__).resolve().parent
_SUITE_DIR = _HERE / "tests"
sys.path.insert(0, str(_HERE))
sys.path.insert(1, str(_SUITE_DIR))
# The shared runner that kills a child's whole tree at its bound: a folder beside this one,
# named rather than searched for, so a same-named folder above the clone is never imported in
# its place, and put just after this folder and its suite, ahead of every installed
# distribution, so none of the same name can shadow it.
sys.path.insert(2, str(_HERE.parent / "bounded_run"))

import bounded_run  # noqa: E402 - a folder beside this one, via the insert above
import csp_watch  # noqa: E402 - this folder, via the insert above

#: The listener's own text, read here rather than taken from the module under test.
_LISTENER_SOURCE = (_HERE / "src" / "csp_listener.js").read_text(encoding="utf-8")

#: A payload exactly as the listener sends one.
_WELL_FORMED = {
    "document": "/404.html?x=1",
    "directive": "style-src-attr",
    "blocked": "inline",
    "sample": "",
}

#: What a binding's callback is handed first: the page's context, page and frame.
_SOURCE = {"context": None, "page": None, "frame": None}

#: Playwright's own classes, handed to the collector as they are. mypy checks each of these
#: assignments, so a member a protocol asks for that Playwright's class lacks, or types another
#: way, fails the type check here rather than a live run.
_TAKES_A_REAL_CONTEXT: Final[Callable[[BrowserContext], csp_watch.CspLog]] = (
    csp_watch.install
)
_TAKES_A_REAL_PAGE: Final[
    tuple[Callable[[Page], str | None], Callable[[Page], None]]
] = (csp_watch.policy_of, csp_watch.plant)

#: A fresh interpreter's program: import the collector from the folder named, then name the
#: top-level package of every module that import loaded, one per line.
_LIST_IMPORTS: Final = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import csp_watch\n"
    "print('\\n'.join(sorted({name.partition('.')[0] for name in sys.modules})))\n"
)

#: Bound on that interpreter, which imports the standard library and one module.
_IMPORT_TIMEOUT_SECONDS: Final = 60.0

# What each grouped file must yield, floored SEPARATELY: an empty or half-failed discovery is
# otherwise a PASSING run. Restated, not derived: a floor computed from discovery would compare
# discovery against itself and pass for an empty suite.
_MIN_TESTS_BY_FILE: dict[str, int] = {
    "test_csp_watch_live.py": 15,
    "test_csp_watch_live_setup.py": 20,
    "test_csp_watch_bounds.py": 6,
}

# This module's OWN tests, counted exactly and apart from the floors, so no amount of grouped
# growth pays for a deleted one.
_OWN_TESTS = 32


class _FakeContext:
    """Stands in for a BrowserContext: records `expose_binding` and `add_init_script`, in order."""

    def __init__(self) -> None:
        """No calls yet."""
        self.calls: list[tuple[str, object]] = []
        self.callback: Callable[[object, object], None] | None = None

    def expose_binding(
        self, name: str, callback: Callable[[object, object], None]
    ) -> None:
        """Record the binding's name, and keep its callback for a test to call as a page would."""
        self.calls.append(("expose_binding", name))
        self.callback = callback

    def add_init_script(self, script: str) -> None:
        """Record the init script's text."""
        self.calls.append(("add_init_script", script))


class _FakePage:
    """Stands in for a Page: records each `evaluate` and answers every one with `answer`."""

    def __init__(self, answer: object = None) -> None:
        """No calls yet; every evaluation will answer `answer`."""
        self.answer = answer
        self.calls: list[tuple[str, object]] = []

    def evaluate(self, expression: str, arg: object = None) -> object:
        """Record the expression and its argument; answer as configured."""
        self.calls.append((expression, arg))
        return self.answer


def _installed() -> tuple[_FakeContext, csp_watch.CspLog]:
    """A stand-in context with the collector installed in it, and the log it returned."""
    context = _FakeContext()
    return context, csp_watch.install(context)


def _violation(
    sample: str = "", *, directive: str = "style-src-attr"
) -> dict[str, str]:
    """A well-formed payload with the given sample and directive."""
    return {**_WELL_FORMED, "directive": directive, "sample": sample}


class InstallTest(unittest.TestCase):
    """`install` exposes the binding, then adds the listener as an init script."""

    def test_the_binding_is_exposed_before_the_init_script_is_added(self) -> None:
        context, _ = _installed()
        self.assertEqual(
            [name for name, _ in context.calls], ["expose_binding", "add_init_script"]
        )
        self.assertEqual(context.calls[0], ("expose_binding", csp_watch.BINDING))

    def test_the_init_script_is_the_listener_then_the_call_naming_the_binding(
        self,
    ) -> None:
        context, _ = _installed()
        script = cast(str, context.calls[1][1])
        self.assertTrue(script.startswith(_LISTENER_SOURCE.rstrip()))
        self.assertTrue(script.rstrip().endswith('cspWatch(window, "__cspWatch");'))

    def test_the_binding_name_is_the_one_the_init_script_calls(self) -> None:
        context, _ = _installed()
        script = cast(str, context.calls[1][1])
        self.assertIn(f"cspWatch(window, {json.dumps(csp_watch.BINDING)});", script)

    def test_what_the_page_sends_lands_in_the_returned_log(self) -> None:
        context, log = _installed()
        assert context.callback is not None
        context.callback(_SOURCE, _WELL_FORMED)
        self.assertEqual(
            log.violations,
            (csp_watch.Violation("/404.html?x=1", "style-src-attr", "inline", ""),),
        )


class RecordTest(unittest.TestCase):
    """`record` keeps a well-formed payload and refuses any other as an instrument fault."""

    def test_a_well_formed_payload_becomes_a_violation(self) -> None:
        log = csp_watch.CspLog()
        log.record(_SOURCE, _WELL_FORMED)
        self.assertEqual(
            log.violations,
            (
                csp_watch.Violation(
                    document="/404.html?x=1",
                    directive="style-src-attr",
                    blocked="inline",
                    sample="",
                ),
            ),
        )

    def test_violations_are_kept_in_arrival_order(self) -> None:
        log = csp_watch.CspLog()
        log.record(_SOURCE, _violation(directive="style-src-attr"))
        log.record(_SOURCE, _violation(directive="img-src"))
        self.assertEqual(
            [violation.directive for violation in log.violations],
            ["style-src-attr", "img-src"],
        )

    def test_a_payload_missing_a_key_is_refused(self) -> None:
        # Every key-shape fault names all four keys first, so the match is on what it lacks.
        payload = {key: value for key, value in _WELL_FORMED.items() if key != "sample"}
        with self.assertRaisesRegex(
            csp_watch.CspWatchError, r"lacks \['sample'\] and adds \[\]"
        ):
            csp_watch.CspLog().record(_SOURCE, payload)

    def test_a_payload_with_a_non_string_value_is_refused(self) -> None:
        with self.assertRaisesRegex(csp_watch.CspWatchError, "blocked"):
            csp_watch.CspLog().record(_SOURCE, {**_WELL_FORMED, "blocked": None})

    def test_a_payload_with_an_extra_key_is_refused(self) -> None:
        with self.assertRaisesRegex(csp_watch.CspWatchError, "disposition"):
            csp_watch.CspLog().record(
                _SOURCE, {**_WELL_FORMED, "disposition": "enforce"}
            )

    def test_a_payload_that_is_not_an_object_is_refused(self) -> None:
        with self.assertRaisesRegex(csp_watch.CspWatchError, "list"):
            csp_watch.CspLog().record(_SOURCE, list(_WELL_FORMED.values()))

    def test_a_refused_payload_is_raised_again_by_every_read_of_the_log(self) -> None:
        # Playwright hands a callback's exception back to the PAGE, as the binding call's
        # rejection, so the raise in `record` never reaches the measuring code: the log keeps it.
        log = csp_watch.CspLog()
        log.record(_SOURCE, _WELL_FORMED)
        with self.assertRaises(csp_watch.CspWatchError):
            log.record(_SOURCE, {})
        for read in (lambda: log.violations, log.mark):
            with self.assertRaisesRegex(
                csp_watch.CspWatchError,
                r"lacks \['document', 'directive', 'blocked', 'sample'\] and adds \[\]",
            ):
                read()

    def test_the_first_fault_is_the_one_kept(self) -> None:
        log = csp_watch.CspLog()
        payloads: tuple[object, ...] = ({**_WELL_FORMED, "sample": 1}, [], {})
        for payload in payloads:
            with self.assertRaises(csp_watch.CspWatchError):
                log.record(_SOURCE, payload)
        # Only the first payload's fault is about a value that is not text.
        with self.assertRaisesRegex(csp_watch.CspWatchError, "not text"):
            log.mark()

    def test_a_later_well_formed_payload_does_not_clear_a_fault(self) -> None:
        log = csp_watch.CspLog()
        with self.assertRaises(csp_watch.CspWatchError):
            log.record(_SOURCE, {})
        log.record(_SOURCE, _WELL_FORMED)
        with self.assertRaises(csp_watch.CspWatchError):
            log.mark()

    @given(
        st.fixed_dictionaries({key: st.text() for key in _WELL_FORMED})
        | st.recursive(
            st.none() | st.booleans() | st.integers() | st.floats() | st.text(),
            lambda inner: (
                st.lists(inner, max_size=3)
                | st.dictionaries(
                    st.sampled_from([*_WELL_FORMED, "extra"]) | st.text(),
                    inner,
                    max_size=5,
                )
            ),
            max_leaves=12,
        )
    )
    def test_fuzz_violation_from(self, payload: object) -> None:
        # Whatever a page hands the binding (its own script can call it too), the answer is a
        # Violation of four strings or the instrument fault, never another exception.
        try:
            violation = csp_watch.violation_from(payload)
        except csp_watch.CspWatchError:
            return
        self.assertEqual(payload, vars(violation))
        self.assertLessEqual(len(violation.sample), csp_watch.SAMPLE_LIMIT)


class AdmitTest(unittest.TestCase):
    """With `admit`, a log keeps only the reports whose caller it admits."""

    def setUp(self) -> None:
        self.ours, self.theirs = object(), object()
        self.log = csp_watch.CspLog(admit=lambda source: source is self.ours)

    def test_a_report_from_a_caller_not_admitted_is_not_kept(self) -> None:
        self.log.record(self.theirs, _WELL_FORMED)
        self.log.record(self.ours, _violation(directive="img-src"))
        self.assertEqual(
            [violation.directive for violation in self.log.violations], ["img-src"]
        )

    def test_a_payload_from_a_caller_not_admitted_is_never_a_fault(self) -> None:
        # Not the measured page's report, so not the instrument's either: never even read.
        self.log.record(self.theirs, {})
        self.assertEqual(self.log.mark(), 0)

    def test_install_hands_its_admit_to_the_log(self) -> None:
        context = _FakeContext()
        log = csp_watch.install(context, admit=lambda source: source is self.ours)
        assert context.callback is not None
        context.callback(self.theirs, _WELL_FORMED)
        context.callback(self.ours, _WELL_FORMED)
        self.assertEqual(log.mark(), 1)


class DescribeTest(unittest.TestCase):
    """`describe` names the directive, what it blocked and where, and the sample when one came."""

    def test_without_a_sample(self) -> None:
        violation = csp_watch.Violation("/404.html", "style-src-attr", "inline", "")
        self.assertEqual(
            violation.describe(), "style-src-attr blocked inline on /404.html"
        )

    def test_with_a_sample(self) -> None:
        violation = csp_watch.Violation(
            "/", "style-src-attr", "inline", csp_watch.PLANT_SAMPLE
        )
        self.assertEqual(
            violation.describe(),
            f"style-src-attr blocked inline on /: {csp_watch.PLANT_SAMPLE}",
        )


class MarkAndPlantCaughtTest(unittest.TestCase):
    """`mark` splits the log at a moment; `plant_caught` reads only what came after it."""

    def test_mark_counts_the_violations_so_far(self) -> None:
        log = csp_watch.CspLog()
        self.assertEqual(log.mark(), 0)
        log.record(_SOURCE, _WELL_FORMED)
        log.record(_SOURCE, _WELL_FORMED)
        self.assertEqual(log.mark(), 2)

    def test_the_plant_after_the_mark_is_caught(self) -> None:
        log = csp_watch.CspLog()
        log.record(_SOURCE, _WELL_FORMED)
        since = log.mark()
        log.record(_SOURCE, _violation(csp_watch.PLANT_SAMPLE))
        self.assertTrue(csp_watch.plant_caught(log, since))

    def test_a_plant_before_the_mark_is_not_caught_again(self) -> None:
        log = csp_watch.CspLog()
        log.record(_SOURCE, _violation(csp_watch.PLANT_SAMPLE))
        since = log.mark()
        log.record(_SOURCE, _WELL_FORMED)
        self.assertFalse(csp_watch.plant_caught(log, since))

    def test_a_violation_after_the_mark_without_the_plant_sample_is_not_the_plant(
        self,
    ) -> None:
        log = csp_watch.CspLog()
        since = log.mark()
        log.record(_SOURCE, _violation("color: red"))
        self.assertFalse(csp_watch.plant_caught(log, since))

    def test_a_sample_holding_the_plant_sample_among_other_text_is_the_plant(
        self,
    ) -> None:
        log = csp_watch.CspLog()
        log.record(_SOURCE, _violation(f"color: red; {csp_watch.PLANT_SAMPLE}"))
        self.assertTrue(csp_watch.plant_caught(log, 0))


class PolicyOfTest(unittest.TestCase):
    """`policy_of` answers what the page's evaluation returns: a policy's text, or None."""

    def test_the_evaluated_policy_text_is_returned(self) -> None:
        page = _FakePage("default-src 'self'")
        self.assertEqual(csp_watch.policy_of(page), "default-src 'self'")
        self.assertEqual(len(page.calls), 1)

    def test_no_policy_meta_is_none(self) -> None:
        self.assertIsNone(csp_watch.policy_of(_FakePage(None)))

    def test_an_answer_that_is_neither_is_an_instrument_fault(self) -> None:
        with self.assertRaisesRegex(csp_watch.CspWatchError, "int"):
            csp_watch.policy_of(_FakePage(3))


class PlantTest(unittest.TestCase):
    """`plant` asks the page, in one evaluation, for the planted policy and the planted style."""

    def test_one_evaluation_carries_the_policy_and_the_sample(self) -> None:
        page = _FakePage()
        csp_watch.plant(page)
        self.assertEqual(len(page.calls), 1)
        _, arg = page.calls[0]
        self.assertEqual(arg, [csp_watch.PLANT_POLICY, csp_watch.PLANT_SAMPLE])

    def test_the_planted_policy_blocks_style_attributes_and_reports_their_text(
        self,
    ) -> None:
        # The keyword sits in style-src: WebKit reports no sample from style-src-attr's. The
        # live suite's plant case is what shows each engine then reports the sample.
        self.assertEqual(csp_watch.PLANT_POLICY, "style-src 'self' 'report-sample'")
        self.assertEqual(csp_watch.PLANT_SAMPLE, "--csp-watch-plant: 1")


class ImportTest(unittest.TestCase):
    """The collector brings no Playwright of its own: its caller hands it a context or a page."""

    def test_importing_it_imports_no_playwright(self) -> None:
        # A fresh, isolated interpreter: in this one the live suite has imported Playwright, and
        # neither the environment nor the working folder may add a folder of its own.
        answer = bounded_run.run_bounded(
            [sys.executable, "-I", "-c", _LIST_IMPORTS, str(_HERE)],
            timeout=_IMPORT_TIMEOUT_SECONDS,
            env={},
        )
        self.assertEqual(answer.returncode, 0, answer.stderr)
        imported = set(answer.stdout.split())
        self.assertIn(csp_watch.__name__, imported)
        self.assertNotIn("playwright", imported)


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


class GroupedSuiteDiscoveryTest(unittest.TestCase):
    """Fail loudly when the grouped suite is lost, or when a test in here is."""

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
        self.assertEqual(own_found, _OWN_TESTS)
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
    """unittest's own hook: fold the grouped suite in beside this file's own tests."""
    standard_tests.addTests(_grouped_suite())
    return standard_tests


if __name__ == "__main__":
    unittest.main()
