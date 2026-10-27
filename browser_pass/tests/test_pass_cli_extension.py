"""Grouped suite: the CLI's extension options and `--loopback-only`, through the fake engine.

Each refusal is exit 2 with one line naming why, before any browser opens -- the fake engine
records no call. An accepted run hands the engine its isolation and records, in the report's
facts, whether off-loopback requests were refused and each engine's extension by name and version
(and gecko id). The engine is `_cli_fixtures.FakeEngine`, as in `test_pass_cli.py`.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's folder on
# sys.path, and the CLI it imports adds the tool's src/, for every import after it.
from _cli_fixtures import CliCase

import json
import unittest
from typing import Any

import pass_extension
from _extension_fixtures import GECKO_ID, unpacked, xpi
from browser_pass import build_parser
from pass_extension import ExtensionFacts, Isolation
from pass_report import JSON_NAME, MARKDOWN_NAME


class ExtensionCliTests(CliCase):
    """`--chromium-extension`, `--firefox-extension` and `--loopback-only`."""

    def setUp(self) -> None:
        super().setUp()
        self.chromium = unpacked(self.root / "ext")
        self.firefox = xpi(self.root / "fixture.xpi")

    def _refused(self, *args: str) -> str:
        code, out, err = self.run_cli("--serve", str(self.site), *args)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(self.engine.calls, [], "no browser opens for a refused run")
        (line,) = err.strip().splitlines()
        return line

    def _facts(self) -> dict[str, Any]:
        record = json.loads((self.only_run_dir() / JSON_NAME).read_text("utf-8"))
        facts: dict[str, Any] = record["facts"]
        return facts

    def test_they_are_off_by_default(self) -> None:
        args = build_parser().parse_args(["--targets", "t.toml"])
        self.assertIsNone(args.chromium_extension)
        self.assertIsNone(args.firefox_extension)
        self.assertFalse(args.loopback_only)
        code, _, _ = self.run_cli("--serve", str(self.site), "--engines", "webkit")
        self.assertEqual(code, 0)
        self.assertEqual(self.engine.calls[0]["isolation"], pass_extension.NO_ISOLATION)
        self.assertIs(self._facts()["loopback_only"], False)
        self.assertEqual(self._facts()["extensions"], [])
        markdown = (self.only_run_dir() / MARKDOWN_NAME).read_text("utf-8")
        self.assertIn("- Off-loopback requests: allowed", markdown)

    def test_loopback_only_alone_guards_a_baseline_in_every_engine(self) -> None:
        code, _, _ = self.run_cli("--serve", str(self.site), "--loopback-only")
        self.assertEqual(code, 0)
        self.assertEqual(
            self.engine.calls[0]["isolation"], Isolation(loopback_only=True)
        )
        self.assertIs(self._facts()["loopback_only"], True)
        self.assertEqual(self._facts()["extensions"], [])

    def test_both_extensions_run_with_the_guard_and_are_recorded(self) -> None:
        code, _, _ = self.run_cli(
            "--serve",
            str(self.site),
            "--engines",
            "chromium",
            "firefox",
            "--chromium-extension",
            str(self.chromium),
            "--firefox-extension",
            str(self.firefox),
        )
        self.assertEqual(code, 0)
        isolation = self.engine.calls[0]["isolation"]
        assert isinstance(isolation, Isolation)
        self.assertTrue(isolation.loopback_only)
        self.assertEqual(
            isolation.facts,
            (
                ExtensionFacts("chromium", "Fixture", "2.5.1"),
                ExtensionFacts("firefox", "Fixture", "4.9", GECKO_ID),
            ),
        )
        facts = self._facts()
        self.assertIs(facts["loopback_only"], True)
        self.assertEqual(
            facts["extensions"],
            [
                {
                    "engine": "chromium",
                    "name": "Fixture",
                    "version": "2.5.1",
                    "gecko_id": None,
                },
                {
                    "engine": "firefox",
                    "name": "Fixture",
                    "version": "4.9",
                    "gecko_id": GECKO_ID,
                },
            ],
        )
        markdown = (self.only_run_dir() / MARKDOWN_NAME).read_text("utf-8")
        self.assertIn("- Off-loopback requests: refused (--loopback-only)", markdown)
        self.assertIn("- Extension in chromium: Fixture 2.5.1", markdown)
        self.assertIn(f"- Extension in firefox: Fixture 4.9 ({GECKO_ID})", markdown)

    def test_a_folder_without_a_manifest_is_refused(self) -> None:
        line = self._refused(
            "--engines", "chromium", "--chromium-extension", str(self.root / "absent")
        )
        self.assertIn("is not a folder holding manifest.json", line)

    def test_an_xpi_without_a_gecko_id_is_refused(self) -> None:
        bare = xpi(self.root / "bare.xpi", {"name": "x", "version": "1"})
        line = self._refused("--engines", "firefox", "--firefox-extension", str(bare))
        self.assertIn("names no gecko id", line)

    def test_an_engine_left_without_an_extension_is_refused_naming_it(self) -> None:
        # The default --engines names all three, so firefox has none.
        line = self._refused("--chromium-extension", str(self.chromium))
        self.assertIn("--engines names firefox, which has no extension", line)

    def test_webkit_cannot_load_one(self) -> None:
        line = self._refused(
            "--engines",
            "chromium",
            "firefox",
            "webkit",
            "--chromium-extension",
            str(self.chromium),
            "--firefox-extension",
            str(self.firefox),
        )
        self.assertIn("webkit, which cannot load an extension", line)

    def test_an_off_loopback_base_url_cannot_be_guarded(self) -> None:
        self.targets.write_text(
            'base_url = "http://localhost:8123"\n'
            + self.targets.read_text(encoding="utf-8"),
            encoding="utf-8",
            newline="\n",
        )
        code, _, err = self.run_cli("--loopback-only")
        self.assertEqual(code, 2)
        self.assertEqual(self.engine.calls, [])
        self.assertIn("base_url is on localhost: pass --serve", err)

    def test_help_names_the_three_options(self) -> None:
        text = build_parser().format_help()
        for option in (
            "--chromium-extension",
            "--firefox-extension",
            "--loopback-only",
        ):
            self.assertIn(option, text)


if __name__ == "__main__":
    unittest.main()
