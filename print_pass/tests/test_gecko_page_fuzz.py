"""Suite: `gecko_page.timing_rows` by property.

Whatever a page hands back for its Resource Timing read (any nesting of lists, strings,
numbers, booleans, None and objects, as WebDriver's JSON can carry) is either refused with
`EngineError`, or read as exactly one document row and any number of resource rows, each a
URL and a whole status from 0 to 999. Nothing else is ever raised, and nothing malformed is
ever handed on. The strategies weight well-formed reads, so that acceptance is reached as
often as each refusal; the same function by example is `test_gecko_page.py`.

Entry point assumptions: standalone, or discovered by `unittest discover`; the bootstrap
below names the tool's private `src/`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_page  # noqa: E402 - the tool's private src/, via the insert above
from print_record import EngineError  # noqa: E402 - as above

# The determinism convention: a profile registered at import, never an
# `@settings` decorator. deadline=None and too_slow suppressed: a busy machine measures load.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: What JSON can carry, as WebDriver hands a script's answer back.
_JSON = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(max_size=10),
    ),
    lambda inner: st.one_of(
        st.lists(inner, max_size=4),
        st.dictionaries(st.text(max_size=5), inner, max_size=3),
    ),
    max_leaves=12,
)

#: A row much as a page gives one: mostly a URL and a status near the range's edges.
_ROWS = st.one_of(
    st.tuples(
        st.text(max_size=20),
        st.one_of(st.sampled_from((-1, 0, 200, 404, 999, 1000)), st.integers()),
    ).map(list),
    _JSON,
)
_READS = st.one_of(
    st.tuples(st.lists(_ROWS, min_size=1, max_size=2), st.lists(_ROWS, max_size=4)).map(
        list
    ),
    _JSON,
)

#: The widest status an accepted row may hold.
_HIGHEST_STATUS = 999


def _is_status(value: object) -> bool:
    """Whether `value` is a whole number of an HTTP status's range, and no boolean."""
    return type(value) is int and 0 <= value <= _HIGHEST_STATUS


class TimingRowsFuzzTests(unittest.TestCase):
    """Any answer is refused with `EngineError`, or read as well-formed rows."""

    @given(_READS)
    def test_fuzz_timing_rows(self, raw: object) -> None:
        try:
            document, entries = gecko_page.timing_rows(raw)
        except EngineError as error:
            self.assertTrue(
                str(error).startswith("Resource Timing read back malformed: ")
            )
            return
        for row in (document, *entries):
            self.assertIs(type(row["name"]), str)
            self.assertTrue(_is_status(row["responseStatus"]), row)


if __name__ == "__main__":
    unittest.main()
