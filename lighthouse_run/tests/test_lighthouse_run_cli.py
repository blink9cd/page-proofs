"""Grouped suite: the command line, the schedule, and whole runs against a fake Lighthouse."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The vendored formatter library, at the fixed sibling path the runner's bootstrap names, so
# the help test can name the class it expects without relying on that bootstrap. It goes just
# after the tool folder, ahead of every installed distribution, so none of the same name can
# shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "help_formatter"))

import help_formatter  # noqa: E402 - import follows the sys.path bootstrap above
import lighthouse_run  # noqa: E402 - import follows the sys.path bootstrap above
from _lighthouse_run_case import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    ProjectCase,
)
from _lighthouse_run_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    FAKE_CHROME_VERSION,
    FAKE_COMMIT,
    FAKE_LIGHTHOUSE,
    FONT_BYTES_BEFORE_FCP,
    PACKAGE_MARK,
    PAGE_FILES,
    make_package,
    write_script,
)


class ParserTests(unittest.TestCase):
    """The argument surface: named options, sane defaults, refusals at parse time."""

    def refuses(self, argv: list[str]) -> None:
        """Assert the parser exits on `argv`, swallowing argparse's own usage text."""
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            lighthouse_run.build_parser().parse_args(argv)

    def test_root_is_required(self) -> None:
        self.refuses([])

    def test_fewer_runs_than_a_median_needs_are_refused(self) -> None:
        self.refuses(["--root", ".", "--runs", str(lighthouse_run.MIN_RUNS - 1)])

    def test_a_run_count_at_or_above_the_floor_is_kept(self) -> None:
        # argparse applies `type` only to what was typed, never to the default, so the
        # accepting half of the run-count check needs a value on the command line.
        args = lighthouse_run.build_parser().parse_args(["--root", ".", "--runs", "5"])
        self.assertEqual(args.runs, 5)

    def test_a_run_count_must_be_a_number(self) -> None:
        self.refuses(["--root", ".", "--runs", "three"])

    def test_a_page_must_be_a_server_path(self) -> None:
        self.refuses(["--root", ".", "--pages", "archive/"])

    def test_an_unknown_preset_is_refused(self) -> None:
        self.refuses(["--root", ".", "--presets", "tablet"])

    def test_defaults_measure_the_home_page_on_both_presets(self) -> None:
        args = lighthouse_run.build_parser().parse_args(["--root", "."])
        self.assertEqual(args.pages, ["/"])
        self.assertEqual(args.presets, ["mobile", "desktop"])
        self.assertEqual(args.runs, lighthouse_run.MIN_RUNS)
        self.assertIsNone(args.out)
        self.assertIsNone(args.serve)

    def test_serve_names_the_folder_to_measure(self) -> None:
        args = lighthouse_run.build_parser().parse_args(
            ["--root", ".", "--serve", "package"]
        )
        self.assertEqual(args.serve, Path("package"))

    def test_help_shows_defaults_and_prerequisites(self) -> None:
        # Flattened: argparse wraps the epilog to the terminal width, anywhere a space falls.
        text = " ".join(lighthouse_run.build_parser().format_help().split())
        self.assertIn("default:", text)
        self.assertIn("npm ci", text)

    def test_help_binds_the_formatter_hiding_a_required_default(self) -> None:
        # Identity, not rendering: CPython 3.13 fixed the bogus `(default: None)` on a
        # required option, so a rendering test passes there with the stock formatter back.
        self.assertIs(
            lighthouse_run.build_parser().formatter_class,
            help_formatter.ReqAwareDefaultsFormatter,
        )


class ScheduleTests(unittest.TestCase):
    """What runs, in what order, with which flags, into which file."""

    def test_rounds_interleave_every_page_and_preset(self) -> None:
        schedule = lighthouse_run.build_schedule(
            ("/", "/archive/"), ("mobile", "desktop"), 2
        )
        self.assertEqual(
            [(job.round, job.page, job.preset) for job in schedule],
            [
                (1, "/", "mobile"),
                (1, "/", "desktop"),
                (1, "/archive/", "mobile"),
                (1, "/archive/", "desktop"),
                (2, "/", "mobile"),
                (2, "/", "desktop"),
                (2, "/archive/", "mobile"),
                (2, "/archive/", "desktop"),
            ],
        )

    def test_page_slugs_name_report_files(self) -> None:
        slugs = [
            lighthouse_run.page_slug(p)
            for p in ("/", "/archive/", "/404.html", "/a/b/")
        ]
        self.assertEqual(slugs, ["home", "archive", "404.html", "a_b"])

    def test_only_desktop_passes_a_preset_flag(self) -> None:
        binary, out = Path("lh"), Path("report.json")
        mobile = lighthouse_run.lighthouse_argv(binary, "http://x/", "mobile", out)
        desktop = lighthouse_run.lighthouse_argv(binary, "http://x/", "desktop", out)
        self.assertNotIn("--preset=desktop", mobile)
        self.assertEqual(desktop, [*mobile, "--preset=desktop"])

    def test_every_run_is_headless_private_and_json(self) -> None:
        argv = lighthouse_run.lighthouse_argv(
            Path("lh"), "http://x/", "mobile", Path("r.json")
        )
        for flag in (
            "--output=json",
            "--output-path=r.json",
            "--only-categories=performance",
            "--chrome-flags=--headless=new",
            "--no-enable-error-reporting",
        ):
            self.assertIn(flag, argv)
        self.assertFalse(any("user-data-dir" in part for part in argv))


