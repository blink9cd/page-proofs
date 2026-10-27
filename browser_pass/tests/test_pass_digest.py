"""Grouped suite: the per-element digest, and the cross-engine comparison with its noise filtered."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_digest  # noqa: E402 - the tool's private src/, via the insert above
from pass_checks import ProbeError  # noqa: E402 - the tool's private src/, via the insert above

# The determinism convention: a profile registered at import, never an `@settings`
# decorator, which a fuzzing run applying its own settings could not override.
# deadline=None: a per-example wall-clock deadline measures machine load, not
# correctness, so a busy machine would report a passing property as flaky.
# too_slow is suppressed for the same reason: on a cold .hypothesis cache the
# first draw also builds Hypothesis's unicode tables and scans the local modules
# for constants, which under load outlasts the limit that check allows even
# without a deadline.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

Box = pass_digest.Box


def _box(
    x: float = 0.0,
    y: float = 0.0,
    width: float = 100.0,
    height: float = 20.0,
    font: float = 16.0,
    *,
    shown: bool = True,
) -> pass_digest.Box:
    """A box with defaults, so each test names only what it varies."""
    return Box(shown, x, y, width, height, font)


class ParseDigestTests(unittest.TestCase):
    """Reading the probe's digest rows."""

    def test_reads_one_row_per_element_keyed_by_path(self) -> None:
        digest = pass_digest.parse_digest(
            [
                ["body>main:1", 1, 0, 68.5, 390, 900, 16],
                ["body>p:1", 0, 0, 0, 0, 0, 13.33],
            ]
        )
        self.assertEqual(
            digest,
            {
                "body>main:1": Box(True, 0.0, 68.5, 390.0, 900.0, 16.0),
                "body>p:1": Box(False, 0.0, 0.0, 0.0, 0.0, 13.33),
            },
        )

    def test_refuses_rows_of_the_wrong_shape(self) -> None:
        cases: tuple[object, ...] = (
            "rows",
            [["body>p:1", 1, 0, 0]],
            [[3, 1, 0, 0, 0, 0, 16]],
            [["p", 2, 0, 0, 0, 0, 16]],
            [["p", 1.0, 0, 0, 0, 0, 16]],
            [["p", 1, 0, 0, 0, 0, "16px"]],
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaisesRegex(ProbeError, "digest"):
                pass_digest.parse_digest(raw)

    @given(
        st.recursive(
            st.none()
            | st.booleans()
            | st.integers()
            | st.floats()
            | st.text(max_size=6),
            lambda inner: st.lists(inner, max_size=8),
            max_leaves=30,
        )
    )
    def test_fuzz_parse_digest(self, raw: object) -> None:
        """Any shape parses or is refused as a ProbeError -- never another exception."""
        try:
            digest = pass_digest.parse_digest(raw)
        except ProbeError:
            return
        self.assertTrue(all(isinstance(box, Box) for box in digest.values()))


class CompareTests(unittest.TestCase):
    """Differences between two engines' digests of the same rendering."""

    def test_identical_digests_have_no_differences(self) -> None:
        digest = {"body>p:1": _box()}
        self.assertEqual(pass_digest.compare_digests(digest, dict(digest)), [])

    def test_a_shift_within_half_a_pixel_is_rounding_not_a_difference(self) -> None:
        self.assertEqual(
            pass_digest.compare_digests(
                {"p": _box(x=10)}, {"p": _box(x=10.5, height=20.4)}
            ),
            [],
        )

    def test_reports_horizontal_and_vertical_separately(self) -> None:
        differences = pass_digest.compare_digests(
            {"p": _box(x=10, y=100)}, {"p": _box(x=10, width=102, y=103)}
        )
        self.assertEqual(
            differences,
            [
                pass_digest.Difference("p", "horizontal", 2.0),
                pass_digest.Difference("p", "vertical", 3.0),
            ],
        )

    def test_a_font_size_under_a_tenth_of_a_pixel_is_noise(self) -> None:
        self.assertEqual(
            pass_digest.compare_digests(
                {"p": _box(font=13.333)}, {"p": _box(font=13.4)}
            ),
            [],
        )
        self.assertEqual(
            pass_digest.compare_digests({"p": _box(font=13.3)}, {"p": _box(font=13.4)}),
            [pass_digest.Difference("p", "font", 0.1)],
        )

    def test_an_element_neither_engine_displays_is_noise(self) -> None:
        hidden = _box(shown=False)
        self.assertEqual(
            pass_digest.compare_digests({"p": hidden}, {"p": _box(shown=False, x=500)}),
            [],
        )

    def test_an_element_one_engine_displays_is_a_difference(self) -> None:
        self.assertEqual(
            pass_digest.compare_digests({"p": _box()}, {"p": _box(shown=False)}),
            [pass_digest.Difference("p", "display", 0.0)],
        )

    def test_an_element_only_one_engine_built_counts_only_when_displayed(self) -> None:
        self.assertEqual(
            pass_digest.compare_digests(
                {"a": _box(), "b": _box(shown=False)}, {"c": _box()}
            ),
            [
                pass_digest.Difference("a", "presence", 0.0),
                pass_digest.Difference("c", "presence", 0.0),
            ],
        )


class SummaryTests(unittest.TestCase):
    """Collapsing the differences to one line per axis, located at the first in page order."""

    def test_names_the_count_the_first_element_and_where_the_largest_is(self) -> None:
        summary = pass_digest.summarize(
            [
                pass_digest.Difference("header", "vertical", 0.92),
                pass_digest.Difference("main", "vertical", 2.92),
                pass_digest.Difference("select", "horizontal", 2.0),
            ]
        )
        self.assertEqual(
            summary,
            {
                "vertical": pass_digest.AxisSummary(2, "header", 0.92, "main", 2.92),
                "horizontal": pass_digest.AxisSummary(1, "select", 2.0, "select", 2.0),
            },
        )

    def test_a_tie_for_the_largest_names_the_first_in_page_order(self) -> None:
        summary = pass_digest.summarize(
            [
                pass_digest.Difference("header", "vertical", 0.92),
                pass_digest.Difference("main", "vertical", 9.5),
                pass_digest.Difference("footer", "vertical", 9.5),
            ]
        )
        self.assertEqual(summary["vertical"].max_path, "main")

    def test_nothing_to_summarize_is_an_empty_summary(self) -> None:
        self.assertEqual(pass_digest.summarize([]), {})


if __name__ == "__main__":
    unittest.main()
