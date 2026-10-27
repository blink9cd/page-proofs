"""Grouped suite: print_modes' make run end to end -- the site served, printed, read, to a report.

Each case runs `main` past its checks into a run. Nothing here launches a browser or runs
poppler. `run_targets` is replaced by a fake that prints nothing and returns one record per
state it is handed, and Playwright's entry point by a fake context manager. The readers under
`read_pdf` and `compare_pdfs` are `_print_report_fixtures.fake_readers`, so both run for real
over scripted readings, and `report.json` is the real report of those. The site IS served, on
loopback, so a case can show the engine was handed the tree `--serve` named. The read and
compare runs are the sibling `test_print_modes_read.py`'s.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import sys
import unittest
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from fractions import Fraction
from pathlib import Path
from types import TracebackType
from typing import Final
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# ...and the tool's own folder, where the CLI that `run_main` runs is found.
sys.path.insert(0, str(_TOOL_DIR))
import pdf_privacy  # noqa: E402 - the tool's private src/, via the insert above
import print_engine  # noqa: E402 - as above
import print_modes  # noqa: E402 - as above
import print_preflight  # noqa: E402 - as above
import print_report  # noqa: E402 - as above
from _print_pass_fixtures import (  # noqa: E402 - this folder's shared fixtures
    IDENTITY,
    PAGE,
    Outcome,
    RunCase,
    run_main,
)
from _print_playwright_fixtures import (  # noqa: E402 - as above
    CallLog,
    FakeBounder,
)
from _print_playwright_fixtures import (  # noqa: E402 - as above
    call as log_entry,  # `call` is the name the cases give a recorded engine call
)
from _print_report_fixtures import (  # noqa: E402 - as above
    DPI,
    fake_readers,
    gray_raster,
    state_record,
)
from pdf_raster import RasterError, ink_extent  # noqa: E402 - the tool's private src/
from print_engine import (  # noqa: E402 - as above
    MARGIN_INCHES,
    EngineError,
    EngineSettings,
    MadePdf,
    Problem,
    StateRecord,
    effective_settings,
    pdf_name,
)
from print_judge import running_animations_warning, warning  # noqa: E402 - src/
from print_targets import TargetsFile, Viewport, adhoc_targets, load_targets  # noqa: E402
from print_watchdog import Watchdog  # noqa: E402 - the tool's private src/

#: Stands in for Playwright's root object; the fake engine never touches it.
_PLAYWRIGHT: Final = object()

#: What an engine failure names: an executable's path, which the report must never hold.
_ENGINE_FAILURE: Final = "could not launch the shell engine: no executable at <marker>"

#: The --timeout a case watching the driver's bounds runs under, as the command line has it.
_TIMEOUT: Final = "12.5"


@contextlib.contextmanager
def _fake_sync_playwright() -> Iterator[object]:
    """`sync_playwright()` as the fake engine needs it: a context yielding the root object."""
    yield _PLAYWRIGHT


class _DeadPlaywright:
    """`sync_playwright()` whose driver never starts: entering it raises."""

    def __enter__(self) -> object:
        """Fail as a driver that went away does."""
        raise RuntimeError("the driver went away")

    def __exit__(self, *exc_info: object) -> None:
        """Never reached: entering raised."""


class _LoggedDriver:
    """`sync_playwright()` whose start and stop are logged beside the watchdog's bounds."""

    def __init__(self, calls: CallLog) -> None:
        """Write into `calls`, the watchdog stand-in's own log."""
        self._calls = calls

    def __enter__(self) -> object:
        """Log the start; hand over the root object the fake engine never touches."""
        self._calls.append(log_entry("playwright.start"))
        return _PLAYWRIGHT

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Log the stop, naming the type of the error the run ends with (NoneType: none)."""
        self._calls.append(log_entry("playwright.stop", type(exc_value).__name__))


def _driven(ended_by: str) -> list[tuple[str, ...]]:
    """A make run's log: the driver's start and stop each in its own bound, the engine between."""
    return [
        log_entry("bound", "playwright.start", _TIMEOUT),
        log_entry("playwright.start"),
        log_entry("unbound", "playwright.start"),
        log_entry("run_targets"),
        log_entry("bound", "playwright.stop", _TIMEOUT),
        log_entry("playwright.stop", ended_by),
        log_entry("unbound", "playwright.stop"),
    ]


class _FakeEngine:
    """Stands in for `run_targets`: one record per state handed over, nothing printed."""

    def __init__(
        self,
        *,
        problems: tuple[Problem, ...] = (),
        sides: tuple[bool, ...] = (False, True),
        margin_inches: Fraction = MARGIN_INCHES,
    ) -> None:
        """Print each state with backgrounds as `sides` says, each holding `problems`.

        Each PDF records `margin_inches` as the margin it was printed with.
        """
        self.problems = problems
        self.sides = sides
        self.margin_inches = margin_inches
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        playwright: object,
        base_url: str,
        targets: TargetsFile,
        settings: EngineSettings,
        *,
        bounder: Watchdog,
    ) -> tuple[StateRecord, ...]:
        """Record the call, what the served url answers and the engine builds' variable."""
        with urllib.request.urlopen(f"{base_url}/index.html", timeout=10) as response:
            served = response.read().decode("utf-8")
        self.calls.append(
            {
                "playwright": playwright,
                "base_url": base_url,
                "targets": targets,
                "settings": settings,
                "bounder": bounder,
                "served": served,
                "browsers": os.environ.get(print_preflight.BROWSERS_ENV),
                "pdf_dir_made": settings.pdf_dir.is_dir(),
            }
        )
        return tuple(
            self.record(settings, target.name, state.name)
            for target in targets.targets
            for state in target.states
        )

    def record(self, settings: EngineSettings, target: str, state: str) -> StateRecord:
        """The fixed record, as this state, its PDFs named as the engine names them."""
        pdfs = tuple(
            MadePdf(
                side,
                settings.pdf_dir / pdf_name(target, state, side),
                self.margin_inches,
            )
            for side in self.sides
        )
        return dataclasses.replace(
            state_record(settings.pdf_dir.parent),
            target=target,
            state=state,
            pdfs=pdfs,
            problems=self.problems,
        )


