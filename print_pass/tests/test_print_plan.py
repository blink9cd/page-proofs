"""Grouped suite: print_plan, what the CLI needs before a run, imported without a PDF reader.

What this module promises beyond its names is what it does NOT import: the PDF readers and
Playwright, so the CLI importing it shows --help and refuses a run without them. That is
checked in a fresh interpreter, where nothing a suite imported first can hide an import; so
is the other import promise here, that the fixtures this suite shares with the CLI's and the
runs' import before any module of the tool, finding the shared runner themselves. And so is
the promise every bootstrap of the tool makes: a module of a shared library's name ahead of
it on the path, as an installed one would be, never stands in for the folder beside the tool.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Final

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# ...and the tool's own folder, where the CLI the shared fixtures import is found.
sys.path.insert(0, str(_TOOL_DIR))
import print_plan  # noqa: E402 - the tool's private src/, via the insert above

import print_watchdog  # noqa: E402 - as above
from _print_pass_fixtures import (  # noqa: E402 - this folder's shared fixtures
    MEASURE_PACKAGES,
    packages_imported,
)

import bounded_run  # noqa: E402 - the shared runner, which the fixtures insert

#: A fresh interpreter's program: put the folders named on the path, as a suite's own
#: bootstrap does, then import the shared fixtures before anything else.
_IMPORT_FIXTURES_FIRST: Final = (
    "import importlib, sys\n"
    "sys.path[:0] = sys.argv[1:]\n"
    "importlib.import_module('_print_pass_fixtures')\n"
)

#: Bound on the fresh interpreter that imports them.
_PYTHON_TIMEOUT_SECONDS: Final = 60.0


class ImportTest(unittest.TestCase):
    """Importing the plans pulls in no PDF reader and no Playwright."""

    def test_it_imports_no_pdf_reader_and_no_playwright(self) -> None:
        imported = packages_imported("print_plan", _TOOL_DIR / "src")
        self.assertIn("print_plan", imported)
        self.assertEqual(imported & MEASURE_PACKAGES, frozenset())

    def test_the_shared_fixtures_import_before_any_tool_module(self) -> None:
        folders = (Path(__file__).resolve().parent, _TOOL_DIR / "src", _TOOL_DIR)
        answer = bounded_run.run_bounded(
            [sys.executable, "-I", "-c", _IMPORT_FIXTURES_FIRST, *map(str, folders)],
            timeout=_PYTHON_TIMEOUT_SECONDS,
            env={},
        )
        self.assertEqual(answer.returncode, 0, answer.stderr)


class VoiceTest(unittest.TestCase):
    """Every message the CLI and its runs print opens with the tool's name."""

    def test_the_watchdog_leads_with_the_same_name(self) -> None:
        # The watchdog imports nothing of the tool's, so it restates the name; this keeps
        # the two one.
        self.assertEqual(print_watchdog._TOOL_NAME, print_plan.TOOL)

    def test_progress_goes_to_stdout_and_a_problem_to_stderr(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            print_plan.say("made")
            print_plan.warn("refused")
        self.assertEqual(stdout.getvalue(), "print_pass: made\n")
        self.assertEqual(stderr.getvalue(), "print_pass: refused\n")

    def test_what_a_terminal_would_act_on_is_spelled_as_its_escape(self) -> None:
        # A line can quote a page's or a log's text; each of its own lines stays a line.
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            print_plan.say("a\u202eb")
            print_plan.warn("\x1b[2J\nTraceback\r")
        self.assertEqual(stdout.getvalue(), "print_pass: a\\u202eb\n")
        self.assertEqual(stderr.getvalue(), "print_pass: \\x1b[2J\nTraceback\\x0d\n")


#: Every library a print_pass bootstrap puts on `sys.path`, each a folder at the repository
#: root.
_SIBLINGS: Final = (
    "bounded_run",
    "csp_watch",
    "help_formatter",
    "loopback_site",
    "stdio_utf8",
)

#: A fresh interpreter's program: put the folders named first (a path list) on the path, as a
#: suite's own bootstrap does, import the module named second, so its bootstrap runs, then
#: import each library named after them and print the file it came from, one per line.
_IMPORT_FROM: Final = (
    "import importlib, os, sys\n"
    "folders, module, *names = sys.argv[1:]\n"
    "sys.path[:0] = folders.split(os.pathsep)\n"
    "importlib.import_module(module)\n"
    "print(*(importlib.import_module(n).__file__ for n in names), sep='\\n')\n"
)

#: Bound on that interpreter, which imports Playwright and the PDF readers at most.
_IMPORT_TIMEOUT_SECONDS: Final = 120.0


def _bootstraps(tool: Path) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Each module whose bootstrap inserts a library: its folders, and the libraries it inserts.

    A module that imports another inserting the same library first is checked all the same:
    what it imports is still the folder beside the tool.
    """
    # A suite's module is imported as its run imports it: the shared fixtures, src/ and the CLI's
    # folder on the path with it.
    src, tests = (
        str(tool / "src"),
        os.pathsep.join(map(str, (tool / "tests", tool / "src", tool))),
    )
    runner = ("bounded_run",)
    return {
        "print_pass": (
            str(tool),
            ("bounded_run", "loopback_site", "stdio_utf8", "help_formatter"),
        ),
        "print_csp": (src, ("csp_watch",)),
        "print_modes": (src, ("loopback_site", "stdio_utf8")),
        "print_plan": (src, ("stdio_utf8",)),
        "print_redirects": (src, ("loopback_site",)),
        **{
            module: (src, runner)
            for module in (
                "print_preflight",
                "pdf_tools",
                "pdf_raster",
                "pdf_read",
                "gecko_bound",
                "gecko_launch",
                "gecko_process",
                "gecko_scratch",
            )
        },
        "_print_pass_fixtures": (tests, runner),
        "test_print_watchdog": (tests, runner),
        "test_gecko_process": (tests, runner),
        "test_print_csp": (tests, ("csp_watch",)),
    }


class SiblingShadowingTest(unittest.TestCase):
    """A same-named module ahead of the libraries on `sys.path` never shadows the shipped one.

    Each decoy sits on `PYTHONPATH`, which a fresh interpreter puts ahead of every installed
    distribution, so it stands for one: a bootstrap that appended a library's folder would
    import the decoy instead.
    """

    def test_every_bootstrap_imports_the_folder_beside_the_tool(self) -> None:
        root = _TOOL_DIR.parent
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        decoys, elsewhere = (
            Path(holder.name) / "decoys",
            Path(holder.name) / "elsewhere",
        )
        decoys.mkdir()
        elsewhere.mkdir()
        for name in _SIBLINGS:
            (decoys / f"{name}.py").write_text(
                "DECOY = True\n", encoding="utf-8", newline="\n"
            )
        environment = {**os.environ, "PYTHONPATH": str(decoys)}
        for module, (folders, names) in _bootstraps(_TOOL_DIR).items():
            with self.subTest(module=module):
                done = bounded_run.run_bounded(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        _IMPORT_FROM,
                        folders,
                        module,
                        *names,
                    ],
                    timeout=_IMPORT_TIMEOUT_SECONDS,
                    cwd=elsewhere,
                    env=environment,
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                imported = [Path(line).parent for line in done.stdout.splitlines()]
                self.assertEqual(imported, [root / name for name in names])


if __name__ == "__main__":
    unittest.main()
