r"""This machine's identifiers, and the matcher that flags a PDF carrying any of them.

A flag says WHERE (`metadata:Author`, `xmp`, `link on page 2`, `text on page 1`) and
WHAT CLASS (`a home-rooted path`), and never the value it found: the report quoting it is
meant to be safe to share, and a flag that repeated a leaked path would leak it again.
That holds for an Info field's NAME as much as its value, since a PDF's maker chooses
both: a field whose name carries an identifier is flagged under the neutral
`metadata:(a field whose name is flagged)`.

What is sought where:

| Where | Flagged when |
| --- | --- |
| each Info field | `Author` is non-empty and not a generator's placeholder; any field holding the login or the full name (except `Title`), a hostname, or a home-rooted path |
| each Info field's name | the login, the full name, a hostname, or a home-rooted path |
| the XMP packet | the same identifiers and paths as an Info field |
| each link URI | a `file:` scheme; a hostname standing alone; a home-rooted path |
| each link destination | a hostname standing alone; a home-rooted path |
| each text page | `file://`; a hostname standing alone; a home-rooted path |

A page's own words legitimately carry its author's name, so the text layer, the link
URIs and destinations and the `Title` field (which a browser fills from the page's
`<title>`) are NOT searched for the full name or the login. In metadata, identifiers match as
case-insensitive substrings, which favors recall: a false flag is cheap and visible, a
missed leak is silent. In a page's text and links a hostname must stand alone, never
inside a longer name, because a page's words and addresses are the site's own and a
short hostname is often part of an ordinary word. An identifier with nothing in it (an
empty login, a home of `/`) would match every value, so it is never sought, and neither
is `localhost`: the pages are served over loopback, so that name identifies no machine.

A home-rooted path is this account's home directory, or anything shaped like
someone's: `/Users/<name>` or `/home/<name>`, bare or under a Windows drive's mount
(WSL's `/mnt/<letter>`, Cygwin's `/cygdrive/<letter>`, Git Bash's `/<letter>`) or a
macOS volume (`/System/Volumes/Data`, `/Volumes/<disk>`), or `<drive>:\Users\<name>`
(either slash, or backslashes doubled as Python quotes them), each in any case; but
never a web address's own folder, or a relative path, that merely shares a home root's
name.

Entry point assumptions: `host_identity` is the only function that reads the machine, so
every caller that tests the matcher injects its own `Identity`. The full name comes from
the passwd database, so on Windows, which has none, no full name is sought.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pdf_text import Link
from pdf_tools import AUTHOR_FIELD, TITLE_FIELD

#: The classes a flag can carry, in the order one location reports them.
NAMES_A_PERSON: Final = "names a person"
CONTAINS_LOGIN: Final = "contains this machine's login"
CONTAINS_FULL_NAME: Final = "contains this machine's full name"
CONTAINS_HOSTNAME: Final = "contains this machine's hostname"
HOME_ROOTED_PATH: Final = "a home-rooted path"
FILE_URL: Final = "a file: URL"

#: Where a flag on an Info field whose NAME carries an identifier says it is: the name
#: itself would repeat the identifier.
_FLAGGED_FIELD_WHERE: Final = "metadata:(a field whose name is flagged)"

#: Author values a generator writes when it knows no author: they name nobody.
_PLACEHOLDER_AUTHORS: Final = frozenset({"anonymous", "unknown"})

#: The folders home directories live in, kept as separate words so this file holds no
#: home-rooted literal of its own for the repository's PII scan to trip on.
_USERS: Final = "Users"
_HOME: Final = "home"

#: Where a POSIX tool also sees a home's root: under WSL's, Cygwin's or Git Bash's mount
#: of a Windows drive, under macOS's data volume, and under another disk's volume, whose
#: name may hold single spaces (`Macintosh HD`).
_MOUNTS: Final = (
    r"/mnt/[a-z]|/cygdrive/[a-z]|/[a-z]|/System/Volumes/Data"
    + r"|/Volumes/[^/\s]+(?: [^/\s]+)*"
)

#: `/Users/<name>` or `/home/<name>`, bare or under one of `_MOUNTS`, and
#: `<drive>:\Users\<name>` with either slash, or with each backslash doubled as Python
#: quotes a path; every folder name in any case, since a Mac's and a Windows file system
#: both ignore it. Exactly two things are refused. A root slash (a mount's own first)
#: straight after a word character, a dot, a `%` or a hyphen: that is a web address's
#: folder (a domain's last letter, a port's digit, a language folder's) or a relative
#: path (`../home/<name>`, which names no home until it is known where it was read
#: from), never a filesystem root. And a drive letter before a forward slash straight
#: after another letter, so a `file:` URL's `e:` is no drive; before a backslash, which
#: no URL writes, a drive is one whatever precedes it, so a word run into it hides
#: nothing. A colon, a slash, a space or a bracket before the root is not refused: a
#: one-slash `file:` URL's colon, a `file:` URL's third slash, "saved to" a path. Every
#: match holds a path separator. Public because the report scrubs every problem text
#: with this same shape.
HOME_ROOTED: Final = re.compile(
    rf"(?<![\w.%-])(?i:(?:{_MOUNTS})?/(?:{_USERS}|{_HOME}))/[^/\s]+"
    + rf"|[A-Za-z]:\\\\?(?i:{_USERS})(?:\\\\?|/)[^\\/\s]+"
    + rf"|(?<![A-Za-z])[A-Za-z]:/(?i:{_USERS})[\\/][^\\/\s]+"
)

#: A character that continues a hostname: a hostname in text or a link counts only
#: when neither side of it is one, so `mac` never flags `machine` or `mac-mini`. Public
#: because the report's scrub replaces a hostname only where this matcher would find it.
NAME_CHARACTER: Final = r"[\w-]"

#: The loopback name the pages are served under: it identifies no machine.
_LOOPBACK_HOSTNAME: Final = "localhost"

#: How a link to a local file opens, and the text a printed one leaves behind. The
#: scheme is matched as a prefix rather than parsed: urllib's parser raises on text no
#: URL parser accepts (an unclosed IPv6 bracket), and a PDF's link can hold any text.
_FILE_SCHEME: Final = "file:"
_FILE_URL_TEXT: Final = "file://"

#: What an identifier consisting only of these characters is: nothing worth seeking.
#: Public because the scrub seeks no out dir or home made of them either: one of `/`
#: would turn every slash into its token.
NOT_AN_IDENTIFIER: Final = " \t/\\."

#: The passwd GECOS field holds the full name first, then comma-separated extras.
_GECOS_SEPARATOR: Final = ","

#: A fully-qualified hostname's short form is its first label.
_LABEL_SEPARATOR: Final = "."

#: How one location decides whether a value carries a hostname.
_FindsHostname = Callable[[str, str], bool]


@dataclass(frozen=True)
class Identity:
    """What would identify this machine or its account inside a PDF."""

    login: str
    full_name: str | None
    hostnames: tuple[str, ...]
    home: str


@dataclass(frozen=True)
class Flag:
    """One finding: where it is and what class of identifier it is, never the value."""

    where: str
    what: str


def _full_name() -> str | None:
    """This account's full name from its GECOS field, or None if it has none.

    Windows keeps no GECOS field, so there no full name is sought and the login, hostnames
    and home still are; the arm is an ``if``, not a module-level split, so a
    win32 type-check pass sees every other name in this module. The passwd module is
    imported in the POSIX arm alone, since Windows has none to import.
    """
    if sys.platform == "win32":
        return None  # pragma: only-on win32
    else:  # pragma: only-on posix
        import pwd

        try:
            gecos = pwd.getpwuid(os.getuid()).pw_gecos
        except KeyError:
            # An account missing from the passwd database (a container's arbitrary uid)
            # simply has no full name to seek.
            return None
        return gecos.split(_GECOS_SEPARATOR, 1)[0].strip() or None


def _hostnames(hostname: str) -> tuple[str, ...]:
    """The lower-cased full and short forms of `hostname`, de-duplicated, empties dropped."""
    full = hostname.strip().lower()
    forms = (full, full.split(_LABEL_SEPARATOR, 1)[0])
    return tuple(dict.fromkeys(form for form in forms if form))


def host_identity() -> Identity:
    """This machine's login, account full name, hostnames and home directory."""
    return Identity(
        login=getpass.getuser(),
        full_name=_full_name(),
        hostnames=_hostnames(socket.gethostname()),
        home=str(Path.home()),
    )