class _MakeCase(RunCase):
    """A make run over a fake engine, fake readers and a fake Playwright."""

    def make(
        self,
        engine: object,
        *argv: str,
        readers: Mapping[str, object] | None = None,
        playwright: object = _fake_sync_playwright,
    ) -> Outcome:
        """Run --mode make with `engine` as run_targets and `playwright` as its entry point."""
        with (
            mock.patch.object(print_engine, "run_targets", engine),
            mock.patch.object(
                print_preflight, "import_sync_playwright", return_value=playwright
            ),
            fake_readers(**(readers or {})),
        ):
            return run_main(
                *("--mode", "make", "--out", str(self.out), "--serve", str(self.site)),
                *("--dpi", str(DPI), *argv),
            )


class MakeTest(_MakeCase):
    """What a make run hands the engine, what it writes, and what it exits with."""

    def test_a_clean_run_writes_the_report_and_exits_zero(self) -> None:
        outcome = self.make(_FakeEngine(), "--browsers", str(self.builds))
        self.assertEqual(outcome.code, 0, outcome.stderr)
        report = self.report()
        self.assertEqual(report["mode"], "make")
        self.assertEqual(
            report["totals"], {"states": 1, "pdfs": 2, "findings": 0, "warnings": 0}
        )
        comparisons = report["comparisons"]
        assert isinstance(comparisons, list)
        pairs = [(c["a"], c["b"], c["identical"]) for c in comparisons]
        off, on = "pdfs/page-1--default--bg-off.pdf", "pdfs/page-1--default--bg-on.pdf"
        self.assertEqual(pairs, [(off, on, True)])
        self.assertIn(
            "totals: states 1, pdfs 2, findings 0, warnings 0", outcome.stdout
        )
        self.assertIn(f"report: {self.out / 'report.json'}", outcome.stdout)

    def test_a_state_printed_one_way_is_read_and_left_uncompared(self) -> None:
        outcome = self.make(_FakeEngine(sides=(False,)), "--browsers", str(self.builds))
        self.assertEqual(outcome.code, 0, outcome.stderr)
        report = self.report()
        self.assertEqual(report["comparisons"], [])
        pdfs = report["pdfs"]
        assert isinstance(pdfs, list)
        self.assertEqual(
            [pdf["file"] for pdf in pdfs], ["pdfs/page-1--default--bg-off.pdf"]
        )

    def test_a_finding_exits_one(self) -> None:
        engine = _FakeEngine(problems=(Problem("finding", "an image never loaded"),))
        outcome = self.make(engine, "--browsers", str(self.builds))
        self.assertEqual(outcome.code, 1, outcome.stderr)
        self.assertIn("FINDING page-1/default: an image never loaded", outcome.stdout)

    def test_page_chosen_text_reaches_the_summary_spelled_not_acted_on(self) -> None:
        # An animation's id is the page's own: a terminal must not act on its controls.
        warned = warning(running_animations_warning(["a\x1b[2K\rOK\nFINDING b\u2028"]))
        outcome = self.make(
            _FakeEngine(problems=(warned,)), "--browsers", str(self.builds)
        )
        self.assertIn(r"(a\x1b[2K\x0dOK\x0aFINDING b\u2028)", outcome.stdout)
        self.assertFalse({"\x1b", "\r", "\u2028"} & set(outcome.stdout))

    def test_the_engine_is_handed_the_served_site_and_every_option(self) -> None:
        engine = _FakeEngine()
        self.make(
            engine,
            *("--browsers", str(self.builds), "--engine", "chrome"),
            *("--page", "/", "--page", "/b/", "--backgrounds", "both"),
            *("--header-footer", "on", "--viewport", "800x600", "--timeout", "12.5"),
        )
        (call,) = engine.calls
        self.assertIs(call["playwright"], _PLAYWRIGHT)
        self.assertTrue(str(call["base_url"]).startswith("http://127.0.0.1:"))
        self.assertEqual(call["served"], PAGE)
        self.assertEqual(call["targets"], adhoc_targets(["/", "/b/"]))
        self.assertEqual(
            call["settings"],
            EngineSettings(
                "chrome", "both", True, Viewport(800, 600), 12.5, self.out / "pdfs"
            ),
        )
        self.assertIsInstance(call["bounder"], Watchdog)
        self.assertTrue(call["pdf_dir_made"])

    def test_a_targets_files_keys_win_over_the_options(self) -> None:
        targets = self.root / "targets.toml"
        targets.write_text(
            'viewport = { width = 390, height = 844 }\nbackgrounds = "both"\n'
            '[[target]]\nname = "home"\npage = "/"\n',
            encoding="utf-8",
            newline="\n",
        )
        engine = _FakeEngine()
        self.make(
            engine,
            *("--browsers", str(self.builds), "--targets", str(targets)),
            *("--viewport", "800x600", "--backgrounds", "off"),
        )
        (call,) = engine.calls
        handed = call["settings"]
        assert isinstance(handed, EngineSettings)
        loaded = load_targets(targets)
        self.assertEqual(call["targets"], loaded)
        # The options are handed on as given; the engine's own rule puts the file's first.
        self.assertEqual(
            (handed.viewport, handed.backgrounds), (Viewport(800, 600), "off")
        )
        effective = effective_settings(handed, loaded)
        self.assertEqual(
            (effective.viewport, effective.backgrounds), (Viewport(390, 844), "both")
        )

    def test_bad_targets_are_refused_before_anything_runs(self) -> None:
        targets = self.root / "targets.toml"
        targets.write_text(
            'bogus = 1\n[[target]]\nname = "home"\npage = "/"\n',
            encoding="utf-8",
            newline="\n",
        )
        for argv, says in (
            (("--targets", str(targets)), "print_pass: bogus: unknown key"),
            (("--page", "home"), "print_pass: page 'home': must be a server path"),
        ):
            with self.subTest(argv=argv):
                engine = _FakeEngine()
                outcome = self.make(engine, "--browsers", str(self.builds), *argv)
                self.assertEqual(outcome.code, 2)
                self.assertIn(says, outcome.stderr)
                self.assertEqual(engine.calls, [])
                self.assertFalse(self.out.exists())

    def test_one_identity_reads_every_pdf_and_shapes_the_report(self) -> None:
        with (
            mock.patch.object(
                pdf_privacy, "host_identity", return_value=IDENTITY
            ) as host,
            mock.patch.object(
                print_report, "read_pdf", wraps=print_report.read_pdf
            ) as reads,
            mock.patch.object(
                print_report, "make_report", wraps=print_report.make_report
            ) as shape,
        ):
            self.make(_FakeEngine(), "--browsers", str(self.builds))
        host.assert_called_once_with()
        calls = (*reads.call_args_list, *shape.call_args_list)
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertIs(call.kwargs["identity"], IDENTITY)

    def test_each_pdf_is_measured_against_the_margin_it_was_printed_with(self) -> None:
        # A margin no engine prints with, so only the record could have carried it here.
        margin = Fraction(1, 3)
        with mock.patch.object(
            print_report, "read_pdf", wraps=print_report.read_pdf
        ) as reads:
            outcome = self.make(
                _FakeEngine(margin_inches=margin), "--browsers", str(self.builds)
            )
        self.assertEqual(outcome.code, 0, outcome.stderr)
        margins = [call.kwargs["margin_inches"] for call in reads.call_args_list]
        self.assertEqual(margins, [margin, margin])
        blank = ink_extent(gray_raster(), DPI, margin_inches=margin).blank_below_share
        pdfs = self.report()["pdfs"]
        assert isinstance(pdfs, list)
        for pdf in pdfs:
            self.assertEqual(pdf["renders"]["blank_below_last_ink"], [blank])

    def test_an_unrenderable_pair_is_not_compared_and_its_pdfs_are_findings(
        self,
    ) -> None:
        failed = RasterError("pdftoppm exited 1: damaged")
        outcome = self.make(
            _FakeEngine(),
            *("--browsers", str(self.builds)),
            readers={"render_rasters": failed},
        )
        self.assertEqual(outcome.code, 1, outcome.stderr)
        self.assertEqual(self.report()["comparisons"], [])
        self.assertIn("gray render failed: pdftoppm exited 1: damaged", outcome.stdout)
        self.assertIn(
            "page-1/default: backgrounds off and on not compared", outcome.stderr
        )

    def test_an_engine_error_is_two_and_never_reaches_a_file(self) -> None:
        engine = mock.MagicMock(side_effect=EngineError(_ENGINE_FAILURE))
        outcome = self.make(engine, "--browsers", str(self.builds))
        self.assertEqual(outcome.code, 2)
        self.assertEqual(
            outcome.stderr.splitlines()[-1], f"print_pass: {_ENGINE_FAILURE}"
        )
        self.assertFalse((self.out / "report.json").exists())
        for path in self.out.rglob("*"):
            if path.is_file():
                self.assertNotIn(
                    "<marker>", path.read_text(encoding="utf-8", errors="replace")
                )

    def test_a_playwright_that_cannot_start_is_two_with_its_traceback(self) -> None:
        outcome = self.make(
            _FakeEngine(), "--browsers", str(self.builds), playwright=_DeadPlaywright
        )
        self.assertEqual(outcome.code, 2)
        self.assertIn("print_pass: the run could not finish:", outcome.stderr)
        self.assertIn("RuntimeError: the driver went away", outcome.stderr)

    def test_a_missing_playwright_is_refused_naming_the_extra(self) -> None:
        missing = ModuleNotFoundError("No module named 'playwright'", name="playwright")
        with mock.patch.object(
            print_preflight, "import_sync_playwright", side_effect=missing
        ):
            outcome = run_main(
                *("--mode", "make", "--out", str(self.out), "--serve", str(self.site)),
                *("--browsers", str(self.builds)),
            )
        self.assertEqual(outcome.code, 2)
        self.assertIn("Playwright is not installed", outcome.stderr)
        self.assertIn("`print` extra", outcome.stderr)
        self.assertFalse(self.out.exists())


