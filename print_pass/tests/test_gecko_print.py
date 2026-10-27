"""Suite: the WebDriver Print body Firefox is printed with, and what that command cannot set.

The parameters are fixed values, so nothing checks them at run time. Firefox does not check
them either: it accepted a 5 cm margin on a 1 cm page without a word. So this suite is
where an impossible paper or margin fails, and where the paper and margin are tied to the
inches they were measured in.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`.
"""

from __future__ import annotations

import sys
import unittest
from fractions import Fraction
from pathlib import Path
from typing import Final, cast

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_print  # noqa: E402 - the tool's private src/, via the insert above

#: Centimeters per inch, exactly.
_CM_PER_INCH: Final = Fraction(254, 100)

#: US Letter in inches, and the half inch Firefox's print dialog puts on every side.
_LETTER_INCHES: Final = (Fraction(17, 2), Fraction(11))
_MARGIN_INCHES: Final = Fraction(1, 2)

#: The four sides the W3C Print command's `margin` names.
_SIDES: Final = frozenset({"top", "bottom", "left", "right"})


def _numbers(value: object) -> dict[str, float]:
    """A parameter that is a table of numbers, as the suite reads it."""
    return cast(dict[str, float], value)


class ParameterTests(unittest.TestCase):
    """The body is the W3C Print command's, every value the one asked."""

    def test_the_body_holds_exactly_the_w3c_print_parameters(self) -> None:
        self.assertEqual(
            gecko_print.print_parameters(backgrounds=False),
            {
                "page": {"width": 21.59, "height": 27.94},
                "margin": {"top": 1.27, "bottom": 1.27, "left": 1.27, "right": 1.27},
                "background": False,
                "scale": 1,
                "shrinkToFit": True,
                "orientation": "portrait",
            },
        )

    def test_backgrounds_is_the_one_parameter_a_call_chooses(self) -> None:
        on = gecko_print.print_parameters(backgrounds=True)
        off = gecko_print.print_parameters(backgrounds=False)
        self.assertIs(on["background"], True)
        self.assertEqual(
            {key: value for key, value in on.items() if key != "background"},
            {key: value for key, value in off.items() if key != "background"},
        )

    def test_each_call_hands_out_tables_of_its_own(self) -> None:
        first = gecko_print.print_parameters(backgrounds=False)
        _numbers(first["page"])["width"] = 1.0
        _numbers(first["margin"])["top"] = 9.0
        second = gecko_print.print_parameters(backgrounds=False)
        self.assertEqual(
            _numbers(second["page"])["width"], gecko_print.PAGE_CM["width"]
        )
        self.assertEqual(_numbers(second["margin"])["top"], gecko_print.MARGIN_CM)

    def test_the_page_table_cannot_be_changed_in_place(self) -> None:
        with self.assertRaises(TypeError):
            gecko_print.PAGE_CM["width"] = 1.0  # type: ignore[index]


class MeasureTests(unittest.TestCase):
    """The paper is US Letter, and the margins are Firefox's own dialog default."""

    def test_the_page_is_us_letter(self) -> None:
        width, height = (inches * _CM_PER_INCH for inches in _LETTER_INCHES)
        # Read back exactly, as the margin is: each centimeter value sent is a short decimal.
        self.assertEqual(Fraction(str(gecko_print.PAGE_CM["width"])), width)
        self.assertEqual(Fraction(str(gecko_print.PAGE_CM["height"])), height)

    def test_the_margin_is_half_an_inch(self) -> None:
        self.assertEqual(gecko_print.MARGIN_INCHES, _MARGIN_INCHES)

    def test_the_margin_measured_is_the_margin_printed(self) -> None:
        # Two spellings of one margin: the command is sent centimeters, and the ink extent
        # measures a Firefox PDF against `MARGIN_INCHES`. Each side sent must be that, exactly.
        sent = _numbers(gecko_print.print_parameters(backgrounds=False)["margin"])
        for side, centimeters in sent.items():
            with self.subTest(side=side):
                inches = Fraction(str(centimeters)) / _CM_PER_INCH
                self.assertEqual(inches, gecko_print.MARGIN_INCHES)

    def test_the_margins_leave_a_printable_area_on_the_page(self) -> None:
        # Firefox accepts margins wider than the page without a word, so this is the
        # only place an impossible one fails.
        parameters = gecko_print.print_parameters(backgrounds=False)
        page, margin = _numbers(parameters["page"]), _numbers(parameters["margin"])
        self.assertEqual(margin.keys(), _SIDES)
        self.assertTrue(all(value >= 0 for value in margin.values()), margin)
        self.assertGreater(page["width"] - margin["left"] - margin["right"], 0)
        self.assertGreater(page["height"] - margin["top"] - margin["bottom"], 0)


class CannotControlTests(unittest.TestCase):
    """What the command cannot set: one sentence each, for the report to state."""

    def test_tagging_the_header_and_footer_and_the_title_are_each_named(self) -> None:
        sentences = gecko_print.CANNOT_CONTROL
        self.assertEqual(len(sentences), 3)
        self.assertIn("untagged", sentences[0])
        self.assertIn("header", sentences[1])
        self.assertIn("footer", sentences[1])
        self.assertIn("Title", sentences[2])

    def test_each_is_one_sentence(self) -> None:
        for sentence in gecko_print.CANNOT_CONTROL:
            with self.subTest(sentence=sentence):
                self.assertTrue(sentence[0].isupper(), sentence)
                self.assertTrue(sentence.endswith("."), sentence)
                self.assertNotIn(". ", sentence)
                self.assertNotIn("\n", sentence)


if __name__ == "__main__":
    unittest.main()
