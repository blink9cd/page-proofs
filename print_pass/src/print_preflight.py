"""What a run checks before it makes anything: where --out may go, poppler, the builds, Playwright.

Each check answers and prints nothing. A refusal comes back as the sentence the CLI prints
before it exits 2; a lookup that finds nothing comes back as None.

- `refuse_out`: `--out` must be new, under a folder that exists, and outside every git work
  tree. A PDF records when it was made, so `--out` is refused inside any work tree by
  construction, whatever its ignore rules say. git is asked, and only its two answers for
  "outside" allow the run: `false` (a `.git` folder is no work tree) and its fatal "not a
  git repository (or any of the parent directories)". Any other answer (git missing,
  hung, refusing the folder, a `.git` file naming nothing, or saying anything else) is
  no answer, and refuses the run rather than letting it write where git could not see.
- `missing_poppler`: which poppler readers `PATH` lacks.
- `browsers_dir`: where the pinned engine builds are, when something names a folder.
- `load_playwright`: Playwright's entry point, or None when it is not installed.

Entry point assumptions: git and poppler are found on `PATH`, if at all, and every git call
runs through the shared `bounded_run`, bounded. Playwright is imported here only, and only
when `load_playwright` is called.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Final

# The shared tree-killing subprocess runner: a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place,
# and put near the front of the path, ahead of every installed distribution, so none of the
# same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run
import pdf_tools
from print_playwright import PlaywrightLike

#: Playwright's own variable naming where its engine builds are.
BROWSERS_ENV: Final = "PLAYWRIGHT_BROWSERS_PATH"
#: The variable's value for Playwright's hermetic mode: builds inside its own package. It is a
#: value, not a folder, so it reaches Playwright as given and is never made absolute.
HERMETIC_BROWSERS: Final = "0"

#: Bound on the git call asking where --out lies.
GIT_TIMEOUT_SECONDS: Final = 10

#: git's answers. `true` inside a work tree; `false` inside a `.git` folder, which is no
#: work tree; and for a folder in no repository at all, a fatal exit whose line opens
#: with these words under the C locale: the search ran out of parents. The same fatal exit
#: opens "not a git repository" alone for a `.git` FILE naming a folder that is gone (a
#: pruned linked worktree, a moved submodule), and "... (or any parent up to mount point"
#: for a search that stopped at a filesystem boundary. Both can lie inside a work tree, so
#: neither is this answer, and neither allows the run.
_INSIDE: Final = "true"
_OUTSIDE_WORK_TREE: Final = "false"
_GIT_FATAL: Final = 128
_NO_PARENT_IS_A_REPOSITORY: Final = (
    "fatal: not a git repository (or any of the parent directories)"
)

#: What every git call runs without, and with: see `git_env`.
_GIT_PREFIX: Final = "GIT_"
_GIT_SETTINGS: Final = {"LC_ALL": "C", "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1"}

#: The top-level package whose absence is Playwright's own, not a dependency's.
_PLAYWRIGHT: Final = "playwright"

type SyncPlaywright = Callable[[], AbstractContextManager[PlaywrightLike]]


def refuse_out(out: Path) -> str | None:
    """Why `out` may not be written, or None when it may: new, its parent a folder, no repo."""
    if os.path.lexists(out):
        return f"--out {out} already exists: name a folder that does not"
    parent = out.absolute().parent
    if not parent.is_dir():
        return f"--out {out}: its parent folder {parent} does not exist"
    return _refuse_work_tree(out, parent)


def _refuse_work_tree(out: Path, parent: Path) -> str | None:
    """Why `out` may not be written under `parent`, as git answers; None when outside."""
    unverified = f"cannot verify that --out {out} lies outside every git work tree"
    argv = ["git", "-C", str(parent), "rev-parse", "--is-inside-work-tree"]
    try:
        answer = bounded_run.run_bounded(
            argv, timeout=GIT_TIMEOUT_SECONDS, env=git_env()
        )
    except subprocess.TimeoutExpired:
        return f"{unverified}: git did not answer within {GIT_TIMEOUT_SECONDS} s"
    except OSError as error:
        reason = error.strerror or type(error).__name__
        return f"{unverified}: git could not be run ({reason})"
    said = answer.stdout.strip()
    if answer.returncode == 0 and said == _INSIDE:
        return (
            f"--out {out} lies inside a git work tree: a PDF records when it was made, "
            "so --out is refused inside any work tree by construction; name a folder "
            "outside every repository"
        )
    if answer.returncode == 0 and said == _OUTSIDE_WORK_TREE:
        return None
    if answer.returncode == _GIT_FATAL and _ran_out_of_parents(answer.stderr):
        return None
    detail = (answer.stderr.strip() or said or "nothing").splitlines()[-1]
    return f"{unverified}: git exited {answer.returncode}: {detail}"


def _ran_out_of_parents(stderr: str) -> bool:
    """Whether git's fatal line says its search found no repository in any parent.

    Matched as a line's opening, not anywhere in the text: the words a dangling `.git`
    file draws are followed by the path it names, and a path can hold any words.
    """
    return any(
        line.startswith(_NO_PARENT_IS_A_REPOSITORY) for line in stderr.splitlines()
    )


def git_env() -> dict[str, str]:
    """This process's environment for a git call: no `GIT_` variable, and git's settings.

    A `GIT_DIR` or `GIT_WORK_TREE` inherited from a hook would answer for that repository,
    not the folder asked about. The C locale keeps git's refusal in the words matched, and
    searching across filesystems keeps a mount point from hiding a work tree above it.
    """
    kept = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_GIT_PREFIX)
    }
    return {**kept, **_GIT_SETTINGS}


def missing_poppler() -> str | None:
    """Which poppler readers PATH lacks, as a refusal; None when every one is found."""
    missing = pdf_tools.missing_tools()
    if not missing:
        return None
    return f"poppler's {', '.join(missing)} not found on PATH: install poppler"


def browsers_dir(flag: Path | None, env: Mapping[str, str]) -> Path | None:
    """Where the pinned engine builds are, or None to leave Playwright its own default.

    `--browsers`, else `$PLAYWRIGHT_BROWSERS_PATH` (empty is unset). With neither, the
    builds are wherever a plain `python -m playwright install` put them, which Playwright
    finds itself, and a build missing there fails its launch.
    """
    if flag is not None:
        return flag
    named = env.get(BROWSERS_ENV)
    return Path(named) if named else None


def import_sync_playwright() -> SyncPlaywright:
    """Playwright's synchronous entry point, imported only when a make run needs it."""
    # Deferred: read and compare need no Playwright, and make refuses cleanly without it.
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 - deferred, as above

    return sync_playwright


def load_playwright() -> SyncPlaywright | None:
    """Playwright's entry point, or None when Playwright itself is not installed.

    Any other module missing is raised: it is not the refusal this names.
    """
    try:
        return import_sync_playwright()
    except ModuleNotFoundError as error:
        # Playwright's own submodule is missing when its package is: `playwright.sync_api`.
        if (error.name or "").partition(".")[0] == _PLAYWRIGHT:
            return None
        raise
