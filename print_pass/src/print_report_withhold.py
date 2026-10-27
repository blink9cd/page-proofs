"""What the report prints of a PDF's own values, each withheld when the privacy matcher flags it.

The report prints a PDF's Title, Creator and Producer and each link's URI and destination,
and a flag's own promise is never to repeat the value it found. So each printed value is
judged ALONE by `pdf_privacy`'s matcher, and replaced by `WITHHELD` when it draws any flag:
judged together, one value's flag could not say which value carried it.

Entry point assumptions: `pdf_privacy`, `pdf_text` and `pdf_tools` import beside this module;
`print_report_pdf` hands every PdfInfo and link it reports through here.
"""

from __future__ import annotations

import dataclasses
from typing import Final

from pdf_privacy import Identity, privacy_flags
from pdf_text import Link
from pdf_tools import CREATOR_FIELD, PRODUCER_FIELD, TITLE_FIELD, PdfInfo

#: What a flagged metadata value, link URI or link destination reads as in the report.
WITHHELD: Final = "(withheld: flagged)"


def withhold_field(field: str, value: str, identity: Identity) -> str:
    """`value`, or `WITHHELD` when the privacy matcher flags the Info field `field` holding it.

    The field is judged ALONE, as `withhold_link` judges a link, and any flag withholds it,
    not only one placed on `field`: when the field's own name carries an identifier (a
    two-letter login inside `Title`), every flag on it moves to the neutral place, which
    names no field. Such a field is then withheld whatever it holds: the safe direction.
    """
    alone = privacy_flags(
        identity, info={field: value}, xmp=None, links=(), text_pages=()
    )
    return WITHHELD if alone else value


def withhold_info(info: PdfInfo | None, identity: Identity) -> PdfInfo | None:
    """`info` with each printed field `withhold_field` withholds replaced by `WITHHELD`."""
    if info is None:
        return None
    return dataclasses.replace(
        info,
        title=_withheld_if_present(TITLE_FIELD, info.title, identity),
        creator=_withheld_if_present(CREATOR_FIELD, info.creator, identity),
        producer=_withheld_if_present(PRODUCER_FIELD, info.producer, identity),
    )


def withhold_link(link: Link, identity: Identity) -> Link:
    """`link` with its URI and its destination each replaced by `WITHHELD` when flagged alone."""
    uri = dataclasses.replace(link, destination=None)
    destination = dataclasses.replace(link, uri=None)
    return dataclasses.replace(
        link,
        uri=WITHHELD if _flagged(uri, identity) else link.uri,
        destination=WITHHELD if _flagged(destination, identity) else link.destination,
    )


def _flagged(link: Link, identity: Identity) -> bool:
    """Whether the privacy matcher flags `link`; one with neither value draws no flag."""
    return bool(
        privacy_flags(identity, info={}, xmp=None, links=(link,), text_pages=())
    )


def _withheld_if_present(
    field: str, value: str | None, identity: Identity
) -> str | None:
    """`withhold_field`'s verdict on a field pdfinfo printed; a field it did not stays None."""
    return None if value is None else withhold_field(field, value, identity)
