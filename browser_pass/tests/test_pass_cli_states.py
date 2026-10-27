"""Grouped suite: what the CLI does with a targets file's states.

Each state renders after the pages, with scripts on only; its page is confirmed against the
served tree like any other page; the engine is handed the states to reach; a finding in one is
announced with its state; and a state `--scripts` leaves out is counted and said, never dropped
in silence. The engine is `_cli_fixtures.FakeEngine`, as in `test_pass_cli.py`.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's folder on
# sys.path, and the CLI it imports adds the tool's src/, for every import after it.
from _cli_fixtures import TARGETS, CliCase

import json
import unittest

from pass_report import JSON_NAME
from pass_targets import Action, Rendering, State

#: One state of the `/archive/` page, reached through a query and two actions.
_STATE_TABLE = """
[[state]]
name = "opened"
page = "/archive/?from=state"
actions = [{ click = "#open" }, { wait_ms = 50 }]
"""

_OPENED = State(
    name="opened",
    page="/archive/?from=state",
    storage=(),
    actions=(Action("click", "#open", None), Action("wait_ms", None, 50)),
)


class StateRunTests(CliCase):
    """A targets file holding a state."""

    def setUp(self) -> None:
        super().setUp()
        self.write_targets(_STATE_TABLE)

    def write_targets(self, state_table: str) -> None:
        """The scratch project's targets file, with `state_table` after its pages."""
        self.targets.write_text(TARGETS + state_table, encoding="utf-8", newline="\n")

    def test_renders_a_state_with_scripts_on_and_hands_the_engine_the_states(
        self,
    ) -> None:
        code, _, err = self.run_cli(
            "--serve",
            str(self.site),
            "--engines",
            "chromium",
            "--orientations",
            "portrait",
            "--modes",
            "light",
        )
        self.assertEqual((code, err), (0, ""))
        (call,) = self.engine.calls
        self.assertEqual(
            call["renderings"],
            [
                Rendering("chromium", "/", 390, 844, "light", "on"),
                Rendering("chromium", "/", 390, 844, "light", "off"),
                Rendering("chromium", "/archive/", 390, 844, "light", "on"),
                Rendering("chromium", "/archive/", 390, 844, "light", "off"),
                Rendering(
                    "chromium",
                    "/archive/?from=state",
                    390,
                    844,
                    "light",
                    "on",
                    "opened",
                ),
            ],
        )
        self.assertEqual(call["states"], [_OPENED])

    def test_a_state_page_the_served_tree_does_not_hold_is_refused(self) -> None:
        self.write_targets(_STATE_TABLE.replace("/archive/?", "/gone/?"))
        code, _, err = self.run_cli("--serve", str(self.site))
        self.assertEqual(code, 2)
        self.assertIn("cannot serve", err)
        self.assertEqual(self.engine.calls, [])

    def test_a_finding_in_a_state_is_announced_with_its_state(self) -> None:
        self.write_targets(_STATE_TABLE.replace("/archive/?from=state", "/"))
        self.engine.findings = {"small_target": ("body>button:1 16x16",)}
        code, out, _ = self.run_cli("--serve", str(self.site), "--engines", "chromium")
        self.assertEqual(code, 1)
        self.assertIn("chromium / [opened] 390x844 light scripts-on: small_target", out)

    def test_a_state_scripts_off_leaves_out_is_counted_and_said(self) -> None:
        code, out, _ = self.run_cli(
            "--serve", str(self.site), "--engines", "chromium", "--scripts", "off"
        )
        self.assertEqual(code, 0)
        renderings = self.engine.calls[0]["renderings"]
        self.assertEqual({r.state for r in renderings}, {None})  # type: ignore[attr-defined] # a list of Rendering
        self.assertIn("1 state not rendered: a state renders with scripts on", out)

    def test_every_rendered_state_is_said_nothing_of(self) -> None:
        _, out, _ = self.run_cli("--serve", str(self.site), "--engines", "chromium")
        self.assertNotIn("not rendered", out)

    def test_the_report_records_each_rendering_s_state(self) -> None:
        self.run_cli("--serve", str(self.site), "--engines", "webkit")
        text = (self.only_run_dir() / JSON_NAME).read_text(encoding="utf-8")
        states = [record["state"] for record in json.loads(text)["renderings"]]
        self.assertEqual(states.count("opened"), 4)
        self.assertEqual(states.count(None), 16)


if __name__ == "__main__":
    unittest.main()
