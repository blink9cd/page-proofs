"""Grouped suite: print_pass's command line -- its options, each mode's refusals, and --out's.

Every case here ends before anything is printed or read: the parser's help and errors, the
checks `_validate_args` makes per mode, and `main` refusing a run whose `--out` a git alone
on `PATH` cannot place, whose poppler readers are missing, or whose PDF readers are not
installed -- that last also in a fresh interpreter, where nothing this suite imported can
stand in for one. The checks themselves are `test_print_preflight`'s; what a run does
after them is `test_print_modes`'.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
sys.path.insert(0, str(_TOOL_DIR))
import print_pass  # noqa: E402 - the CLI, via the insert above

import help_formatter  # noqa: E402 - beside the tool's folder, via the CLI's own insert

import pdf_tools  # noqa: E402 - the tool's private src/
import print_modes  # noqa: E402 - as above
from _print_fixtures import fake_bin  # noqa: E402 - this folder's shared fixtures
from _print_pass_fixtures import (  # noqa: E402 - as above
    MEASURE_PACKAGES,
    outside_folder,
    packages_imported,
    pdf_file,
    ready_host,
    run_main,
    run_without,
)
from print_engine import EngineSettings  # noqa: E402 - the tool's private src/
from print_plan import MakePlan, ReadPlan  # noqa: E402 - as above
from print_targets import Viewport  # noqa: E402 - as above

#: A fake git that says what the real one says of a folder outside every repository.
_GIT_NOT_A_REPOSITORY = (
    "import sys\n"
    'sys.stderr.write("fatal: not a git repository (or any of the parent'
    ' directories): .git\\n")\n'
    "sys.exit(128)\n"
)

#: Every option only --mode make takes, each with a value it would accept.
_MAKE_ONLY = (
    ("--serve", "site"),
    ("--targets", "targets.toml"),
    ("--page", "/"),
    ("--engine", "shell"),
    ("--browsers", "builds"),
    ("--backgrounds", "off"),
    ("--header-footer", "off"),
    ("--viewport", "1280x800"),
)

#: The PDF readers' packages, as the runs import them.
_READERS = ("pypdf", "pdfminer")


def _plan(*argv: str) -> object:
    """What `_validate_args` makes of `argv`."""
    parser = print_pass.build_parser()
    return print_pass._validate_args(parser, *print_pass._parse(parser, list(argv)))


class _FolderCase(unittest.TestCase):
    """A case with a temp folder outside every repository, a site in it, and an --out."""

    def setUp(self) -> None:
        """Make the folder, a site folder to serve, and name an --out that does not exist."""
        self.root = outside_folder(self)
        self.site = self.root / "site"
        self.site.mkdir()
        self.out = self.root / "out"

    def assert_refused(self, *argv: str, says: str) -> None:
        """`argv` exits 2, saying `says` on stderr, and makes no --out."""
        outcome = run_main(*argv)
        self.assertEqual(outcome.code, 2, outcome.stderr)
        self.assertIn(says, outcome.stderr)
        self.assertFalse(self.out.exists())


class HelpTest(unittest.TestCase):
    """`--help` names every option, each default, the prerequisites and the exit codes."""

    def help_text(self) -> str:
        """`--help` on one line: argparse wraps at the terminal's width."""
        with mock.patch.dict(os.environ, {"COLUMNS": "10000"}):
            outcome = run_main("--help")
        self.assertEqual(outcome.code, 0)
        return " ".join(outcome.stdout.split())

    def test_help_shows_every_option_and_each_default(self) -> None:
        text = self.help_text()
        for option, _ in _MAKE_ONLY:
            self.assertIn(option, text)
        options = ("--mode", "--pdf FILE", "--out DIR", "--dpi N", "--timeout SECONDS")
        for option in options:
            self.assertIn(option, text)
        for default in ("1280x800", "off", "shell", "96", "30", "['/']"):
            self.assertIn(f"(default: {default})", text)
        # A required option has no default to show: argparse below 3.13 would show None,
        # which the requirement-aware formatter keeps off on every version, this one too.
        formatter = print_pass.build_parser().formatter_class
        self.assertIs(formatter, help_formatter.ReqAwareDefaultsFormatter)
        for required in ("or compare two.", "and report.json."):
            self.assertNotIn(f"{required} (default: None)", text)

    def test_help_says_what_the_timeout_bounds_in_each_mode(self) -> None:
        text = self.help_text()
        self.assertIn("in make, each page's load, actions and settle", text)
        self.assertIn("in every mode, each poppler call", text)
        reads = (
            "the pypdf and pdfminer reads of one PDF, together, in one child process"
        )
        self.assertIn(reads, text)

    def test_help_states_the_prerequisites_the_exit_codes_and_which_setting_wins(
        self,
    ) -> None:
        text = self.help_text()
        for needed in (
            "pip install '.[print]'",
            "python -m playwright install --only-shell chromium",
            "$PLAYWRIGHT_BROWSERS_PATH",
            "pdfinfo, pdffonts, pdfimages, pdftoppm",
            "Google Chrome",
            "outside every git work tree",
            "A key the targets file sets (viewport, backgrounds) wins over",
            "Exit codes: 0",
        ):
            self.assertIn(needed, text)

    def test_mode_and_out_are_required(self) -> None:
        outcome = run_main()
        self.assertEqual(outcome.code, 2)
        self.assertIn("--mode", outcome.stderr)
        self.assertIn("--out", outcome.stderr)


