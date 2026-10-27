"""Grouped suite: where the engine builds live by default.

The commit a run measured is read by the shared `commit_facts`, and tested beside it.

Every case hands over its own environment, platform and home folder, so nothing here reads the
host's own.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_facts  # noqa: E402 - the tool's private src/, via the insert above

#: A home folder no test reads or writes: each only joins paths onto it.
_HOME = Path("test-home")


class DefaultBuildsTests(unittest.TestCase):
    """Playwright's variable, else the folder a plain `playwright install` fills."""

    def default(self, env: dict[str, str], platform: str) -> Path | None:
        """The default for `env` on `platform`, with the test's home folder."""
        return pass_facts.default_browsers_path(env, platform=platform, home=_HOME)

    def test_playwrights_own_variable_wins_when_set(self) -> None:
        chosen = "/elsewhere/builds"
        env = {pass_facts.BROWSERS_ENV: chosen, "XDG_CACHE_HOME": "/cache"}
        self.assertEqual(self.default(env, "linux"), Path(chosen))

    def test_an_empty_variable_is_not_a_choice(self) -> None:
        env = {pass_facts.BROWSERS_ENV: ""}
        self.assertEqual(
            self.default(env, "darwin"),
            _HOME / "Library" / "Caches" / "ms-playwright",
        )

    def test_macos_keeps_them_in_the_users_caches(self) -> None:
        self.assertEqual(
            self.default({}, "darwin"), _HOME / "Library" / "Caches" / "ms-playwright"
        )

    def test_linux_keeps_them_in_the_users_cache(self) -> None:
        self.assertEqual(self.default({}, "linux"), _HOME / ".cache" / "ms-playwright")

    def test_linux_follows_xdg_cache_home(self) -> None:
        env = {"XDG_CACHE_HOME": "/cache"}
        self.assertEqual(self.default(env, "linux"), Path("/cache") / "ms-playwright")

    def test_windows_keeps_them_in_local_app_data(self) -> None:
        self.assertEqual(
            self.default({}, "win32"), _HOME / "AppData" / "Local" / "ms-playwright"
        )

    def test_windows_follows_localappdata(self) -> None:
        env = {"LOCALAPPDATA": "/local"}
        self.assertEqual(self.default(env, "win32"), Path("/local") / "ms-playwright")

    def test_a_platform_playwright_does_not_install_on_has_no_default(self) -> None:
        self.assertIsNone(self.default({}, "aix"))

    def test_no_home_folder_leaves_no_default_where_one_is_needed(self) -> None:
        self.assertIsNone(
            pass_facts.default_browsers_path({}, platform="darwin", home=None)
        )

    def test_a_named_cache_folder_needs_no_home_folder(self) -> None:
        env = {"XDG_CACHE_HOME": "/cache"}
        self.assertEqual(
            pass_facts.default_browsers_path(env, platform="linux", home=None),
            Path("/cache") / "ms-playwright",
        )


class HomeFolderTests(unittest.TestCase):
    """The home folder, or None where the platform cannot name one."""

    def test_the_home_folder_is_the_users(self) -> None:
        self.assertEqual(pass_facts.home_folder(), Path.home())

    def test_no_home_folder_is_none_rather_than_an_error(self) -> None:
        with mock.patch.object(Path, "home", side_effect=RuntimeError("no home")):
            self.assertIsNone(pass_facts.home_folder())


if __name__ == "__main__":
    unittest.main()
