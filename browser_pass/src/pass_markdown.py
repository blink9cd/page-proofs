"""Page-derived text, made safe for the one Markdown construct report.md puts it in.

A page under test chooses its element ids, its console messages and the URLs it fails to load,
and each reaches the report a person reads. Written as it came, a pipe ends a table cell, a
backtick ends a code span, a line break ends a list item or a table row, and the rest is Markdown
the page gets to write into the report. Each helper here keeps the text inside its construct, and
shows it as it came: a reader sees every character but a line break, which a code span would show
as a space anyway. report.json is written from the same values unescaped.

Entry point assumptions: the standard library and `pass_targets`; imported by the report's
writers, with this `src/` already on `sys.path`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Final

from pass_targets import Rendering

#: Each line ending Markdown reads; inside one construct each is shown as a space.
_LINE_BREAK: Final = re.compile(r"\r\n|[\r\n]")

#: A run of backticks: a code span's fence is one longer than the longest in its text.
_BACKTICKS: Final = re.compile(r"`+")

#: The characters that open or close an inline construct in plain text, each escaped with a
#: backslash, the backslash first among them.
_INLINE_MARKUP: Final = re.compile(r"([\\`*_\[\]<>])")


# fuzz-target
def code_span(text: str, *, in_table: bool = False) -> str:
    """`text` as one code span, shown as it came, that no character in it can close early.

    The fence is one backtick longer than the longest run inside, and a space pads the text off
    it where the text starts or ends with a backtick, or with a space at both ends, which a
    reader would otherwise strip. In a table cell (`in_table`) each pipe is escaped too, since a
    table ends a cell at a bare pipe even inside a span; a reader shows the escaped pipe as one.
    Empty text is a span of one space, since two backticks alone are no span at all.
    """
    flat = _LINE_BREAK.sub(" ", text) or " "
    if in_table:
        flat = flat.replace("|", "\\|")
    fence = "`" * (max((len(run) for run in _BACKTICKS.findall(flat)), default=0) + 1)
    padded = flat.startswith("`") or flat.endswith("`")
    padded = padded or (
        flat.startswith(" ") and flat.endswith(" ") and bool(flat.strip())
    )
    pad = " " if padded else ""
    return f"{fence}{pad}{flat}{pad}{fence}"


def plain_text(text: str, *, in_table: bool = False) -> str:
    """`text` on one line, its inline markup escaped, so it reads as it came and links nothing.

    In a table cell (`in_table`) each pipe is escaped too, as `code_span` escapes one.
    """
    flat = _INLINE_MARKUP.sub(r"\\\1", _LINE_BREAK.sub(" ", text))
    return flat.replace("|", "\\|") if in_table else flat


def rendering_label(rendering: Rendering, *, in_table: bool = False) -> str:
    """`rendering`'s one-line name with its page as `plain_text`, as a followed redirect chose it.

    The rest, a state's name included, is the targets file's and the run's, and stays as written.
    """
    return replace(rendering, page=plain_text(rendering.page, in_table=in_table)).label
