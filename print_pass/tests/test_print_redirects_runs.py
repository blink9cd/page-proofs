"""Grouped suite: a make run over a meta-refresh target, through to its report and exit code.

Each case runs the CLI's make run over a real loopback server of a temp site whose `/archive/`
is the archive stub, a refresh at once to `2026/`. The engine is a fake that prints nothing
and returns one record per state it is handed, so a case sees which pages each engine was
given; the Firefox case fakes `gecko_engine.run_targets` the same way. A declared redirect is
printed at its destination and reported as followed; an undeclared one is a finding and its
states are never handed over; a declared one that cannot be followed is exit 2, no report.
The report's own shaping of the redirects, and the summary's line for one, close the suite.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/` and the tool's folder.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import sys
import unittest
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Final, cast
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# ...and the tool's own folder, where the CLI that `run_main` runs is found.
sys.path.insert(0, str(_TOOL_DIR))

import gecko_engine  # noqa: E402 - the tool's private src/, via the insert above
import gecko_launch  # noqa: E402 - as above
import print_engine  # noqa: E402 - as above
import print_modes  # noqa: E402 - as above
import print_preflight  # noqa: E402 - as above
import print_report_redirects  # noqa: E402 - as above
from _print_engine_fixtures import VIEWPORT  # noqa: E402 - beside this module
from _print_pass_fixtures import IDENTITY, Outcome, RunCase, run_main  # noqa: E402
from _print_report_fixtures import state_record  # noqa: E402 - as above
from print_plan import MakePlan  # noqa: E402 - the tool's private src/
from print_record import EngineSettings, StateRecord  # noqa: E402 - as above
from print_redirects import Redirect  # noqa: E402 - as above
from print_report_output import summary_lines  # noqa: E402 - as above
from print_targets import TargetsFile  # noqa: E402 - as above

#: The archive stub's shape: a refresh at once to the year page beside it.
_STUB: Final = '<!doctype html><meta http-equiv="refresh" content="0; url=2026/">\n'

#: The year page the stub leads to.
_YEAR: Final = "<!doctype html><title>2026</title><p>the year</p>\n"

#: The finding an undeclared stub draws, as the report and the summary write it.
_UNDECLARED: Final = (
    "/archive/ is a meta refresh to /archive/2026/, so its states were not printed: "
    "set follows_redirect = true on its target to print the page it leads to"
)

#: Two targets: the site's home, and `archive` at the stub, `archive_keys` layered onto it.
_TARGETS: Final = """[[target]]
name = "home"
page = "/"

[[target]]
name = "archive"
page = "/archive/"
{archive_keys}
[[target.state]]
name = "default"

[[target.state]]
name = "stored-dark"
storage = {{ mode = "dark" }}
"""


@contextlib.contextmanager
def _no_driver() -> Iterator[object]:
    """`sync_playwright()` as the fake engine needs it: a context yielding a root it ignores."""
    yield object()


class _PagesEngine:
    """Stands in for either engine's `run_targets`: records the pages, prints nothing."""

    def __init__(self) -> None:
        """Nothing handed over yet."""
        self.pages: list[tuple[str, str]] = []

    def __call__(self, *args: object, **kwargs: object) -> tuple[StateRecord, ...]:
        """One record per state of the targets handed over, as the engine names its page."""
        targets = next(arg for arg in args if isinstance(arg, TargetsFile))
        settings = next(arg for arg in args if isinstance(arg, EngineSettings))
        self.pages += [(target.name, target.page) for target in targets.targets]
        return tuple(
            dataclasses.replace(
                state_record(settings.pdf_dir.parent),
                target=target.name,
                state=state.name,
                page=target.page,
                pdfs=(),
                problems=(),
            )
            for target in targets.targets
            for state in target.states
        )


class _RedirectRunCase(RunCase):
    """A site whose `/archive/` is the stub, and a targets file naming it."""

    def setUp(self) -> None:
        """Write the stub and the year page beside the run case's own home page."""
        super().setUp()
        (self.site / "archive" / "2026").mkdir(parents=True)
        (self.site / "archive" / "index.html").write_text(
            _STUB, encoding="utf-8", newline="\n"
        )
        (self.site / "archive" / "2026" / "index.html").write_text(
            _YEAR, encoding="utf-8", newline="\n"
        )
        self.engine = _PagesEngine()

    def targets(self, archive_keys: str) -> Path:
        """The targets file, `archive_keys` added to the `archive` target."""
        path = self.root / "targets.toml"
        path.write_text(
            _TARGETS.format(archive_keys=archive_keys), encoding="utf-8", newline="\n"
        )
        return path

    def make(self, archive_keys: str) -> Outcome:
        """A make run over the site with the fake Playwright engine."""
        with (
            mock.patch.object(print_engine, "run_targets", self.engine),
            mock.patch.object(
                print_preflight, "import_sync_playwright", return_value=_no_driver
            ),
        ):
            return run_main(
                *("--mode", "make", "--out", str(self.out), "--serve", str(self.site)),
                *("--targets", str(self.targets(archive_keys))),
                *("--browsers", str(self.builds)),
            )


