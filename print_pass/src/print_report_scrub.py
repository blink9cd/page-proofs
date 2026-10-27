"""The report's text scrub: what a text names of this machine, replaced by tokens.

A library's message, a page's error or a request the engine blocked can each echo a path or
this machine's hostname, so every text the report carries passes through `scrub` before it
is written. The scrub is pure text in, text out: the reading half scrubs each PDF's problems
and pdfminer's log templates with it, and the report scrubs each state's problems, network
entries and read-back strings.

No home-rooted shape the privacy matcher seeks survives the scrub, and neither does this
account's home, in any case, unless a word character or a hyphen follows it and continues
its last name. That is not everything the matcher flags: its own home rule is a plain
substring, so another account whose name extends this one's (`sammy` beside `sam`) is
flagged there and, in a web folder, left whole here (see `scrub`).

Entry point assumptions: the privacy module's `Identity`, the one the PDFs were read with; its
`HOME_ROOTED` pattern, the shapes the scrub labels as another account's home; and its rules for
which hostnames are sought, where one stands alone, and what identifier is hollow, so the scrub
replaces what the matcher would find by the matcher's own definitions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final

from pdf_privacy import (
    HOME_ROOTED,
    NAME_CHARACTER,
    NOT_AN_IDENTIFIER,
    Identity,
    sought_hostnames,
)

#: What the scrub writes for the out dir, this account's home, this machine's hostnames,
#: and any other home.
OUT_TOKEN: Final = "<out>"
HOME_TOKEN: Final = "~"
HOSTNAME_TOKEN: Final = "<hostname>"
OTHER_HOME_TOKEN: Final = "<home>"

#: A path's end: the out dir and the home match only where their last name ends, never
#: inside a longer one (an account `sam`'s home is not the start of an account `sammy`'s).
#: A dot continues the name only when a word character follows it (`sam.bak` is not
#: `sam`), so the full stop ending a sentence still ends the path before it.
_PATH_END: Final = r"(?![\w-]|\.\w)"

#: Where a name goes no further: no word character or hyphen follows it, so a dot ends it
#: whatever follows the dot. The home's last pass ends there, once the home-rooted pass has
#: labeled every `sam.bak` shape it can as another account's.
_NAME_END: Final = r"(?![\w-])"

#: One replacement the scrub makes: what it seeks, and the token it writes in its place.
type _Pass = tuple[re.Pattern[str], str]


# fuzz-target
def scrub(text: str, *, out_dir: Path, identity: Identity) -> str:
    """`text` safe to share: the out dir, home, hostnames and any other home as tokens.

    The out dir reads `<out>`, the home `~`, each hostname `<hostname>` and any other home
    `<home>`, in that order, and then the home once more. The out dir goes first, as given
    and resolved, so one under the home still reads as the out dir. The home matches in any
    case, as the privacy matcher's does, but the two differ on where: the matcher's is a
    plain substring, since there a false flag is cheap, while the scrub's never matches
    where more of a name follows. Replacing the home inside a longer name would leave
    another account's path a partial tilde (`~my/x`) the home-rooted pass no longer knows;
    left whole, it reads as `<home>`.

    The first home pass takes the home at a path's end, where a dot followed by a word
    character still continues its name (`sam.bak` is another account). The last runs after
    the home-rooted pass has labeled every such shape it can, and takes the home past a
    dot too, in the places that pass cannot see: after a host's or a folder's last
    character (a web folder's `.html`, a copy's in a backup folder). Its cost is in the
    safe direction: another account's name past a dot there, `sam.bak` beside the home of
    `sam`, reads as `~.bak`. A hostname is replaced only where it stands alone, as the
    matcher seeks one in a page's text.

    Every pass repeats until none changes anything, since one can uncover another's match:
    a replacement right after a home root's own `/Users/` forms a new home-rooted path, a
    named host hides the home-shaped web folder after it, a drive letter hides the end of a
    hostname before it, and a hostname after a dot continues the home's name.

    The repeating ends. Each out-dir, home or home-rooted replacement removes a path
    separator, and no token contains one, so those run out; this is why a home is sought
    only when it is absolute, since a relative one may hold no separator at all. Between
    them the hostname pass can only use up the text outside the tokens it wrote, since it
    never seeks the one name its own token spells, and a name of the characters a hostname
    holds cannot straddle a token's brackets.
    """
    passes = _scrub_passes(out_dir, identity)
    while True:
        scrubbed = text
        for pattern, token in passes:
            scrubbed = pattern.sub(token, scrubbed)
        if scrubbed == text:
            return text
        text = scrubbed


def _scrub_passes(out_dir: Path, identity: Identity) -> list[_Pass]:
    """What `scrub` replaces, in the order it replaces it, each with the token it writes."""
    forms = sorted(
        {str(out_dir.absolute()), str(out_dir.resolve())}, key=len, reverse=True
    )
    passes = [
        (re.compile(re.escape(form) + _PATH_END), OUT_TOKEN)
        for form in forms
        if form.strip(NOT_AN_IDENTIFIER)
    ]
    home_sought = _is_sought_home(identity.home)
    if home_sought:
        passes.append(_home_pass(identity.home, _PATH_END))
    hostnames = _sought_hostnames(identity)
    if hostnames:
        passes.append((_standing_alone(hostnames), HOSTNAME_TOKEN))
    passes.append((HOME_ROOTED, OTHER_HOME_TOKEN))
    if home_sought:
        passes.append(_home_pass(identity.home, _NAME_END))
    return passes


def _is_sought_home(home: str) -> bool:
    """Whether the scrub seeks `home`: an absolute path, POSIX or Windows, not a hollow one.

    A relative home (`Path.home()` returns a relative `$HOME` as it is) may hold no path
    separator for its replacement to remove, and the loop's end rests on that removal.
    """
    absolute = PurePosixPath(home).is_absolute() or PureWindowsPath(home).is_absolute()
    return absolute and bool(home.strip(NOT_AN_IDENTIFIER))


def _home_pass(home: str, end: str) -> _Pass:
    """The home, in any case, where `end` says its last name ends, written as a tilde."""
    return re.compile(re.escape(home) + end, re.IGNORECASE), HOME_TOKEN


def _sought_hostnames(identity: Identity) -> tuple[str, ...]:
    """The hostnames the scrub seeks: those the privacy matcher seeks, less the token's own.

    The matcher's own list, so never a hollow one and never `localhost`. A machine named by
    the token's own word is never sought: each replacement would spell its name again, so
    the scrub would never finish, and the token already reads as that name.
    """
    return tuple(
        name
        for name in sought_hostnames(identity)
        if _standing_alone((name,)).search(HOSTNAME_TOKEN) is None
    )


def _standing_alone(hostnames: Sequence[str]) -> re.Pattern[str]:
    """Any of `hostnames`, in any case, where no name character touches either side of it.

    The name character is the matcher's own, so a hostname is replaced exactly where the
    matcher would find it standing alone.

    The longest is tried first, so a full hostname is replaced whole, never its short form
    alone with the domain left behind.
    """
    names = "|".join(
        re.escape(name) for name in sorted(hostnames, key=len, reverse=True)
    )
    return re.compile(
        rf"(?<!{NAME_CHARACTER})(?:{names})(?!{NAME_CHARACTER})", re.IGNORECASE
    )
