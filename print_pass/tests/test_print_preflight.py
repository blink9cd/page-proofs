"""Grouped suite: print_preflight (where --out may go, poppler, the engine builds, Playwright).

Each case calls a check itself, apart from any run: `refuse_out` against the real git (and a
git whose answer is scripted, for what no real layout here gives), `missing_poppler`, where
the engine builds are (`browsers_dir`, which asks no repository), and Playwright's import
(`load_playwright`, whose absence is a refusal). And what installs every one of those
packages: each the repository's extras pin needs, read from its installed metadata, is pinned
in the same extra, so a resolver never picks one.
"""

from __future__ import annotations

import importlib.metadata
import json
import operator
import os
import re
import subprocess
import sys
import tomllib
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# ...and the tool's own folder, where the CLI the shared fixtures import is found.
sys.path.insert(0, str(_TOOL_DIR))
import print_preflight  # noqa: E402 - the tool's private src/, via the insert above

import bounded_run  # noqa: E402 - the shared runner, which print_preflight inserts
from _print_pass_fixtures import (  # noqa: E402 - this folder's shared fixtures
    init_repository,
    outside_folder,
    ready_host,
)

#: git's own setting for whether a repository search crosses into another filesystem.
_ACROSS_FILESYSTEMS = "GIT_DISCOVERY_ACROSS_FILESYSTEM"

#: A requirement's distribution name, and one clause of its marker naming a Python version.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_VERSION_CLAUSE = re.compile(
    r"python(?:_full)?_version\s*([<>=!]=?)\s*['\"]([\d.]+)['\"]"
)
_COMPARE = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
_COMPARE |= {"==": operator.eq, "!=": operator.ne}


def _canonical(requirement: str) -> str:
    """The distribution a requirement names, in its canonical form (PEP 503)."""
    match = _NAME.match(requirement.strip())
    if match is None:
        raise ValueError(f"no distribution named in {requirement!r}")
    return re.sub(r"[-_.]+", "-", match.group(0)).lower()


def _needed_at(requirement: str, floor: tuple[int, ...]) -> bool:
    """Whether a requirement applies with no extra on Python `floor`.

    A marker naming an extra is that extra's. A Python version clause is judged at the
    floor, by major and minor alone; a marker joined by `or`, or naming anything else (a
    platform, an implementation), counts as needed: the safe direction.
    """
    marker = requirement.partition(";")[2]
    if "extra" in marker:
        return False
    if " or " in marker:
        return True
    return all(
        _COMPARE[op](floor, tuple(int(part) for part in version.split(".")[:2]))
        for op, version in _VERSION_CLAUSE.findall(marker)
    )


class _OutCase(unittest.TestCase):
    """A case with a temp folder outside every repository, and an --out not made in it."""

    def setUp(self) -> None:
        """Make the folder, and name an --out that does not exist."""
        self.root = outside_folder(self)
        self.out = self.root / "out"


class OutRefusalTest(_OutCase):
    """--out must be new, under a folder that exists, outside every git work tree (real git)."""

    def test_an_out_that_exists_is_refused(self) -> None:
        self.out.mkdir()
        self.assertIn("already exists", print_preflight.refuse_out(self.out) or "")
        self.out.rmdir()
        self.out.write_text("x", encoding="utf-8", newline="\n")
        self.assertIn("already exists", print_preflight.refuse_out(self.out) or "")
        self.out.unlink()
        # A link to nothing is still in the way of the folder the run would make.
        self.out.symlink_to(self.root / "nowhere")
        self.assertIn("already exists", print_preflight.refuse_out(self.out) or "")

    def test_an_out_whose_parent_is_missing_is_refused(self) -> None:
        out = self.root / "absent" / "out"
        self.assertIn("does not exist", print_preflight.refuse_out(out) or "")

    def test_an_out_inside_a_git_work_tree_is_refused(self) -> None:
        repository = init_repository(self.root / "repo")
        (repository / "sub").mkdir()
        for out in (repository / "out", repository / "sub" / "out"):
            with self.subTest(out=out.name):
                self.assertIn(
                    "lies inside a git work tree", print_preflight.refuse_out(out) or ""
                )

    def test_the_refusal_gives_its_durable_reason(self) -> None:
        # Why a work tree is refused whatever its ignore rules say: not what any one
        # repository's ignore file holds today, which can change under it.
        repository = init_repository(self.root / "repo")
        reason = print_preflight.refuse_out(repository / "out") or ""
        self.assertIn(
            "a PDF records when it was made, so --out is refused inside any work tree by"
            " construction",
            reason,
        )

    def test_an_out_outside_every_repository_is_allowed(self) -> None:
        self.assertIsNone(print_preflight.refuse_out(self.out))

    def test_an_out_in_a_git_directory_is_outside_every_work_tree(self) -> None:
        repository = init_repository(self.root / "repo")
        self.assertIsNone(print_preflight.refuse_out(repository / ".git" / "out"))

    def test_an_inherited_git_directory_does_not_answer_for_the_folder(self) -> None:
        # A hook runs with GIT_DIR set; git would then take the asked folder as that
        # repository's work tree, and refuse every --out.
        repository = init_repository(self.root / "repo")
        with mock.patch.dict(os.environ, {"GIT_DIR": str(repository / ".git")}):
            self.assertIsNone(print_preflight.refuse_out(self.out))

    def test_git_is_asked_about_the_parent_within_its_bound(self) -> None:
        answer = subprocess.CompletedProcess([], 0, "false\n", "")
        with mock.patch.object(bounded_run, "run_bounded", return_value=answer) as run:
            self.assertIsNone(print_preflight.refuse_out(self.out))
        self.assertEqual(
            run.call_args.args[0],
            ["git", "-C", str(self.root), "rev-parse", "--is-inside-work-tree"],
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_every_git_call_searches_across_filesystems(self) -> None:
        # Else a search stops at a mount point, and a work tree above it goes unseen. An
        # inherited setting saying otherwise is dropped with every other GIT_ variable.
        answer = subprocess.CompletedProcess([], 0, "false\n", "")
        inherited = {_ACROSS_FILESYSTEMS: "0"}
        with (
            mock.patch.dict(os.environ, inherited),
            mock.patch.object(bounded_run, "run_bounded", return_value=answer) as run,
        ):
            print_preflight.refuse_out(self.out)
        self.assertEqual(run.call_count, 1)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["env"].get(_ACROSS_FILESYSTEMS), "1")


