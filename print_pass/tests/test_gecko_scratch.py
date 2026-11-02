"""Suite: removing the run's scratch folder: every ACL in it stripped, then the tree removed.

macOS makes some folders in a home it is pointed at with an everyone-deny-delete ACL, so a
plain recursive removal of the scratch fails on them. `remove_scratch` names every entry of the
tree to `/bin/chmod -h -N`, never following a symlink, then removes it. Every case but the last
runs both steps through fakes over a tree made under a temporary root; the last, on macOS, sets
such an ACL on folders this suite made and removes them for real.

Entry point assumptions: standalone, or discovered by `unittest discover`. The insert below
reaches the subject's private `src/` beside this folder, never the working directory.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_launch  # noqa: E402 - the tool's private src/, via the insert above
import gecko_scratch  # noqa: E402 - as above
from print_record import EngineError  # noqa: E402 - as above

#: The strip's command before the paths it is handed: macOS's own chmod, by its full path,
#: changing a symlink itself rather than what it points at, and removing every ACL entry.
_STRIP = ["/bin/chmod", "-h", "-N"]

#: The ACL macOS gives a folder it makes in a home it is pointed at (measured).
_DENY_DELETE = "group:everyone deny delete"

#: Bound on each real command a live case runs.
_TIMEOUT_S = 30.0


class FakeSteps:
    """The strip and the removal, recorded: each strip answers as scripted, then the removal."""

    def __init__(self) -> None:
        """Every strip succeeds and the removal too, until a case says otherwise."""
        self.calls: list[tuple[object, ...]] = []
        self.returncode = 0
        self.stderr = ""
        self.run_error: BaseException | None = None
        self.remove_error: OSError | None = None

    def run(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """Record the strip; answer with the scripted exit, or raise the scripted error."""
        self.calls.append(("run", list(argv), timeout))
        if self.run_error is not None:
            raise self.run_error
        return subprocess.CompletedProcess(list(argv), self.returncode, "", self.stderr)

    def remove(self, path: Path) -> None:
        """Record the removal; raise the scripted error, if any."""
        self.calls.append(("remove", path))
        if self.remove_error is not None:
            raise self.remove_error

    def stripped(self) -> list[str]:
        """Every path the strips were handed, in order, their command checked."""
        paths: list[str] = []
        for entry in self.calls:
            if entry[0] == "run":
                argv = entry[1]
                assert isinstance(argv, list)
                assert argv[: len(_STRIP)] == _STRIP, argv
                paths.extend(argv[len(_STRIP) :])
        return paths


class InterfaceTests(unittest.TestCase):
    """`gecko_launch` gives the removal as the very object `gecko_scratch` defines."""

    def test_it_is_the_same_object_there(self) -> None:
        self.assertIs(gecko_launch.remove_scratch, gecko_scratch.remove_scratch)

    def test_its_steps_are_the_bounded_runner_and_a_tree_removal(self) -> None:
        defaults = inspect.signature(gecko_scratch.remove_scratch).parameters
        self.assertIs(defaults["run"].default, gecko_scratch.bounded_run.run_bounded)
        self.assertIs(defaults["remove"].default, shutil.rmtree)


class RemoveScratchTests(unittest.TestCase):
    """Every entry is stripped, a symlink never followed, then the tree removed; or it says why."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.scratch = gecko_launch.make_scratch(self.base / "scratch")
        self.steps = FakeSteps()

    def remove(self, root: Path | None = None) -> None:
        """Remove the scratch, or `root`, through the fakes."""
        gecko_scratch.remove_scratch(
            self.scratch.root if root is None else root,
            run=self.steps.run,
            remove=self.steps.remove,
        )

    def refusal(self, root: Path | None = None) -> str:
        """Remove the scratch, or `root`, through the fakes, which must fail; its message."""
        with self.assertRaises(EngineError) as raised:
            self.remove(root)
        message = str(raised.exception)
        self.assertNotIn("\n", message)
        self.assertIn(str(self.scratch.root if root is None else root), message)
        return message

    def every_entry(self) -> set[str]:
        """The root and every entry under it, symlinks included and never followed."""
        found = {str(self.scratch.root)}
        for folder, folders, files in os.walk(self.scratch.root):
            found.update(os.path.join(folder, name) for name in (*folders, *files))
        return found

    def test_every_entry_is_stripped_once_then_the_tree_removed(self) -> None:
        (self.scratch.home / "Documents" / "note.txt").write_bytes(b"x")
        self.remove()
        stripped = self.steps.stripped()
        self.assertEqual(sorted(stripped), sorted(self.every_entry()))
        self.assertEqual(stripped[0], str(self.scratch.root))
        self.assertEqual(self.steps.calls[-1], ("remove", self.scratch.root))
        self.assertEqual(
            [entry[2] for entry in self.steps.calls if entry[0] == "run"],
            [gecko_scratch.STRIP_TIMEOUT_SECONDS],
        )

    def test_a_symlink_is_named_itself_and_never_followed(self) -> None:
        # Measured: `chmod -R -N` strips the ACL of a symlink's target outside the tree.
        outside = self.base / "outside"
        (outside / "inner").mkdir(parents=True)
        link = self.scratch.home / "link-out"
        link.symlink_to(outside, target_is_directory=True)
        self.remove()
        stripped = self.steps.stripped()
        self.assertIn(str(link), stripped)
        self.assertFalse(
            [path for path in stripped if not path.startswith(str(self.scratch.root))]
        )
        self.assertNotIn(str(link / "inner"), stripped)

    def test_a_large_tree_is_stripped_in_batches_before_the_removal(self) -> None:
        cache = self.scratch.local_app_data / "cache2"
        cache.mkdir()
        for index in range(gecko_scratch.PATHS_PER_STRIP):
            (cache / f"entry-{index}").write_bytes(b"")
        self.remove()
        runs = [entry for entry in self.steps.calls if entry[0] == "run"]
        self.assertEqual(len(runs), 2)
        for entry in runs:
            argv = entry[1]
            assert isinstance(argv, list)
            self.assertLessEqual(len(argv) - len(_STRIP), gecko_scratch.PATHS_PER_STRIP)
        self.assertEqual(sorted(self.steps.stripped()), sorted(self.every_entry()))
        self.assertEqual(self.steps.calls[-1], ("remove", self.scratch.root))

    def test_a_strip_that_fails_is_reported_and_nothing_removed(self) -> None:
        self.steps.returncode = 1
        self.steps.stderr = (
            "chmod: Failed to clear ACL on file x: Operation not permitted\n"
        )
        message = self.refusal()
        self.assertIn("/bin/chmod exited 1", message)
        self.assertIn("Failed to clear ACL", message)
        self.assertNotIn("remove", [entry[0] for entry in self.steps.calls])

    def test_a_strip_that_says_nothing_is_still_reported(self) -> None:
        self.steps.returncode = 1
        self.assertIn("nothing on stderr", self.refusal())

    def test_a_strip_that_cannot_run_is_reported_and_nothing_removed(self) -> None:
        self.steps.run_error = FileNotFoundError(2, "No such file or directory")
        self.assertIn("/bin/chmod could not run", self.refusal())
        self.assertNotIn("remove", [entry[0] for entry in self.steps.calls])

    def test_a_strip_past_its_timeout_is_reported_and_nothing_removed(self) -> None:
        self.steps.run_error = subprocess.TimeoutExpired("/bin/chmod", 30.0)
        message = self.refusal()
        self.assertIn("/bin/chmod hit its 30 s timeout", message)
        self.assertNotIn("remove", [entry[0] for entry in self.steps.calls])

    def test_a_removal_that_fails_is_reported(self) -> None:
        self.steps.remove_error = PermissionError(13, "Operation not permitted")
        self.assertIn("Operation not permitted", self.refusal())

    def test_a_root_that_is_not_there_is_reported_and_nothing_run(self) -> None:
        shutil.rmtree(self.scratch.root)
        self.assertIn("could not be examined", self.refusal())
        self.assertEqual(self.steps.calls, [])

    def test_a_symlinked_root_is_refused_and_nothing_run(self) -> None:
        # A walk from it, and each strip, would reach whatever it points at.
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "kept").write_bytes(b"x")
        for target in (outside, self.scratch.root):
            with self.subTest(target=target.name):
                link = self.base / f"link-to-{target.name}"
                link.symlink_to(target, target_is_directory=True)
                self.assertIn("is a symlink", self.refusal(link))
                self.assertEqual(self.steps.calls, [])
        self.assertTrue((outside / "kept").is_file())

    def test_a_root_that_is_not_a_folder_is_refused_and_nothing_run(self) -> None:
        plain = self.base / "plain"
        plain.write_bytes(b"x")
        self.assertIn("is not a folder", self.refusal(plain))
        self.assertEqual(self.steps.calls, [])

    def test_a_relative_root_is_refused_and_nothing_run(self) -> None:
        # Resolved against whatever the working directory is, it could name anything.
        for root in (Path("scratch"), Path(".")):
            with self.subTest(root=str(root)):
                self.assertIn("is not absolute", self.refusal(root))
                self.assertEqual(self.steps.calls, [])

    @unittest.skipIf(sys.platform == "win32", "Windows cannot make a folder unreadable")
    def test_a_tree_that_cannot_be_walked_is_reported_and_nothing_run(self) -> None:
        self.scratch.home.chmod(0)
        self.addCleanup(self.scratch.home.chmod, 0o700)
        self.assertIn("could not be walked", self.refusal())
        self.assertEqual(self.steps.calls, [])

    def test_no_path_can_pass_for_an_option_and_no_option_end_is_passed(self) -> None:
        # After `-N`, macOS's chmod takes `--` for a path in the working directory and exits
        # 1 for want of it (measured), so none is passed: each path is absolute instead.
        (self.scratch.home / "-R").mkdir()
        self.remove()
        argv = self.steps.calls[0][1]
        assert isinstance(argv, list)
        self.assertNotIn("--", argv)
        stripped = self.steps.stripped()
        self.assertIn(str(self.scratch.home / "-R"), stripped)
        for path in stripped:
            with self.subTest(path=path):
                self.assertTrue(os.path.isabs(path))
                self.assertFalse(path.startswith("-"))