class MakeRefusalTest(_FolderCase):
    """What --mode make refuses before anything runs."""

    def make(self, *argv: str) -> tuple[str, ...]:
        """A make invocation writing to this case's --out, with `argv` added."""
        return ("--mode", "make", "--out", str(self.out), *argv)

    def test_make_needs_a_folder_to_serve(self) -> None:
        self.assert_refused(*self.make(), says="--mode make needs --serve")
        page = self.site / "index.html"
        page.write_text("<p>x</p>\n", encoding="utf-8", newline="\n")
        self.assert_refused(
            *self.make("--serve", str(page)), says=f"--serve {page} is not a folder"
        )

    def test_make_refuses_a_pdf(self) -> None:
        self.assert_refused(
            *self.make("--serve", str(self.site), "--pdf", "a.pdf"),
            says="--mode read and compare alone take --pdf",
        )

    def test_targets_and_page_are_exclusive(self) -> None:
        self.assert_refused(
            *self.make("--serve", str(self.site), "--targets", "t.toml", "--page", "/"),
            says="not allowed with argument",
        )

    def test_a_page_with_a_dot_segment_is_a_planning_refusal(self) -> None:
        self.assert_refused(
            *self.make("--serve", str(self.site), "--page", "/../secret.html"),
            says="a . or .. segment",
        )

    def test_a_viewport_that_is_not_width_by_height(self) -> None:
        self.assert_refused(
            *self.make("--serve", str(self.site), "--viewport", "1280"),
            says="must be WIDTHxHEIGHT",
        )

    def test_browsers_must_be_a_folder(self) -> None:
        missing = self.root / "builds"
        self.assert_refused(
            *self.make("--serve", str(self.site), "--browsers", str(missing)),
            says=f"--browsers {missing} is not a folder",
        )

    def test_a_timeout_that_is_not_a_bound(self) -> None:
        # A call handed the timeout is bounded at twice it, so a timeout past half what a
        # lock can wait is one the engine would refuse once the driver had started.
        too_long = (str(threading.TIMEOUT_MAX), str(threading.TIMEOUT_MAX * 2))
        for value in ("0", "-1", "nan", "inf", "soon", *too_long):
            with self.subTest(value=value):
                self.assert_refused(
                    *self.make("--serve", str(self.site), f"--timeout={value}"),
                    says="argument --timeout",
                )

    def test_the_longest_timeout_every_bound_keeps_reaches_the_plan(self) -> None:
        longest = threading.TIMEOUT_MAX / 2
        plan = _plan(*self.make("--serve", str(self.site), f"--timeout={longest!r}"))
        assert isinstance(plan, MakePlan)
        self.assertEqual(plan.settings.timeout_s, longest)

    def test_a_dpi_out_of_range(self) -> None:
        too_fine = str(print_pass.MAX_DPI + 1)
        for value in ("0", "-5", "9.5", "fine", too_fine):
            with self.subTest(value=value):
                self.assert_refused(
                    *self.make("--serve", str(self.site), f"--dpi={value}"),
                    says="argument --dpi",
                )

    def test_every_default_fills_a_plan_serving_the_root_page(self) -> None:
        plan = _plan(*self.make("--serve", str(self.site)))
        self.assertEqual(
            plan,
            MakePlan(
                out=self.out,
                dpi=96,
                serve=self.site,
                targets=None,
                pages=("/",),
                browsers=None,
                settings=EngineSettings(
                    engine="shell",
                    backgrounds="off",
                    header_footer=False,
                    viewport=Viewport(1280, 800),
                    timeout_s=30,
                    pdf_dir=self.out / "pdfs",
                ),
            ),
        )

    def test_every_option_given_reaches_the_plan(self) -> None:
        plan = _plan(
            *self.make("--serve", str(self.site), "--page", "/a/", "--page", "/b/"),
            *("--engine", "chrome", "--browsers", str(self.site), "--dpi", "10"),
            *("--backgrounds", "both", "--header-footer", "on"),
            *("--viewport", "800x600", "--timeout", "12.5"),
        )
        assert isinstance(plan, MakePlan)
        # The pages asked, in order, and not the default page as well: `--page` appends.
        self.assertEqual(plan.pages, ("/a/", "/b/"))
        self.assertEqual((plan.browsers, plan.dpi), (self.site, 10))
        self.assertEqual(
            plan.settings,
            EngineSettings(
                "chrome", "both", True, Viewport(800, 600), 12.5, plan.out / "pdfs"
            ),
        )

    def test_a_targets_file_is_named_in_the_plan(self) -> None:
        targets = self.root / "targets.toml"
        plan = _plan(*self.make("--serve", str(self.site), "--targets", str(targets)))
        assert isinstance(plan, MakePlan)
        self.assertEqual(plan.targets, targets)


