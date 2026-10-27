r"""Shared fixtures for pdf_read's suites: a small valid PDF, and the crashes found mutating it.

`SEED` is one Letter page holding a link, a Title and one line of text, written by pypdf
uncompressed, so a byte-level mutation lands in its structure. `CRASHES` holds the three
crash classes a mutation campaign over such PDFs found escaping pypdf and pdfminer, each
rebuilt from `SEED` by one edit, and each keyed by the reader it crashes in this process:

- `links`: the catalog's `/Pages` made a reference to the page, which pypdf's object cache
  then tries to stamp with its own reference (`_reader.py`, `cache_indirect_object`): an
  AttributeError, since a reference's own reference is read-only.
- `text`: an octal escape past 255 in the page's text, `(\777)`: pdfminer's string
  parser asserts it is under 256 (`psparser.py`, `_parse_string_1`): an AssertionError.
- `metadata`: a trailer `/Prev` past the largest file offset: pypdf seeks there and raises
  OverflowError. pdfminer seeks there too (`psparser.py`, `seek`), which raises
  OverflowError over a PDF held in memory but ValueError over a file, the way pdf_text
  reads one, so its read is already an ordinary error.

Entry point assumptions: imported by a suite that `unittest` discovery loaded with this
folder as its top-level directory; it needs pypdf and nothing from the tool's `src/`.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

import pypdf
import pypdf.annotations
from pypdf.generic import ContentStream, DictionaryObject, NameObject

#: A US Letter page, in points.
_PAGE: Final = (612, 792)

#: The seed's one line of text, as its content stream draws it.
_SHOWN: Final = b"(Hi)"

#: The seed's catalog `/Pages` dictionary, as pypdf writes it.
_PAGES: Final = b"<<\n/Type /Pages\n/Count 1\n/Kids [ 4 0 R ]\n>>"

#: An offset no file holds: past the largest a 64-bit seek takes.
_PAST_ANY_OFFSET: Final = b"99999999999999999999"


def _seed() -> bytes:
    """One Letter page with a link, a Title and the text `Hi`, uncompressed."""
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=_PAGE[0], height=_PAGE[1])
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    fonts = DictionaryObject({NameObject("/F1"): font})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
    content = ContentStream(None, None)
    content.set_data(b"BT /F1 12 Tf 72 720 Td " + _SHOWN + b" Tj ET")
    page.replace_contents(content)
    link = pypdf.annotations.Link(rect=(10, 10, 50, 50), url="https://example.com/")
    writer.add_annotation(page_number=0, annotation=link)
    writer.add_metadata({"/Title": "Seed"})
    sink = io.BytesIO()
    writer.write(sink)
    return sink.getvalue()


def _replaced(seed: bytes, old: bytes, new: bytes) -> bytes:
    """`seed` with its one `old` replaced by `new`; raises if `old` is not there once."""
    if seed.count(old) != 1:
        raise ValueError(f"the seed holds {old!r} {seed.count(old)} times, not once")
    return seed.replace(old, new)


def _crashes(seed: bytes) -> Mapping[str, bytes]:
    """Each crash class, rebuilt from `seed`, by the reader it crashes."""
    # Padded to the dictionary's length, so every later offset in the xref stays right.
    pages_as_reference = b"4 0 R".ljust(len(_PAGES))
    octal = _replaced(seed, _SHOWN, b"(\\777)")
    return MappingProxyType(
        {
            "links": _replaced(seed, _PAGES, pages_as_reference),
            "text": _replaced(octal, b"/Length 33", b"/Length 35"),
            "metadata": _replaced(
                seed,
                b"trailer\n<<\n",
                b"trailer\n<<\n/Prev " + _PAST_ANY_OFFSET + b"\n",
            ),
        }
    )


#: The seed every mutation starts from.
SEED: Final = _seed()

#: The three crash classes, by the reader each crashes in this process.
CRASHES: Final = _crashes(SEED)
