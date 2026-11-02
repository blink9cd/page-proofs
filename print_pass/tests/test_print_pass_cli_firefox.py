"""Grouped suite: the command line's `--engine firefox`, and the two options it refuses.

Firefox prints through geckodriver's WebDriver Print, which never prints the print dialog's
header and footer, and needs none of Playwright's engine builds. So `--header-footer` and
`--browsers` are refused beside it with exit 2, as an option of another mode is: given at
all, even at a value Firefox would honor. `--help` says what it needs (macOS, geckodriver
and the command that installs it, and Firefox itself) beside what each Playwright engine
needs, and names it beside Playwright's Chromium as a way a page is printed. Every case ends
before anything is printed.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/` and its own folder, and
discovery's top-level directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Final
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
sys.path.insert(0, str(_TOOL_DIR))
import print_pass  # noqa: E402 - the CLI, via the insert above

from _print_pass_fixtures import outside_folder, run_main  # noqa: E402 - beside this
from print_plan import MakePlan  # noqa: E402 - the tool's private src/

#: The refusals' openings, each naming the engine and the option refused beside it.
_NO_HEADER_FOOTER: Final = "--engine firefox takes no --header-footer"
_NO_BROWSERS: Final = "--engine firefox takes no --browsers"


class _FirefoxCase(unittest.TestCase):
    """A site to serve and an --out, under a temp folder outside every repository."""

    def setUp(self) -> None:
        """Make the folder and its site, and name an --out that does not exist."""
        self.root = outside_folder(self)
        self.site = self.root / "site"
        self.site.mkdir()
        self.out = self.root / "out"

    def firefox(self, *argv: str) -> tuple[str, ...]:
        """A make invocation printing through Firefox, with `argv` added."""
        return (
            *("--mode", "make", "--out", str(self.out), "--serve", str(self.site)),
            *("--engine", "firefox", *argv),
        )

    def assert_refused(self, *argv: str, says: str) -> None:
        """`argv` exits 2, saying `says` on stderr, and makes no --out."""
        outcome = run_main(*argv)
        self.assertEqual(outcome.code, 2, outcome.stderr)
        self.assertIn(says, outcome.stderr)
        self.assertFalse(self.out.exists())


class EngineChoiceTests(_FirefoxCase):
    """Firefox is one of the engines, and a make run can be planned with it."""

    def test_the_engines_are_the_records_own_three(self) -> None:
        self.assertEqual(print_pass.ENGINES, ("shell", "chrome", "firefox"))

    def help_text(self) -> str:
        """`--help` on one line: argparse wraps at the terminal's width."""
        with mock.patch.dict(os.environ, {"COLUMNS": "10000"}):
            outcome = run_main("--help")
        self.assertEqual(outcome.code, 0)
        return " ".join(outcome.stdout.split())

    def test_help_states_what_each_engine_needs(self) -> None:
        # A reader of the chrome clause installs what it names; naming the pinned builds
        # there would send a chrome user to download a browser that engine never starts.
        text = self.help_text()
        for clause in (
            "--engine shell needs the extra's Playwright and the pinned engine builds, "
            "installed with `python -m playwright install --only-shell chromium` into "
            "$PLAYWRIGHT_BROWSERS_PATH, or Playwright's own folder when it is unset;",
            "--engine chrome needs the extra's Playwright and installed Google Chrome, "
            "and never starts the pinned builds;",
            "a native-dark state under either of the two needs installed Google Chrome;",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, text)

    def test_help_names_firefox_and_what_it_needs(self) -> None:
        text = self.help_text()
        self.assertIn("--engine {shell,chrome,firefox}", text)
        self.assertIn("--engine firefox runs on macOS only", text)
        self.assertIn(
            "geckodriver on PATH, which `brew install geckodriver` installs, and Firefox "
            "itself",
            text,
        )

    def test_the_description_names_both_ways_a_page_is_printed(self) -> None:
        description = " ".join((print_pass.build_parser().description or "").split())
        self.assertIn("Playwright's Chromium", description)
        self.assertIn("Firefox's WebDriver Print", description)

    def test_firefox_alone_reaches_the_plan(self) -> None:
        parser = print_pass.build_parser()
        plan = print_pass._validate_args(
            parser, *print_pass._parse(parser, list(self.firefox()))
        )
        assert isinstance(plan, MakePlan)
        self.assertEqual(plan.settings.engine, "firefox")
        self.assertIs(plan.settings.header_footer, False)
        self.assertIsNone(plan.browsers)


class RefusalTests(_FirefoxCase):
    """`--header-footer` and `--browsers` are refused beside `--engine firefox`."""

    def test_a_header_and_footer_is_refused_at_either_value(self) -> None:
        for value in ("on", "off"):
            with self.subTest(value=value):
                self.assert_refused(
                    *self.firefox("--header-footer", value), says=_NO_HEADER_FOOTER
                )

    def test_the_refusal_says_why(self) -> None:
        self.assert_refused(
            *self.firefox("--header-footer", "on"),
            says="WebDriver's Print never prints a header or footer",
        )

    def test_a_folder_of_engine_builds_is_refused(self) -> None:
        builds = self.root / "builds"
        builds.mkdir()
        self.assert_refused(*self.firefox("--browsers", str(builds)), says=_NO_BROWSERS)

    def test_a_missing_builds_folder_is_refused_for_firefox_first(self) -> None:
        missing = self.root / "builds"
        outcome = run_main(*self.firefox("--browsers", str(missing)))
        self.assertEqual(outcome.code, 2)
        self.assertIn(_NO_BROWSERS, outcome.stderr)
        self.assertNotIn("is not a folder", outcome.stderr)

    def test_the_other_engines_still_take_both(self) -> None:
        parser = print_pass.build_parser()
        for engine in ("shell", "chrome"):
            with self.subTest(engine=engine):
                argv = [
                    *("--mode", "make", "--out", str(self.out)),
                    *("--serve", str(self.site), "--engine", engine),
                    *("--header-footer", "on", "--browsers", str(self.site)),
                ]
                plan = print_pass._validate_args(
                    parser, *print_pass._parse(parser, argv)
                )
                assert isinstance(plan, MakePlan)
                self.assertIs(plan.settings.header_footer, True)


if __name__ == "__main__":
    unittest.main()