class DeclaredRunTest(_RedirectRunCase):
    """`follows_redirect = true`: printed at the destination, reported as followed."""

    def test_the_engine_prints_the_destination_and_the_report_names_both(self) -> None:
        outcome = self.make("follows_redirect = true\n")
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(
            self.engine.pages, [("home", "/"), ("archive", "/archive/2026/")]
        )
        report = self.report()
        self.assertEqual(report["schema"], 2)
        self.assertEqual(
            report["redirects"],
            [
                {
                    "target": "archive",
                    "page": "/archive/",
                    "destination": "/archive/2026/",
                    "followed": True,
                    "problems": [],
                }
            ],
        )
        states = cast(list[dict[str, object]], report["states"])
        self.assertEqual(
            [(s["target"], s["state"], s["page"]) for s in states],
            [
                ("home", "default", "/"),
                ("archive", "default", "/archive/2026/"),
                ("archive", "stored-dark", "/archive/2026/"),
            ],
        )

    def test_a_declared_redirect_that_cannot_be_followed_is_exit_two(self) -> None:
        (self.site / "archive" / "2026" / "index.html").unlink()
        outcome = self.make("follows_redirect = true\n")
        self.assertEqual(outcome.code, 2)
        self.assertIn("target[1].follows_redirect", outcome.stderr)
        self.assertIn("/archive/2026/ is not served", outcome.stderr)
        self.assertEqual(self.engine.pages, [])
        self.assertFalse(self.out.exists())


class UndeclaredRunTest(_RedirectRunCase):
    """No `follows_redirect`: a finding, the run's exit 1, and no state of it printed."""

    def test_the_redirect_is_a_finding_and_its_states_are_not_printed(self) -> None:
        outcome = self.make("")
        self.assertEqual(outcome.code, 1, outcome.stderr)
        self.assertEqual(self.engine.pages, [("home", "/")])
        report = self.report()
        self.assertEqual(
            report["redirects"],
            [
                {
                    "target": "archive",
                    "page": "/archive/",
                    "destination": "/archive/2026/",
                    "followed": False,
                    "problems": [{"severity": "finding", "what": _UNDECLARED}],
                }
            ],
        )
        totals = cast(dict[str, object], report["totals"])
        self.assertEqual((totals["states"], totals["findings"]), (1, 1))
        self.assertIn(f"FINDING archive: {_UNDECLARED}", outcome.stdout)
        self.assertNotIn("Execution context was destroyed", outcome.stdout)

    def test_a_run_whose_only_target_is_undeclared_prints_nothing(self) -> None:
        # Every target filtered out hands the engine an empty tuple: the run must still
        # report the finding and exit 1, not crash or pass on zero states.
        only_then = _TARGETS.split("[[target]]", 2)[2]
        with mock.patch.object(
            sys.modules[__name__], "_TARGETS", "[[target]]" + only_then
        ):
            outcome = self.make("")
        self.assertEqual(outcome.code, 1, outcome.stderr)
        self.assertEqual(self.engine.pages, [])
        totals = cast(dict[str, object], self.report()["totals"])
        self.assertEqual((totals["states"], totals["findings"]), (0, 1))
        self.assertIn(f"FINDING archive: {_UNDECLARED}", outcome.stdout)


class FirefoxRunTest(_RedirectRunCase):
    """The Firefox engine is handed the destination too."""

    def test_the_gecko_engine_prints_the_destination(self) -> None:
        settings = EngineSettings(
            engine="firefox",
            backgrounds="off",
            header_footer=False,
            viewport=VIEWPORT,
            timeout_s=5.0,
            pdf_dir=self.out / "pdfs",
        )
        plan = MakePlan(
            out=self.out,
            dpi=72,
            serve=self.site,
            targets=self.targets("follows_redirect = true\n"),
            pages=(),
            browsers=None,
            settings=settings,
        )
        with (
            mock.patch.object(gecko_engine, "run_targets", self.engine),
            mock.patch.object(gecko_launch, "find_geckodriver", return_value="gd"),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            code = print_modes.run_make(plan, platform="darwin")
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(
            self.engine.pages, [("home", "/"), ("archive", "/archive/2026/")]
        )


class RedirectReportTest(unittest.TestCase):
    """The report's `redirects`: each scrubbed, each undeclared one counted a finding."""

    _REPORT: Final[dict[str, object]] = {
        "mode": "make",
        "states": [],
        "pdfs": [],
        "totals": {"states": 0, "pdfs": 0, "findings": 2, "warnings": 0},
    }

    def test_an_undeclared_redirect_adds_a_finding_to_the_totals(self) -> None:
        redirects = (
            Redirect("archive", "/archive/", "/archive/2026/", followed=False),
            Redirect("old", "/old/", "/new/", followed=True),
        )
        report = print_report_redirects.with_redirects(
            self._REPORT, redirects, out_dir=Path("/out"), identity=IDENTITY
        )
        totals = cast(dict[str, object], report["totals"])
        self.assertEqual(totals["findings"], 3)
        unchanged = cast(dict[str, object], self._REPORT["totals"])
        self.assertEqual(unchanged["findings"], 2)
        self.assertEqual(summary_lines(report)[0], f"FINDING archive: {_UNDECLARED}")

    def test_a_destination_naming_a_hostname_is_scrubbed(self) -> None:
        redirects = (
            Redirect("archive", "/archive/", "http://nowhere/x", followed=False),
        )
        report = print_report_redirects.with_redirects(
            self._REPORT, redirects, out_dir=Path("/out"), identity=IDENTITY
        )
        (entry,) = cast(list[dict[str, object]], report["redirects"])
        self.assertNotIn("nowhere", str(entry))

    def test_no_redirect_leaves_the_totals_alone(self) -> None:
        report = print_report_redirects.with_redirects(
            self._REPORT, (), out_dir=Path("/out"), identity=IDENTITY
        )
        self.assertEqual(report["redirects"], [])
        self.assertEqual(report["totals"], self._REPORT["totals"])


if __name__ == "__main__":
    unittest.main()
