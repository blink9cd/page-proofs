"""Read a PDF through poppler's own inspectors, and parse what they print.

`pdfinfo`, `pdffonts` and `pdfimages -list` are poppler's command-line readers. Each runs
through the shared `bounded_run`, so a reader that hangs is killed, tree and all, instead of
hanging the pass; every failure -- an absent binary, a non-zero exit, a timeout -- surfaces as
one `PopplerError` naming the tool. The parsers are pure functions of the text a reader
printed, so they are tested, and fuzzed, on captured output alone.

Measured traps (poppler 26.08) shape the reading:

- Dates are read RAW (`pdfinfo -rawdates`). Plain `pdfinfo` DISPLAYS a date converted to the
  local zone, which is not what the file stores. Only a date's class is kept here -- `utc`,
  `local offset (withheld)` or `unparsed` -- never its value.
- `pdffonts` pads its name column to 36 BYTES and lets a longer name overflow it, so in
  characters every later column moves from row to row: right after a long name, left after a
  multibyte one. Columns are located from the dashed rule, then re-anchored on each row.
- An object with no number of its own gets one token where `num gen` would be: `[inline]`
  for an inline image, `[none]` for a direct dictionary (measured in pdffonts; the pdfimages
  binary holds the same string).
- `pdfimages -list` prints a density under 1 ppi as a decimal.
- pdfinfo prints a title's U+2028 raw, and pdffonts a name's form feed and U+2028, so output
  is cut into lines at a newline alone, never by `str.splitlines`. Decoding reads a carriage
  return as a newline, though: a font name holding one fails the fonts read, as a finding.

Entry point assumptions: poppler's readers are found on `PATH` (`missing_tools` says which
are not), and every run is given a timeout.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

# The shared tree-killing subprocess runner: a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place,
# and put near the front of the path, ahead of every installed distribution, so none of the
# same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run

#: Every poppler executable print_pass runs; `pdftoppm` renders pages for another module.
REQUIRED_TOOLS: Final = ("pdfinfo", "pdffonts", "pdfimages", "pdftoppm")

#: Paper sizes named in a report, in points, portrait. Looked up after rounding, either way up.
NAMED_SIZES: Final[Mapping[tuple[int, int], str]] = MappingProxyType(
    {(612, 792): "Letter", (595, 842): "A4"}
)

#: A date's class. Its value is never kept: a local offset says where the file was made.
DATE_UTC: Final = "utc"
DATE_LOCAL: Final = "local offset (withheld)"
DATE_UNPARSED: Final = "unparsed"

#: The Info fields the report prints or asks after, and its dates, as pdfinfo names them.
TITLE_FIELD: Final = "Title"
CREATOR_FIELD: Final = "Creator"
PRODUCER_FIELD: Final = "Producer"
AUTHOR_FIELD: Final = "Author"
_DATE_FIELDS: Final = ("CreationDate", "ModDate")

#: `-l` for "through the last page". pdfinfo clamps a larger last page to the document's own
#: (measured: 99 and 2**31 - 1 both list every page of a 3-page file, exit 0), so one call
#: lists every page. The largest C int, because poppler reads the value into one.
_LAST_PAGE_CEILING: Final = 2**31 - 1

#: Digits allowed in a whole number. No real count needs more, and the bound keeps `int()`
#: inside its conversion limit and a size inside a float's finite range.
_MAX_DIGITS: Final = 18
_WHOLE: Final = re.compile(rf"[0-9]{{1,{_MAX_DIGITS}}}")
_DECIMAL: Final = rf"[0-9]{{1,{_MAX_DIGITS}}}(?:\.[0-9]+)?"

#: `Key: value`, the key from pdfinfo's fixed vocabulary shape; the padding is not the value.
_FIELD_LINE: Final = re.compile(
    r"(?P<key>[A-Za-z][A-Za-z0-9 ]*):(?:[ ]+(?P<value>.*))?"
)
#: `Page size` (plain pdfinfo) or `Page    N size` (with -f/-l).
_PAGE_SIZE_KEY: Final = re.compile(r"Page(?: +[0-9]+)? size")
_PAGE_SIZE: Final = re.compile(rf"(?P<width>{_DECIMAL}) x (?P<height>{_DECIMAL}) pts")

#: A raw PDF date, `D:YYYYMMDDHHmmSS` and its offset. Zero offset either sign, or Z, is UTC,
#: and so is a Z followed by its zero offset: macOS's Quartz PDFContext, which Safari prints
#: through, writes `Z00'00'` (measured).
_UTC_DATE: Final = re.compile(r"D:[0-9]{14}(?:Z(?:00'00'?)?|[+-]00'00'?)")
_ZONED_DATE: Final = re.compile(r"D:[0-9]{14}[+-][0-9]{2}'[0-9]{2}'?")

#: A table's dashed rule: one run of dashes per column, single spaces between.
_RULE: Final = re.compile(r"-+(?: -+)*")
_DASH_RUN: Final = re.compile(r"-+")

#: An object ID's two tokens, `num gen`.
_ID_TOKENS: Final = 2
#: What poppler prints for `num gen` of an object with no number of its own (a trap above).
_NO_NUMBER: Final = "[none]"
_FONT_NO_ID: Final = frozenset({_NO_NUMBER})
_IMAGE_NO_ID: Final = frozenset({_NO_NUMBER, "[inline]"})

#: pdffonts' header words, in order.
_FONT_HEADER: Final = tuple("name type encoding emb sub uni object ID".split())
#: The dashed runs of pdffonts' rule: `object ID` shares one.
_FONT_COLUMNS: Final = 7
#: The answers between the encoding and the object ID: emb, sub, uni.
_FONT_ANSWERS: Final = 3
#: A subset tag: six capitals and `+` (ISO 32000-1, 9.6.4) before a font's name; pdf_text's too.
SUBSET_TAG: Final = re.compile(r"^[A-Z]{6}\+(?=.)", re.DOTALL)

#: pdfimages -list's header words, in order.
_IMAGE_HEADER: Final = tuple(
    "page num type width height color comp bpc enc interp object ID x-ppi y-ppi size ratio".split()
)
#: Tokens before the object ID and after it; between them, `num gen` or an `_IMAGE_NO_ID`.
_IMAGE_HEAD: Final = 10
_IMAGE_TAIL: Final = 4
#: A density: pdfimages prints one under 1 ppi as `%5.3f` (`0.072`, measured), any other whole.
_PPI: Final = re.compile(_DECIMAL)
#: pdfimages prints a stored size as a number and a binary unit: `203B`, `12.5K`, `1.2M`.
_SIZE: Final = re.compile(rf"(?P<number>{_DECIMAL})(?P<unit>[BKM])")
_SIZE_UNITS: Final[Mapping[str, int]] = MappingProxyType(
    {"B": 1, "K": 1024, "M": 1024 * 1024}
)

_YES_NO: Final[Mapping[str, bool]] = MappingProxyType({"yes": True, "no": False})


class PopplerError(RuntimeError):
    """A poppler reader could not run, failed, or printed something its parser cannot read."""


@dataclass(frozen=True)
class PageSize:
    """One page's size in points, and its paper name when it is a named size."""

    width_pt: float
    height_pt: float
    name: str | None