class PdfModeRefusalTest(_FolderCase):
    """What --mode read and --mode compare refuse before anything runs."""

    def test_read_needs_a_pdf(self) -> None:
        self.assert_refused(
            "--mode",
            "read",
            "--out",
            str(self.out),
            says="--mode read needs at least one --pdf",
        )

    def test_a_pdf_that_is_not_a_file(self) -> None:
        for pdf in (self.root / "absent.pdf", self.site):
            with self.subTest(pdf=pdf.name):
                self.assert_refused(
                    *("--mode", "read", "--out", str(self.out), "--pdf", str(pdf)),
                    says=f"--pdf {pdf} is not a file",
                )

    def test_compare_needs_exactly_two(self) -> None:
        pdfs = [pdf_file(self.root, f"{name}.pdf") for name in "abc"]
        for count in (1, 3):
            with self.subTest(count=count):
                given = [arg for pdf in pdfs[:count] for arg in ("--pdf", str(pdf))]
                self.assert_refused(
                    *("--mode", "compare", "--out", str(self.out), *given),
                    says=f"--mode compare needs exactly two --pdf, not {count}",
                )

    def test_every_make_option_is_refused_naming_make(self) -> None:
        pdfs = (
            "--pdf",
            str(pdf_file(self.root, "a.pdf")),
            "--pdf",
            str(pdf_file(self.root, "b.pdf")),
        )
        for mode in ("read", "compare"):
            for option, value in _MAKE_ONLY:
                with self.subTest(mode=mode, option=option):
                    # Refused at a value it would take, even the default: it was given.
                    self.assert_refused(
                        *("--mode", mode, "--out", str(self.out), *pdfs, option, value),
                        says=f"--mode make alone takes {option}",
                    )

    def test_two_make_options_are_named_together(self) -> None:
        pdf = str(pdf_file(self.root, "a.pdf"))
        self.assert_refused(
            *("--mode", "read", "--out", str(self.out), "--pdf", pdf),
            *("--serve", str(self.site), "--engine", "shell"),
            says="--mode make alone takes --serve, --engine",
        )

    def test_read_and_compare_take_a_timeout_for_each_reader(self) -> None:
        pdfs = (pdf_file(self.root, "a.pdf"), pdf_file(self.root, "b.pdf"))
        for mode in ("read", "compare"):
            with self.subTest(mode=mode):
                plan = _plan(
                    *("--mode", mode, "--out", str(self.out), "--timeout", "12.5"),
                    *(arg for pdf in pdfs for arg in ("--pdf", str(pdf))),
                )
                self.assertEqual(plan, ReadPlan(mode, self.out, 96, 12.5, pdfs))

    def test_read_refuses_two_pdfs_whose_outputs_would_collide(self) -> None:
        first = pdf_file(self.root / "one", "report.pdf")
        for second in (
            pdf_file(self.root / "two", "report.pdf"),
            pdf_file(self.root / "three", "Report.PDF"),
        ):
            with self.subTest(second=second.name):
                self.assert_refused(
                    *("--mode", "read", "--out", str(self.out)),
                    *("--pdf", str(first), "--pdf", str(second)),
                    says=f"--pdf report.pdf and {second.name} would write the same",
                )

    def test_read_plans_every_pdf_in_order(self) -> None:
        pdfs = (pdf_file(self.root, "b.pdf"), pdf_file(self.root, "a.pdf"))
        plan = _plan(
            *("--mode", "read", "--out", str(self.out), "--dpi", "72"),
            *(arg for pdf in pdfs for arg in ("--pdf", str(pdf))),
        )
        self.assertEqual(plan, ReadPlan("read", self.out, 72, 30, pdfs))

    def test_compare_takes_two_pdfs_of_one_name(self) -> None:
        # Nothing collides: a comparison writes no file of either PDF's own.
        pdfs = (
            pdf_file(self.root / "one", "a.pdf"),
            pdf_file(self.root / "two", "a.pdf"),
        )
        plan = _plan(
            *("--mode", "compare", "--out", str(self.out)),
            *(arg for pdf in pdfs for arg in ("--pdf", str(pdf))),
        )
        self.assertEqual(plan, ReadPlan("compare", self.out, 96, 30, pdfs))


