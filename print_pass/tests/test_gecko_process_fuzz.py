"""Suite: the process listing's readers by property -- any text, and any rows written as `ps` would.

`parse_listing` reads a listing that names every process on the host, so its arguments are text
anyone can choose, and it is fuzzed. Any text gives rows whose numbers are ASCII digits, nine at
most, and whose arguments hold no newline: a row can never be forged across one. And any rows
written as `ps` writes them -- each number right-aligned, then the elapsed time, then the
arguments, which may hold any line break but a newline -- read back as exactly those rows.

`parse_etime` reads each row's elapsed time, which decides whether a crash helper is ours, so it
is fuzzed too. Any text gives None or a count of seconds; every `[[dd-]hh:]mm:ss` form reads as
its fields; times order as their fields do; and a well-formed time broken in any of several ways
reads as None. The same readers by example are `test_gecko_process_listing.py`'s.

Entry point assumptions: standalone, or discovered by `unittest discover`. The insert below
reaches the subject's private `src/` beside this folder, never the working directory.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_process  # noqa: E402 - the tool's private src/, via the insert above
from gecko_process import ProcessRow  # noqa: E402 - as above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: The largest number a listing's column holds that the reader takes: nine digits.
_LARGEST = 999_999_999

#: Line breaks other than a newline: whatever `ps` writes for one, the reader keeps it inside
#: the argument that holds it.
_OTHER_BREAKS = "\r\x0b\x0c\x1c\x1d\x1e\x85" + chr(0x2028) + chr(0x2029)

#: A digit `int()` reads as 4 that no listing holds: ARABIC-INDIC DIGIT FOUR.
_NON_ASCII_FOUR = chr(0x0664)

#: Text near a listing: digits, spaces, newlines, the other breaks, and a helper's words.
_LISTING_TEXT = st.lists(
    st.sampled_from(
        ["0", "1", "9", " ", "\n", "-", ":", "x", "/", "crashhelper", *_OTHER_BREAKS]
    )
).map("".join)

#: Arguments as a listing may show them: any text but a newline, weighted to the other breaks.
_ARGS = st.one_of(
    st.text().map(lambda text: text.replace("\n", "")),
    st.text(alphabet=st.sampled_from(["a", " ", "/", "1", *_OTHER_BREAKS])),
)

_NUMBERS = st.integers(min_value=0, max_value=_LARGEST)

#: Seconds in a day, an hour and a minute.
_DAY, _HOUR, _MINUTE = 24 * 60 * 60, 60 * 60, 60

#: The most days an elapsed time's `dd` field takes: nine digits.
_MOST_DAYS = 999_999_999

#: An elapsed time's fields, each in its range: days, hours, minutes, seconds.
_FIELDS = st.tuples(
    st.integers(min_value=0, max_value=_MOST_DAYS),
    st.integers(min_value=0, max_value=23),
    st.integers(min_value=0, max_value=59),
    st.integers(min_value=0, max_value=59),
)

#: An elapsed time as a row holds it: whole seconds, or None when it could not be read.
_ELAPSED = st.one_of(
    st.none(), st.integers(min_value=0, max_value=(_MOST_DAYS + 1) * _DAY - 1)
)

_ROWS = st.lists(
    st.builds(ProcessRow, _NUMBERS, _NUMBERS, _NUMBERS, _ELAPSED, _ARGS), max_size=8
)

#: Text near an elapsed time: its digits and separators, and a few characters it never holds.
_ETIME_TEXT = st.lists(
    st.sampled_from(
        ["0", "1", "2", "5", "9", ":", "-", " ", "\n", "x", _NON_ASCII_FOUR]
    )
).map("".join)

#: Ways to break a well-formed elapsed time, each leaving text that is no time at all.
_BREAKS: dict[str, Callable[[str], str]] = {
    "a trailing colon": lambda text: f"{text}:",
    "a trailing space": lambda text: f"{text} ",
    "a leading space": lambda text: f" {text}",
    "a sign": lambda text: f"-{text}",
    "seconds past 59": lambda text: f"{text[:-2]}60",
    "minutes past 59": lambda text: f"{text[:-5]}60{text[-3:]}",
    "hours past 23": lambda text: f"24:{text[-5:]}",
    "days without hours": lambda text: f"1-{text[-5:]}",
    "one-digit seconds": lambda text: text[:-1],
    "three-digit seconds": lambda text: f"{text}0",
    "a digit beyond ASCII": lambda text: f"{text[:-1]}{_NON_ASCII_FOUR}",
}


def _etime(days: int, hours: int, minutes: int, seconds: int) -> str:
    """The fields in the shortest `[[dd-]hh:]mm:ss` form that holds them."""
    if days:
        return f"{days}-{hours:02d}:{minutes:02d}:{seconds:02d}"
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _etime_of(elapsed_s: int | None) -> str:
    """`elapsed_s` as an elapsed time, or a dash, which is none, for None."""
    if elapsed_s is None:
        return "-"
    days, rest = divmod(elapsed_s, _DAY)
    hours, rest = divmod(rest, _HOUR)
    return _etime(days, hours, *divmod(rest, _MINUTE))


def _written(rows: list[ProcessRow]) -> str:
    """`rows` as `ps` writes them: right-aligned numbers and elapsed time, then the arguments.

    Each number fills a column of five, and the elapsed time one of eleven, as measured on macOS.
    """
    lines = [
        f"{row.pid:5d} {row.ppid:5d} {row.pgid:5d} {_etime_of(row.elapsed_s):>11} "
        + row.args
        for row in rows
    ]
    return "".join(line + "\n" for line in lines)


class ParseListingFuzzTests(unittest.TestCase):
    """Any text reads as well-formed rows; any rows written as `ps` would read back whole."""

    @given(st.one_of(st.text(), _LISTING_TEXT))
    def test_fuzz_parse_listing(self, text: str) -> None:
        for row in gecko_process.parse_listing(text):
            for number in (row.pid, row.ppid, row.pgid):
                self.assertGreaterEqual(number, 0)
                self.assertLessEqual(number, _LARGEST)
            if row.elapsed_s is not None:
                self.assertGreaterEqual(row.elapsed_s, 0)
            self.assertNotIn("\n", row.args)

    @given(_ROWS)
    def test_rows_written_as_ps_writes_them_read_back_whole(
        self, rows: list[ProcessRow]
    ) -> None:
        self.assertEqual(gecko_process.parse_listing(_written(rows)), tuple(rows))


class ParseEtimeFuzzTests(unittest.TestCase):
    """Any text reads as None or seconds; each form reads as its fields, in their order."""

    @given(st.one_of(st.text(), _ETIME_TEXT))
    def test_fuzz_parse_etime(self, text: str) -> None:
        seconds = gecko_process.parse_etime(text)
        if seconds is not None:
            self.assertIs(type(seconds), int)
            self.assertGreaterEqual(seconds, 0)
            self.assertLessEqual(set(text), set("0123456789:-"))

    @given(_FIELDS)
    def test_each_form_holding_the_fields_reads_as_them(
        self, fields: tuple[int, int, int, int]
    ) -> None:
        days, hours, minutes, seconds = fields
        forms = [f"{days}-{hours:02d}:{minutes:02d}:{seconds:02d}"]
        if days == 0:
            forms.append(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        if days == hours == 0:
            forms.append(f"{minutes:02d}:{seconds:02d}")
        expected = days * _DAY + hours * _HOUR + minutes * _MINUTE + seconds
        for form in forms:
            self.assertEqual(gecko_process.parse_etime(form), expected, form)

    @given(_FIELDS, _FIELDS)
    def test_times_order_as_their_fields_do(
        self, first: tuple[int, int, int, int], second: tuple[int, int, int, int]
    ) -> None:
        # No run of later fields outweighs one more of an earlier field.
        read_first = gecko_process.parse_etime(_etime(*first))
        read_second = gecko_process.parse_etime(_etime(*second))
        assert read_first is not None and read_second is not None
        self.assertEqual(read_first < read_second, first < second)
        self.assertEqual(read_first == read_second, first == second)

    @given(_FIELDS, st.sampled_from(sorted(_BREAKS)))
    def test_a_time_broken_any_of_these_ways_reads_as_none(
        self, fields: tuple[int, int, int, int], way: str
    ) -> None:
        text = _BREAKS[way](_etime(*fields))
        self.assertIsNone(gecko_process.parse_etime(text), text)


if __name__ == "__main__":
    unittest.main()