@dataclass(frozen=True)
class PdfInfo:
    """What `pdfinfo -rawdates` says about a document.

    `fields` holds EVERY printed field, raw, for the privacy matcher alone; the report reads
    the typed attributes. `dates` maps each Info date present to its class, never its value.
    """

    pages: int
    page_sizes: tuple[PageSize, ...]
    fields: Mapping[str, str]
    title: str | None
    creator: str | None
    producer: str | None
    author_present: bool
    tagged: bool | None
    pdf_version: str | None
    encrypted: bool
    dates: Mapping[str, str]


@dataclass(frozen=True)
class FontRow:
    """One `pdffonts` row: the name as printed, and without its subset tag."""

    name: str
    base_name: str
    subset: bool
    type: str
    encoding: str
    embedded: bool
    unicode: bool


@dataclass(frozen=True)
class ImageRow:
    """One `pdfimages -list` row: where an image is drawn, how it is stored, its density."""

    page: int
    width: int
    height: int
    color: str
    bpc: int
    encoding: str
    x_ppi: int
    y_ppi: int
    size_bytes: int


def missing_tools(which: Callable[[str], str | None] = shutil.which) -> tuple[str, ...]:
    """The `REQUIRED_TOOLS` that `which` cannot find, in `REQUIRED_TOOLS` order."""
    return tuple(tool for tool in REQUIRED_TOOLS if which(tool) is None)