class UnverifiableOutTest(_FolderCase):
    """A git that cannot say where --out lies refuses the run: exit 2, nothing written."""

    def read_with_git(self, body: str | None) -> tuple[str, ...]:
        """A read invocation, with `body` as the only git on PATH, or no git at all."""
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        env = {"PATH": str(bin_dir)} if body is None else fake_bin(bin_dir, "git", body)
        self.enterContext(mock.patch.dict(os.environ, env))
        pdf = pdf_file(self.root, "a.pdf")
        return ("--mode", "read", "--out", str(self.out), "--pdf", str(pdf))

    def test_a_git_exiting_127_cannot_verify(self) -> None:
        argv = self.read_with_git("import sys\nsys.exit(127)\n")
        self.assert_refused(*argv, says="cannot verify that --out")

    def test_no_git_at_all_cannot_verify(self) -> None:
        argv = self.read_with_git(None)
        self.assert_refused(*argv, says="git could not be run")

    def test_a_git_refusing_the_folder_cannot_verify(self) -> None:
        body = (
            "import sys\n"
            'sys.stderr.write("fatal: detected dubious ownership in repository\\n")\n'
            "sys.exit(128)\n"
        )
        argv = self.read_with_git(body)
        self.assert_refused(
            *argv, says="git exited 128: fatal: detected dubious ownership"
        )

    def test_a_git_answering_neither_cannot_verify(self) -> None:
        argv = self.read_with_git('print("maybe")\n')
        self.assert_refused(*argv, says="git exited 0: maybe")


