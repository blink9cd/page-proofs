"""The fixtures pdf_raster's grouped suites are built on.

Shared across the grouped suites, by import or by inheritance alike. They live here
rather than inside one suite because a fixture reached ACROSS suites is what makes
the decomposition honest: the alternative is near-copies that drift, and a drifted
copy fails in whichever suite touches it first, pointing at the wrong subject.

Every helper is PUBLIC. A leading underscore would claim these are private to one
module, which stopped being true the moment a second suite imported them.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from hypothesis import HealthCheck, settings  # noqa: E402 - position only; keep below the bootstrap above
from pdf_raster import InkExtent, Raster, ink_extent  # noqa: E402 - position only; keep below the bootstrap above

# The determinism convention: a profile registered at import, never an `@settings`
# decorator, which a fuzzing campaign could not override. deadline=None: a per-example
# wall-clock deadline measures machine load, not correctness.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")


DPI = 96


#: How many centimeters make an inch, exactly, and two margins a PDF is printed with, in
#: inches: 1 cm, as Chromium prints, and the half inch Firefox prints with.
CM_PER_INCH = Fraction(254, 100)


ONE_CM = 1 / CM_PER_INCH


def gray(width: int, height: int, pixels: bytes) -> Raster:
    """A gray raster of `pixels`."""
    return Raster(width, height, 1, pixels)


def ink(gray: Raster, dpi: int = DPI) -> InkExtent:
    """The ink extent of `gray` at `dpi`, measured against a 1 cm margin, as Chromium prints."""
    return ink_extent(gray, dpi, margin_inches=ONE_CM)
