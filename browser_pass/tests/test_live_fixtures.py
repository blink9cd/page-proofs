"""Grouped suite: what the live suites share that needs no engine build.

A live module skips at setup where a pinned engine build is not installed, naming the install
command, in the skip and in one line on stderr, so the suites run on a machine with Playwright
and no builds. With `PAGE_PROOFS_REQUIRE_BROWSERS=1` it fails instead, so a run that must
measure the engines can never pass with them unmeasured. The pin checks and the launch budget's
checks live here too rather than in a live module, so a skipped live module never hides them.
Nothing here launches an engine, so this suite runs where no build is installed.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

# Imported first: the fixture module puts the tool's src/ on sys.path for every import after it.
import _live_fixtures
import bounded_run
import loopback_site
import pass_engine
import pass_facts
import playwright
from _live_fixtures import LaunchCounter, LiveSite, over_budget

#: The opening of the message a live module's setup gives where no build is installed.
_NOT_INSTALLED = r"^the pinned engine builds are not installed \(looked in "

#: The manifest that pins this tool's engine: the repository root's `pyproject.toml`, beside
#: this tool's folder, whose `browser` extra installs Playwright for it.
_PIN_MANIFEST = _live_fixtures.TOOL_DIR.parent / "pyproject.toml"

#: The opt-in's variable, named once for the tests that set it.
_REQUIRE = _live_fixtures.REQUIRE_BROWSERS_ENV


def _folder_holding(case: unittest.TestCase, *names: str) -> Path:
    """A scratch folder, removed when `case` ends, holding an empty folder of each of `names`."""
    scratch = tempfile.TemporaryDirectory()
    case.addCleanup(scratch.cleanup)
    for name in names:
        Path(scratch.name, name).mkdir(parents=True)
    return Path(scratch.name)


class MissingBuildsTest(unittest.TestCase):
    """`LiveSite.start` with the builds folder named but absent."""

    def start_without_builds(
        self, *, required: str | None
    ) -> tuple[BaseException, str]:
        """What `LiveSite.start` raises with no build installed, and what it wrote to stderr.

        `required` is the opt-in's value, None for unset.
        """
        stderr = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as scratch,
            mock.patch.dict(
                os.environ, {pass_facts.BROWSERS_ENV: str(Path(scratch, "absent"))}
            ),
            contextlib.redirect_stderr(stderr),
            self.assertRaises((RuntimeError, unittest.SkipTest)) as raised,
        ):
            os.environ.pop(_REQUIRE, None)
            if required is not None:
                os.environ[_REQUIRE] = required
            LiveSite(launch_budget={}).start()
        return raised.exception, stderr.getvalue()

    def test_a_start_with_no_build_skips_naming_the_install(self) -> None:
        raised, _ = self.start_without_builds(required=None)
        self.assertIsInstance(raised, unittest.SkipTest)
        self.assertRegex(str(raised), _NOT_INSTALLED)
        self.assertIn("for chromium, firefox, webkit)", str(raised))
        self.assertIn(f"`{_live_fixtures.INSTALL_COMMAND}` installs them", str(raised))

    def test_a_skip_writes_its_reason_to_stderr_once(self) -> None:
        # unittest shows a skip's reason only when run verbosely, so a plain run would show a
        # bare count of skips: the line on stderr is what names the install command there.
        raised, stderr = self.start_without_builds(required=None)
        self.assertEqual(stderr, f"{_live_fixtures.SKIP_NOTICE}{raised}\n")

    def test_an_empty_opt_in_is_off(self) -> None:
        raised, _ = self.start_without_builds(required="")
        self.assertIsInstance(raised, unittest.SkipTest)

    def test_with_the_opt_in_a_start_with_no_build_fails_naming_the_install(
        self,
    ) -> None:
        # A skip is a passing run to every gate, so with the opt-in only the error will do.
        raised, stderr = self.start_without_builds(required="1")
        self.assertIsInstance(raised, RuntimeError)
        self.assertRegex(str(raised), _NOT_INSTALLED)
        self.assertIn(f"`{_live_fixtures.INSTALL_COMMAND}` installs them", str(raised))
        self.assertEqual(stderr, "")

    def test_any_other_opt_in_value_is_refused_naming_the_accepted_values(
        self,
    ) -> None:
        # A value meant as on, read as off, would let a run pass with the engines unmeasured.
        for value in ("0", "yes", "true"):
            with self.subTest(value=value):
                raised, _ = self.start_without_builds(required=value)
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
            mock.patch.object(_live_fixtures, "engines_missing", return_value=[]),
            mock.patch.object(
                loopback_site, "serve_directory", side_effect=AssertionError("served")
            ),
            self.assertRaisesRegex(RuntimeError, f"^{_REQUIRE} is 'yes'"),
        ):
            LiveSite(launch_budget={}).start()


class EnginesMissingTest(unittest.TestCase):
    """Which engines' headless builds a folder lacks."""

    def test_no_folder_lacks_every_engine(self) -> None:
        self.assertEqual(
            _live_fixtures.engines_missing(None), ["chromium", "firefox", "webkit"]
        )

    def test_a_folder_with_one_build_lacks_the_other_two(self) -> None:
        # The full Chromium build is not the headless one the suites launch.
        builds = _folder_holding(self, "webkit-2359", "chromium-1243")
        self.assertEqual(
            _live_fixtures.engines_missing(builds), ["chromium", "firefox"]
        )

    def test_a_folder_with_every_headless_build_lacks_none(self) -> None:
        builds = _folder_holding(
            self, "chromium_headless_shell-1243", "firefox-1543", "webkit-2359"
        )
        self.assertEqual(_live_fixtures.engines_missing(builds), [])

    def test_the_prefixes_asked_about_are_the_ones_checked(self) -> None:
        builds = _folder_holding(self, "chromium_headless_shell-1243")
        self.assertEqual(
            _live_fixtures.engines_missing(builds, _live_fixtures.FULL_CHROMIUM),
            ["chromium"],
        )