def run_tool(argv: Sequence[str], *, timeout: float) -> str:
    """Run `argv` (a poppler reader first) bounded by `timeout`; return its stdout.

    Raises:
        PopplerError: the binary could not be started, did not finish within `timeout`, or
            exited non-zero. The message names the tool and quotes stderr's last line, which
            is where poppler says why.
    """
    tool = Path(argv[0]).name
    try:
        completed = bounded_run.run_bounded(argv, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise PopplerError(
            f"{tool} hit its {timeout:g} s timeout and was killed"
        ) from error
    except OSError as error:
        # FileNotFoundError for an absent binary; strerror, never the path, is quoted.
        reason = error.strerror or type(error).__name__
        raise PopplerError(
            f"{tool} could not be started ({reason}); poppler provides it"
        ) from error
    if completed.returncode != 0:
        raise PopplerError(
            f"{tool} exited {completed.returncode}: {_last_line(completed.stderr)}"
        )
    return completed.stdout


def _last_line(stderr: str) -> str:
    """The last non-blank line of `stderr`, or a note that it said nothing."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1] if lines else "(nothing on stderr)"


def read_info(pdf: Path, *, timeout: float) -> PdfInfo:
    """`pdfinfo -rawdates` over every page of `pdf`, parsed."""
    every_page = ["-f", "1", "-l", str(_LAST_PAGE_CEILING)]
    # `--` ends the options, so a file whose name starts with `-` is still read as a file.
    argv = ["pdfinfo", "-rawdates", *every_page, "--", str(pdf)]
    return parse_pdfinfo(run_tool(argv, timeout=timeout))


def read_fonts(pdf: Path, *, timeout: float) -> tuple[FontRow, ...]:
    """`pdffonts` over `pdf`, parsed."""
    return parse_pdffonts(run_tool(["pdffonts", "--", str(pdf)], timeout=timeout))


def read_images(pdf: Path, *, timeout: float) -> tuple[ImageRow, ...]:
    """`pdfimages -list` over `pdf`, parsed; `-list` extracts nothing."""
    text = run_tool(["pdfimages", "-list", "--", str(pdf)], timeout=timeout)
    return parse_pdfimages_list(text)


# fuzz-target
def parse_pdfinfo(text: str) -> PdfInfo:
    """Parse `pdfinfo -rawdates` output.

    Raises:
        PopplerError: no `Pages` line, a count or page size that is not a number, a `Tagged`
            that is neither yes nor no, or a first line that is no field.
    """
    pairs = _split_fields(text)
    fields: dict[str, str] = {}
    for key, value in pairs:
        # A repeated key keeps both values: each must still reach the privacy matcher.
        fields[key] = f"{fields[key]}\n{value}" if key in fields else value
    if "Pages" not in fields:
        raise PopplerError("pdfinfo printed no Pages line")
    tagged = fields.get("Tagged")
    return PdfInfo(
        pages=_parse_whole(fields["Pages"], "pdfinfo Pages"),
        page_sizes=tuple(
            _parse_page_size(value)
            for key, value in pairs
            if _PAGE_SIZE_KEY.fullmatch(key)
        ),
        fields=MappingProxyType(fields),
        title=fields.get(TITLE_FIELD),
        creator=fields.get(CREATOR_FIELD),
        producer=fields.get(PRODUCER_FIELD),
        author_present=bool(fields.get(AUTHOR_FIELD)),
        tagged=None if tagged is None else _parse_yes_no(tagged, "pdfinfo Tagged"),
        pdf_version=fields.get("PDF version"),
        # `yes`, or `yes (print:yes copy:no ...)` with the permissions; absent reads as no.
        encrypted=fields.get("Encrypted", "").split(" ", 1)[0] == "yes",
        dates=MappingProxyType(
            {key: _classify_date(fields[key]) for key in _DATE_FIELDS if key in fields}
        ),
    )


def _split_fields(text: str) -> list[tuple[str, str]]:
    """Each `Key: value` line of `text`, in order, a line that is no field joined to the last.

    poppler prints a newline inside a value as `?` (measured), so a continuation line is not
    expected; it is kept rather than dropped, because the privacy matcher reads every value.
    Lines end at a newline alone (a trap above): a title's U+2028 could start an `Author:`.
    """
    pairs: list[tuple[str, str]] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if not line:
            continue
        match = _FIELD_LINE.fullmatch(line)
        if match is not None:
            pairs.append((match["key"], match["value"] or ""))
        elif pairs:
            key, value = pairs[-1]
            pairs[-1] = (key, f"{value}\n{line}")
        else:
            raise PopplerError(
                "pdfinfo printed a line that is no field before any field"
            )
    return pairs


def _parse_page_size(value: str) -> PageSize:
    """A `612 x 792 pts (letter)` value as a PageSize, named from `NAMED_SIZES`."""
    match = _PAGE_SIZE.match(value)
    if match is None:
        raise PopplerError(
            f"pdfinfo printed a page size that is not `W x H pts`: {value!r}"
        )
    width, height = float(match["width"]), float(match["height"])
    return PageSize(width, height, _paper_name(width, height))


def _paper_name(width: float, height: float) -> str | None:
    """The `NAMED_SIZES` name of a size rounded to whole points, whichever way up it is."""
    portrait = (round(min(width, height)), round(max(width, height)))
    return NAMED_SIZES.get(portrait)


def _classify_date(raw: str) -> str:
    """The class of a raw PDF date: `utc`, `local offset (withheld)` or `unparsed`."""
    if _UTC_DATE.fullmatch(raw):
        return DATE_UTC
    if _ZONED_DATE.fullmatch(raw):
        return DATE_LOCAL
    return DATE_UNPARSED


# fuzz-target
def parse_pdffonts(text: str) -> tuple[FontRow, ...]:
    """Parse `pdffonts` output; a table with no rows is `()`.

    Raises:
        PopplerError: no pdffonts table, or a row that does not fit it.
    """
    rule, rows = _table(text, _FONT_HEADER, "pdffonts")
    columns = [run.span() for run in _DASH_RUN.finditer(rule)]
    if len(columns) != _FONT_COLUMNS:
        raise PopplerError(f"pdffonts printed a rule of {len(columns)} columns")
    name_end, encoding_start = columns[0][1], columns[2][0]
    return tuple(_parse_font_row(row, name_end, encoding_start) for row in rows)


def _parse_font_row(row: str, name_end: int, encoding_start: int) -> FontRow:
    """One pdffonts row, re-anchored on its encoding column.

    The answers and the object ID after the encoding are whitespace tokens, read from the
    right. The encoding is the last token before them, and where it starts, against where the
    rule puts it, is how far this row's name moved every later column. The name column's end
    moved by the same amount, which is how a name holding spaces is told from the type after it.
    """
    left, emb, sub, uni = _font_cells(row)
    encoding = left.split()[-1]
    encoding_at = len(left) - len(encoding)
    split_at = name_end + encoding_at - encoding_start
    if not 0 < split_at < encoding_at or not left[split_at].isspace():
        raise PopplerError(
            f"pdffonts printed a row whose columns do not line up: {row!r}"
        )
    name, kind = left[:split_at].strip(), left[split_at:encoding_at].strip()
    if not name or not kind:
        raise PopplerError(f"pdffonts printed a row with an empty column: {row!r}")
    return FontRow(
        name=name,
        base_name=SUBSET_TAG.sub("", name, count=1),
        subset=_parse_yes_no(sub, "pdffonts sub"),
        type=kind,
        encoding=encoding,
        embedded=_parse_yes_no(emb, "pdffonts emb"),
        unicode=_parse_yes_no(uni, "pdffonts uni"),
    )


def _font_cells(row: str) -> list[str]:
    """`row` as [the name through the encoding, emb, sub, uni], its object ID checked and cut.

    The ID is `num gen`, or pdffonts' `[none]` (a trap above): one ID token or two.
    """
    id_length = 1 if row.endswith(_NO_NUMBER) else _ID_TOKENS
    parts = row.rsplit(maxsplit=_FONT_ANSWERS + id_length)
    if len(parts) != 1 + _FONT_ANSWERS + id_length:
        raise PopplerError(
            f"pdffonts printed a row that does not fit its table: {row!r}"
        )
    if not _is_object_id(parts[1 + _FONT_ANSWERS :], _FONT_NO_ID):
        raise PopplerError(
            f"pdffonts printed a row whose object ID is neither `num gen` nor [none]: {row!r}"
        )
    return parts[: 1 + _FONT_ANSWERS]


# fuzz-target
def parse_pdfimages_list(text: str) -> tuple[ImageRow, ...]:
    """Parse `pdfimages -list` output; a table with no rows is `()`.

    Raises:
        PopplerError: no pdfimages table, or a row that does not fit it.
    """
    _, rows = _table(text, _IMAGE_HEADER, "pdfimages")
    return tuple(_parse_image_row(row) for row in rows)


def _parse_image_row(row: str) -> ImageRow:
    """One pdfimages row. Its rule is one unbroken run, so the row splits on whitespace."""
    tokens = row.split()
    # A row too short to hold both ends leaves no object ID between them, and fails here.
    if not _is_object_id(tokens[_IMAGE_HEAD:-_IMAGE_TAIL], _IMAGE_NO_ID):
        raise PopplerError(
            f"pdfimages printed a row that does not fit its table: {row!r}"
        )
    # Each cell by its header word; `type` and `ratio` are read by nothing.
    cells = dict(zip(_IMAGE_HEADER[:_IMAGE_HEAD], tokens[:_IMAGE_HEAD], strict=True))
    cells.update(zip(_IMAGE_HEADER[-_IMAGE_TAIL:], tokens[-_IMAGE_TAIL:], strict=True))
    _parse_whole(cells["num"], "pdfimages num")
    _parse_whole(cells["comp"], "pdfimages comp")
    _parse_yes_no(cells["interp"], "pdfimages interp")
    return ImageRow(
        page=_parse_whole(cells["page"], "pdfimages page"),
        width=_parse_whole(cells["width"], "pdfimages width"),
        height=_parse_whole(cells["height"], "pdfimages height"),
        color=cells["color"],
        bpc=_parse_whole(cells["bpc"], "pdfimages bpc"),
        encoding=cells["enc"],
        x_ppi=_parse_ppi(cells["x-ppi"], "pdfimages x-ppi"),
        y_ppi=_parse_ppi(cells["y-ppi"], "pdfimages y-ppi"),
        size_bytes=_parse_size(cells["size"]),
    )


def _is_object_id(tokens: list[str], no_number: frozenset[str]) -> bool:
    """Whether `tokens` are an object ID: `num gen`, or one token of `no_number`."""
    if len(tokens) == 1:
        return tokens[0] in no_number
    return len(tokens) == _ID_TOKENS and all(map(_WHOLE.fullmatch, tokens))


def _parse_ppi(token: str, what: str) -> int:
    """A density as the nearest whole ppi, so a decimal under 1 such as `0.180` is 0."""
    if _PPI.fullmatch(token) is None:
        raise PopplerError(f"{what} is not a number: {token!r}")
    return round(float(token))


def _parse_size(token: str) -> int:
    """A stored size such as `203B`, `12.5K` or `1.2M`, in bytes (K and M are 1024-based)."""
    match = _SIZE.fullmatch(token)
    if match is None:
        raise PopplerError(
            f"pdfimages printed a size that is not a number and unit: {token!r}"
        )
    return round(float(match["number"]) * _SIZE_UNITS[match["unit"]])


def _table(text: str, header: tuple[str, ...], tool: str) -> tuple[str, list[str]]:
    """The dashed rule and the rows of `tool`'s table, checked against its `header` words.

    Rows end at a newline alone (a trap above): a name's form feed would cut one in two.
    """
    lines = [line.rstrip() for line in text.split("\n") if line.strip()]
    if (
        len(lines) < 2
        or tuple(lines[0].split()) != header
        or not _RULE.fullmatch(lines[1])
    ):
        raise PopplerError(f"{tool} printed no table headed `{' '.join(header)}`")
    return lines[1], lines[2:]


def _parse_whole(token: str, what: str) -> int:
    """`token` as a non-negative whole number, or a PopplerError naming `what`."""
    if _WHOLE.fullmatch(token) is None:
        raise PopplerError(f"{what} is not a whole number: {token!r}")
    return int(token)


def _parse_yes_no(token: str, what: str) -> bool:
    """`yes` or `no` as a bool, or a PopplerError naming `what`."""
    if token not in _YES_NO:
        raise PopplerError(f"{what} is neither yes nor no: {token!r}")
    return _YES_NO[token]
