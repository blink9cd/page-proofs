"""`commit_facts`: the commit a scope is at, whether the scope differs, or that git cannot say.

Two halves. The repository tests build a throwaway repository with the real `git`, isolated
from the host's configuration and from any variable pointing git elsewhere, so nothing here
reads or changes a real checkout. The failure tests hand `read` a fake `git` -- a real process,
so the subprocess boundary is crossed for real -- because a `status` that fails, or a git that
hangs, is not a state a healthy repository can be put in.

Entry point assumptions: standalone, or discovered by a test runner. The inserts below reach
the subject beside this file and `bounded_run/`, never the working directory.
"""

from __future__ import annotations

import errno
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import commit_facts  # noqa: E402 - import follows the sys.path insert above

# Reachable because importing the subject above put its folder on sys.path.
import bounded_run  # noqa: E402 - see the comment above

_GIT = shutil.which("git")

#: Bound on each git call a test makes.
_TIMEOUT = 30.0

#: Bound on a call to the fake git that hangs: long enough to start it, short enough to wait.
_HANG_TIMEOUT = 1.0

#: Variables that would point git at a repository other than the one under test -- set, for
#: instance, when a suite runs inside a git hook.
_GIT_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")

#: Keeps the host's own git configuration (and any hooks it names) out of the throwaway repos.
_ISOLATED_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

#: The commit the fake git reports as HEAD.
_FAKE_COMMIT = "0123456789abcdef0123456789abcdef01234567"

#: A fake git: HEAD resolves, then `status` fails, or every call hangs, as `FAKE_GIT_MODE` says.
_FAKE_GIT = f"""#!{sys.executable}
import os, sys, time
if os.environ.get("FAKE_GIT_MODE") == "hang":
    while True:
        time.sleep(1)
if "rev-parse" in sys.argv:
    print("{_FAKE_COMMIT}")
    sys.exit(0)
sys.stderr.write("fatal: unable to read the index\\n")
sys.exit(128)
"""


def launch_path(script: Path) -> Path:
    """The path that runs `script`: itself on POSIX, a `.cmd` shim beside it on Windows.

    Windows honors no shebang, so CreateProcess refuses the script (`[WinError 193]`). The
    shim runs it with this interpreter instead.
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


class _ScratchCase(unittest.TestCase):
    """A scratch folder holding a `repo/src` tree, and an environment git cannot escape."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.repo = self.root / "repo"
        self.src = self.repo / "src"
        self.src.mkdir(parents=True)
        (self.src / "index.html").write_text("home\n", encoding="utf-8", newline="\n")
        cleaned = {k: v for k, v in os.environ.items() if k not in _GIT_REDIRECTS}
        patcher = mock.patch.dict(
            os.environ, {**cleaned, **_ISOLATED_GIT_ENV}, clear=True
        )
        patcher.start()
        self.addCleanup(patcher.stop)


@unittest.skipIf(_GIT is None, "git is not installed")
class RepositoryTests(_ScratchCase):
    """Facts read from a real, throwaway repository, with `src/` as the scope."""

    def setUp(self) -> None:
        super().setUp()
        for step in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
            self.git(*step)

    def git(self, *args: str) -> str:
        """Run git in the repository with an identity and no hooks; return its stdout."""
        assert _GIT is not None
        done = bounded_run.run_bounded(
            [
                _GIT,
                "-c",
                f"core.hooksPath={self.root / 'no-hooks'}",
                "-c",
                "user.name=fixture",
                "-c",
                "user.email=fixture@example.com",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=self.repo,
            timeout=_TIMEOUT,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def read(self, scope: Path) -> commit_facts.CommitFacts:
        """Commit facts for `scope`, with the real git."""
        return commit_facts.read(scope, git=_GIT, timeout=_TIMEOUT)

    def test_a_clean_scope_records_its_head(self) -> None:
        self.assertEqual(
            self.read(self.src),
            commit_facts.CommitFacts(sha=self.git("rev-parse", "HEAD"), dirty=False),
        )

    def test_an_edit_inside_the_scope_is_dirty(self) -> None:
        (self.src / "index.html").write_text("edited\n", encoding="utf-8", newline="\n")
        facts = self.read(self.src)
        self.assertIs(facts.dirty, True)
        self.assertEqual(facts.sha, self.git("rev-parse", "HEAD"))

    def test_a_change_outside_the_scope_is_not_dirty(self) -> None:
        (self.repo / "notes.txt").write_text(
            "scratch\n", encoding="utf-8", newline="\n"
        )
        self.assertIs(self.read(self.src).dirty, False)

    def test_the_same_change_is_dirty_when_the_scope_holds_it(self) -> None:
        (self.repo / "notes.txt").write_text(
            "scratch\n", encoding="utf-8", newline="\n"
        )
        self.assertIs(self.read(self.repo).dirty, True)

    def test_a_scope_outside_any_repository_is_unknown_and_nothing_went_wrong(
        self,
    ) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        self.assertEqual(self.read(outside), commit_facts.UNKNOWN)


class UnknownTests(_ScratchCase):
    """Git absent, or present and failing: never a clean tree, and a reason when it failed."""

    def fake_git(self) -> str:
        """Write the fake git into the scratch folder; returns the path that launches it."""
        fake = self.root / "git"
        fake.write_text(_FAKE_GIT, encoding="utf-8", newline="\n")
        fake.chmod(0o755)
        return str(launch_path(fake))

    def test_without_git_nothing_is_known_and_nothing_went_wrong(self) -> None:
        self.assertEqual(
            commit_facts.read(self.src, git=None, timeout=_TIMEOUT),
            commit_facts.UNKNOWN,
        )
        self.assertIsNone(commit_facts.UNKNOWN.problem)

    def test_a_failed_status_is_unknown_rather_than_clean(self) -> None:
        facts = commit_facts.read(self.src, git=self.fake_git(), timeout=_TIMEOUT)
        self.assertIsNone(facts.sha)
        self.assertIsNone(facts.dirty)
        self.assertEqual(
            facts.problem, "git status exited 128: fatal: unable to read the index"
        )

    def test_a_git_that_hangs_is_unknown_and_says_it_timed_out(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_GIT_MODE": "hang"}):
            facts = commit_facts.read(
                self.src, git=self.fake_git(), timeout=_HANG_TIMEOUT
            )
        self.assertEqual((facts.sha, facts.dirty), (None, None))
        self.assertIn("timed out", facts.problem or "")

    def test_a_git_that_cannot_start_is_unknown_and_says_why(self) -> None:
        missing = str(self.root / "no-such-git")
        facts = commit_facts.read(self.src, git=missing, timeout=_TIMEOUT)
        self.assertEqual((facts.sha, facts.dirty), (None, None))
        self.assertIn("no-such-git", facts.problem or "")

    def test_a_start_failure_naming_no_file_still_names_the_git(self) -> None:
        # Windows' CreateProcess raises its start failure with no filename ("[WinError 2] The
        # system cannot find the file specified"), where POSIX's exec names the file; the
        # reason must say WHICH git could not start on both.
        git = "C:\\tools\\no-such-git.exe"
        unnamed = OSError(errno.ENOENT, "The system cannot find the file specified")
        with mock.patch.object(bounded_run, "run_bounded", side_effect=unnamed):
            facts = commit_facts.read(self.src, git=git, timeout=_TIMEOUT)
        self.assertEqual((facts.sha, facts.dirty), (None, None))
        self.assertIn(repr(git), facts.problem or "")


if __name__ == "__main__":
    unittest.main()