class HermeticBuildsTest(unittest.TestCase):
    """`PLAYWRIGHT_BROWSERS_PATH=0`: Playwright's own mode keeping the builds in its package."""

    def test_hermetic_mode_looks_inside_the_playwright_package(self) -> None:
        package = _folder_holding(self, "driver/package/.local-browsers/webkit-2359")
        looked_in, missing = _live_fixtures.missing_builds(Path("0"), package)
        self.assertEqual(looked_in, package / "driver" / "package" / ".local-browsers")
        self.assertEqual(missing, ["chromium", "firefox"])

    def test_hermetic_mode_without_that_package_layout_reports_nothing_missing(
        self,
    ) -> None:
        # Where the builds are cannot be known, so the launch, not a skip, says what is wrong.
        package = _folder_holding(self)
        self.assertEqual(
            _live_fixtures.missing_builds(Path("0"), package), (Path("0"), [])
        )

    def test_any_other_folder_is_looked_in_as_named(self) -> None:
        builds = _folder_holding(self, "chromium-1243")
        self.assertEqual(
            _live_fixtures.missing_builds(
                builds, _folder_holding(self), _live_fixtures.FULL_CHROMIUM
            ),
            (builds, []),
        )
        self.assertEqual(
            _live_fixtures.missing_builds(None, _folder_holding(self)),
            (None, ["chromium", "firefox", "webkit"]),
        )

    def test_the_package_is_the_installed_playwright(self) -> None:
        self.assertEqual(
            _live_fixtures.PLAYWRIGHT_PACKAGE,
            Path(playwright.__file__).resolve().parent,
        )

    def test_a_start_in_hermetic_mode_it_cannot_check_goes_on_to_serve(self) -> None:
        # No skip where the builds cannot be found: the start reaches the server.
        with (
            mock.patch.dict(os.environ, {pass_facts.BROWSERS_ENV: "0"}),
            mock.patch.object(
                _live_fixtures, "PLAYWRIGHT_PACKAGE", _folder_holding(self)
            ),
            mock.patch.object(
                loopback_site, "serve_directory", side_effect=OSError("served")
            ),
            self.assertRaisesRegex(OSError, "served"),
        ):
            os.environ.pop(_REQUIRE, None)
            LiveSite(launch_budget={}).start()


