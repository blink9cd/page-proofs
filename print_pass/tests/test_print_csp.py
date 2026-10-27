"""Suite: the shared CSP violation collector on every state's context, each violation a finding.

The engine cases run the engine over one target against the fake Playwright. Its context keeps
every binding exposed to it and, as the page loads, hands each scripted payload to them as the
collector's in-page listener would. The collector itself is the real shared one, `csp_watch`,
installed through the fake context's two calls. The run cases go one step further out: `main`
makes a run over the real engine and that fake Playwright, with the PDF readers replaced, so
the exit code a violation gives is the CLI's own. The import cases run fresh interpreters: the
collector loads with the engine and brings no measuring package with it, and without it the
CLI stops before it writes anything. How the judge weighs a violation is
`test_print_judge.py`'s, and how the report scrubs one `test_print_report.py`'s.
"""

from __future__ import annotations

import contextlib
import functools
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Final, cast
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# ...and the tool's own folder, where the CLI that `run_main` runs is found.
sys.path.insert(0, str(_TOOL_DIR))
# The shared library: a folder beside this tool's own, named rather than searched for, so a
# same-named folder above the clone is never imported in its place, and put near the front
# of the path, ahead of every installed distribution, so none of the same name can shadow
# it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "csp_watch"))

import csp_watch  # noqa: E402 - the shared collector, via the insert above
import print_engine  # noqa: E402 - the tool's private src/, via the insert above
import print_preflight  # noqa: E402 - as above
from _print_engine_fixtures import (  # noqa: E402 - beside this module
    TIMEOUT_S,
    FakeRun,
    StateTestCase,
    state,
)
from _print_pass_fixtures import (  # noqa: E402 - as above
    MEASURE_PACKAGES,
    RunCase,
    packages_imported,
    run_main,
    run_without,
)
from _print_playwright_fixtures import (  # noqa: E402 - as above
    FakeContext,
    FakePlaywright,
    FakeScript,
    call,
)
from _print_report_fixtures import DPI, fake_readers  # noqa: E402 - as above
from print_playwright import LOOPBACK_HOST  # noqa: E402 - the tool's private src/
from print_targets import Action  # noqa: E402 - as above

#: The default state, as each watchdog bound in it is named after its call.
_WHERE: Final = "home/default"

#: The two calls that install the collector on a context, in the order they are made.
_INSTALL_CALLS: Final = ("context.expose_binding", "context.add_init_script")

#: A style attribute a page's policy refused, as the collector describes it.
_REFUSED: Final = "style-src-attr blocked inline on /"

#: What a payload the listener never sends is recorded as, ahead of its count and reason.
_REFUSAL: Final = "a page sent the collector what its listener never sends"


def _payload(
    *,
    directive: str = "style-src-attr",
    blocked: str = "inline",
    sample: str = "",
    document: str = "/",
) -> dict[str, object]:
    """One violation exactly as the collector's in-page listener sends it."""
    return {
        "document": document,
        "directive": directive,
        "blocked": blocked,
        "sample": sample,
    }


def _finding(what: str) -> print_engine.Problem:
    return print_engine.Problem("finding", what)


def _contexts(run: FakeRun) -> list[FakeContext]:
    """Every context opened, in order, across every browser."""
    return [
        context
        for browser in run.playwright.chromium.browsers
        for context in browser.contexts
    ]


class InstallTest(StateTestCase):
    """The collector goes on each state's context before its page opens."""

    def test_the_collector_is_installed_before_the_page_opens(self) -> None:
        run, _ = self.run_one()
        exposed = run.calls.index(call("context.expose_binding", csp_watch.BINDING))
        added = run.calls.index(call("context.add_init_script"))
        opened = run.calls.index(call("context.new_page"))
        self.assertLess(exposed, added)
        self.assertLess(added, opened)

    def test_each_install_call_is_bounded_under_its_own_name(self) -> None:
        run, _ = self.run_one()
        for name in _INSTALL_CALLS:
            with self.subTest(call=name):
                (entry,) = run.named(name)
                what = f"{name} {_WHERE}"
                self.assertEqual(
                    run.around(entry),
                    (call("bound", what, TIMEOUT_S), call("unbound", what)),
                )
        self.assertEqual(run.unbounded(), [])

    def test_every_state_gets_a_collector_of_its_own_in_any_browser(self) -> None:
        # The second state prints in installed Chrome, a browser of its own: the collector
        # is installed per context, so every engine a state prints in has it.
        run, _ = self.run_states(
            state("first"), state("second", color_scheme="native-dark")
        )
        contexts = _contexts(run)
        self.assertEqual(len(contexts), 2)
        for context in contexts:
            self.assertEqual(list(context.bindings), [csp_watch.BINDING])
            self.assertEqual(len(context.init_scripts), 1)


