"""Grouped suite: page-derived text in report.md cannot break the construct it is put in.

A page under test chooses its element ids, its console messages and the URLs it fails to load,
and each reaches report.md. These tests hand the report hostile text of each kind and check that
every finding stays one list line, every table row keeps its cells, and no code span closes
early; report.json is untouched, so it keeps the text exactly.
"""

from __future__ import annotations

import re
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast

from hypothesis import given
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_extension  # noqa: E402 - the tool's private src/, via the insert above
import pass_markdown  # noqa: E402 - the tool's private src/, via the insert above
import pass_report  # noqa: E402 - the tool's private src/, via the insert above
import pass_shift  # noqa: E402 - the tool's private src/, via the insert above
from pass_digest import Box  # noqa: E402 - the tool's private src/, via the insert above
from pass_motion import NO_MOTION, Motion  # noqa: E402 - the tool's private src/
from pass_redirects import Redirect, report_lines  # noqa: E402 - the tool's private src/
from pass_targets import Rendering  # noqa: E402 - the tool's private src/, via the insert above

_HOME = Rendering("chromium", "/", 390, 844, "light", "on")

#: Text a hostile page could choose: a cell break, a span break, line breaks, and Markdown.
_HOSTILE = "a|b`c``d\ne\r\nf\rg [x](javascript:alert(1)) <img src=x> **bold**"

_FACTS = pass_report.RunFacts(
    started="20260920T230000Z",
    commit="abc1234",
    tree_dirty=False,
    served="src",
    base_url="http://127.0.0.1:50000",
    playwright="1.63.0",
    engines={"chromium": "153.0.8010.12", "webkit": "26.6"},
    prove=False,
    require_csp=False,
)

#: An unescaped table-cell boundary: a pipe with no backslash before it.
_CELL_BOUNDARY = re.compile(r"(?<!\\)\|")

#: A rendering moved by a followed redirect to a destination the page chose.
_MOVED = replace(_HOME, page="/" + _HOSTILE)

#: Inline markup, or a line break, with no backslash before it: what plain text must not hold.
_BARE_MARKUP = re.compile(r"(?<!\\)[`*_\[\]<>]|[\r\n]")


def _result(
    rendering: Rendering = _HOME, **changes: object
) -> pass_report.RenderingResult:
    """A clean result for `rendering`, with `changes` applied."""
    clean = pass_report.RenderingResult(
        rendering=rendering,
        faults=(),
        findings={},
        verdicts={},
        digest={"body>main:1": Box(True, 0.0, 68.0, 390.0, 900.0, 16.0)},
        late_shift=pass_shift.LateShift(pass_shift.MEASURED),
        motion=NO_MOTION,
    )
    return replace(clean, **changes)  # type: ignore[arg-type] # changes name real fields


def _markdown(results: list[pass_report.RenderingResult]) -> str:
    """report.md for `results`."""
    comparisons = pass_report.cross_engine(results, reference="chromium")
    return pass_report.report_markdown(results, comparisons, _FACTS)


def _span_text(span: str) -> str:
    """What a CommonMark reader shows for the code span `span`, as its spec decodes one."""
    fence = re.match(r"`+", span)
    assert fence is not None and span.endswith(fence.group(0)), span
    content = span[len(fence.group(0)) : -len(fence.group(0))]
    if content.startswith(" ") and content.endswith(" ") and content.strip(" "):
        content = content[1:-1]
    return content


def _longest_backtick_run(text: str) -> int:
    """The longest run of backticks in `text`, 0 for none."""
    return max((len(run) for run in re.findall(r"`+", text)), default=0)


