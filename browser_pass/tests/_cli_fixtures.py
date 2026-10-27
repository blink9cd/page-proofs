"""Shared fixtures for browser_pass's CLI suites: a fake engine, and a scratch project to run.

`test_pass_cli.py` drives the CLI's options, refusals and reports, and `test_pass_cli_states.py`
what it does with a targets file's states. Both replace the engine with `FakeEngine`, which
returns scripted results, so they reach every exit path without a browser; the real engines are
driven in the live suites.

Entry point assumptions: imported first by a suite in this folder, it puts the tool's folder on
`sys.path`, and the CLI's own bootstrap adds the tool's `src/` and the shared libraries.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL_DIR))

import browser_pass  # noqa: E402 - the CLI, via the insert above
import pass_facts  # noqa: E402 - the tool's private src/, put on sys.path by the CLI's bootstrap
import pass_shift  # noqa: E402 - the tool's private src/, put on sys.path by the CLI's bootstrap
import pass_targets  # noqa: E402 - the tool's private src/, put on sys.path by the CLI's bootstrap
from pass_digest import Box  # noqa: E402 - the tool's private src/
from pass_motion import NO_MOTION, Motion  # noqa: E402 - the tool's private src/
from pass_report import RenderingResult  # noqa: E402 - the tool's private src/
from pass_targets import Rendering  # noqa: E402 - the tool's private src/

#: Two pages at one phone viewport, which the scratch project serves.
TARGETS = """pages = ["/", "/archive/"]
[[viewport]]
width = 390
height = 844
rotates = true
"""


def launch_path(script: Path) -> Path:
    """The path that runs `script`: itself on POSIX, a `.cmd` shim beside it on Windows.

    Windows honors no shebang, so CreateProcess refuses the script (`[WinError 193]`), and its
    PATH lookup tries only names carrying a PATHEXT extension, so a bare stand-in there is
    passed over for the real tool further down PATH. The shim runs it with this interpreter.
    """
    if sys.platform == "win32":  # pragma: only-on win32
        shim = script.with_name(f"{script.name}.cmd")
        shim.write_text(
            f'@"{sys.executable}" "%~dp0{script.name}" %*\n',
            encoding="utf-8",
            newline="\r\n",
        )
        return shim
    return script  # pragma: only-on posix


class EngineFailure(RuntimeError):
    """The fake engine's launch failure."""


@dataclass
class FakeEngine:
    """Stands in for `pass_engine`: records each call and returns scripted results.

    Every rendering of the page `/` reports `findings`, `motion` and `late_motion`; every other
    page is clean.
    """

    findings: dict[str, tuple[str, ...]] = field(default_factory=dict)
    verdicts: dict[str, str] = field(default_factory=dict)
    motion: Motion = NO_MOTION
    late_motion: Motion = NO_MOTION
    fail: bool = False
    crash: bool = False
    calls: list[dict[str, object]] = field(default_factory=list)
    EngineError: type[Exception] = EngineFailure

    def playwright_version(self) -> str:
        """A fixed version, as the report records it."""
        return "1.63.0"

    def run_matrix(
        self,
        renderings: Sequence[Rendering],
        base_url: str,
        *,
        timeout: float,
        prove: bool,
        scratch: Path,
        progress: Callable[[RenderingResult], None],
        states: Sequence[pass_targets.State] = (),
        require_csp: bool = False,
        missing_pages: Set[str] = frozenset(),
        isolation: object = None,
    ) -> tuple[dict[str, str], list[RenderingResult]]:
        """Record what was asked for, then report each rendering's scripted result."""
        self.calls.append(
            {
                "renderings": list(renderings),
                "states": list(states),
                "base_url": base_url,
                "timeout": timeout,
                "prove": prove,
                "require_csp": require_csp,
                "missing_pages": missing_pages,
                "isolation": isolation,
                "browsers": os.environ.get(pass_facts.BROWSERS_ENV),
                "scratch_exists": scratch.is_dir(),
            }
        )
        if self.fail:
            raise EngineFailure("cannot launch webkit: Executable doesn't exist")
        if self.crash:
            raise RuntimeError("the probe returned a record of the wrong shape")
        results = [
            RenderingResult(
                r,
                (),
                self.findings if r.page == "/" else {},
                self.verdicts,
                {"body>main:1": Box(True, 0.0, 0.0, 390.0, 100.0, 16.0)},
                pass_shift.not_measured(r) or pass_shift.LateShift(pass_shift.MEASURED),
                self.motion if r.page == "/" else NO_MOTION,
                late_motion=self.late_motion if r.page == "/" else NO_MOTION,
            )
            for r in renderings
        ]
        for result in results:
            progress(result)
        engines = dict.fromkeys(r.engine for r in renderings)
        return {engine: f"{engine}-version" for engine in engines}, results


class CliCase(unittest.TestCase):
    """A scratch project: a served tree, a targets file, and an isolated environment."""

    def setUp(self) -> None:
        """Write the served tree and `TARGETS`, point at scratch builds, fake the engine."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.site = self.root / "site"
        (self.site / "archive").mkdir(parents=True)
        (self.site / "index.html").write_text(
            "<p>home</p>\n", encoding="utf-8", newline="\n"
        )
        (self.site / "archive" / "index.html").write_text(
            "<p>archive</p>\n", encoding="utf-8", newline="\n"
        )
        self.targets = self.root / "targets.toml"
        self.targets.write_text(TARGETS, encoding="utf-8", newline="\n")
        self.browsers = self.root / "builds"
        env = mock.patch.dict(os.environ, {pass_facts.BROWSERS_ENV: str(self.browsers)})
        env.start()
        self.addCleanup(env.stop)
        self.engine = FakeEngine()
        loader = mock.patch.object(browser_pass, "_load_engine", lambda: self.engine)
        loader.start()
        self.addCleanup(loader.stop)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        """Run `main` with `args`, capturing both streams."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = browser_pass.main(["--targets", str(self.targets), *args])
        return code, out.getvalue(), err.getvalue()

    def only_run_dir(self) -> Path:
        """The single timestamped folder a run wrote under the default results folder."""
        (run_dir,) = (self.root / browser_pass.RESULTS_DIR_NAME).iterdir()
        return run_dir
