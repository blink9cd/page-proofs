"""Grouped suite: the CLI (its options, every refusal, and what a run writes and prints).

The engine is replaced here by `_cli_fixtures.FakeEngine`, which returns scripted results, so these
tests reach every exit path without a browser. The real engines are driven in `test_pass_live.py`,
and what the CLI does with a targets file's states is `test_pass_cli_states.py`'s.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's folder on
# sys.path, and the CLI it imports adds the tool's src/, for every import after it.
from _cli_fixtures import TARGETS, CliCase, launch_path

import contextlib
import io
import json
import os
import sys
import unittest
from typing import Any
from unittest import mock

import browser_pass
import pass_extension
import pass_facts
from pass_motion import Motion
from pass_report import JSON_NAME, MARKDOWN_NAME
from pass_targets import Rendering

#: The commit the fake `git` reports as HEAD.
_FAKE_COMMIT = "0123456789abcdef0123456789abcdef01234567"

#: A fake `git` that logs each call's arguments to `FAKE_GIT_LOG`, then answers HEAD with
#: `_FAKE_COMMIT` and a clean status, or hangs, when `FAKE_GIT_MODE` is `hang`.
_FAKE_GIT = f"""#!{sys.executable}
import os, sys, time
with open(os.environ["FAKE_GIT_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_GIT_MODE") == "hang":
    while True:
        time.sleep(1)
if "rev-parse" in sys.argv:
    print("{_FAKE_COMMIT}")
"""


class ParserTests(unittest.TestCase):
    """Named options only, each optional's default shown."""

    def test_targets_is_required(self) -> None:
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            browser_pass.build_parser().parse_args([])
        self.assertEqual(caught.exception.code, 2)

    def test_the_whole_matrix_is_the_default(self) -> None:
        args = browser_pass.build_parser().parse_args(["--targets", "t.toml"])
        self.assertEqual(args.engines, ["chromium", "firefox", "webkit"])
        self.assertEqual(args.orientations, ["portrait", "landscape"])
        self.assertEqual(args.modes, ["light", "dark"])
        self.assertEqual(args.scripts, ["on", "off"])
        self.assertFalse(args.prove)
        self.assertIsNone(args.serve)

    def test_refuses_a_timeout_that_is_not_positive(self) -> None:
        for bad in ("0", "-1", "soon"):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    browser_pass.build_parser().parse_args(
                        ["--targets", "t", "--timeout", bad]
                    )

    def test_help_shows_the_defaults_and_the_prerequisites(self) -> None:
        text = browser_pass.build_parser().format_help()
        self.assertIn("(default: 30.0)", text)
        self.assertIn("Installing the engines", text)

    def test_help_shows_no_default_an_option_left_out_never_had(self) -> None:
        # Flattened: argparse may wrap a default note across two lines.
        text = " ".join(browser_pass.build_parser().format_help().split())
        for unset in ("(default: None)", "(default: False)"):
            with self.subTest(unset=unset):
                self.assertNotIn(unset, text)


class RefusalTests(CliCase):
    """Exit 2, with the reason on stderr, before anything is measured."""

    def test_a_targets_file_that_does_not_parse(self) -> None:
        self.targets.write_text("pages = [", encoding="utf-8", newline="\n")
        code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertIn("not valid TOML", err)
        self.assertEqual(self.engine.calls, [])

    def test_a_targets_file_that_does_not_exist(self) -> None:
        self.targets.unlink()
        code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertIn("targets.toml", err)

    def test_no_server_named_anywhere(self) -> None:
        code, _, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("--serve", err)

    def test_a_serve_folder_that_is_not_one(self) -> None:
        code, _, err = self.run_cli("--serve", str(self.root / "nowhere"))
        self.assertEqual(code, 2)
        self.assertIn("not a folder", err)

    def test_no_engine_builds_to_be_found(self) -> None:
        del os.environ[pass_facts.BROWSERS_ENV]
        with mock.patch.object(pass_facts, "default_browsers_path", return_value=None):
            code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertIn("--browsers", err)

    def test_playwright_not_installed(self) -> None:
        with mock.patch.object(browser_pass, "_load_engine", return_value=None):
            code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertIn("`browser` extra", err)

    def test_a_page_the_served_tree_does_not_hold(self) -> None:
        (self.site / "archive" / "index.html").unlink()
        code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertIn("cannot serve", err)
        self.assertEqual(self.engine.calls, [])

    def test_a_run_that_crashes_is_two_and_prints_where(self) -> None:
        # Never 1: that is the code for a detector having found something, and a run that died
        # measured nothing. The traceback is printed rather than swallowed.
        self.engine.crash = True
        code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertIn("the probe returned a record of the wrong shape", err)
        self.assertIn("Traceback", err)

    def test_an_engine_that_cannot_launch(self) -> None:
        self.engine.fail = True
        code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertIn("cannot launch webkit", err)


class RunTests(CliCase):
    """A run that measures: what the engine is asked for, and what is written and printed."""

    def test_a_clean_run_exits_zero_and_writes_both_reports(self) -> None:
        code, out, _ = self.run_cli("--serve", str(self.site), "--engines", "webkit")
        self.assertEqual(code, 0)
        run_dir = self.only_run_dir()
        document = json.loads((run_dir / JSON_NAME).read_text(encoding="utf-8"))
        self.assertEqual(document["exit_code"], 0)
        self.assertEqual(document["facts"]["served"], str(self.site))
        self.assertIn(
            "**Verdict: CLEAN**", (run_dir / MARKDOWN_NAME).read_text(encoding="utf-8")
        )
        self.assertIn("verdict CLEAN", out)
        self.assertIn(str(run_dir / MARKDOWN_NAME), out)
        self.assertNotIn("motion", out)

    def test_motion_is_named_on_one_line_before_the_verdict_and_changes_no_exit(
        self,
    ) -> None:
        self.engine.motion = Motion(finished=1, paused=1, running=1)
        code, out, _ = self.run_cli("--serve", str(self.site), "--engines", "webkit")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        (motion,) = [line for line in lines if "motion" in line]
        # Eight renderings of `/` (two orientations, two modes, scripts on and off), each
        # stilling the same, and none of `/archive/`, which the line does not name.
        self.assertEqual(
            motion,
            "browser_pass: motion stilled or left running while settling: "
            "/ (8 finished, 8 paused, 8 still running)",
        )
        self.assertIn("verdict CLEAN", lines[lines.index(motion) + 1])

    def test_the_late_load_s_motion_has_a_line_of_its_own_after_the_page_load_s(
        self,
    ) -> None:
        # Counted apart, since both loads still the same animations, and said apart too.
        self.engine.motion = Motion(finished=1, paused=0, running=0)
        self.engine.late_motion = Motion(finished=0, paused=1, running=1)
        code, out, _ = self.run_cli("--serve", str(self.site), "--engines", "webkit")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        loaded, late = [line for line in lines if "motion" in line]
        self.assertEqual(
            loaded,
            "browser_pass: motion stilled or left running while settling: "
            "/ (8 finished, 0 paused, 0 still running)",
        )
        self.assertEqual(
            late,
            "browser_pass: late-script load: motion stilled or left running while "
            "settling: / (0 finished, 8 paused, 8 still running)",
        )
        self.assertIn("verdict CLEAN", lines[lines.index(late) + 1])

    def test_asks_the_engine_for_the_filtered_matrix_on_the_served_url(self) -> None:
        self.run_cli(
            "--serve",
            str(self.site),
            "--engines",
            "firefox",
            "--orientations",
            "landscape",
            "--modes",
            "dark",
            "--scripts",
            "off",
            "--timeout",
            "12",
            "--prove",
        )
        (call,) = self.engine.calls
        self.assertEqual(
            call["renderings"],
            [
                Rendering("firefox", "/", 844, 390, "dark", "off"),
                Rendering("firefox", "/archive/", 844, 390, "dark", "off"),
            ],
        )
        self.assertRegex(str(call["base_url"]), r"^http://127\.0\.0\.1:[1-9][0-9]*$")
        self.assertEqual((call["timeout"], call["prove"]), (12.0, True))
        self.assertTrue(call["scratch_exists"])

    def test_each_flag_left_out_reaches_the_run_as_false(self) -> None:
        # A flag left out parses as None, so --help shows no default; the run gets False.
        self.run_cli("--serve", str(self.site))
        (call,) = self.engine.calls
        isolation = call["isolation"]
        assert isinstance(isolation, pass_extension.Isolation)
        for flag, value in (
            ("--prove", call["prove"]),
            ("--require-csp", call["require_csp"]),
            ("--loopback-only", isolation.loopback_only),
        ):
            with self.subTest(flag=flag):
                self.assertIs(value, False)

    def test_points_playwright_at_the_named_engine_builds(self) -> None:
        chosen = self.root / "chosen-builds"
        self.run_cli("--serve", str(self.site), "--browsers", str(chosen))
        self.assertEqual(self.engine.calls[0]["browsers"], str(chosen))

    def test_the_default_engine_builds_are_where_playwright_keeps_them(self) -> None:
        del os.environ[pass_facts.BROWSERS_ENV]
        derived = self.root / "cache" / "ms-playwright"
        with mock.patch.object(
            pass_facts, "default_browsers_path", return_value=derived
        ):
            self.run_cli("--serve", str(self.site))
        self.assertEqual(self.engine.calls[0]["browsers"], str(derived))

    def test_a_finding_exits_one_and_is_named_as_it_arrives(self) -> None:
        self.engine.findings = {"small_target": ("body>a:1 20x20",)}
        code, out, _ = self.run_cli("--serve", str(self.site), "--engines", "chromium")
        self.assertEqual(code, 1)
        self.assertIn("chromium / 390x844 light scripts-on: small_target", out)
        self.assertIn("verdict FINDINGS", out)

    def test_a_missed_plant_is_named_as_it_arrives(self) -> None:
        self.engine.verdicts = {"page_overflow": "caught", "late_shift": "missed"}
        code, out, _ = self.run_cli("--serve", str(self.site), "--engines", "chromium")
        self.assertEqual(code, 1)
        self.assertIn("chromium / 390x844 light scripts-on: late_shift missed", out)

    def test_a_plant_nothing_could_catch_is_not_announced_as_a_miss(self) -> None:
        self.engine.verdicts = {
            "page_overflow": "caught",
            "page_error": "n/a",
            "late_shift": "not measured",
        }
        code, out, _ = self.run_cli("--serve", str(self.site), "--engines", "webkit")
        self.assertEqual(code, 0)
        self.assertNotIn("webkit /", out)

    def test_a_page_with_a_fragment_is_confirmed_against_its_file(self) -> None:
        # The fragment never reaches the server: `/index.html#latest` is index.html, scrolled.
        self.targets.write_text(
            TARGETS.replace('["/", "/archive/"]', '["/index.html#latest"]'),
            encoding="utf-8",
            newline="\n",
        )
        code, _, err = self.run_cli("--serve", str(self.site), "--engines", "chromium")
        self.assertEqual((code, err), (0, ""))
        pages = {r.page for r in self.engine.calls[0]["renderings"]}  # type: ignore[attr-defined] # a list of Rendering
        self.assertEqual(pages, {"/index.html#latest"})

    def test_a_page_with_a_query_is_confirmed_against_its_file(self) -> None:
        # The query reaches the server, but names no other file: `/?panel=1` is index.html.
        self.targets.write_text(
            TARGETS.replace('["/", "/archive/"]', '["/?panel=1"]'),
            encoding="utf-8",
            newline="\n",
        )
        code, _, err = self.run_cli("--serve", str(self.site), "--engines", "chromium")
        self.assertEqual((code, err), (0, ""))
        pages = {r.page for r in self.engine.calls[0]["renderings"]}  # type: ignore[attr-defined] # a list of Rendering
        self.assertEqual(pages, {"/?panel=1"})

    def test_measures_a_running_server_from_the_targets_base_url(self) -> None:
        self.targets.write_text(
            'base_url = "http://127.0.0.1:8123/"\n' + TARGETS,
            encoding="utf-8",
            newline="\n",
        )
        code, _, _ = self.run_cli("--engines", "webkit")
        self.assertEqual(code, 0)
        self.assertEqual(self.engine.calls[0]["base_url"], "http://127.0.0.1:8123")
        text = (self.only_run_dir() / MARKDOWN_NAME).read_text(encoding="utf-8")
        self.assertIn("not served by this run", text)

    def test_writes_under_out_when_given(self) -> None:
        out_dir = self.root / "elsewhere"
        self.run_cli("--serve", str(self.site), "--out", str(out_dir))
        (run_dir,) = out_dir.iterdir()
        self.assertTrue((run_dir / JSON_NAME).is_file())


class CommitReadTests(CliCase):
    """The commit is read before the matrix runs, and git failing costs no report."""

    def setUp(self) -> None:
        super().setUp()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "git"
        fake.write_text(_FAKE_GIT, encoding="utf-8", newline="\n")
        fake.chmod(0o755)
        launch_path(fake)
        self.git_log = self.root / "git.log"
        path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        env = mock.patch.dict(
            os.environ, {"PATH": path, "FAKE_GIT_LOG": str(self.git_log)}
        )
        env.start()
        self.addCleanup(env.stop)

    def git_calls(self) -> list[str]:
        """Every call the fake git logged so far, in order."""
        if not self.git_log.exists():
            return []
        return self.git_log.read_text(encoding="utf-8").splitlines()

    def facts(self) -> dict[str, Any]:
        """The facts block of the one report the run wrote."""
        text = (self.only_run_dir() / JSON_NAME).read_text(encoding="utf-8")
        facts: dict[str, Any] = json.loads(text)["facts"]
        return facts

    def test_the_commit_is_read_before_the_matrix_runs(self) -> None:
        asked: list[int] = []
        run_matrix = self.engine.run_matrix

        def watched(*args: Any, **kwargs: Any) -> Any:
            asked.append(len(self.git_calls()))
            return run_matrix(*args, **kwargs)

        with mock.patch.object(self.engine, "run_matrix", watched):
            code, _, _ = self.run_cli("--serve", str(self.site), "--engines", "webkit")
        self.assertEqual(code, 0)
        self.assertEqual(self.facts()["commit"], _FAKE_COMMIT)
        self.assertIs(self.facts()["tree_dirty"], False)
        self.assertGreater(asked[0], 0)
        self.assertEqual(asked, [len(self.git_calls())])

    def test_a_git_that_times_out_still_writes_the_report(self) -> None:
        with (
            mock.patch.dict(os.environ, {"FAKE_GIT_MODE": "hang"}),
            mock.patch.object(browser_pass, "_GIT_TIMEOUT_SECONDS", 1.0),
        ):
            code, _, err = self.run_cli(
                "--serve", str(self.site), "--engines", "webkit"
            )
        self.assertEqual(code, 0)
        self.assertIsNone(self.facts()["commit"])
        self.assertIsNone(self.facts()["tree_dirty"])
        markdown = (self.only_run_dir() / MARKDOWN_NAME).read_text(encoding="utf-8")
        self.assertIn("- Commit: unknown\n", markdown)
        self.assertIn("timed out", err)


class LoadEngineTests(unittest.TestCase):
    """Importing the engine: a missing Playwright is a refusal, anything else a real error."""

    def test_returns_the_engine_when_playwright_is_installed(self) -> None:
        engine = browser_pass._load_engine()
        self.assertIsNotNone(engine)

    def test_a_missing_playwright_is_none(self) -> None:
        with mock.patch.dict(sys.modules, {"playwright.sync_api": None}):
            sys.modules.pop("pass_engine", None)
            self.assertIsNone(browser_pass._load_engine())
        sys.modules.pop("pass_engine", None)

    def test_any_other_missing_module_is_raised(self) -> None:
        with mock.patch.dict(sys.modules, {"pass_digest": None}):
            sys.modules.pop("pass_engine", None)
            with self.assertRaises(ModuleNotFoundError):
                browser_pass._load_engine()
        sys.modules.pop("pass_engine", None)


if __name__ == "__main__":
    unittest.main()