class MakeBrowsersTest(_MakeCase):
    """The engine builds the run resolved are where Playwright is pointed, or left alone."""

    def test_playwright_is_pointed_at_the_resolved_builds(self) -> None:
        os.environ.pop(print_preflight.BROWSERS_ENV, None)
        engine = _FakeEngine()
        outcome = self.make(engine, "--browsers", str(self.builds))
        self.assertEqual(engine.calls[0]["browsers"], str(self.builds.absolute()))
        self.assertIn(f"engine builds: {self.builds}", outcome.stdout)

    def test_playwrights_hermetic_value_reaches_it_unchanged(self) -> None:
        # "0" is Playwright's own hermetic mode, builds inside its package; made absolute,
        # it would name a folder `0` under the cwd, which holds no build.
        # The run resolves it as browsers_dir(None, os.environ, ...) and hands it on.
        os.environ[print_preflight.BROWSERS_ENV] = print_preflight.HERMETIC_BROWSERS
        engine = _FakeEngine()
        self.make(engine)
        self.assertEqual(engine.calls[0]["browsers"], "0")
        self.assertEqual(os.environ[print_preflight.BROWSERS_ENV], "0")

    def test_playwrights_own_default_is_left_alone(self) -> None:
        os.environ.pop(print_preflight.BROWSERS_ENV, None)
        engine = _FakeEngine()
        outcome = self.make(engine)
        self.assertIsNone(engine.calls[0]["browsers"])
        self.assertIn("engine builds: Playwright's own default", outcome.stdout)