class CodeSpanTests(unittest.TestCase):
    """`code_span` writes text a reader sees exactly, on one line."""

    def test_plain_text_is_the_span_it_always_was(self) -> None:
        self.assertEqual(pass_markdown.code_span("body>a:1 20x20"), "`body>a:1 20x20`")

    def test_a_backtick_in_the_text_gets_a_longer_fence(self) -> None:
        self.assertEqual(pass_markdown.code_span("a`b``c"), "```a`b``c```")

    def test_a_backtick_at_an_end_is_padded_off_the_fence(self) -> None:
        span = pass_markdown.code_span("`x")
        self.assertEqual(span, "`` `x ``")
        self.assertEqual(_span_text(span), "`x")

    def test_text_with_a_space_at_each_end_keeps_both(self) -> None:
        self.assertEqual(_span_text(pass_markdown.code_span(" x ")), " x ")

    def test_each_line_break_reads_as_a_space(self) -> None:
        self.assertEqual(pass_markdown.code_span("a\nb\r\nc\rd"), "`a b c d`")

    def test_empty_text_is_a_span_still(self) -> None:
        self.assertEqual(pass_markdown.code_span(""), "` `")

    def test_a_pipe_is_escaped_in_a_table_cell_alone(self) -> None:
        self.assertEqual(pass_markdown.code_span("a|b"), "`a|b`")
        self.assertEqual(pass_markdown.code_span("a|b", in_table=True), "`a\\|b`")

    @given(st.text(), st.booleans())
    def test_fuzz_code_span(self, text: str, in_table: bool) -> None:
        # Whatever the page chose, the span is one line, closes only at its own fence, keeps
        # a table cell's pipes escaped, and a reader decodes it back to the text.
        span = pass_markdown.code_span(text, in_table=in_table)
        self.assertNotRegex(span, r"[\r\n]")
        fence = len(re.match(r"`+", span).group(0))  # type: ignore[union-attr] # starts with one
        self.assertLess(_longest_backtick_run(span[fence:-fence]), fence)
        shown = _span_text(span)
        if in_table:
            self.assertIsNone(_CELL_BOUNDARY.search(span))
            shown = shown.replace("\\|", "|")
        expected = re.sub(r"\r\n|[\r\n]", " ", text) or " "
        self.assertEqual(shown, expected)


class PlainTextTests(unittest.TestCase):
    """`plain_text` writes text that opens no inline construct and ends no line."""

    def test_ordinary_text_is_unchanged(self) -> None:
        self.assertEqual(
            pass_markdown.plain_text("a classic scrollbar takes 15px"),
            "a classic scrollbar takes 15px",
        )

    def test_a_pipe_is_escaped_in_a_table_cell_alone(self) -> None:
        self.assertEqual(pass_markdown.plain_text("a|b"), "a|b")
        self.assertEqual(pass_markdown.plain_text("a|b", in_table=True), "a\\|b")

    def test_markup_is_escaped_and_lines_are_joined(self) -> None:
        self.assertEqual(
            pass_markdown.plain_text("[x](y) <b>*z*</b>\n`q`_r_\\"),
            "\\[x\\](y) \\<b\\>\\*z\\*\\</b\\> \\`q\\`\\_r\\_\\\\",
        )


class HostileReportTests(unittest.TestCase):
    """Hostile page text in each place report.md shows it."""

    def test_a_finding_stays_one_list_line_whose_span_shows_the_text(self) -> None:
        text = _markdown([_result(findings={"console_error": (_HOSTILE,)})])
        lines = [line for line in text.splitlines() if " in 1 rendering:" in line]
        self.assertEqual(len(lines), 1)
        span = lines[0].removeprefix("- ").partition(" in 1 rendering:")[0]
        self.assertEqual(_span_text(span), re.sub(r"\r\n|[\r\n]", " ", _HOSTILE))

    def test_a_cross_engine_row_keeps_its_seven_cells(self) -> None:
        hostile = {_HOSTILE: Box(True, 0.0, 68.0, 390.0, 900.0, 16.0)}
        moved = {_HOSTILE: Box(True, 0.0, 99.0, 390.0, 900.0, 16.0)}
        text = _markdown(
            [
                _result(digest=hostile),
                _result(replace(_HOME, engine="webkit"), digest=moved),
            ]
        )
        rows = [line for line in text.splitlines() if line.startswith("| / | webkit |")]
        self.assertEqual(len(rows), 1)
        # Eight boundaries: one before each of the seven cells and one after the last.
        self.assertEqual(len(_CELL_BOUNDARY.findall(rows[0])), 8)

    def test_a_fault_stays_one_list_line_and_opens_no_link(self) -> None:
        text = _markdown([_result(faults=(_HOSTILE,))])
        section = text.partition("## Instrument faults\n\n")[2].split("\n\n")[0]
        self.assertEqual(len(section.splitlines()), 1)
        self.assertNotIn("[x](", section)

    def test_a_redirect_destination_is_a_span_that_closes_at_its_own_fence(
        self,
    ) -> None:
        (line,) = report_lines([Redirect("/old/", "/new`/")])
        self.assertEqual(line, "- Redirects followed: `/old/` to ``/new`/``")

    def test_report_json_keeps_the_text_exactly(self) -> None:
        result = _result(findings={"console_error": (_HOSTILE,)})
        document = pass_report.report_document(
            [result], pass_report.cross_engine([result], reference="chromium"), _FACTS
        )
        (record,) = cast(list[dict[str, object]], document["renderings"])
        self.assertEqual(record["findings"], {"console_error": [_HOSTILE]})


