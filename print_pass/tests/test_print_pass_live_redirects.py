"""Grouped suite: the LIVE run over a meta-refresh stub -- the regression for its race.

The site's `/archive/` is the archive stub, a refresh at once to `2026/`, and the year page is
real. One run, on the pinned headless shell, names the stub twice: declared with
`follows_redirect = true`, whose state must print the year page with no finding; and
undeclared, which must be the one finding that names the key, never the probe racing the
navigation (`Execution context was destroyed`) that the stub drew before the tool read it.
Its cases share that one run, whose bound is spent from the aggregator's own ten-minute
budget (`_live_run_bounds()`, in `test_print_pass_live_sites.py`, counts it).

It skips, naming the install, where poppler or the headless shell Playwright pins is not
installed, or fails instead with `PAGE_PROOFS_REQUIRE_BROWSERS=1`.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import ClassVar, Final, cast

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
from _print_pass_live_fixtures import (
    EXIT_FINDINGS,
    REDIRECT_PAGES,
    REDIRECT_TARGETS,
    REDIRECT_TIMEOUT_SECONDS,
    REPORT_NAME,
    at,
    load_json,
    make_run_process,
    problems,
    redirect_run_bound_seconds,
    require_live_engine,
    served_pages,
)

from _print_pass_fixtures import Outcome  # this folder's fixtures, as above

#: What the probe raised when it raced the stub's navigation: never to be seen again.
_RACED: Final = "Execution context was destroyed"

#: The finding the undeclared stub draws.
_UNDECLARED: Final = (
    "/archive/ is a meta refresh to /archive/2026/, so its states were not printed: "
    "set follows_redirect = true on its target to print the page it leads to"
)


def setUpModule() -> None:
    """Skip every case here, naming the install, without poppler or the real engine."""
    require_live_engine()


class RedirectRunTest(unittest.TestCase):
    """The declared stub prints the year page; the undeclared one is a finding, not a race."""

    outcome: ClassVar[Outcome]
    out: ClassVar[Path]
    report: ClassVar[dict[str, object]]

    @classmethod
    def setUpClass(cls) -> None:
        """Serve the stub and the year page, and run the tool over both targets once.

        The folder is removed by a class cleanup, which runs even when this raises.
        """
        folder = tempfile.TemporaryDirectory(prefix="print_pass-live-")
        cls.addClassCleanup(folder.cleanup)
        root = Path(folder.name)
        cls.out = root / "out"
        targets = root / "targets.toml"
        targets.write_text(REDIRECT_TARGETS, encoding="utf-8", newline="\n")
        site = served_pages(root, REDIRECT_PAGES)
        cls.outcome = make_run_process(
            site,
            cls.out,
            *("--targets", str(targets)),
            *("--timeout", str(REDIRECT_TIMEOUT_SECONDS)),
            timeout=redirect_run_bound_seconds(),
        )
        report_file = cls.out / REPORT_NAME
        if not report_file.is_file():
            raise RuntimeError(
                f"the run wrote no report (exit {cls.outcome.code}):\n"
                f"{cls.outcome.stderr}"
            )
        cls.report = load_json(report_file)

    def test_the_declared_stub_prints_the_year_page_with_no_finding(self) -> None:
        states = cast(list[dict[str, object]], self.report["states"])
        (state,) = states
        self.assertEqual((state["target"], state["state"]), ("archive", "default"))
        self.assertEqual(state["page"], "/archive/2026/")
        self.assertEqual(at(state, "read_back", "title"), "2026")
        self.assertEqual(at(state, "pdfs"), ["pdfs/archive--default--bg-off.pdf"])
        self.assertNotIn("finding", [severity for severity, _ in problems(state)])

    def test_the_undeclared_stub_is_the_runs_one_finding(self) -> None:
        self.assertEqual(self.outcome.code, EXIT_FINDINGS, self.outcome.stderr)
        self.assertEqual(at(self.report, "totals", "findings"), 1)
        self.assertEqual(
            self.report["redirects"],
            [
                {
                    "target": "archive",
                    "page": "/archive/",
                    "destination": "/archive/2026/",
                    "followed": True,
                    "problems": [],
                },
                {
                    "target": "undeclared",
                    "page": "/archive/",
                    "destination": "/archive/2026/",
                    "followed": False,
                    "problems": [{"severity": "finding", "what": _UNDECLARED}],
                },
            ],
        )
        self.assertIn(f"FINDING undeclared: {_UNDECLARED}", self.outcome.stdout)

    def test_no_probe_races_the_stubs_navigation(self) -> None:
        self.assertNotIn(_RACED, self.outcome.stdout)
        self.assertNotIn(_RACED, (self.out / REPORT_NAME).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