class MakeDriverTest(_MakeCase):
    """Playwright's driver starts and stops, each inside the watchdog: neither takes a timeout."""

    def driven(self, engine: Callable[..., object]) -> tuple[Outcome, CallLog]:
        """Run make under `_TIMEOUT` with `engine`, logging the bounds, driver and engine."""
        calls: CallLog = []

        def logged(*args: object, **kwargs: object) -> object:
            calls.append(log_entry("run_targets"))
            return engine(*args, **kwargs)

        with mock.patch.object(
            print_modes, "Watchdog", return_value=FakeBounder(calls)
        ):
            outcome = self.make(
                logged,
                *("--browsers", str(self.builds), "--timeout", _TIMEOUT),
                playwright=lambda: _LoggedDriver(calls),
            )
        return outcome, calls

    def test_the_start_and_the_stop_are_each_bounded_at_the_timeout(self) -> None:
        outcome, calls = self.driven(_FakeEngine())
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(calls, _driven("NoneType"))

    def test_the_stop_is_bounded_when_the_engine_fails(self) -> None:
        outcome, calls = self.driven(
            mock.MagicMock(side_effect=EngineError(_ENGINE_FAILURE))
        )
        self.assertEqual(outcome.code, 2)
        self.assertEqual(calls, _driven("EngineError"))


if __name__ == "__main__":
    unittest.main()
