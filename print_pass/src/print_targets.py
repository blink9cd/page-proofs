"""The targets file: the pages to print, the states to print each in, and what to read back.

A targets file is TOML, and it is the only knowledge of any one site the engine is ever
given, so every key is known here and an unknown one is refused: a misspelled key must
fail loudly rather than pass as its default. Each refusal is a `TargetsError` whose message
opens with the PATH to the bad key -- `target[1].state[2].color_scheme`, arrays counted from
zero in file order -- because that path is the whole of what an operator is shown. What a
value must be, whatever key holds it, is checked by `print_targets_checks`, which raises
that error and knows no key; what an expected off-loopback host may be, by `print_offsite`.

Entry point assumptions: `load_targets` reads one file the caller names, `parse_targets`
takes what `tomllib` made of one, and `adhoc_targets` / `parse_viewport` take command-line
values. Nothing here touches a browser, a server or the network.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final, Literal

import print_targets_checks as checks
from print_offsite import expected_hosts
from print_targets_checks import TargetsError

type ActionKind = Literal["click", "wait_for", "wait_ms"]
type ReadKind = Literal["attribute", "property", "text"]

#: The requested color scheme installed Chrome shows through a real dark setting it is
#: launched with, never through emulation. Spelled here once, for the engine and the judge.
NATIVE_DARK: Final = "native-dark"

#: How a state's color scheme is set. The FIRST is the default: `native` leaves the
#: engine's own setting alone; `light` and `dark` are emulated; `native-dark` asks installed
#: Chrome for a real dark setting.
COLOR_SCHEMES: Final = ("native", "light", "dark", NATIVE_DARK)

#: How a state's reduced-motion preference is set. The FIRST, left alone, is the default.
REDUCED_MOTIONS: Final = ("native", "reduce")

#: Backgrounds printed off (the print dialog's default), on, or both from one load.
BACKGROUNDS: Final = ("off", "on", "both")

#: What a `[[read]]` reads from the element its selector matches; a read names exactly one.
READ_KINDS: Final[tuple[ReadKind, ...]] = ("attribute", "property", "text")

#: The name of the one state a target gets when it declares none.
DEFAULT_STATE_NAME: Final = "default"

#: The longest `wait_ms` or `print_at_ms` a state may ask for: a day, in milliseconds, a
#: ceiling far inside what the watchdog can wait. A wait's watchdog bound is its own length
#: plus the run's timeout, so a wait long enough to pass the watchdog's limit would
#: otherwise be refused only after the run had started; anything over a day is refused
#: here, with its key.
MAX_MS: Final = 24 * 60 * 60 * 1000

#: CSS pixels per inch: what a `page_width` in CSS px is to the page's inches.
PX_PER_INCH: Final = 96
#: US Letter's 8.5 in width in CSS px. A `page_width` is laid out inside it, so the widest
#: one taken is a pixel under it: at 816 no margin would be left.
LETTER_WIDTH_PX: Final = 816
#: The narrowest `page_width` taken: 320 px, the narrowest phone a page is laid out for
#: (the smallest iPhones' CSS width). A width exists to approximate a phone's print, and no
#: phone lays a page out narrower; it also keeps each margin under 2.6 in, so Letter's height
#: is never all margin.
MIN_PAGE_WIDTH: Final = 320

# An action waits on, or clicks, the element a selector matches -- or waits a set time.
_SELECTOR_ACTIONS: Final[tuple[ActionKind, ...]] = ("click", "wait_for")
_WAIT_ACTION: Final[ActionKind] = "wait_ms"
_ACTION_KINDS: Final = (*_SELECTOR_ACTIONS, _WAIT_ACTION)

# The read kind that names no key: an element's text is read whole.
_TEXT_KIND: Final[ReadKind] = "text"

# The keys each table may hold. Anything else is refused by name.
_FILE_READS: Final = "read"  # also the path every target's reads open with
_FILE_HOSTS: Final = "expected_offsite_hosts"  # what each may be is `print_offsite`'s
_FILE_KEYS: Final = frozenset(
    {"viewport", "backgrounds", _FILE_HOSTS, _FILE_READS, "target"}
)
_VIEWPORT_KEYS: Final = ("width", "height")
_READ_KEYS: Final = frozenset({"name", "selector", "all", *READ_KINDS})
_TARGET_KEYS: Final = frozenset(
    {"name", "page", "storage_reads", "read", "state", "follows_redirect"}
)
_STATE_KEYS: Final = frozenset(
    {
        "name",
        "color_scheme",
        "reduced_motion",
        "storage",
        "actions",
        "print_at_ms",
        "page_width",
    }
)

# Target and state names are joined with `--` into output file names, so a name is runs of
# lowercase letters and digits joined by ONE dot or hyphen each: a doubled separator would
# let two target and state pairs name the same file. Matched WHOLE: a `$` anchor would let
# a trailing newline through.
_NAME_PATTERN: Final = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")

# `--viewport`'s form. Five digits a side is far past any real window, and short of the digit
# count at which `int` raises a plain ValueError instead of converting.
_VIEWPORT_PATTERN: Final = re.compile(r"([0-9]{1,5})x([0-9]{1,5})")

# A viewport side of zero lays nothing out; a wait or print time of zero means "now", and a
# negative one means nothing.
_MIN_PIXELS: Final = 1
_MIN_MS: Final = 0

# A page is a server path, opening with one `/`. Concatenated onto the origin's url string
# it cannot move the host; the refusal guards a page ever RESOLVED as a url reference (by
# `urljoin`, or a WHATWG parser such as a browser's, which reads `/\` as `//` in an http
# url), where an opening `//` names ANOTHER host.
_PAGE_PREFIX: Final = "/"
_NETWORK_PATH_PREFIXES: Final = ("//", "/\\")


@dataclass(frozen=True)
class Viewport:
    """The window a state is laid out in, in CSS pixels."""

    width: int
    height: int


@dataclass(frozen=True)
class Action:
    """One step run after load: click or wait for a selector, or wait a set time."""

    kind: ActionKind
    selector: str | None  # click / wait_for
    ms: int | None  # wait_ms


@dataclass(frozen=True)
class Read:
    """One value read back in-page from the element a selector matches.

    It reads an attribute, a property, or the element's text; `all` reads every match
    rather than the first.
    """

    name: str
    selector: str
    kind: ReadKind
    key: str | None  # the attribute or property name; None for text
    all: bool


@dataclass(frozen=True)
class State:
    """One way to print a page: its emulation, stored values, actions and print moment.

    `page_width` is the width in CSS px its page is laid out at, which the engine prints at
    through the margin `state_margin_inches` derives; None prints at the engine's own margin.
    """

    name: str
    color_scheme: str  # one of COLOR_SCHEMES
    reduced_motion: str  # one of REDUCED_MOTIONS
    storage: tuple[tuple[str, str], ...]
    actions: tuple[Action, ...]
    print_at_ms: int | None
    page_width: int | None = None


@dataclass(frozen=True)
class Target:
    """One served page, the states to print it in, and what to read back in each.

    `follows_redirect` declares `page` a meta refresh to follow (`print_redirects`).
    """

    name: str
    page: str
    storage_reads: tuple[str, ...]
    reads: tuple[Read, ...]  # the file's [[read]] followed by the target's own
    states: tuple[State, ...]
    follows_redirect: bool = False


@dataclass(frozen=True)
class TargetsFile:
    """A whole targets file; `viewport` and `backgrounds` are None where it leaves them out.

    Those two override the command line's for every state; a state's own `page_width`
    changes its print margin, and every other print setting stays the engine's.
    `expected_offsite_hosts` are the hosts off loopback its pages are known to load from, in
    file order, and empty where it names none: a request blocked on one is still aborted,
    and judged a warning rather than a finding.
    """

    viewport: Viewport | None
    backgrounds: str | None
    expected_offsite_hosts: tuple[str, ...]
    targets: tuple[Target, ...]


# What `_refuse_duplicate_names` compares: anything a targets file names.
type _Named = Target | State | Read

_DEFAULT_STATE: Final = State(
    name=DEFAULT_STATE_NAME,
    color_scheme=COLOR_SCHEMES[0],
    reduced_motion=REDUCED_MOTIONS[0],
    storage=(),
    actions=(),
    print_at_ms=None,
    page_width=None,
)


def load_targets(path: Path) -> TargetsFile:
    """Read and validate the targets file at `path`.

    Raises:
        TargetsError: the file cannot be read, is not TOML (bytes that are not UTF-8
            included, which `tomllib` reports as a `UnicodeDecodeError`), or its content is
            refused -- the first two naming the file, the last the key.
    """
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise TargetsError(f"{path}: not a readable targets file: {error}") from error
    return parse_targets(data)


# fuzz-target
def parse_targets(data: Mapping[str, object]) -> TargetsFile:
    """Validate a parsed targets document into a `TargetsFile`.

    Raises:
        TargetsError: naming the path to the first key refused.
    """
    checks.require_keys(data, "", required=("target",), allowed=_FILE_KEYS)
    file_reads: tuple[Read, ...] = checks.optional(
        data, "", _FILE_READS, checks.array_of(_read), ()
    )
    return TargetsFile(
        viewport=checks.optional(data, "", "viewport", _viewport, None),
        backgrounds=checks.optional(
            data, "", "backgrounds", checks.one_of(BACKGROUNDS), None
        ),
        expected_offsite_hosts=checks.optional(
            data, "", _FILE_HOSTS, expected_hosts, ()
        ),
        targets=_targets(data["target"], file_reads),
    )


def adhoc_targets(pages: Sequence[str]) -> TargetsFile:
    """One target per page, each in the default state, for a run given pages, not a file.

    Targets are named `page-1`, `page-2`, ... in the order given.

    Raises:
        TargetsError: no pages, or a page that is not a server path (quoted).
    """
    if not pages:
        raise TargetsError("page: at least one page is required")
    return TargetsFile(
        viewport=None,
        backgrounds=None,
        expected_offsite_hosts=(),
        targets=tuple(
            Target(
                name=f"page-{number}",
                page=_page(page, f"page {page!r}"),
                storage_reads=(),
                reads=(),
                states=(_DEFAULT_STATE,),
            )
            # Counted from one, as the names are read.
            for number, page in enumerate(pages, start=1)
        ),
    )


def state_margin_inches(state: State, default: Fraction) -> Fraction:
    """The margin, on every side, `state` is printed with, in exact inches.

    With a `page_width`, it is half of what that width leaves of US Letter's: `(816 -
    page_width) / 2` CSS px, exact, so an odd width's half-pixel margin is not rounded. With
    none, it is `default`, the engine's own margin, returned as it was given. Both engines
    derive a state's margin here, so a width means one margin whichever prints it.
    """
    if state.page_width is None:
        return default
    return Fraction(LETTER_WIDTH_PX - state.page_width, 2 * PX_PER_INCH)


def parse_viewport(text: str) -> Viewport:
    """Parse `WIDTHxHEIGHT` (`1280x800`), each side a whole number of pixels above zero.

    Raises:
        TargetsError: any other shape, quoting `text`.
    """
    where = f"viewport {text!r}"
    match = _VIEWPORT_PATTERN.fullmatch(text)
    if match is None:
        raise TargetsError(f"{where}: must be WIDTHxHEIGHT in pixels, as in 1280x800")
    width, height = (int(side) for side in match.groups())
    return Viewport(
        width=checks.int_at_least(width, f"{where} width", _MIN_PIXELS),
        height=checks.int_at_least(height, f"{where} height", _MIN_PIXELS),
    )


def _name(value: object, where: str) -> str:
    """A target or state name, in `_NAME_PATTERN`'s alphabet."""
    if not isinstance(value, str) or _NAME_PATTERN.fullmatch(value) is None:
        raise TargetsError(
            f"{where}: must be lowercase letters and digits, joined by single dots"
            " or hyphens"
        )
    return value


def _ms(value: object, where: str) -> int:
    """A time in whole milliseconds, from zero, which means now, to `MAX_MS`."""
    return checks.int_between(value, where, _MIN_MS, MAX_MS)


def _page_width(value: object, where: str) -> int:
    """A page width in whole CSS px, from `MIN_PAGE_WIDTH` to a pixel under Letter's."""
    return checks.int_between(value, where, MIN_PAGE_WIDTH, LETTER_WIDTH_PX - 1)


def _page(value: object, where: str) -> str:
    """A server path: one opening `/`, no slash or backslash after it, no dot segment.

    No page needs a `.` or `..` segment, and a `..` can name a file outside the served
    folder, so one is refused as browser_pass refuses it.
    """
    if (
        not isinstance(value, str)
        or not value.startswith(_PAGE_PREFIX)
        or value.startswith(_NETWORK_PATH_PREFIXES)
    ):
        raise TargetsError(f"{where}: must be a server path opening with a single /")
    if checks.has_dot_segment(value):
        raise TargetsError(f"{where}: it has a . or .. segment, which no page needs")
    return value


def _viewport(value: object, where: str) -> Viewport:
    """The `viewport` table: exactly `width` and `height`, each at least one pixel."""
    table = checks.table(value, where)
    checks.require_keys(table, where, required=_VIEWPORT_KEYS, allowed=_VIEWPORT_KEYS)
    return Viewport(
        width=checks.int_at_least(
            table["width"], checks.at(where, "width"), _MIN_PIXELS
        ),
        height=checks.int_at_least(
            table["height"], checks.at(where, "height"), _MIN_PIXELS
        ),
    )


def _read(value: object, where: str) -> Read:
    """One `[[read]]`: a name, a selector, exactly one read kind, and an optional `all`."""
    table = checks.table(value, where)
    checks.require_keys(table, where, required=("name", "selector"), allowed=_READ_KEYS)
    kind = checks.exactly_one(table, where, READ_KINDS)
    return Read(
        name=checks.text(table["name"], checks.at(where, "name")),
        selector=checks.text(table["selector"], checks.at(where, "selector")),
        kind=kind,
        key=_read_key(table[kind], checks.at(where, kind), kind),
        all=checks.optional(table, where, "all", checks.boolean, False),
    )


def _read_key(value: object, where: str, kind: ReadKind) -> str | None:
    """The attribute or property a read names, or None for `text = true`."""
    if kind != _TEXT_KIND:
        return checks.text(value, where)
    if value is not True:
        raise TargetsError(f"{where}: must be true, since a text read names no key")
    return None


def _action(value: object, where: str) -> Action:
    """One action: a table holding exactly one of `click`, `wait_for`, `wait_ms`."""
    table = checks.table(value, where)
    checks.require_keys(table, where, required=(), allowed=_ACTION_KINDS)
    kind = checks.exactly_one(table, where, _ACTION_KINDS)
    argument, at = table[kind], checks.at(where, kind)
    if kind == _WAIT_ACTION:
        return Action(kind, None, _ms(argument, at))
    return Action(kind, checks.text(argument, at), None)


def _storage(value: object, where: str) -> tuple[tuple[str, str], ...]:
    """A `storage` table of string keys to string values, kept in file order."""
    pairs: list[tuple[str, str]] = []
    for key, stored in checks.table(value, where).items():
        # A key is checked as well as a value: TOML makes only string keys, but a
        # caller handing a mapping straight to `parse_targets` can make any.
        if not isinstance(key, str) or not isinstance(stored, str):
            raise TargetsError(
                f"{where}.{key}: must map a string key to a string value"
            )
        pairs.append((key, stored))
    return tuple(pairs)


def _state(value: object, where: str) -> State:
    """One `[[target.state]]`; every key but `name` is optional."""
    table = checks.table(value, where)
    checks.require_keys(table, where, required=("name",), allowed=_STATE_KEYS)
    scheme, motion = checks.one_of(COLOR_SCHEMES), checks.one_of(REDUCED_MOTIONS)
    return State(
        name=_name(table["name"], checks.at(where, "name")),
        color_scheme=checks.optional(
            table, where, "color_scheme", scheme, COLOR_SCHEMES[0]
        ),
        reduced_motion=checks.optional(
            table, where, "reduced_motion", motion, REDUCED_MOTIONS[0]
        ),
        storage=checks.optional(table, where, "storage", _storage, ()),
        actions=checks.optional(table, where, "actions", checks.array_of(_action), ()),
        print_at_ms=checks.optional(table, where, "print_at_ms", _ms, None),
        page_width=checks.optional(table, where, "page_width", _page_width, None),
    )


def _refuse_duplicate_names(*arrays: tuple[Sequence[_Named], str]) -> None:
    """Refuse the second use of a name across `arrays`, each some items and their path."""
    seen: set[str] = set()
    for items, where in arrays:
        for index, name in enumerate(item.name for item in items):
            if name in seen:
                raise TargetsError(f"{where}[{index}].name: {name!r} is already used")
            seen.add(name)


def _states(value: object, where: str) -> tuple[State, ...]:
    """A target's states: at least one, each name used once."""
    states = checks.array_of(_state)(value, where)
    if not states:
        raise TargetsError(
            f"{where}: needs at least one state, or leave it out for the default"
        )
    _refuse_duplicate_names((states, where))
    return states


def _target(value: object, where: str, file_reads: tuple[Read, ...]) -> Target:
    """One `[[target]]`, its reads led by the file's own."""
    table = checks.table(value, where)
    checks.require_keys(table, where, required=("name", "page"), allowed=_TARGET_KEYS)
    name = _name(table["name"], checks.at(where, "name"))
    page = _page(table["page"], checks.at(where, "page"))
    own: tuple[Read, ...] = checks.optional(
        table, where, "read", checks.array_of(_read), ()
    )
    # A read-back is reported by its read's name, so a name used twice across the file's
    # reads and the target's own would lose a value: refused at the LATER read's path.
    _refuse_duplicate_names((file_reads, _FILE_READS), (own, checks.at(where, "read")))
    return Target(
        name=name,
        page=page,
        storage_reads=checks.optional(
            table, where, "storage_reads", checks.array_of(checks.text), ()
        ),
        reads=file_reads + own,
        states=checks.optional(table, where, "state", _states, (_DEFAULT_STATE,)),
        follows_redirect=checks.optional(
            table, where, "follows_redirect", checks.boolean, False
        ),
    )


def _targets(value: object, file_reads: tuple[Read, ...]) -> tuple[Target, ...]:
    """The file's `[[target]]` array: at least one target, each name used once."""
    where = "target"
    targets = checks.array_of(lambda item, at: _target(item, at, file_reads))(
        value, where
    )
    if not targets:
        raise TargetsError(f"{where}: at least one [[target]] is required")
    _refuse_duplicate_names((targets, where))
    return targets
