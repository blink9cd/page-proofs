"""Grouped suite: the documented upward-search bootstrap, run in real children.

It builds each layout the library can be found in on disk and runs a tool out of it, the only
way to prove the search rather than an already-loaded module.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# A sibling tool sits one level above the folder the bootstrap above reaches, so it
# needs its own insert. Directory and depth both come from the original suite.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above
import bounded_run  # noqa: E402 - reached by its own insert above


#: A generous ceiling for the black-box child: a hung CLI must fail this suite
#: rather than hang it, because unattended runs have no one to notice a hang.
_SUBPROCESS_TIMEOUT = 120


class TestPortableBootstrapResolution(unittest.TestCase):
    """The documented upward-search bootstrap must resolve in both shipped layouts.

    stdio_utf8.md tells a tool with no sibling copy to find this library by walking up from
    its own file, accepting ``<root>/utilities/<name>/`` (a shared-helpers folder) or
    ``<root>/stdio_utf8/`` (a copy placed directly in an ancestor folder).

    A tree normally holds only one of those, so a typo in the other would pass every other
    test here and fail only in a tree of the other shape. These build each layout on disk
    and run a tool out of it in a real subprocess: the only way to reach both branches, and
    the only way to prove the search rather than an already-loaded module.

    The search walks every ancestor up to the filesystem root, so it can also match a
    same-named folder planted above the tree: a shared temporary directory, or an enclosing
    checkout. The last three tests pin the two properties that keep such a folder harmless,
    one each: only the nearest match joins sys.path, and it joins at the end, where it
    cannot shadow the standard library or the tool's own modules.
    """

    #: The bootstrap exactly as stdio_utf8.md publishes it and the adopting tools carry it.
    BOOTSTRAP = (
        "sys.path += [\n"
        "    str(folder)\n"
        "    for ancestor in Path(__file__).resolve().parents\n"
        '    for folder in (ancestor / "utilities" / "stdio_utf8", ancestor / "stdio_utf8")\n'
        '    if (folder / "stdio_utf8.py").is_file()\n'
        "][:1]\n"
    )

    #: The document's second form, for a tool that also reaches a sibling folder of its own.
    NAMED_BOOTSTRAP = (
        "sys.path += [\n"
        "    nearest\n"
        '    for name in ("stdio_utf8", "sibling")\n'
        "    for nearest in [\n"
        "        str(folder)\n"
        "        for ancestor in Path(__file__).resolve().parents\n"
        '        for folder in (ancestor / "utilities" / name, ancestor / name)\n'
        '        if (folder / f"{name}.py").is_file()\n'
        "    ][:1]\n"
        "]\n"
    )

    def _plant_tool(
        self, root: Path, bootstrap: str = BOOTSTRAP, modules: tuple[str, ...] = ()
    ) -> Path:
        """Write a tool carrying *bootstrap*, nested as deeply as a real one.

        After the bootstrap the tool imports this library, each of *modules*, and then
        ``json``, a standard-library module imported late on purpose, because a folder the
        bootstrap wrongly admits ahead of the standard library would shadow it. It prints the
        file each came from, the library's encoding, and its final sys.path, one per line.
        """
        imported = ("stdio_utf8", *modules, "json")
        program = (
            "import sys\nfrom pathlib import Path\n\n"
            + bootstrap
            + "\n"
            + "".join(f"import {name}  # noqa: E402\n" for name in imported)
            + "".join(f'print("{name}", {name}.__file__)\n' for name in imported)
            + 'print("encoding", stdio_utf8.ENCODING)\n'
            + 'print(*("path " + entry for entry in sys.path), sep="\\n")\n'
        )
        tool_dir = root / "workflow_scripts" / "some_tool"
        tool_dir.mkdir(parents=True)
        tool = tool_dir / "some_tool.py"
        tool.write_text(program, encoding="utf-8", newline="\n")
        return tool

    def _run(self, tool: Path, root: Path) -> subprocess.CompletedProcess[str]:
        return bounded_run.run_bounded(
            [sys.executable, str(tool)],
            timeout=_SUBPROCESS_TIMEOUT,
            cwd=str(root),
        )

    def _report(self, tool: Path, root: Path) -> tuple[dict[str, str], list[str]]:
        """Run *tool*; return where each import came from, and its sys.path."""
        done = self._run(tool, root)
        self.assertEqual(done.returncode, 0, done.stderr)
        origins: dict[str, str] = {}
        path: list[str] = []
        for line in done.stdout.splitlines():
            key, _, value = line.partition(" ")
            if key == "path":
                path.append(value)
            else:
                origins[key] = value
        return origins, path

    @staticmethod
    def _plant_library(folder: Path) -> Path:
        """Copy this library into *folder*; return the folder."""
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(stdio_utf8.__file__), folder / "stdio_utf8.py")
        return folder

    def _plant_decoy(self, folder: Path) -> Path:
        """Plant a working copy of this library in *folder*, with a ``json.py`` beside it.

        A working copy, so a tool that wrongly resolves the library here still runs and the
        test observes what it imported rather than a crash.
        """
        self._plant_library(folder)
        (folder / "json.py").write_text(
            "# a planted decoy\n", encoding="utf-8", newline="\n"
        )
        return folder

    def _run_with_library_at(self, root: Path, library_parent: Path) -> str:
        """Plant the library under *library_parent*, run the tool, return its encoding."""
        self._plant_library(library_parent)
        origins, _path = self._report(self._plant_tool(root), root)
        return origins["encoding"]

    def test_resolves_in_the_repository_layout(self) -> None:
        """A ``utilities/`` folder holding ``stdio_utf8/``: the shared-helpers layout."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            found = self._run_with_library_at(root, root / "utilities" / "stdio_utf8")
        self.assertEqual(found, stdio_utf8.ENCODING)

    def test_resolves_in_the_delivered_archive_layout(self) -> None:
        """``<root>/stdio_utf8/`` with no utilities/ folder: the library placed directly in an ancestor.

        Where a utilities/ folder holds a copy, the first candidate always matches first, so only
        a synthetic tree without one exercises the second.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            found = self._run_with_library_at(root, root / "stdio_utf8")
            self.assertFalse(
                (root / "utilities").exists(), "this layout has no utilities/"
            )
        self.assertEqual(found, stdio_utf8.ENCODING)

    def test_a_farther_copy_never_reaches_sys_path(self) -> None:
        """Nearest-only: a copy planted above the tree is never on the path at all."""
        with tempfile.TemporaryDirectory() as td:
            above = Path(td).resolve()
            root = above / "checkout"
            real = self._plant_library(root / "utilities" / "stdio_utf8")
            decoy = self._plant_decoy(above / "stdio_utf8")
            origins, path = self._report(self._plant_tool(root), root)
        self.assertEqual(origins["stdio_utf8"], str(real / "stdio_utf8.py"))
        self.assertNotIn(str(decoy), path)

    def test_a_sole_planted_copy_cannot_shadow_the_standard_library(self) -> None:
        """Append: when the planted copy is the only match, it still comes after the stdlib.

        The search cannot tell a planted copy from a vendored one, so here it resolves the
        library itself from the planted folder: the tool asked for a library by that name
        and nothing nearer has one. What it must not do is let that folder's other modules
        replace anything the tool imports afterward.
        """
        with tempfile.TemporaryDirectory() as td:
            above = Path(td).resolve()
            root = above / "checkout"
            decoy = self._plant_decoy(above / "stdio_utf8")
            origins, _path = self._report(self._plant_tool(root), root)
        self.assertEqual(origins["stdio_utf8"], str(decoy / "stdio_utf8.py"))
        self.assertNotEqual(origins["json"], str(decoy / "json.py"))

    def test_each_name_takes_its_own_nearest_copy(self) -> None:
        """The several-names form: every name resolves, each from its own nearest match."""
        with tempfile.TemporaryDirectory() as td:
            above = Path(td).resolve()
            root = above / "checkout"
            real = self._plant_library(root / "utilities" / "stdio_utf8")
            sibling = root / "sibling"
            sibling.mkdir()
            (sibling / "sibling.py").write_text("", encoding="utf-8", newline="\n")
            decoys = [self._plant_decoy(above / "stdio_utf8"), above / "sibling"]
            decoys[1].mkdir()
            (decoys[1] / "sibling.py").write_text("", encoding="utf-8", newline="\n")
            tool = self._plant_tool(root, self.NAMED_BOOTSTRAP, ("sibling",))
            origins, path = self._report(tool, root)
        self.assertEqual(origins["stdio_utf8"], str(real / "stdio_utf8.py"))
        self.assertEqual(origins["sibling"], str(sibling / "sibling.py"))
        self.assertEqual([str(d) for d in decoys if str(d) in path], [])

    def test_missing_library_fails_loudly_rather_than_silently(self) -> None:
        """No copy anywhere -> ImportError naming the module, never a quiet skip.

        A tool that cannot establish its output encoding must say so: degrading silently
        would reintroduce exactly the defect this library exists to remove.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            done = self._run(self._plant_tool(root), root)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("ModuleNotFoundError", done.stderr)
        self.assertIn("stdio_utf8", done.stderr)