class PinTests(unittest.TestCase):
    """The installed Playwright is the one the repository's manifest pins, and pinned exactly."""

    def _extra(self, name: str) -> list[str]:
        """The entries of the manifest's optional dependency group `name`."""
        with _PIN_MANIFEST.open("rb") as handle:
            extras = tomllib.load(handle)["project"]["optional-dependencies"][name]
        return list(extras)

    def test_the_installed_playwright_matches_the_browser_pin(self) -> None:
        # Filtered rather than unpacked from the whole list: the extra also pins
        # playwright's own closure, and the contract is that the playwright entry
        # agrees with the installed engine, not that it is the only entry.
        (pin,) = [
            match
            for match in (
                re.fullmatch(r"playwright==(\S+)", extra)
                for extra in self._extra("browser")
            )
            if match is not None
        ]
        self.assertEqual(pass_engine.playwright_version(), pin.group(1))

    def test_every_browser_entry_is_an_exact_pin(self) -> None:
        """One floating entry would give back what pinning the closure bought.

        A range here lets the resolver pick a Playwright, and with it engine builds, that
        no test here has run against.
        """
        for extra in self._extra("browser"):
            self.assertRegex(extra, r"^[A-Za-z0-9._-]+==\S+$", extra)

    def test_the_test_extra_repeats_every_browser_pin_exactly(self) -> None:
        # The suites are installed through the test extra, so a browser pin it states
        # differently, or leaves out, tests a closure no user of the browser extra gets.
        test = set(self._extra("test"))
        for extra in self._extra("browser"):
            self.assertIn(extra, test, "the test extra does not repeat a browser pin")


class LaunchBudgetTests(unittest.TestCase):
    """The per-module launch budget: every launch counted, and any engine past its budget named."""

    def test_an_engine_launched_past_its_budget_is_named_with_its_count(self) -> None:
        over = over_budget({"chromium": 3, "webkit": 1}, {"chromium": 2, "webkit": 1})
        self.assertEqual(over, {"chromium": 3})

    def test_an_engine_with_no_budget_may_not_launch_at_all(self) -> None:
        self.assertEqual(over_budget({"firefox": 1}, {"chromium": 1}), {"firefox": 1})

    def test_the_counter_passes_each_launch_through_and_counts_it_per_engine(
        self,
    ) -> None:
        calls: list[tuple[object, str, dict[str, object]]] = []

        def launch(playwright: object, engine: str, **options: object) -> str:
            calls.append((playwright, engine, options))
            return f"{engine} browser"

        counter = LaunchCounter(launch)
        self.assertEqual(counter("driver", "webkit", timeout=1.0), "webkit browser")
        counter("driver", engine="webkit", timeout=1.0)
        self.assertEqual(counter.counts, {"webkit": 2})
        self.assertEqual(calls[0], ("driver", "webkit", {"timeout": 1.0}))

    def test_a_start_that_fails_part_way_undoes_what_it_had_started(self) -> None:
        # No tearDownModule runs after a setUpModule that raised, so a half-started site must
        # not leave its environment patch in place for later modules. The variable is taken
        # out first, so a leak shows, and the builds check is passed, so the start reaches the
        # server whatever builds this machine has.
        site = LiveSite(launch_budget={})
        with mock.patch.dict(os.environ):
            os.environ.pop(pass_facts.BROWSERS_ENV, None)
            os.environ.pop(_REQUIRE, None)
            with (
                mock.patch.object(_live_fixtures, "engines_missing", return_value=[]),
                mock.patch.object(
                    loopback_site, "serve_directory", side_effect=OSError("no port")
                ),
                self.assertRaisesRegex(OSError, "no port"),
            ):
                site.start()
            # A bare assertNotIn would print the whole environment, secrets and all, on failure.
            self.assertFalse(
                pass_facts.BROWSERS_ENV in os.environ, "the builds path leaked"
            )


