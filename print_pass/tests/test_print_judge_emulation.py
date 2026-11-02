"""Suite: the emulated-dark warning follows the emulation, not the scheme a state asked for.

Playwright's emulated color scheme persists into `page.pdf()`, which is what the warning is
about. A state that asked for dark and was not emulated (Firefox reaches dark through a
preference of its own, and records it as not emulated, as installed Chrome's native dark is)
prints the way a reader's dark setting does, so it carries no such warning. Each case
hands `state_problems` one state and says whether its color scheme was emulated.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Final

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_judge  # noqa: E402 - the tool's private src/, via the insert above
from print_probe import ProbeResult  # noqa: E402 - as above
from print_record import NetworkRecord, Problem  # noqa: E402 - as above
from print_targets import State  # noqa: E402 - as above

#: A network record with nothing in it.
_QUIET: Final = NetworkRecord(
    failed=(), http_errors=(), blocked=(), page_errors=(), csp_violations=()
)

#: The warning an emulated-dark state carries.
_EMULATED_DARK: Final = Problem("warning", print_judge.EMULATED_DARK_WARNING)


def _state(color_scheme: str) -> State:
    """A state asking for `color_scheme`, and nothing else."""
    return State(color_scheme, color_scheme, "native", (), (), None)


def _probe(color_scheme: str) -> ProbeResult:
    """A settled read-back of a page reading `color_scheme`, with nothing to warn of."""
    return ProbeResult(
        color_scheme=color_scheme,
        reduced_motion="no-preference",
        ready_state="complete",
        fonts_status="loaded",
        title="Sample",
        images=(),
        animations=(),
        reads=(),
        storage=(),
    )


def _judged(
    color_scheme: str, *, emulated: bool, read_back: bool = True
) -> tuple[Problem, ...]:
    """The problems of a quiet state asking for `color_scheme`, emulated or not.

    Without `read_back`, the state stopped before its read-back.
    """
    probe = _probe(color_scheme) if read_back else None
    return print_judge.state_problems(
        _state(color_scheme),
        probe,
        _QUIET,
        expected_hosts=(),
        color_scheme_emulated=emulated,
    )


class EmulatedDarkTests(unittest.TestCase):
    """Only a dark scheme that was emulated carries the warning."""

    def test_an_emulated_dark_state_carries_the_warning(self) -> None:
        self.assertEqual(_judged("dark", emulated=True), (_EMULATED_DARK,))

    def test_a_dark_state_not_emulated_carries_none(self) -> None:
        # Firefox's dark, set through a preference: recorded as not emulated.
        self.assertEqual(_judged("dark", emulated=False), ())

    def test_one_stopped_before_its_read_back_is_judged_the_same(self) -> None:
        self.assertEqual(
            _judged("dark", emulated=True, read_back=False), (_EMULATED_DARK,)
        )
        self.assertEqual(_judged("dark", emulated=False, read_back=False), ())

    def test_an_emulated_light_state_carries_none(self) -> None:
        self.assertEqual(_judged("light", emulated=True), ())

    def test_a_native_state_carries_none(self) -> None:
        self.assertEqual(_judged("native", emulated=False), ())


if __name__ == "__main__":
    unittest.main()