def _acl(path: Path) -> str:
    """The ACL `ls -led` shows for `path` itself, joined on one line; empty when it has none.

    Run through the bounded runner: a capturing `subprocess.run` cannot bound a child whose
    own children hold its pipes.
    """
    listed = gecko_scratch.bounded_run.run_bounded(
        ["/bin/ls", "-led", str(path)], timeout=_TIMEOUT_S
    )
    if listed.returncode != 0:
        raise AssertionError(f"ls exited {listed.returncode}: {listed.stderr.strip()}")
    return " | ".join(line.strip() for line in listed.stdout.splitlines()[1:])


def _deny_delete(path: Path) -> None:
    """Give `path`, which this suite made, the ACL macOS gives a folder it makes in a home."""
    subprocess.run(
        ["/bin/chmod", "+a", _DENY_DELETE, str(path)], timeout=_TIMEOUT_S, check=True
    )


def _lift(path: Path) -> None:
    """Strip every ACL from `path`, which this suite made, so its cleanup can remove it.

    A path already gone (a passing case removed it) is skipped, so chmod says nothing.
    """
    if not os.path.lexists(path):
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["/bin/chmod", "-N", str(path)], timeout=_TIMEOUT_S, check=False)


@unittest.skipUnless(sys.platform == "darwin", "the ACLs and /bin/chmod -N are macOS's")
class LiveRemoveScratchTests(unittest.TestCase):
    """For real, on folders this suite made: the locked folders go, and nothing outside changes."""

    def test_a_home_holding_locked_folders_is_removed_and_a_linked_folder_kept(
        self,
    ) -> None:
        # Registered first, so it runs last: after each ACL below is lifted again.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        scratch = gecko_launch.make_scratch(base / "scratch")
        outside = base / "outside"
        outside.mkdir()
        locked = scratch.home / "Desktop"
        locked.mkdir()
        note = scratch.home / "Documents" / "note.txt"
        note.write_bytes(b"x")
        for path in (locked, note, outside):
            _deny_delete(path)
            self.addCleanup(_lift, path)
        (scratch.home / "link-out").symlink_to(outside, target_is_directory=True)
        # The precondition, so a pass is never vacuous: the ACL is there and it refuses.
        self.assertIn("deny delete", _acl(locked))
        with self.assertRaises(PermissionError):
            locked.rmdir()
        gecko_scratch.remove_scratch(scratch.root)
        self.assertFalse(scratch.root.exists())
        self.assertIn("deny delete", _acl(outside))


if __name__ == "__main__":
    unittest.main()