#: Every library the export's bootstraps put on `sys.path`, each a folder at the repository root.
_SIBLINGS = (
    "bounded_run",
    "commit_facts",
    "csp_watch",
    "help_formatter",
    "loopback_site",
    "stdio_utf8",
    "suite_select",
)

#: A fresh interpreter's program: put the folder named first, import the module named second,
#: so its bootstrap runs, then import each library named after them and print the file it came
#: from, one per line.
_IMPORT_FROM = (
    "import importlib, sys\n"
    "folder, module, *names = sys.argv[1:]\n"
    "sys.path.insert(0, folder)\n"
    "importlib.import_module(module)\n"
    "print(*(importlib.import_module(n).__file__ for n in names), sep='\\n')\n"
)

#: Bound on that interpreter, which imports Playwright at most.
_IMPORT_TIMEOUT_SECONDS = 120.0


class SiblingShadowingTest(unittest.TestCase):
    """A same-named module ahead of the libraries on `sys.path` never shadows the shipped one.

    Each decoy sits on `PYTHONPATH`, which a fresh interpreter puts ahead of every installed
    distribution, so it stands for one: a bootstrap that appended a library's folder would
    import the decoy instead.
    """

    def test_every_bootstrap_imports_the_folder_beside_the_tool(self) -> None:
        tool = _live_fixtures.TOOL_DIR
        root = tool.parent
        cases = {
            "browser_pass": (
                tool,
                [n for n in _SIBLINGS if n not in {"csp_watch", "suite_select"}],
            ),
            "pass_csp": (tool / "src", ["csp_watch"]),
            "test_browser_pass": (tool, ["suite_select"]),
            "_live_fixtures": (tool / "tests", ["bounded_run", "loopback_site"]),
            "test_pass_progress_serve": (
                tool / "tests",
                ["bounded_run", "loopback_site"],
            ),
            "test_pass_watchdog": (tool / "tests", ["bounded_run"]),
            # The other tools' and libraries' bootstraps, rewritten the same way.
            "lighthouse_run": (
                root / "lighthouse_run",
                [
                    "bounded_run",
                    "commit_facts",
                    "help_formatter",
                    "loopback_site",
                    "stdio_utf8",
                ],
            ),
            "run_facts": (root / "lighthouse_run" / "src", ["bounded_run"]),
            "test_lighthouse_run_serve": (
                root / "lighthouse_run" / "tests",
                ["loopback_site"],
            ),
            "test_lighthouse_run_url": (
                root / "lighthouse_run" / "tests",
                ["loopback_site"],
            ),
            "test_lighthouse_run_cli": (
                root / "lighthouse_run" / "tests",
                ["help_formatter"],
            ),
            "commit_facts": (root / "commit_facts", ["bounded_run"]),
            "test_csp_watch": (root / "csp_watch", ["bounded_run"]),
            "test_csp_watch_live": (root / "csp_watch" / "tests", ["loopback_site"]),
            "bounded_run": (root / "bounded_run", ["stdio_utf8"]),
            "loopback_site": (root / "loopback_site", ["stdio_utf8"]),
        }
        scratch = _folder_holding(self, "decoys", "elsewhere")
        decoys = scratch / "decoys"
        for name in _SIBLINGS:
            (decoys / f"{name}.py").write_text(
                "DECOY = True\n", encoding="utf-8", newline="\n"
            )
        environment = {**os.environ, "PYTHONPATH": str(decoys)}
        for module, (folder, names) in cases.items():
            with self.subTest(module=module):
                done = bounded_run.run_bounded(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        _IMPORT_FROM,
                        str(folder),
                        module,
                        *names,
                    ],
                    timeout=_IMPORT_TIMEOUT_SECONDS,
                    cwd=scratch / "elsewhere",
                    env=environment,
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                imported = [Path(line) for line in done.stdout.splitlines()]
                self.assertEqual(
                    [path.parent for path in imported],
                    [root / name for name in names],
                )


if __name__ == "__main__":
    unittest.main()
