"""Grouped suite: recording the Chrome that measured a run.

The commit a run measured is read by the shared `commit_facts`, and tested beside it.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import run_facts  # noqa: E402 - the tool's private src/, via the insert above
from _lighthouse_run_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    FAKE_CHROME,
    FAKE_CHROME_VERSION,
    FAKE_NODE,
    write_script,
)

_TIMEOUT = 30.0


class ChromeFactsTests(unittest.TestCase):
    """The Chrome Lighthouse would launch, located the way Lighthouse locates it."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.chrome = write_script(self.root / "bin" / "fake-chrome", FAKE_CHROME)
        self.node = write_script(self.root / "bin" / "node", FAKE_NODE)

    def facts(self, **environment: str) -> run_facts.ChromeFacts:
        """Chrome facts through the fake node, with `environment` steering the fakes."""
        steering = {"FAKE_CHROME_PATH": str(self.chrome), **environment}
        with mock.patch.dict(os.environ, steering):
            return run_facts.chrome_facts(
                self.root, node=str(self.node), timeout=_TIMEOUT
            )

    def assert_refused(self, fragment: str, **environment: str) -> None:
        """Assert locating Chrome fails with a message naming `fragment`."""
        with self.assertRaises(run_facts.FactError) as caught:
            self.facts(**environment)
        self.assertIn(fragment, str(caught.exception))

    def test_reports_the_version_of_the_chrome_node_located(self) -> None:
        self.assertEqual(
            self.facts(),
            run_facts.ChromeFacts(path=str(self.chrome), version=FAKE_CHROME_VERSION),
        )

    def test_without_node_chrome_cannot_be_located(self) -> None:
        with self.assertRaises(run_facts.FactError) as caught:
            run_facts.chrome_facts(self.root, node=None, timeout=_TIMEOUT)
        self.assertIn("node", str(caught.exception))

    def test_a_node_failure_carries_its_stderr(self) -> None:
        self.assert_refused("cannot resolve chrome-launcher", FAKE_NODE_MODE="fail")

    def test_node_printing_no_path_is_refused(self) -> None:
        self.assert_refused("no path", FAKE_CHROME_PATH="")

    def test_a_chrome_that_cannot_report_its_version_is_refused(self) -> None:
        self.assert_refused("--version", FAKE_CHROME_MODE="fail")

    def real_node(self) -> str:
        """The real node on PATH, skipping the test when there is none."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        return node

    def test_the_real_node_resolves_chrome_launcher_through_lighthouse(self) -> None:
        # The live boundary: the real node running the real script, against a fake
        # chrome-launcher placed where npm hoists it. The fakes above ignore their argv, so
        # only this proves the script survives `-e` and resolves from Lighthouse's install.
        facts = run_facts.chrome_facts(
            self.fake_launcher_project(), node=self.real_node(), timeout=_TIMEOUT
        )
        self.assertEqual(facts.version, FAKE_CHROME_VERSION)

    def test_a_relative_project_root_still_resolves_through_lighthouse(self) -> None:
        # Regression: the operator's documented command is `--root .`, and node's
        # createRequire refuses a relative manifest path -- the first live run exited 2 on
        # exactly that, while the absolute root above passed.
        node = self.real_node()
        project = self.fake_launcher_project()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(project.parent)
        facts = run_facts.chrome_facts(Path(project.name), node=node, timeout=_TIMEOUT)
        self.assertEqual(facts.version, FAKE_CHROME_VERSION)

    def fake_launcher_project(self) -> Path:
        """A project whose node_modules holds Lighthouse's manifest and a fake chrome-launcher."""
        project = self.root / "project"
        modules = project / "node_modules"
        (modules / "lighthouse").mkdir(parents=True)
        (modules / "lighthouse" / "package.json").write_text(
            '{"name": "lighthouse"}', encoding="utf-8", newline="\n"
        )
        launcher = modules / "chrome-launcher"
        launcher.mkdir()
        (launcher / "package.json").write_text(
            '{"name": "chrome-launcher", "type": "module", "main": "./index.js"}',
            encoding="utf-8",
            newline="\n",
        )
        (launcher / "index.js").write_text(
            f"export function getChromePath() {{ return {json.dumps(str(self.chrome))}; }}\n",
            encoding="utf-8",
            newline="\n",
        )
        return project


if __name__ == "__main__":
    unittest.main()