class UnverifiableOutTest(_OutCase):
    """A git that cannot say where --out lies is a refusal, not an answer."""

    def test_a_git_that_hangs_cannot_verify(self) -> None:
        hung = subprocess.TimeoutExpired(["git"], 10)
        with mock.patch.object(bounded_run, "run_bounded", side_effect=hung):
            reason = print_preflight.refuse_out(self.out)
        self.assertIn("git did not answer within 10 s", reason or "")

    def test_a_git_file_naming_a_folder_that_is_gone_cannot_verify(self) -> None:
        # A pruned linked worktree, or a submodule whose git folder moved, leaves a `.git`
        # FILE naming nothing. git calls its folder "not a git repository", yet the outer
        # work tree holds it, and would stage whatever the run wrote there.
        outer = init_repository(self.root / "outer")
        inner = outer / "sub"
        inner.mkdir()
        (inner / ".git").write_text(
            f"gitdir: {self.root / 'gone'}\n", encoding="utf-8", newline="\n"
        )
        self.assertIn(
            "lies inside a git work tree",
            print_preflight.refuse_out(outer / "out") or "",
        )
        reason = print_preflight.refuse_out(inner / "out") or ""
        self.assertIn("cannot verify that --out", reason)
        self.assertIn("git exited 128: fatal: not a git repository", reason)

    def test_the_outside_phrase_counts_only_where_it_opens_a_line(self) -> None:
        # A dangling `.git` file's fatal line names the path it points at, and a path can
        # hold any words, the "outside" phrase included: only a line OPENING with it is
        # git's answer that no parent is a repository.
        phrase = "fatal: not a git repository (or any of the parent directories)"
        dangling = subprocess.CompletedProcess(
            [], 128, "", f"fatal: not a git repository: /gone/{phrase}/.git\n"
        )
        with mock.patch.object(bounded_run, "run_bounded", return_value=dangling):
            reason = print_preflight.refuse_out(self.out) or ""
        self.assertIn("cannot verify that --out", reason)

    def test_a_search_stopped_at_a_mount_point_cannot_verify(self) -> None:
        # git's words when it stops at a filesystem boundary: a work tree may lie above it.
        stopped = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: not a git repository (or any parent up to mount point /mnt)\n"
            f"Stopping at filesystem boundary ({_ACROSS_FILESYSTEMS} not set).\n",
        )
        with mock.patch.object(bounded_run, "run_bounded", return_value=stopped):
            reason = print_preflight.refuse_out(self.out) or ""
        self.assertIn("cannot verify that --out", reason)
        self.assertIn("git exited 128: Stopping at filesystem boundary", reason)


class PopplerTest(unittest.TestCase):
    """Which poppler readers PATH lacks."""

    def test_every_reader_found_passes_the_check(self) -> None:
        with ready_host():
            self.assertIsNone(print_preflight.missing_poppler())


