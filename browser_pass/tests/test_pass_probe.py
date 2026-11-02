"""Grouped suite: the in-page probe's source, as the driver composes it from its three files.

The probe is three bare function expressions: `page_probe.js`, a function of the other two;
`page_probe_motion.js`, the stilling both settles run; and `page_probe_trees.js`, the one walk
over the page's elements, each open shadow root's included. The driver hands an engine ONE
function's source (the first applied to the other two), and the late-script load's init script
calls that same function. These tests pin the text. Whether an engine runs it is the live suites'
question (`test_pass_live.py`, `test_pass_late_live.py`, whose late-script load needs the init
script to run before the page's own code), and the JS suites compose the same three files through
`page_probe_test_helpers.mjs`.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_probe  # noqa: E402 - the tool's private src/, via the insert above

_SRC = _TOOL_DIR / "src"


def _expression(name: str) -> str:
    """The function expression `name` holds: its text trimmed, its closing semicolon dropped.

    A semicolon ends a statement, so it may not sit inside the parentheses that apply one half
    to the other.
    """
    text = (_SRC / name).read_text(encoding="utf-8").strip()
    return text.removesuffix(";")


class ProbeSourceTests(unittest.TestCase):
    """The driver's probe is `page_probe.js` applied to its motion half and its walker, once."""

    def test_the_source_applies_the_probe_to_its_motion_half_and_its_walker(
        self,
    ) -> None:
        self.assertTrue((_SRC / "page_probe_motion.js").is_file())
        self.assertTrue((_SRC / "page_probe_trees.js").is_file())
        self.assertEqual(
            pass_probe.PROBE_SOURCE,
            f"({_expression('page_probe.js')})"
            f"({_expression('page_probe_motion.js')}, {_expression('page_probe_trees.js')})",
        )

    def test_the_init_script_calls_the_composed_probe_with_its_operation(self) -> None:
        call = json.dumps(["watchShifts", pass_probe.PROBE_CONFIG])
        self.assertEqual(
            pass_probe.init_script("watchShifts"),
            f"({pass_probe.PROBE_SOURCE})({call});",
        )

    def test_an_expression_calls_the_composed_probe_with_its_operation(self) -> None:
        # What the late-script load's DevTools session evaluates: the same probe, called.
        call = json.dumps(["listMotion", pass_probe.PROBE_CONFIG])
        self.assertEqual(
            pass_probe.probe_expression("listMotion"),
            f"({pass_probe.PROBE_SOURCE})({call})",
        )


if __name__ == "__main__":
    unittest.main()
