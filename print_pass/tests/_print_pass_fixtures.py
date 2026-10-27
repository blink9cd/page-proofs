"""Shared by the suites of the CLI and of its checks and runs: `main` run, and folders outside git.

`run_main` runs `main` with its streams captured, and `RunCase` holds what a run needs: a
site, an --out, and a host whose poppler readers are all found. `run_without` runs the CLI
in a fresh interpreter that cannot import one package, and `packages_imported` names what
importing a module pulls in, so neither can be hidden by what a suite imported first. Every
case that imports these writes only under a temp folder `outside_folder` has first shown to
lie outside every git repository: the one place `--out` is allowed, and the one place a
case may run `git init`. No case touches the repository it lives in.

Entry point assumptions: imported by a suite that unittest discovery loaded with this folder
as its top-level directory, AFTER that suite's bootstrap has put the tool's folder and its
private `src/` on the path, which is where every other module imported below comes from.
The shared subprocess runner is found by the search below, whatever the suite imported
first.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from unittest import mock

# The shared tree-killing subprocess runner: a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place,
# and put near the front of the path, ahead of every installed distribution, so none of the
# same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run  # noqa: E402 - the shared runner, via the insert above
import pdf_privacy  # noqa: E402 - the tool's private src/, on the suite's path
import pdf_tools  # noqa: E402 - as above
import print_pass  # noqa: E402 - the CLI, in the tool's folder on the suite's path
from pdf_privacy import Identity  # noqa: E402 - the tool's private src/

#: Stands in for the host's identifiers in every run, so no case depends on, or names, this
#: machine. The home is no home-rooted shape at all.
IDENTITY: Final = Identity(
    login="someone", full_name=None, hostnames=("nowhere",), home="/" + "nohome"
)

#: What a placeholder PDF holds: the readers that would read it are replaced.
PDF_BYTES: Final = b"%PDF-1.4\n"

#: What a run case's served page holds, so a case can fetch it back through the url served.
PAGE: Final = "<p>served</p>\n"

#: The variables git reads before any folder it is pointed at; a fixture's git runs without.
_GIT_PREFIX: Final = "GIT_"

#: Bound on each real git call a fixture makes.
_GIT_TIMEOUT_SECONDS: Final = 30.0

#: The packages of the `print` extra's that only a run imports: the PDF readers, and
#: Playwright. The CLI's own import graph holds none of them.
MEASURE_PACKAGES: Final = frozenset({"pypdf", "pdfminer", "playwright"})

#: Bound on each fresh interpreter a case starts.
_PYTHON_TIMEOUT_SECONDS: Final = 60.0

#: A fresh interpreter's program: import one module from one folder, then name the
#: top-level package of every module it has imported, one per line.
_LIST_IMPORTS: Final = (
    "import importlib, sys\n"
    "folder, module = sys.argv[1:]\n"
    "sys.path.insert(0, folder)\n"
    "importlib.import_module(module)\n"
    "print('\\n'.join(sorted({name.partition('.')[0] for name in sys.modules})))\n"
)

#: A fresh interpreter's program: make one package unimportable, as a host without it
#: has it, then run the CLI as a script with the rest of the arguments.
_RUN_WITHOUT: Final = (
    "import runpy, sys\n"
    "blocked, cli, *argv = sys.argv[1:]\n"
    "sys.modules[blocked] = None\n"
    "sys.argv = [cli, *argv]\n"
    "runpy.run_path(cli, run_name='__main__')\n"
)


@dataclass(frozen=True)
class Outcome:
    """How one `main` run ended, and what it printed on each stream."""

    code: int | str | None
    stdout: str
    stderr: str


def run_main(*argv: str) -> Outcome:
    """Run `print_pass.main(argv)`, its streams captured; a parser's exit is its code."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code: int | str | None = print_pass.main(list(argv))
        except SystemExit as exit_:
            code = exit_.code
    return Outcome(code, stdout.getvalue(), stderr.getvalue())