class PopplerTest(_FolderCase):
    """A poppler reader missing from PATH refuses every mode, naming each one missing."""

    def test_the_missing_readers_are_named(self) -> None:
        bin_dir = self.root / "bin"
        env = fake_bin(bin_dir, "git", _GIT_NOT_A_REPOSITORY)
        fake_bin(bin_dir, "pdfinfo", "")
        pdf = pdf_file(self.root, "a.pdf")
        with mock.patch.dict(os.environ, env):
            self.assert_refused(
                *("--mode", "read", "--out", str(self.out), "--pdf", str(pdf)),
                says="poppler's pdffonts, pdfimages, pdftoppm not found on PATH",
            )


class ReaderImportTest(_FolderCase):
    """A host without a PDF reader still has --help, and refuses a run with 2, naming the extra.

    The readers are the runs' to import, never the CLI's own: an import that fails at the
    CLI's top would exit 1, the code of a finding, with a traceback in place of a reason.
    """

    def ready_path(self) -> dict[str, str]:
        """A PATH of fakes alone: a git placing --out outside, and every poppler reader."""
        bin_dir = self.root / "bin"
        env = fake_bin(bin_dir, "git", _GIT_NOT_A_REPOSITORY)
        for tool in pdf_tools.REQUIRED_TOOLS:
            fake_bin(bin_dir, tool, "")
        return env

    def test_the_cli_imports_no_pdf_reader_and_no_playwright(self) -> None:
        imported = packages_imported("print_pass", _TOOL_DIR)
        self.assertIn("print_pass", imported)
        self.assertEqual(imported & MEASURE_PACKAGES, frozenset())

    def test_help_needs_no_pdf_reader(self) -> None:
        for blocked in _READERS:
            with self.subTest(blocked=blocked):
                answer = run_without(blocked, "--help", env={}, cwd=self.root)
                self.assertEqual(answer.returncode, 0, answer.stderr)
                self.assertIn("usage:", answer.stdout)

    def test_a_run_without_a_pdf_reader_is_two_naming_the_extra(self) -> None:
        env = self.ready_path()
        pdf = pdf_file(self.root, "a.pdf")
        for blocked in _READERS:
            with self.subTest(blocked=blocked):
                answer = run_without(
                    blocked,
                    *("--mode", "read", "--out", str(self.out), "--pdf", str(pdf)),
                    env=env,
                    cwd=self.root,
                )
                self.assertEqual(answer.returncode, 2, answer.stderr)
                self.assertIn(f"print_pass: {blocked} is not installed", answer.stderr)
                self.assertIn("`print` extra", answer.stderr)
                self.assertNotIn("Traceback", answer.stderr)
                self.assertFalse(self.out.exists())

    def test_a_missing_reader_is_refused_before_anything_is_written(self) -> None:
        # In this interpreter, so the refusal's lines are measured: the import raises.
        pdf = pdf_file(self.root, "a.pdf")
        for name in ("pypdf", "pypdf.errors", "pdfminer.high_level"):
            with self.subTest(name=name):
                missing = ModuleNotFoundError(f"No module named {name!r}", name=name)
                with (
                    ready_host(),
                    mock.patch.object(print_pass, "_import_runs", side_effect=missing),
                ):
                    self.assert_refused(
                        *("--mode", "read", "--out", str(self.out), "--pdf", str(pdf)),
                        says=f"{name.partition('.')[0]} is not installed: install the "
                        "repository's `print` extra",
                    )

    def test_any_other_missing_module_is_the_runs_own_failure(self) -> None:
        pdf = pdf_file(self.root, "a.pdf")
        missing = ModuleNotFoundError("No module named 'pypdfium2'", name="pypdfium2")
        with (
            ready_host(),
            mock.patch.object(print_pass, "_import_runs", side_effect=missing),
        ):
            outcome = run_main(
                *("--mode", "read", "--out", str(self.out), "--pdf", str(pdf))
            )
        self.assertEqual(outcome.code, 2)
        self.assertIn("print_pass: the run could not finish:", outcome.stderr)
        self.assertIn(
            "ModuleNotFoundError: No module named 'pypdfium2'", outcome.stderr
        )

    def test_the_real_import_is_the_runs_own_module(self) -> None:
        self.assertIs(print_pass._import_runs(), print_modes)


if __name__ == "__main__":
    unittest.main()