class RedirectedPageTests(unittest.TestCase):
    """A page a followed redirect chose, in each place report.md names its rendering."""

    def test_a_finding_names_the_rendering_in_plain_text(self) -> None:
        text = _markdown([_result(_MOVED, findings={"console_error": ("x",)})])
        (line,) = [ln for ln in text.splitlines() if ln.startswith("- `x` in 1 ")]
        self.assertIsNone(_BARE_MARKUP.search(line.partition("rendering: ")[2]))

    def test_a_fault_names_the_rendering_in_plain_text(self) -> None:
        text = _markdown([_result(_MOVED, faults=("f",))])
        section = text.partition("## Instrument faults\n\n")[2].split("\n\n")[0]
        self.assertIsNone(_BARE_MARKUP.search(section))

    def test_a_late_shift_row_keeps_its_five_cells(self) -> None:
        moved = pass_shift.LateShift(
            pass_shift.MEASURED, shifts=(pass_shift.Shift(0.0, 0.5, ()),)
        )
        text = _markdown([_result(_MOVED, late_shift=moved)])
        section = text.partition("## Late-script layout shifts")[2]
        (row,) = [ln for ln in section.splitlines() if ln.startswith("| chromium |")]
        self.assertEqual(len(_CELL_BOUNDARY.findall(row)), 6)
        self.assertIsNone(_BARE_MARKUP.search(row))

    def test_a_cross_engine_row_keeps_its_seven_cells_and_names_the_page(self) -> None:
        text = _markdown(
            [
                _result(_MOVED),
                _result(
                    replace(_MOVED, engine="webkit"),
                    digest={"body>main:1": Box(True, 0.0, 99.0, 390.0, 900.0, 16.0)},
                ),
            ]
        )
        section = text.partition("## Cross-engine differences")[2]
        (row,) = [ln for ln in section.splitlines() if " webkit |" in ln]
        self.assertEqual(len(_CELL_BOUNDARY.findall(row)), 8)
        self.assertIsNone(_BARE_MARKUP.search(row.partition(" webkit |")[0]))

    def test_a_motion_line_spans_the_page_and_names_renderings_in_plain_text(
        self,
    ) -> None:
        text = _markdown([_result(_MOVED, motion=Motion(1, 0, 1))])
        section = text.partition("## Motion\n\n")[2].split("\n\n")[1]
        (line,) = section.splitlines()
        span, _, rest = line.removeprefix("- ").partition(", 1 rendering: ")
        self.assertEqual(_span_text(span), re.sub(r"\r\n|[\r\n]", " ", _MOVED.page))
        self.assertIsNone(_BARE_MARKUP.search(rest.partition("rendering: ")[2]))


class ExtensionFactsTests(unittest.TestCase):
    """An extension's manifest names it, so its name, version and id are its author's text."""

    def test_each_manifest_value_is_plain_text_on_its_own_line(self) -> None:
        facts = pass_extension.ExtensionFacts("firefox", _HOSTILE, _HOSTILE, _HOSTILE)
        (line,) = pass_extension.isolation_lines(False, (facts,))[1:]
        self.assertIsNone(_BARE_MARKUP.search(line.partition(": ")[2]))


if __name__ == "__main__":
    unittest.main()
