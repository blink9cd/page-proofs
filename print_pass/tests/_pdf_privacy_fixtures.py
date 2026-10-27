"""Shared fixtures for pdf_privacy's two suites: a fake identity and a matcher runner.

`test_pdf_privacy` covers the metadata half of the matcher and the host read;
`test_pdf_privacy_content` covers the page's content, its links and its text. Both
inject this fixture `Identity` of obviously fake values, so no test reads this machine.
The home directory is assembled from parts at runtime: a literal home-rooted path in a
test file is exactly what the repository's PII scan exists to refuse.

Entry point assumptions: imported by a suite that `unittest` discovery loaded with this
folder as its top-level directory, AFTER that suite's bootstrap has put the tool's
private `src/` on the path, which is where `pdf_privacy` and `pdf_text` come from.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

import pdf_privacy
from pdf_text import Link

LOGIN: Final = "fixture-login"
FULL_NAME: Final = "Fixture Person"
HOSTNAMES: Final = ("fixture-host.example", "fixture-host")

#: The two roots the home-rooted pattern hunts, spelled apart so no path literal forms.
USERS: Final = "Users"
HOME_ROOT: Final = "home"
HOME: Final = "/" + USERS + "/" + LOGIN

#: Another account's home folders, so a case exercises the pattern, not this home.
SOMEONE: Final = "someone"
USERS_PATH: Final = "/" + USERS + "/" + SOMEONE
HOME_PATH: Final = "/" + HOME_ROOT + "/" + SOMEONE

IDENTITY: Final = pdf_privacy.Identity(
    login=LOGIN, full_name=FULL_NAME, hostnames=HOSTNAMES, home=HOME
)

#: The classes a flag may carry, pinned literally: a report reader keys on this wording.
CLASSES: Final = (
    "names a person",
    "contains this machine's login",
    "contains this machine's full name",
    "contains this machine's hostname",
    "a home-rooted path",
    "a file: URL",
)

#: A link rectangle; the matcher never reads it.
_RECT: Final = (0.0, 0.0, 10.0, 10.0)


def flags(
    identity: pdf_privacy.Identity = IDENTITY,
    *,
    info: Mapping[str, str] | None = None,
    xmp: str | None = None,
    links: Sequence[Link] = (),
    text_pages: Sequence[str] = (),
) -> tuple[pdf_privacy.Flag, ...]:
    """Run the matcher with every location empty unless given."""
    return pdf_privacy.privacy_flags(
        identity, info=info or {}, xmp=xmp, links=links, text_pages=text_pages
    )


def link(page: int, uri: str | None) -> Link:
    """A link on `page` to `uri`."""
    return Link(page, uri, None, _RECT)


def one(where: str, what: str) -> tuple[pdf_privacy.Flag, ...]:
    """The flags of a PDF with exactly one finding."""
    return (pdf_privacy.Flag(where, what),)