class ViolationTest(StateTestCase):
    """Each violation the collector hears is recorded with the network, and is a finding."""

    def test_a_violation_is_recorded_and_is_a_finding(self) -> None:
        _, record = self.run_one(script=FakeScript(csp_payloads=[_payload()]))
        self.assertEqual(record.network.csp_violations, (_REFUSED,))
        self.assertEqual(record.problems, (_finding(f"csp violation: {_REFUSED}"),))

    def test_a_sample_the_policy_asks_for_ends_the_line(self) -> None:
        script = FakeScript(csp_payloads=[_payload(sample="color: red")])
        _, record = self.run_one(script=script)
        self.assertEqual(
            record.problems, (_finding(f"csp violation: {_REFUSED}: color: red"),)
        )

    def test_each_violation_is_kept_in_the_order_it_arrived(self) -> None:
        image = _payload(directive="img-src", blocked="https://example.net/x.png")
        script = FakeScript(csp_payloads=[_payload(), image, _payload()])
        _, record = self.run_one(script=script)
        lines = (_REFUSED, "img-src blocked https://example.net/x.png on /", _REFUSED)
        self.assertEqual(record.network.csp_violations, lines)
        self.assertEqual(
            record.problems,
            tuple(_finding(f"csp violation: {line}") for line in lines),
        )

    def test_a_refused_url_on_loopback_is_named_by_its_path_and_query(self) -> None:
        # As every other network list names one, so two runs of one site, served on two
        # ports, record the same line.
        refused = f"http://{LOOPBACK_HOST}:8123/pixel.png?v=2"
        script = FakeScript(
            csp_payloads=[_payload(directive="img-src", blocked=refused)]
        )
        _, record = self.run_one(script=script)
        self.assertEqual(
            record.network.csp_violations, ("img-src blocked /pixel.png?v=2 on /",)
        )

    def test_a_page_reporting_none_has_none_and_no_finding(self) -> None:
        _, record = self.run_one()
        self.assertEqual(record.network.csp_violations, ())
        self.assertEqual(record.problems, ())

    def test_a_stopped_state_still_reports_its_violations(self) -> None:
        script = FakeScript(
            csp_payloads=[_payload()], failing_selectors=frozenset({"#start"})
        )
        clicks = state(actions=(Action("click", "#start", None),))
        _, record = self.run_one(clicks, script)
        self.assertIsNone(record.probe)
        self.assertEqual(
            record.problems,
            (
                _finding('action did not complete: click "#start"'),
                _finding(f"csp violation: {_REFUSED}"),
            ),
        )


class RefusalTest(StateTestCase):
    """A payload the listener never sends is the page's own doing: a finding on its state.

    The binding is a global any script in the page can call, so a page sending it nonsense
    must not end the run: it is counted, its first refusal kept, and the state goes on.
    """

    def test_refused_payloads_are_one_finding_on_the_state(self) -> None:
        payloads = [_payload(), {"x": 1}, "not a violation", _payload()]
        _, record = self.run_one(script=FakeScript(csp_payloads=payloads))
        first = (
            "a violation from the listener has exactly the keys "
            "['document', 'directive', 'blocked', 'sample']: this one lacks "
            "['document', 'directive', 'blocked', 'sample'] and adds [\"'x'\"]"
        )
        refused = f"{_REFUSAL} (2), the first: {first}"
        self.assertEqual(record.network.csp_violations, (_REFUSED, _REFUSED, refused))
        self.assertIn(_finding(f"csp violation: {refused}"), record.problems)

    def test_a_refusal_is_never_handed_back_to_the_page(self) -> None:
        run = FakeRun(self.pdf_dir, FakeScript(csp_payloads=[{"x": 1}]))
        run.run()
        (context,) = _contexts(run)
        self.assertEqual(context.binding_errors, [])
        self.assertEqual(run.named("context.close"), [call("context.close")])

    def test_a_long_refusal_is_cut_to_its_bound(self) -> None:
        payload = {"k" * 5000: 1}
        _, record = self.run_one(script=FakeScript(csp_payloads=[payload]))
        (line,) = record.network.csp_violations
        self.assertTrue(line.startswith(f"{_REFUSAL} (1), the first: "), line)
        self.assertTrue(line.endswith("..."), line)
        self.assertLessEqual(len(line), len(_REFUSAL) + 240)


