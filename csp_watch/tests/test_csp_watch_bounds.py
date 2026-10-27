"""The bounds on what the binding admits: a page's own script can call it with anything.

The listener cuts a sample to its first 40 characters, and the other three values are what an
engine reports for a URL, so a longer value is one the listener never sends: refused as an
instrument fault, exactly as a value that is not text is, rather than kept in the log.

Entry point assumptions: discovered by ``test_csp_watch.py`` with this folder as the top-level
directory; it puts the tool folder on ``sys.path`` itself, so it also imports alone.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

_TOOL_DIR: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR))

import csp_watch  # noqa: E402 - the tool folder, via the insert above

#: The listener's own sample cut, read from its text rather than from the module under test.
_SAMPLE_CUT = re.search(
    r"const SAMPLE_LIMIT = (\d+);",
    (_TOOL_DIR / "src" / "csp_listener.js").read_text(encoding="utf-8"),
)
assert _SAMPLE_CUT is not None, "the listener no longer names its sample cut"
_SAMPLE_LIMIT: Final = int(_SAMPLE_CUT.group(1))

#: A payload exactly as the listener sends one.
_WELL_FORMED: Final = {
    "document": "/404.html?x=1",
    "directive": "style-src-attr",
    "blocked": "inline",
    "sample": "",
}

#: What a binding's callback is handed first: the page's context, page and frame.
_SOURCE: Final = {"context": None, "page": None, "frame": None}

#: The values bounded by the URL-sized limit rather than the sample's.
_URL_SIZED: Final = ("document", "directive", "blocked")


class BindingBoundsTest(unittest.TestCase):
    """A value longer than the listener sends is refused, and faults the log."""

    def test_the_sample_bound_is_the_one_the_listener_cuts_to(self) -> None:
        self.assertEqual(csp_watch.SAMPLE_LIMIT, _SAMPLE_LIMIT)

    def test_a_sample_the_listener_would_have_cut_is_refused(self) -> None:
        log = csp_watch.CspLog()
        log.record(_SOURCE, {**_WELL_FORMED, "sample": "x" * _SAMPLE_LIMIT})
        with self.assertRaisesRegex(csp_watch.CspWatchError, r"\['sample'\]"):
            log.record(_SOURCE, {**_WELL_FORMED, "sample": "x" * (_SAMPLE_LIMIT + 1)})
        with self.assertRaisesRegex(csp_watch.CspWatchError, "longer than"):
            log.mark()

    def test_the_sample_bound_counts_characters_as_the_listener_cuts_them(self) -> None:
        # The listener cuts by character (Array.from), so a sample of astral characters is
        # twice as many UTF-16 units as the bound and must still be admitted.
        astral = "\N{GRINNING FACE}" * _SAMPLE_LIMIT
        log = csp_watch.CspLog()
        log.record(_SOURCE, {**_WELL_FORMED, "sample": astral})
        self.assertEqual(log.violations[0].sample, astral)

    def test_each_url_sized_value_is_bounded(self) -> None:
        for key in _URL_SIZED:
            with self.subTest(key=key):
                log = csp_watch.CspLog()
                log.record(_SOURCE, {**_WELL_FORMED, key: "x" * csp_watch.URL_LIMIT})
                over = {**_WELL_FORMED, key: "x" * (csp_watch.URL_LIMIT + 1)}
                with self.assertRaisesRegex(csp_watch.CspWatchError, repr(key)):
                    log.record(_SOURCE, over)

    def test_a_page_calling_the_binding_with_megabytes_faults_the_log(self) -> None:
        # The binding's callback is the log's record: what a page's own script sends it is
        # refused rather than kept, and the fault names the field without repeating its text.
        log = csp_watch.CspLog()
        huge = "x" * (4 * 1024 * 1024)
        with self.assertRaises(csp_watch.CspWatchError) as caught:
            log.record(_SOURCE, {**_WELL_FORMED, "blocked": huge})
        self.assertNotIn(huge[:100], str(caught.exception))
        with self.assertRaisesRegex(csp_watch.CspWatchError, "longer than"):
            log.mark()

    @given(
        st.sampled_from([*_URL_SIZED, "sample"]),
        st.integers(min_value=1, max_value=64),
    )
    def test_fuzz_over_length_values_are_refused(self, key: str, over: int) -> None:
        limit = csp_watch.SAMPLE_LIMIT if key == "sample" else csp_watch.URL_LIMIT
        with self.assertRaisesRegex(csp_watch.CspWatchError, "longer than"):
            csp_watch.violation_from({**_WELL_FORMED, key: "y" * (limit + over)})


if __name__ == "__main__":
    unittest.main()