class RefusalTests(ProjectCase):
    """A measurement that cannot start honestly exits 2 and measures nothing."""

    def test_a_root_without_src_is_refused(self) -> None:
        code, _, err = self.run_main("--root", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("src", err)

    def test_a_project_without_the_pinned_lighthouse_is_refused(self) -> None:
        # Every launcher npm writes for it: on Windows a `lighthouse.cmd` sits beside it.
        for launcher in (self.root / "node_modules" / ".bin").glob("lighthouse*"):
            launcher.unlink()
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("npm ci", err)

    def test_no_locatable_chrome_is_refused(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_NODE_MODE": "fail"}):
            code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("Chrome", err)
        self.assertFalse(self.log.exists())

    def test_a_page_the_server_cannot_confirm_is_refused(self) -> None:
        code, _, err = self.run_main("--pages", "/missing.html")
        self.assertEqual(code, 2)
        self.assertIn("missing.html", err)
        self.assertFalse(self.log.exists())

    def test_a_page_a_request_cannot_carry_is_refused_at_parse_time(self) -> None:
        # Each page has its file, so only the request could fail: http.client refuses to send
        # a non-ASCII, space or control character, and that escaped `main` as a traceback.
        for page, file in (
            ("/caf\u00e9/", "caf\u00e9/index.html"),
            ("/a b.html", "a b.html"),
            ("/a\x7fb.html", "a\x7fb.html"),
        ):
            with self.subTest(page=page):
                (self.root / "src" / file).parent.mkdir(exist_ok=True)
                (self.root / "src" / file).write_bytes(b"<!doctype html>\n")
                err = io.StringIO()
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(err),
                    self.assertRaises(SystemExit) as caught,
                ):
                    lighthouse_run.main(["--root", str(self.root), "--pages", page])
                self.assertEqual(caught.exception.code, 2)
                # Renaming, never percent-encoding: a page is mapped to its file undecoded,
                # while every server decodes the request, so an encoded page names no file.
                message = " ".join(err.getvalue().split())
                self.assertIn("rename its file to printable ASCII", message)
                self.assertNotIn("percent-encode", message)
                self.assertFalse(self.log.exists())


class RunTests(ProjectCase):
    """Whole runs: every page and preset measured, medians written, failures counted."""

    def test_a_full_run_writes_medians_and_exits_zero(self) -> None:
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        summary = self.summary()
        self.assertEqual(summary["chrome_version"], FAKE_CHROME_VERSION)
        self.assertEqual(summary["lighthouse_versions"], ["13.4.1"])
        # Only the fake git reports this commit, so the host's git was never asked.
        self.assertEqual(summary["commit"], FAKE_COMMIT)
        self.assertEqual(len(summary["results"]), 2)
        for result in summary["results"]:
            self.assertEqual(result["measured_runs"], lighthouse_run.MIN_RUNS)
            self.assertEqual(
                result["medians"]["font_bytes_before_fcp"], FONT_BYTES_BEFORE_FCP
            )
        self.assertEqual(len(list(self.out.glob("*/reports/*.json"))), 6)
        self.assertIn("| / | mobile | 3 | 90 |", out)

    def test_the_summary_keeps_every_field_older_summaries_are_read_by(self) -> None:
        # Recorded baselines are compared by these names, so a field is only ever added
        # beside them, never renamed or dropped.
        code, _, err = self.run_main("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0, err)
        self.assertLessEqual(
            {
                "tool",
                "created_utc",
                "commit",
                "src_dirty",
                "lighthouse_versions",
                "chrome_version",
                "runs_per_page_and_preset",
                "flags",
                "results",
            },
            set(self.summary()),
        )

    def test_every_run_fetched_the_served_tree_with_the_located_chrome(self) -> None:
        self.run_main()
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(calls), 6)
        for call in calls:
            self.assertEqual(call["status"], 200)
            self.assertEqual(call["chrome"], self.chrome)
            self.assertTrue(call["argv"][0].startswith("http://127.0.0.1:"))

    def test_named_pages_are_each_measured_on_every_preset(self) -> None:
        # Three pages, two presets, three rounds: six results from eighteen runs, every one
        # fetching a page the server answered.
        code, _, err = self.run_main("--pages", "/", "/archive/", "/404.html")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.summary()["results"]), 6)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(calls), 18)
        for call in calls:
            self.assertEqual(call["status"], 200)

    def test_lighthouse_is_launched_as_the_platform_finds_it(self) -> None:
        # On Windows npm's `.bin/lighthouse` is a shell script CreateProcess cannot run, and
        # the launcher is the `lighthouse.cmd` beside it that a PATHEXT lookup answers. So
        # the named script fails here, and only launching what the lookup answered measures.
        bin_dir = str((self.root / "node_modules" / ".bin").absolute())
        write_script(Path(bin_dir) / "lighthouse", "import sys\nsys.exit(9)\n")
        answered = write_script(self.root / "found" / "lighthouse", FAKE_LIGHTHOUSE)
        real_which = shutil.which

        def which(
            cmd: str, mode: int = os.F_OK | os.X_OK, path: str | None = None
        ) -> str | None:
            if (cmd, path) == ("lighthouse", bin_dir):
                return str(answered)
            return real_which(cmd, mode, path)

        with mock.patch.object(shutil, "which", which):
            code, _, err = self.run_main("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            len(self.log.read_text().splitlines()), lighthouse_run.MIN_RUNS
        )

    def test_results_default_to_a_folder_inside_the_project(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lighthouse_run.main(["--root", str(self.root), "--pages", "/"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(
            len(self.summary(self.root / lighthouse_run.RESULTS_SUBDIR)["results"]), 2
        )

    def test_a_failing_preset_is_counted_and_exits_one(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_LH_MODE": "fail-desktop"}):
            code, out, err = self.run_main("--pages", "/")
        self.assertEqual(code, 1)
        desktop = self.result("/", "desktop")
        self.assertEqual(desktop["measured_runs"], 0)
        self.assertIsNone(desktop["medians"])
        self.assertIn("Chrome crashed", desktop["runs"][0]["error"])
        self.assertEqual(self.result("/", "mobile")["measured_runs"], 3)
        self.assertIn("n/a", out)
        self.assertIn("3 of 6 runs failed", err)

    def test_a_hung_run_is_bounded_and_counted(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_LH_MODE": "hang"}):
            code, _, _ = self.run_main(
                "--pages", "/", "--presets", "mobile", "--timeout", "1"
            )
        self.assertEqual(code, 1)
        self.assertIn(
            "timed out after 1s", self.result("/", "mobile")["runs"][0]["error"]
        )

    def test_an_unreadable_report_is_a_failed_run(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_LH_MODE": "garbage"}):
            code, _, _ = self.run_main("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 1)
        self.assertIn(
            "unreadable report", self.result("/", "mobile")["runs"][0]["error"]
        )


class ServedTreeTests(ProjectCase):
    """What is measured: `src/` under `--root` by default, else the folder `--serve` names."""

    def setUp(self) -> None:
        super().setUp()
        self.package = make_package(self.root.parent / "package")

    def run_one(self, *extra: str) -> tuple[int, str, str]:
        """One page on one preset, the cheapest run that still serves and measures."""
        return self.run_main("--pages", "/", "--presets", "mobile", *extra)

    def fetched_bodies(self) -> set[str]:
        """Every distinct page body the fake Lighthouse was served."""
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return {json.loads(line)["body"] for line in lines}

    def test_the_default_serves_src_under_the_root(self) -> None:
        code, _, err = self.run_one()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fetched_bodies(), {PAGE_FILES["index.html"]})

    def test_serve_measures_the_folder_it_names(self) -> None:
        code, _, err = self.run_one("--serve", str(self.package))
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.fetched_bodies(), {PAGE_FILES["index.html"] + PACKAGE_MARK}
        )

    def test_the_summary_names_the_folder_serve_named(self) -> None:
        # The commit cannot tell a package's record from src/'s: one in an ignored folder
        # inside the checkout reads as the checkout's HEAD, clean, whatever commit it was
        # built from.
        code, _, err = self.run_one("--serve", str(self.package))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.summary()["served"], str(self.package))

    def test_the_summary_names_no_folder_for_the_default(self) -> None:
        # Null is the default, src/ under --root.
        code, _, err = self.run_one()
        self.assertEqual(code, 0, err)
        self.assertIsNone(self.summary()["served"])

    def test_serve_needs_no_src_under_the_root(self) -> None:
        # The root still supplies the pinned Lighthouse; the tree measured is the package.
        shutil.rmtree(self.root / "src")
        code, _, err = self.run_one("--serve", str(self.package))
        self.assertEqual(code, 0, err)

    def test_a_serve_that_is_a_file_is_refused(self) -> None:
        page = self.package / "index.html"
        code, _, err = self.run_main("--serve", str(page))
        self.assertEqual(code, 2)
        self.assertIn(f"--serve {page} is not a folder", err)
        self.assertFalse(self.log.exists())

    def test_a_serve_that_does_not_exist_is_refused(self) -> None:
        missing = self.root.parent / "missing"
        code, _, err = self.run_main("--serve", str(missing))
        self.assertEqual(code, 2)
        self.assertIn(f"--serve {missing} is not a folder", err)
        self.assertFalse(self.log.exists())

    def test_a_page_the_package_lacks_names_the_package(self) -> None:
        code, _, err = self.run_main(
            "--serve", str(self.package), "--pages", "/missing.html"
        )
        self.assertEqual(code, 2)
        self.assertIn(f"cannot measure {self.package}", err)
        self.assertFalse(self.log.exists())


class CommitReadTests(ProjectCase):
    """The commit is read before anything is measured, and git failing costs no summary."""

    def git_calls(self) -> list[dict[str, Any]]:
        """Every call the fake git logged, in order."""
        lines = self.git_log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def test_the_commit_is_read_before_the_first_run(self) -> None:
        code, out, _ = self.run_main("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0)
        self.assertEqual(self.summary()["commit"], FAKE_COMMIT)
        self.assertIs(self.summary()["src_dirty"], False)
        self.assertEqual({call["runs_before"] for call in self.git_calls()}, {0})
        self.assertIn(f"commit {FAKE_COMMIT}\n", out)

    def git_folders(self) -> set[Path]:
        """Every folder the fake git was run in, resolved as the expected folders are.

        Resolved, because git reports its folder in the spelling it was started with: on
        Windows a scratch folder under an 8.3 short name (`RUNNER~1`) is the same folder as
        its resolved long name, but not the same string.
        """
        return {Path(call["cwd"]).resolve() for call in self.git_calls()}

    def test_the_commit_is_read_for_src_by_default(self) -> None:
        code, _, err = self.run_main("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.git_folders(), {(self.root / "src").resolve()})

    def test_the_commit_is_read_for_the_served_folder(self) -> None:
        package = make_package(self.root.parent / "package")
        code, _, err = self.run_main(
            "--serve", str(package), "--pages", "/", "--presets", "mobile"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.git_folders(), {package.resolve()})

    def test_uncommitted_edits_name_the_served_folder(self) -> None:
        package = make_package(self.root.parent / "package")
        with mock.patch.dict(os.environ, {"FAKE_GIT_MODE": "dirty"}):
            code, out, err = self.run_main(
                "--serve", str(package), "--pages", "/", "--presets", "mobile"
            )
        self.assertEqual(code, 0, err)
        self.assertIs(self.summary()["src_dirty"], True)
        self.assertIn(f"commit {FAKE_COMMIT} ({package} has uncommitted edits)", out)

    def test_a_package_outside_any_repository_has_an_unknown_commit(self) -> None:
        # git exits 128 in a folder no repository holds, and commit_facts reads that as
        # unknown with nothing wrong -- no warning, and exit 0. The fake git answers, so
        # neither the host's git nor where its scratch folder sits decides the outcome; what
        # the real git says there is commit_facts' own suite to pin.
        package = make_package(self.root.parent / "package")
        with mock.patch.dict(os.environ, {"FAKE_GIT_MODE": "outside"}):
            code, out, err = self.run_main(
                "--serve", str(package), "--pages", "/", "--presets", "mobile"
            )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.git_folders(), {package.resolve()})
        self.assertIsNone(self.summary()["commit"])
        self.assertIsNone(self.summary()["src_dirty"])
        self.assertIn("commit unknown", out)
        self.assertEqual(err, "")

    def test_a_failed_status_is_unknown_rather_than_clean(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_GIT_MODE": "fail-status"}):
            code, out, err = self.run_main("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0)
        self.assertIsNone(self.summary()["commit"])
        self.assertIsNone(self.summary()["src_dirty"])
        self.assertIn("commit unknown", out)
        self.assertIn("unable to read the index", err)

    def test_a_git_that_times_out_still_writes_the_summary(self) -> None:
        # Only git's bound is shortened: the fake node and Chrome keep theirs, so a loaded
        # machine slowing THEM cannot turn this into a refusal.
        with (
            mock.patch.dict(os.environ, {"FAKE_GIT_MODE": "hang"}),
            mock.patch.object(lighthouse_run, "_GIT_TIMEOUT_SECONDS", 1.0),
        ):
            code, _, err = self.run_main("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0)
        self.assertIsNone(self.summary()["commit"])
        self.assertIsNone(self.summary()["src_dirty"])
        self.assertEqual(self.result("/", "mobile")["measured_runs"], 3)
        self.assertIn("timed out", err)


if __name__ == "__main__":
    unittest.main()