class BrowsersTest(unittest.TestCase):
    """Where the engine builds are: --browsers, the variable, or Playwright's own default."""

    def setUp(self) -> None:
        """A temp folder outside every repository."""
        self.root = outside_folder(self)

    def resolve(self, flag: Path | None, env: Mapping[str, str]) -> Path | None:
        """`print_preflight.browsers_dir`, with any subprocess it might start recorded."""
        run = mock.MagicMock(name="run_bounded")
        with mock.patch.object(bounded_run, "run_bounded", run):
            found = print_preflight.browsers_dir(flag, env)
        run.assert_not_called()
        return found

    def test_the_flag_wins(self) -> None:
        env = {print_preflight.BROWSERS_ENV: str(self.root / "named")}
        self.assertEqual(self.resolve(self.root, env), self.root)

    def test_the_variable_comes_next(self) -> None:
        env = {print_preflight.BROWSERS_ENV: str(self.root / "named")}
        self.assertEqual(self.resolve(None, env), self.root / "named")

    def test_with_neither_playwright_keeps_its_own_default(self) -> None:
        # An empty variable is no variable, and no repository is ever asked.
        for env in ({}, {print_preflight.BROWSERS_ENV: ""}):
            with self.subTest(env=env):
                self.assertIsNone(self.resolve(None, env))


class PlaywrightImportTest(unittest.TestCase):
    """Playwright is imported only for make, and its absence is a refusal naming the extra."""

    def test_a_missing_playwright_is_none(self) -> None:
        for name in ("playwright", "playwright.sync_api"):
            with self.subTest(name=name):
                missing = ModuleNotFoundError(f"No module named {name!r}", name=name)
                with mock.patch.object(
                    print_preflight, "import_sync_playwright", side_effect=missing
                ):
                    self.assertIsNone(print_preflight.load_playwright())

    def test_any_other_missing_module_is_raised(self) -> None:
        # A package whose name merely opens with Playwright's is a dependency, not it.
        for name in ("greenlet", "playwright_stealth", "playwrights.sync"):
            missing = ModuleNotFoundError(f"No module named {name!r}", name=name)
            with (
                self.subTest(name=name),
                mock.patch.object(
                    print_preflight, "import_sync_playwright", side_effect=missing
                ),
                self.assertRaises(ModuleNotFoundError),
            ):
                print_preflight.load_playwright()

    def test_the_real_import_is_playwrights_own_entry_point(self) -> None:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415 - the case's subject

        self.assertIs(print_preflight.import_sync_playwright(), sync_playwright)


class PinClosureTest(unittest.TestCase):
    """The repository's extras pin every package their pins need, at its Python floor."""

    def test_each_extra_pins_what_its_pins_need_at_the_floor(self) -> None:
        # The repository root's, the folder this tool's own folder sits in.
        with (_TOOL_DIR.parent / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        floor_text = str(project["requires-python"]).removeprefix(">=")
        floor = tuple(int(part) for part in floor_text.split("."))
        for extra, pins in project["optional-dependencies"].items():
            pinned = {_canonical(pin) for pin in pins}
            for pin in pins:
                # The closure read below is the installed package's, so it must be the pin's.
                version = pin.partition("==")[2]
                with self.subTest(extra=extra, pin=pin):
                    self.assertEqual(
                        importlib.metadata.version(_canonical(pin)), version
                    )
                for need in importlib.metadata.requires(_canonical(pin)) or ():
                    if _needed_at(need, floor):
                        with self.subTest(extra=extra, pin=pin, need=need):
                            self.assertIn(_canonical(need), pinned)

    def test_a_marker_is_judged_at_the_floor(self) -> None:
        cases = {
            "pyee": True,
            'pycparser; implementation_name != "PyPy"': True,
            "typing-extensions>=4.13.2 ; python_full_version < '3.11'": False,
            'trio; python_version > "3.6" and extra == "dev"': False,
            "exceptiongroup>=1.0.0 ; python_version >= '3.12'": True,
            "a; python_version < '3.11' or sys_platform == 'win32'": True,
        }
        for requirement, needed in cases.items():
            with self.subTest(requirement=requirement):
                self.assertIs(_needed_at(requirement, (3, 12)), needed)
        self.assertEqual(_canonical("Typing_Extensions>=4"), "typing-extensions")


class LockfileTest(unittest.TestCase):
    """The lockfile `npm ci --ignore-scripts` installs from needs no package's own script."""

    def test_no_locked_package_has_an_install_script(self) -> None:
        # The folder above this tool's holds the package.json its node suite runs from. The
        # documented install skips every script; this keeps that from skipping a needed one.
        lockfile = _TOOL_DIR.parent / "package-lock.json"
        packages = json.loads(lockfile.read_text(encoding="utf-8"))["packages"]
        scripted = [
            name for name, entry in packages.items() if "hasInstallScript" in entry
        ]
        self.assertEqual(scripted, [])


if __name__ == "__main__":
    unittest.main()