def _holds(value: str, identifier: str | None) -> bool:
    """Whether `value` contains `identifier`, ignoring case; a hollow one never matches."""
    if identifier is None or not identifier.strip(NOT_AN_IDENTIFIER):
        return False
    return identifier.strip().casefold() in value.casefold()


def _stands_alone(value: str, hostname: str) -> bool:
    """Whether `value` holds `hostname` as a whole name, not inside a longer one."""
    whole = rf"(?<!{NAME_CHARACTER}){re.escape(hostname)}(?!{NAME_CHARACTER})"
    return re.search(whole, value, re.IGNORECASE) is not None


def sought_hostnames(identity: Identity) -> tuple[str, ...]:
    """The hostnames worth seeking, stripped: never a hollow one, and never `localhost`.

    Public because the report's scrub replaces exactly these, less one of its own.
    """
    return tuple(
        hostname.strip()
        for hostname in identity.hostnames
        if hostname.strip(NOT_AN_IDENTIFIER)
        and hostname.strip().casefold() != _LOOPBACK_HOSTNAME
    )


def _is_home_rooted(identity: Identity, value: str) -> bool:
    """Whether `value` holds this account's home, or any path shaped like a home."""
    return _holds(value, identity.home) or HOME_ROOTED.search(value) is not None


def _identifier_classes(
    identity: Identity, value: str, *, names: bool, finds_hostname: _FindsHostname
) -> Iterator[str]:
    """The classes `value` falls in; the login and full name are sought only with `names`."""
    if names and _holds(value, identity.login):
        yield CONTAINS_LOGIN
    if names and _holds(value, identity.full_name):
        yield CONTAINS_FULL_NAME
    if any(finds_hostname(value, host) for host in sought_hostnames(identity)):
        yield CONTAINS_HOSTNAME
    if _is_home_rooted(identity, value):
        yield HOME_ROOTED_PATH


