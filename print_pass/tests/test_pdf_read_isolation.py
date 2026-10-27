"""Suite: the child pdf_read starts is isolated, and the isolation is pinned, not assumed.

The child runs `python -I` with a constant program, the worker's folder and the PDF in argv
and nothing else, an environment cut down to what an interpreter needs to start, and a
memory budget where the host can hold one. Each of those is asserted here: the argv and
environment exactly, through `bounded_run`'s seam, and the isolation live, by running the
real worker from a folder that plants a module of a name the worker imports.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the
tool's aggregator; the bootstrap below names the tool's private `src/`, and discovery's
top-level directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Final
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pdf_read  # noqa: E402 - the tool's private src/, via the insert above
import pdf_read_worker  # noqa: E402 - as above
import pdf_text  # noqa: E402 - as above
from _pdf_read_fixtures import SEED  # noqa: E402 - beside this module

#: A bound no healthy read of the seed comes near, even on a loaded machine.
_TIMEOUT: Final = 60.0

#: A module that fails the moment it is imported: planted where only a leaky child finds it.
_PLANTED: Final = "raise RuntimeError('a planted module ran')\n"

#: A budget small enough to cross on purpose, and the allocation that crosses it.
_SMALL_BUDGET: Final = 256 * 1024 * 1024
_PAST_IT: Final = 512 * 1024 * 1024

#: How long the over-budget child may run: its watch ends it within a fraction of a second.
_OVER_BUDGET_TIMEOUT: Final = 30.0

#: The worker entered through `serve` under the small budget, its text reader replaced by
#: one that allocates past it and then holds on, as an inflating read does. Where the host
#: holds an address-space budget the allocation fails as a MemoryError; elsewhere the
#: worker's memory watch ends the child.
_SERVED_OVER_BUDGET: Final = f"""\
import sys, time
sys.path.insert(0, sys.argv[1])
import pdf_read_worker as worker
def hog(pdf):
    block = b"x" * {_PAST_IT}
    while block:
        time.sleep(0.01)
worker.MEMORY_BUDGET_BYTES = {_SMALL_BUDGET}
worker.READERS = {{**worker.READERS, worker.TEXT: hog}}
raise SystemExit(worker.serve(*sys.argv[2:]))
"""

#: A child that sets the small budget and then allocates past it: prints `unlimited` when
#: the host refused the budget, `refused` when the allocation was, and `allocated` otherwise.
_OVER_BUDGET: Final = f"""\
import sys
sys.path.insert(0, sys.argv[1])
from pdf_read_worker import limit_memory
if not limit_memory({_SMALL_BUDGET}):
    print("unlimited")
    raise SystemExit(0)
try:
    block = bytearray({_PAST_IT})
except MemoryError:
    print("refused")
else:
    print("allocated")
