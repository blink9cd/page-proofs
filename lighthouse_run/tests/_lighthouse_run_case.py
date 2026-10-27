"""The whole-run test case lighthouse_run's grouped suites share: a project, fakes, and `main`.

Kept apart from `_lighthouse_run_fixtures`, which builds files and fake executables and imports
nothing of the tool: this module drives the CLI itself, so it is the one that imports it. Every
suite running whole invocations subclasses `ProjectCase`, so each sees the same project, the
same fakes first on PATH, and the same way of reading back what a run wrote.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lighthouse_run  # noqa: E402 - import follows the sys.path bootstrap above
from _lighthouse_run_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    fake_git,
    fake_report,
    fake_toolchain,
    make_project,
)


class ProjectCase(unittest.TestCase):
    """A throwaway project, a fake toolchain and git first on PATH, and a way to run `main`.

    Every whole run reads its commit, so the fake git answers for all of them: otherwise the
    host's git would, and what it says depends on where the host keeps its scratch folders.
    """

    def setUp(self) -> None:
        """Build the project, the fakes and the canned report, and put the fakes first."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        base = Path(scratch.name)
        self.root = make_project(base / "project")
        self.out = base / "out"
        self.log = base / "lighthouse.log"
        self.git_log = base / "git.log"
        report = base / "report.json"
        report.write_text(json.dumps(fake_report()), encoding="utf-8", newline="\n")
        environment = {
            **fake_toolchain(base / "bin"),
            **fake_git(base / "bin", self.git_log),
            "FAKE_LH_LOG": str(self.log),
            "FAKE_LH_REPORT": str(report),
        }
        self.chrome = environment["FAKE_CHROME_PATH"]
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *extra: str) -> tuple[int, str, str]:
        """Run `main` on the project with `--out` and `extra`; return code, stdout, stderr."""
        out, err = io.StringIO(), io.StringIO()
        argv = ["--root", str(self.root), "--out", str(self.out), *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lighthouse_run.main(argv)
        return code, out.getvalue(), err.getvalue()

    def summary(self, out: Path | None = None) -> dict[str, Any]:
        """The one summary a run wrote under `out` (default: this case's `--out`)."""
        (found,) = sorted((out or self.out).glob("*/summary.json"))
        loaded: dict[str, Any] = json.loads(found.read_text(encoding="utf-8"))
        return loaded

    def result(self, page: str, preset: str) -> dict[str, Any]:
        """One page and preset's entry in the summary."""
        (found,) = [
            r
            for r in self.summary()["results"]
            if r["page"] == page and r["preset"] == preset
        ]
        return found