def packages_imported(module: str, folder: Path) -> frozenset[str]:
    """Every top-level package importing `module` from `folder` imports, in a fresh interpreter.

    Fresh, since in this one a suite imported first would hide what `module` itself pulls
    in; and isolated (`-I`, no environment), so neither `PYTHONPATH` nor the working folder
    adds a folder of its own.
    """
    answer = bounded_run.run_bounded(
        [sys.executable, "-I", "-c", _LIST_IMPORTS, str(folder), module],
        timeout=_PYTHON_TIMEOUT_SECONDS,
        env={},
    )
    if answer.returncode != 0:
        raise AssertionError(f"importing {module} failed:\n{answer.stderr}")
    return frozenset(answer.stdout.split())


def run_without(
    blocked: str, *argv: str, env: Mapping[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    """Run the CLI with `argv` in a fresh, isolated interpreter that cannot import `blocked`."""
    cli = str(Path(print_pass.__file__).resolve())
    return bounded_run.run_bounded(
        [sys.executable, "-I", "-c", _RUN_WITHOUT, blocked, cli, *argv],
        timeout=_PYTHON_TIMEOUT_SECONDS,
        env=env,
        cwd=cwd,
    )


def git(*argv: str) -> subprocess.CompletedProcess[str]:
    """Run the real git with `argv`, no `GIT_` variable inherited, bounded.

    Through the shared runner, which kills the whole tree on expiry: a plain captured run's
    timeout kills only git itself, then waits on pipes a child of git may still hold.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_GIT_PREFIX)
    }
    return bounded_run.run_bounded(
        ["git", *argv], timeout=_GIT_TIMEOUT_SECONDS, env=env
    )


def outside_folder(case: unittest.TestCase) -> Path:
    """A new temp folder outside every git repository, removed after `case`.

    Skips `case` when the temp folder lies inside one, or git cannot say: `--out` is refused
    there, and a `git init` there would nest a repository inside another.
    """
    holder = tempfile.TemporaryDirectory(prefix="print_pass-")
    case.addCleanup(holder.cleanup)
    folder = Path(holder.name).resolve()
    try:
        answer = git("-C", str(folder), "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.TimeoutExpired) as error:
        case.skipTest(f"git cannot say where the temp folder lies: {error}")
    if answer.returncode == 0:
        case.skipTest(
            "the temp folder lies inside a git repository, where --out is refused"
        )
    return folder


def init_repository(folder: Path) -> Path:
    """`git init` a new repository at `folder`, which lies under an `outside_folder`."""
    folder.mkdir(parents=True)
    answer = git("init", "-q", str(folder))
    if answer.returncode != 0:
        raise AssertionError(f"git init failed: {answer.stderr}")
    return folder


def pdf_file(folder: Path, name: str) -> Path:
    """A placeholder PDF named `name` in `folder`, for a mode whose readers are replaced."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(PDF_BYTES)
    return path


@contextlib.contextmanager
def ready_host(missing: tuple[str, ...] = ()) -> Iterator[None]:
    """Every poppler reader found but `missing`, and `IDENTITY` read in place of the host's."""
    with (
        mock.patch.object(pdf_tools, "missing_tools", return_value=missing),
        mock.patch.object(pdf_privacy, "host_identity", return_value=IDENTITY),
    ):
        yield


class RunCase(unittest.TestCase):
    """A site to serve and an --out, under a temp folder outside every repository.

    Every poppler reader counts as found and `IDENTITY` stands in for the host's; the
    environment is restored after each case, since a make run points Playwright's
    variable at the engine builds it resolved.
    """

    def setUp(self) -> None:
        """Make the folder, its site and a folder of engine builds; guard the environment."""
        self.root = outside_folder(self)
        self.site = self.root / "site"
        self.site.mkdir()
        (self.site / "index.html").write_text(PAGE, encoding="utf-8", newline="\n")
        self.out = self.root / "out"
        self.builds = self.root / "builds"
        self.builds.mkdir()
        self.enterContext(mock.patch.dict(os.environ))
        self.enterContext(ready_host())

    def report(self) -> dict[str, object]:
        """The report.json the run wrote."""
        loaded: dict[str, object] = json.loads(
            (self.out / "report.json").read_text(encoding="utf-8")
        )
        return loaded