@contextlib.contextmanager
def _driver(script: FakeScript) -> Iterator[FakePlaywright]:
    """`sync_playwright()` as a make run enters it: the fake Playwright doing `script`."""
    yield FakePlaywright(script)


class RunTest(RunCase):
    """A make run over the real engine: a violation is exit 1, and so is a refused payload."""

    def make(self, script: FakeScript) -> tuple[object, str, str]:
        """Run --mode make on the fake Playwright doing `script`; its code and streams."""
        entry = functools.partial(_driver, script)
        with (
            mock.patch.object(
                print_preflight, "import_sync_playwright", return_value=entry
            ),
            fake_readers(),
        ):
            outcome = run_main(
                *("--mode", "make", "--out", str(self.out), "--serve", str(self.site)),
                *("--browsers", str(self.builds), "--dpi", str(DPI)),
            )
        return outcome.code, outcome.stdout, outcome.stderr

    def only_state(self) -> dict[str, object]:
        """The report's one state."""
        (only,) = cast(list[dict[str, object]], self.report()["states"])
        return only

    def test_a_violation_is_a_finding_and_exit_one(self) -> None:
        code, stdout, stderr = self.make(FakeScript(csp_payloads=[_payload()]))
        self.assertEqual(code, 1, stderr)
        listed = self.only_state()
        self.assertEqual(
            cast(dict[str, object], listed["network"])["csp_violations"], [_REFUSED]
        )
        self.assertEqual(
            listed["problems"],
            [{"severity": "finding", "what": f"csp violation: {_REFUSED}"}],
        )
        self.assertIn(f"FINDING page-1/default: csp violation: {_REFUSED}", stdout)

    def test_none_is_an_empty_list_and_exit_zero(self) -> None:
        code, _, stderr = self.make(FakeScript())
        self.assertEqual(code, 0, stderr)
        listed = self.only_state()
        self.assertEqual(
            cast(dict[str, object], listed["network"])["csp_violations"], []
        )
        self.assertEqual(listed["problems"], [])

    def test_a_refused_payload_is_exit_one_with_the_report_written(self) -> None:
        code, stdout, stderr = self.make(FakeScript(csp_payloads=["not a violation"]))
        self.assertEqual(code, 1, stderr)
        self.assertTrue((self.out / "report.json").is_file())
        refused = f"{_REFUSAL} (1), the first: the listener sent str, not a violation"
        self.assertIn(f"FINDING page-1/default: csp violation: {refused}", stdout)
        self.assertNotIn("Traceback", stderr)


class ImportTest(RunCase):
    """The collector loads with the engine, bringing no measuring package; without it, no run."""

    def test_the_engine_imports_the_collector_and_no_measuring_package(self) -> None:
        imported = packages_imported("print_engine", _TOOL_DIR / "src")
        self.assertIn(csp_watch.__name__, imported)
        self.assertEqual(imported & MEASURE_PACKAGES, set())

    def test_without_the_collector_the_cli_stops_before_writing_anything(self) -> None:
        # A fresh interpreter that cannot import it, as a tree without it would be: the CLI's
        # own import fails, before an option is read.
        answer = run_without(
            csp_watch.__name__,
            *("--mode", "make", "--out", str(self.out), "--serve", str(self.site)),
            env={},
            cwd=self.root,
        )
        self.assertNotEqual(answer.returncode, 0, answer.stderr)
        self.assertRegex(
            answer.stderr, rf"ModuleNotFoundError: .*\b{csp_watch.__name__}\b"
        )
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