def _metadata_classes(identity: Identity, value: str, *, names: bool) -> Iterator[str]:
    """A metadata value's classes: there, every identifier is a plain substring."""
    return _identifier_classes(identity, value, names=names, finds_hostname=_holds)


def _content_classes(identity: Identity, value: str) -> Iterator[str]:
    """A link's or a page's classes: no names, and only a hostname standing alone."""
    return _identifier_classes(
        identity, value, names=False, finds_hostname=_stands_alone
    )


def _names_a_person(field: str, value: str) -> bool:
    """Whether an Info field is an `Author` naming someone, not a generator's placeholder."""
    author = value.strip().casefold()
    return field == AUTHOR_FIELD and bool(author) and author not in _PLACEHOLDER_AUTHORS


def _field_value_classes(identity: Identity, field: str, value: str) -> Iterator[str]:
    """The classes one Info field's value falls in; `Title` is not searched for names."""
    if _names_a_person(field, value):
        yield NAMES_A_PERSON
    yield from _metadata_classes(identity, value, names=field != TITLE_FIELD)


def _info_flags(identity: Identity, info: Mapping[str, str]) -> Iterator[Flag]:
    """Flags for each Info field, name then value, in the order the dictionary holds them."""
    for field, value in info.items():
        name_classes = tuple(_metadata_classes(identity, field, names=True))
        where = _FLAGGED_FIELD_WHERE if name_classes else f"metadata:{field}"
        for what in (*name_classes, *_field_value_classes(identity, field, value)):
            yield Flag(where, what)


def _link_flags(identity: Identity, links: Sequence[Link]) -> Iterator[Flag]:
    """Flags for each link's URI and destination: both are the PDF maker's own text."""
    for link in links:
        where = f"link on page {link.page}"
        for value in (link.uri, link.destination):
            for what in () if value is None else _content_classes(identity, value):
                yield Flag(where, what)
        if link.uri is not None and link.uri.strip().casefold().startswith(
            _FILE_SCHEME
        ):
            yield Flag(where, FILE_URL)


def _text_flags(identity: Identity, text_pages: Sequence[str]) -> Iterator[Flag]:
    """Flags for each page of text; names are not sought, since a site's words carry them."""
    for number, text in enumerate(text_pages, start=1):
        where = f"text on page {number}"
        for what in _content_classes(identity, text):
            yield Flag(where, what)
        if _FILE_URL_TEXT in text.casefold():
            yield Flag(where, FILE_URL)


# fuzz-target
def privacy_flags(
    identity: Identity,
    *,
    info: Mapping[str, str],
    xmp: str | None,
    links: Sequence[Link],
    text_pages: Sequence[str],
) -> tuple[Flag, ...]:
    """Every place a PDF's metadata, links or text identify `identity`, by where and class.

    Flags come Info first, then XMP, then links, then text; a class found twice in one
    place (two `file:` links on one page) is reported once.
    """
    xmp_flags = (
        ()
        if xmp is None
        else (
            Flag("xmp", what) for what in _metadata_classes(identity, xmp, names=True)
        )
    )
    found = (
        *_info_flags(identity, info),
        *xmp_flags,
        *_link_flags(identity, links),
        *_text_flags(identity, text_pages),
    )
    return tuple(dict.fromkeys(found))