"""


class LaunchTests(unittest.TestCase):
    """The argv and environment the child is started with, exactly."""

    def test_the_child_is_started_isolated_with_the_pdf_in_argv_alone(self) -> None:
        pdf = Path("folder") / "seed.pdf"
        with mock.patch.object(pdf_read.bounded_run, "run_bounded") as run:
            pdf_read.run_worker(pdf, 7.0)
        argv = [
            sys.executable,
            "-I",
            "-c",
            pdf_read._LAUNCH,
            str(pdf_read.WORKER_FOLDER),
            str(pdf.absolute()),
        ]
        run.assert_called_once_with(argv, timeout=7.0, env=pdf_read.worker_env())

    def test_the_launch_enters_through_the_budgeted_entry(self) -> None:
        self.assertIn("from pdf_read_worker import serve", pdf_read._LAUNCH)
        self.assertNotIn("main", pdf_read._LAUNCH)

    def test_the_environment_keeps_only_what_an_interpreter_needs(self) -> None:
        parent = {
            "PATH": "/usr/bin",
            "LANG": "C.UTF-8",
            "TMPDIR": "/var/tmp",
            "HOME": "a home folder",
            "PYTHONPATH": "a planted folder",
            "SSLKEYLOGFILE": "a key log",
        }
        kept = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "TMPDIR": "/var/tmp"}
        self.assertEqual(pdf_read.worker_env(parent), kept)

    def test_by_default_the_environment_is_read_from_this_process(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": "/bin", "SECRET_TOKEN": "x"}):
            self.assertEqual(pdf_read.worker_env().get("PATH"), "/bin")
            self.assertNotIn("SECRET_TOKEN", pdf_read.worker_env())

    def test_the_reply_cap_is_the_workers_own(self) -> None:
        self.assertIs(pdf_read.MAX_REPLY_CHARS, pdf_read_worker.MAX_REPLY_CHARS)


class PlantedModuleTests(unittest.TestCase):
    """A module planted in the working directory or on PYTHONPATH is never imported."""

    def test_the_real_worker_reads_past_a_planted_pdf_text(self) -> None:
        with tempfile.TemporaryDirectory() as holder:
            folder = Path(holder)
            for module in ("pdf_text.py", "pypdf.py"):
                (folder / module).write_text(_PLANTED, encoding="utf-8", newline="\n")
            pdf = folder / "seed.pdf"
            pdf.write_bytes(SEED)
            planted = {"PYTHONPATH": str(folder), "PYTHONSTARTUP": str(folder)}
            with contextlib.chdir(folder), mock.patch.dict(os.environ, planted):
                content = pdf_read.read_content(pdf, timeout=_TIMEOUT)
            self.assertEqual(content.links, pdf_text.read_links(pdf))
            self.assertEqual(content.text, pdf_text.read_text(pdf))


class MemoryBudgetLiveTests(unittest.TestCase):
    """Where the host holds an address-space budget, an allocation past it is refused."""

    def test_an_allocation_past_the_budget_is_a_memory_error(self) -> None:
        argv = [sys.executable, "-I", "-c", _OVER_BUDGET, str(pdf_read.WORKER_FOLDER)]
        completed = pdf_read.bounded_run.run_bounded(argv, timeout=_TIMEOUT)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        said = completed.stdout.strip()
        if said == "unlimited":
            self.skipTest("this host refuses an address-space budget (macOS does)")
        self.assertEqual(said, "refused")

    def test_a_read_past_the_budget_fails_alone_and_the_reads_before_it_answer(
        self,
    ) -> None:
        # Skipped in the body, not by a decorator, so mypy's win32 pass skips it too.
        if sys.platform == "win32":  # pragma: only-on win32 - no budget on Windows
            self.skipTest("no budget there")  # pragma: only-on win32 - as above

        def run(pdf: Path, timeout: float) -> subprocess.CompletedProcess[str]:
            argv = [sys.executable, "-I", "-c", _SERVED_OVER_BUDGET]
            argv += [str(pdf_read.WORKER_FOLDER), str(pdf.absolute())]
            return pdf_read.bounded_run.run_bounded(
                argv, timeout=timeout, env=pdf_read.worker_env()
            )

        with tempfile.TemporaryDirectory() as holder:
            pdf = Path(holder) / "seed.pdf"
            pdf.write_bytes(SEED)
            content = pdf_read.read_content(pdf, timeout=_OVER_BUDGET_TIMEOUT, run=run)
            self.assertEqual(content.links, pdf_text.read_links(pdf))
            self.assertEqual(content.metadata, pdf_text.read_metadata(pdf))
        watched = "passed its 2 GiB memory budget and was ended"
        held = "raised MemoryError"
        reason = held if sys.platform.startswith("linux") else watched
        self.assertIsInstance(content.text, pdf_text.PdfTextError)
        failed = f"cannot read text from seed.pdf: its reader {reason}"
        self.assertEqual(str(content.text), failed)


if __name__ == "__main__":
    unittest.main()
