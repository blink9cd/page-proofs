"""Which commit a tree is at, and whether the tree differs from it, or that git cannot say.

A measurement compares with a later one only when both name the code they measured, so a
tool that measures a site records this beside its numbers, for the tree it served:
`lighthouse_run` the `--serve` folder, else the project's `src/`. This module is the one
reading of it.
The tree it is asked about (its scope) is the caller's to name, and each caller maps the
result onto its own report's field names, which predate this module and are left as they are.

The answer is a pair, and it is only worth anything whole: a commit names the code measured
only alongside whether the tree differed from it. So whenever git cannot answer BOTH halves,
both are `None`: never a commit beside a guess, which every report would print as a bare
commit and so as a clean tree. That covers three cases:

- git is absent;
- the scope is outside any repository (`rev-parse` fails), an ordinary case, such as a
  built copy in a scratch folder;
- git is there and fails: `status` exits non-zero, or either call times out or cannot start.

The third case once read as a CLEAN tree: a failed `status` prints nothing, and nothing is what
a clean tree prints. It is not an error either, because the measurement beside it is still
worth keeping and the report says plainly that the commit is unknown. It is not silent: the
result carries `problem`, the reason, for the caller to print. The first two carry none,
because nothing went wrong.

Entry point assumptions: a library, imported by the measuring tools; there is no CLI here.
Callers put this folder on `sys.path` by its fixed path beside them. It needs `bounded_run/`,
the folder beside this one, reached the same way.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# bounded_run is the folder beside this one, at the repository root. It goes second, just
# after the entry an importer puts first, and so ahead of every installed distribution: none of
# the same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "bounded_run"))

import bounded_run


@dataclass(frozen=True)
class CommitFacts:
    """The commit a scope is at and whether the scope differs from it; `None` when unknown.

    `problem` says why git could not answer when it was there to ask and failed; it is `None`
    when git answered, and when it was simply absent or the scope is outside any repository.
    """

    sha: str | None
    dirty: bool | None
    problem: str | None = None


#: Nothing known, and nothing wrong: git is absent, or the scope is outside any repository.
UNKNOWN: Final = CommitFacts(sha=None, dirty=None)


def read(scope: Path, *, git: str | None, timeout: float) -> CommitFacts:
    """The HEAD commit of the repository holding `scope`, and whether `scope` differs from it.

    `scope` is the folder whose uncommitted edits count, and must exist. Each git call is
    bounded by `timeout` seconds. Never raises for git's sake: see the module docstring for
    what each unknown means.
    """
    if git is None:
        return UNKNOWN
    try:
        head = bounded_run.run_bounded(
            [git, "rev-parse", "HEAD"], cwd=scope, timeout=timeout
        )
        if head.returncode != 0:
            return UNKNOWN
        status = bounded_run.run_bounded(
            [git, "status", "--porcelain", "--", "."], cwd=scope, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return _cannot_say(f"git did not answer: {_naming_git(error, git)}")
    if status.returncode != 0:
        said = status.stderr.strip() or "no message"
        return _cannot_say(f"git status exited {status.returncode}: {said}")
    return CommitFacts(sha=head.stdout.strip(), dirty=bool(status.stdout.strip()))


def _naming_git(error: OSError | subprocess.TimeoutExpired, git: str) -> str:
    """`error` as text that names the git which failed.

    A timeout quotes its whole command and a POSIX start failure its file, but Windows'
    CreateProcess raises a start failure that carries no filename, so that one is given the
    path in the form POSIX prints it.
    """
    if isinstance(error, OSError) and error.filename is None:
        return f"{error}: {git!r}"
    return str(error)


def _cannot_say(problem: str) -> CommitFacts:
    """Both halves unknown, because git was there and failed, and why."""
    return CommitFacts(sha=None, dirty=None, problem=problem)
