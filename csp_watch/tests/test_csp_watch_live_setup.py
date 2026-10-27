"""The live suite's setup where an engine build is missing: it needs no engine to check.

The setup skips, naming where it looked and the install command, in the skip and in one line on
stderr, so the suite runs on a machine with Playwright and no builds. With
``PAGE_PROOFS_REQUIRE_BROWSERS=1`` it fails instead: a gate reads a skipped module as a pass,
with none of the three engines' proof run. Where it looks is checked here too, for each platform,
with an environment and a home folder of the test's own.

Entry point assumptions: discovered by ``test_csp_watch.py`` with this folder as the top-level
directory, so the live suite's module imports by its own name.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import playwright
import test_csp_watch_live

#: A home folder no test reads or writes: each only joins paths onto it.
_HOME = Path("test-home")

#: The opt-in's variable, named once for the tests that set it.
_REQUIRE = test_csp_watch_live._REQUIRE_BROWSERS_ENV


def _folder_holding(case: unittest.TestCase, *names: str) -> Path:
    """A scratch folder, removed when `case` ends, holding an empty folder of each of `names`."""
    scratch = tempfile.TemporaryDirectory()
    case.addCleanup(scratch.cleanup)
    for name in names:
        Path(scratch.name, name).mkdir(parents=True)
    return Path(scratch.name)


class LiveSuiteSetupTest(unittest.TestCase):
    """The live suite skips, or with the opt-in fails, where no engine build is installed."""

    def setup_without_builds(
        self, empty: str, required: str | None
    ) -> tuple[BaseException, str]:
        """What the live suite's setup raises with `empty` as the builds folder, and its stderr."""
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": empty}),
            contextlib.redirect_stderr(stderr),
            self.assertRaises((RuntimeError, unittest.SkipTest)) as raised,
        ):
            os.environ.pop(_REQUIRE, None)
            if required is not None:
                os.environ[_REQUIRE] = required
            test_csp_watch_live.setUpModule()
        return raised.exception, stderr.getvalue()

    def expected(self, empty: str) -> str:
        """The one line the setup gives with `empty` as the builds folder."""
        return (
            rf"^the pinned engine builds are not installed \(looked in {re.escape(empty)} "
            r"for chromium, firefox, webkit\); "
            rf"`{re.escape(test_csp_watch_live._INSTALL_COMMAND)}` installs them$"
        )

    def test_with_no_engine_build_the_live_suite_skips_naming_the_install(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as empty:
            raised, _ = self.setup_without_builds(empty, None)
        self.assertIsInstance(raised, unittest.SkipTest)
        self.assertRegex(str(raised), self.expected(empty))

    def test_a_skip_writes_its_reason_to_stderr_once(self) -> None:
        # unittest shows a skip's reason only when run verbosely, so a plain run would show a
        # bare count of skips: the line on stderr is what names the install command there.
        with tempfile.TemporaryDirectory() as empty:
            raised, stderr = self.setup_without_builds(empty, None)
        self.assertEqual(stderr, f"{test_csp_watch_live._SKIP_NOTICE}{raised}\n")

    def test_an_empty_opt_in_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            raised, _ = self.setup_without_builds(empty, "")
        self.assertIsInstance(raised, unittest.SkipTest)

    def test_with_the_opt_in_the_live_suite_fails_at_setup_rather_than_skips(
        self,
    ) -> None:
        # A skip is a passing run to every gate, so with the opt-in only the error will do.
        with tempfile.TemporaryDirectory() as empty:
            raised, stderr = self.setup_without_builds(empty, "1")
        self.assertIsInstance(raised, RuntimeError)
        self.assertRegex(str(raised), self.expected(empty))
        self.assertEqual(stderr, "")

    def test_any_other_opt_in_value_is_refused_naming_the_accepted_values(
        self,
    ) -> None:
        # A value meant as on, read as off, would let a run pass with the engines unmeasured.
        for value in ("0", "yes", "true"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as empty:
                raised, _ = self.setup_without_builds(empty, value)
                self.assertIsInstance(raised, RuntimeError)
                self.assertRegex(
                    str(raised),
                    rf"^{_REQUIRE} is '{value}': set it to 1 .*, or leave it unset or empty$",
                )

    def test_a_refused_opt_in_value_fails_even_with_every_build_installed(
        self,
    ) -> None:
        with (
            mock.patch.dict(os.environ, {_REQUIRE: "yes"}),
            mock.patch.object(test_csp_watch_live, "_engines_missing", return_value=[]),
            mock.patch.object(
                test_csp_watch_live.loopback_site,
                "serve_directory",
                side_effect=AssertionError("served"),
            ),
            self.assertRaisesRegex(RuntimeError, f"^{_REQUIRE} is 'yes'"),
        ):
            test_csp_watch_live.setUpModule()

    def test_a_builds_folder_that_is_not_there_lacks_every_engine(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            absent = Path(scratch, "absent")
            self.assertEqual(
                test_csp_watch_live._engines_missing(absent),
                ["chromium", "firefox", "webkit"],
            )
        self.assertEqual(
            test_csp_watch_live._engines_missing(None),
            ["chromium", "firefox", "webkit"],
        )

    def test_a_full_chromium_build_is_not_the_headless_one(self) -> None:
        builds = _folder_holding(self, "chromium-1243", "firefox-1543", "webkit-2359")
        self.assertEqual(test_csp_watch_live._engines_missing(builds), ["chromium"])


class HermeticBuildsTest(unittest.TestCase):
    """`PLAYWRIGHT_BROWSERS_PATH=0`: Playwright's own mode keeping the builds in its package."""

    def test_hermetic_mode_looks_inside_the_playwright_package(self) -> None:
        package = _folder_holding(self, "driver/package/.local-browsers/webkit-2359")
        looked_in, missing = test_csp_watch_live._missing_builds(Path("0"), package)
        self.assertEqual(looked_in, package / "driver" / "package" / ".local-browsers")
        self.assertEqual(missing, ["chromium", "firefox"])

    def test_hermetic_mode_without_that_package_layout_reports_nothing_missing(
        self,
    ) -> None:
        # Where the builds are cannot be known, so the launch, not a skip, says what is wrong.
        package = _folder_holding(self)
        self.assertEqual(
            test_csp_watch_live._missing_builds(Path("0"), package), (Path("0"), [])
        )

    def test_any_other_folder_is_looked_in_as_named(self) -> None:
        builds = _folder_holding(
            self, "chromium_headless_shell-1", "firefox-1", "webkit-1"
        )
        self.assertEqual(
            test_csp_watch_live._missing_builds(builds, _folder_holding(self)),
            (builds, []),
        )

    def test_the_package_is_the_installed_playwright(self) -> None:
        self.assertEqual(
            test_csp_watch_live._PLAYWRIGHT_PACKAGE,
            Path(playwright.__file__).resolve().parent,
        )

    def test_a_setup_in_hermetic_mode_it_cannot_check_goes_on_to_serve(self) -> None:
        # No skip where the builds cannot be found: the setup reaches the server.
        with (
            mock.patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "0"}),
            mock.patch.object(
                test_csp_watch_live, "_PLAYWRIGHT_PACKAGE", _folder_holding(self)
            ),
            mock.patch.object(
                test_csp_watch_live.loopback_site,
                "serve_directory",
                side_effect=OSError("served"),
            ),
            self.assertRaisesRegex(OSError, "served"),
        ):
            os.environ.pop(_REQUIRE, None)
            test_csp_watch_live.setUpModule()


class EngineBuildsTest(unittest.TestCase):
    """Where the live suite looks: Playwright's variable, else its own cache folder."""

    def builds(
        self, env: dict[str, str], platform: str, home: Path | None = _HOME
    ) -> Path | None:
        """Where the suite looks with `env` on `platform`, with the test's home folder."""
        return test_csp_watch_live._engine_builds(env, platform, home)

    def test_playwrights_own_variable_wins(self) -> None:
        env = {"PLAYWRIGHT_BROWSERS_PATH": "/chosen", "XDG_CACHE_HOME": "/cache"}
        self.assertEqual(self.builds(env, "linux"), Path("/chosen"))

    def test_macos_looks_in_the_users_caches(self) -> None:
        self.assertEqual(
            self.builds({}, "darwin"), _HOME / "Library" / "Caches" / "ms-playwright"
        )

    def test_windows_looks_in_local_app_data_or_where_it_is_named(self) -> None:
        self.assertEqual(
            self.builds({}, "win32"), _HOME / "AppData" / "Local" / "ms-playwright"
        )
        self.assertEqual(
            self.builds({"LOCALAPPDATA": "/local"}, "win32"),
            Path("/local") / "ms-playwright",
        )

    def test_linux_looks_in_the_users_cache_or_where_xdg_names(self) -> None:
        self.assertEqual(self.builds({}, "linux"), _HOME / ".cache" / "ms-playwright")
        self.assertEqual(
            self.builds({"XDG_CACHE_HOME": "/cache"}, "linux"),
            Path("/cache") / "ms-playwright",
        )

    def test_a_platform_playwright_installs_no_builds_on_has_no_cache(self) -> None:
        # As browser_pass's own lookup reads it: not taken for Linux, but nowhere.
        self.assertIsNone(self.builds({}, "sunos5"))

    def test_with_no_home_folder_only_a_named_cache_is_found(self) -> None:
        self.assertIsNone(self.builds({}, "darwin", None))
        self.assertEqual(
            self.builds({"XDG_CACHE_HOME": "/cache"}, "linux", None),
            Path("/cache") / "ms-playwright",
        )

    def test_the_home_folder_is_none_where_the_platform_cannot_name_one(self) -> None:
        self.assertEqual(test_csp_watch_live._home_folder(), Path.home())
        with mock.patch.object(Path, "home", side_effect=RuntimeError("no home")):
            self.assertIsNone(test_csp_watch_live._home_folder())


if __name__ == "__main__":
    unittest.main()
