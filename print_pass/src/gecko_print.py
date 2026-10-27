"""The WebDriver Print body Firefox is printed with, and what that command cannot set.

`print_parameters` is the body of the W3C WebDriver Print command, which geckodriver hands
to Firefox's own print path: the static copy of the page made just after `beforeprint`,
the one a reader's print is laid out from. The command measures the page and its margins
in centimeters. The paper is US Letter, which Firefox also prints on when the command
names none, and every margin is 0.5 in (1.27 cm), what Firefox's own print dialog puts on
Letter, so a print here is laid out as a reader's would be who left the dialog alone --
unless a state's `page_width` sets a page width of its own, whose margin the caller passes.
Scale 1, shrink-to-fit and portrait are the command's own defaults, sent all the same, so
the report can state each from the body itself rather than from a default it assumes.

What the command cannot set is `CANNOT_CONTROL`, and the report states it beside the
body. The command has no tagging parameter, and Firefox's PDF is always untagged; it never
prints the dialog's header and footer; and it writes no `Title` into the PDF, where the
dialog's own Save to PDF copies the page's `<title>`.

The margin is held as exact inches -- `MARGIN_INCHES`, or the one a caller passes -- which
a Firefox PDF's ink is measured against; the centimeters the command is sent are derived
from it here, so the two cannot drift.

Nothing here is checked at run time. Firefox does not check the values either -- a 5 cm
margin on a 1 cm page printed without a word -- so the suite proves the default margins
leave a printable area on the page, and `print_targets` bounds a page width so its margin
always does.

Entry point assumptions: the standard library, and `print_record`'s `CM_PER_INCH`.
"""

from __future__ import annotations

from fractions import Fraction
from types import MappingProxyType
from typing import Final

from print_record import CM_PER_INCH

#: US Letter, 8.5 by 11 in, in the command's centimeters. Read-only: each call hands out a
#: copy of its own.
PAGE_CM: Final = MappingProxyType({"width": 21.59, "height": 27.94})

#: The margin on every side: Firefox's own print dialog default on Letter, 0.5 in, exactly.
MARGIN_INCHES: Final = Fraction(1, 2)
#: The same margin as the command is sent it, in centimeters: 1.27. `print_parameters` derives
#: its default from `MARGIN_INCHES` itself; this is the sent form the tests hold it to.
MARGIN_CM: Final = float(MARGIN_INCHES * CM_PER_INCH)

#: What WebDriver's Print cannot set, one sentence each: tagging, the header and footer,
#: and the Title.
CANNOT_CONTROL: Final[tuple[str, ...]] = (
    "WebDriver's Print takes no tagging parameter, and Firefox's PDF is always untagged.",
    "WebDriver's Print never prints the print dialog's header and footer.",
    "WebDriver's Print writes no Title into the PDF, where the dialog's Save to PDF "
    "copies the page's title.",
)

# The four sides the command's `margin` names, in the order the W3C command lists them.
_MARGIN_SIDES: Final = ("top", "bottom", "left", "right")

# Scale 1, shrink-to-fit on, and portrait: the command's own defaults, sent explicitly.
_SCALE: Final = 1
_SHRINK_TO_FIT: Final = True
_PORTRAIT: Final = "portrait"


def print_parameters(
    *, backgrounds: bool, margin_inches: Fraction = MARGIN_INCHES
) -> dict[str, object]:
    """The WebDriver Print body for one PDF: Letter, `margin_inches` a side, `backgrounds`.

    The margin defaults to the dialog's half inch, and is sent in the command's centimeters,
    as near as a float holds them. The keys are the W3C command's own. Every table is a
    fresh dictionary on every call, so no caller can reach another's, nor the module's page.
    """
    return {
        "page": dict(PAGE_CM),
        "margin": dict.fromkeys(_MARGIN_SIDES, float(margin_inches * CM_PER_INCH)),
        "background": backgrounds,
        "scale": _SCALE,
        "shrinkToFit": _SHRINK_TO_FIT,
        "orientation": _PORTRAIT,
    }
