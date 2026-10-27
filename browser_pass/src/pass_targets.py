"""The targets file, and the matrix of renderings it expands to.

A targets file is TOML: the server paths to measure, the viewports to measure them at, an
optional base url for a server something else is running, and optional STATES -- a page reached
by more than its load, through values stored before it and actions run after it. Everything
site-specific lives in that file, so the tool itself carries no knowledge of any one site.

A viewport marked `rotates` is a phone: it runs PORTRAIT (the taller shape) and LANDSCAPE (width
and height swapped), whichever way round the file writes it. A viewport without it runs once, as
written -- a desktop window does not turn on its side.

A state's keys are `name`, `page`, `storage` and `actions`, each action one of `click`,
`wait_for` and `wait_ms`. A name's alphabet (`[a-z0-9][a-z0-9-]*`) and the `wait_ms` ceiling
(10000) are deliberately narrow.

An optional `not_found` names the file `--serve` answers a page with no file with, status 404,
the way GitHub Pages answers with a site's own 404 page; such a page is then one to measure
(`missing_pages`) rather than one the server does not hold. An optional `redirects` names each
page that is a meta refresh, measured at the page it points at (`pass_redirects`).

Entry point assumptions: `load_targets` reads a file the caller names; nothing here touches a
browser or the network.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: The engines a run can drive, in the order a full run drives them. The first one named on a
#: run is the reference the others are compared against.
ENGINES = ("chromium", "firefox", "webkit")

#: The shapes of a rotating viewport.
ORIENTATIONS = ("portrait", "landscape")

#: The color schemes a page is asked to render in.
MODES = ("light", "dark")

#: Scripting on, and scripting off in the engine itself -- never a CSP, which leaves
#: `(scripting: enabled)` matching and so renders a page no scripts-off browser ever shows.
SCRIPTS = ("on", "off")

#: The scripting a state renders with: its actions click and wait on what a page's scripts
#: build, so a state renders with scripts on only.
STATE_SCRIPTS = "on"

#: What a state's action may do, one per action: click a selector, wait until a selector is
#: visible, or wait a set number of milliseconds.
ACTION_KINDS = ("click", "wait_for", "wait_ms")
_WAIT_ACTION = "wait_ms"

#: The shortest and longest `wait_ms`. A wait of none waits for nothing, and a page that needs
#: longer than ten seconds is one to wait on by a selector, with `wait_for`.
MIN_WAIT_MS = 1
MAX_WAIT_MS = 10_000

#: The keys a targets document, each of its viewports and each of its states may hold. Anything
#: else is refused, so a misspelled key fails loudly instead of silently falling back to a default.
_DOCUMENT_KEYS = frozenset(
    {"pages", "viewport", "base_url", "state", "not_found", "redirects"}
)
_VIEWPORT_KEYS = frozenset({"width", "height", "rotates"})
_STATE_KEYS = frozenset({"name", "page", "storage", "actions"})

#: A state's name: lowercase letters, digits and hyphens, opening with a letter or a digit.
#: Matched WHOLE, never with a `$` anchor, which would let a trailing newline through.
_STATE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


class TargetsError(ValueError):
    """The targets file is not one this tool can measure from."""


@dataclass(frozen=True)
class Viewport:
    """One viewport size; `rotates` marks a phone, measured in both orientations."""

    width: int
    height: int
    rotates: bool


@dataclass(frozen=True)
class Action:
    """One step a state takes once its page has loaded: click or wait for a selector, or wait."""

    kind: str  # one of ACTION_KINDS
    selector: str | None  # click / wait_for
    ms: int | None  # wait_ms


@dataclass(frozen=True)
class State:
    """A page reached by more than its load: values stored before it, actions run after it."""

    name: str
    page: str
    storage: tuple[tuple[str, str], ...]  # localStorage keys and values, in file order
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class Targets:
    """What to measure: the pages, the viewports, where a running server answers, the states.

    `not_found` is the server path of the file `--serve` answers a page with no file with, and
    `redirects` the pages, each one `pages` lists, that are a meta refresh to follow.
    """

    pages: tuple[str, ...]
    viewports: tuple[Viewport, ...]
    base_url: str | None
    states: tuple[State, ...] = ()
    not_found: str | None = None
    redirects: tuple[str, ...] = ()


def page_label(page: str, state: str | None) -> str:
    """`page`, and the state it was reached in when it was one: `/ [after-start]`."""
    return page if state is None else f"{page} [{state}]"


@dataclass(frozen=True)
class Rendering:
    """One page, rendered once: one engine, one viewport size, one mode, scripts on or off.

    `state` names the state the page was reached in, or is None for the page as it loads.
    """

    engine: str
    page: str
    width: int
    height: int
    mode: str
    scripts: str
    state: str | None = None

    @property
    def orientation(self) -> str:
        """`landscape` when wider than tall, else `portrait` -- what the page's media query sees."""
        return "landscape" if self.width > self.height else "portrait"

    @property
    def key(self) -> tuple[str, str | None, int, int, str, str]:
        """Everything but the engine: renderings sharing a key are compared across engines."""
        return (
            self.page,
            self.state,
            self.width,
            self.height,
            self.mode,
            self.scripts,
        )

    @property
    def page_label(self) -> str:
        """The page, and the state it was reached in when it was one."""
        return page_label(self.page, self.state)

    @property
    def label(self) -> str:
        """A one-line name for reports and progress."""
        return (
            f"{self.engine} {self.page_label} {self.width}x{self.height} "
            f"{self.mode} scripts-{self.scripts}"
        )


#: How many renderings a report line names before it counts the rest.
NAMED_RENDERINGS = 5


def name_renderings(labels: Sequence[str]) -> str:
    """How many renderings `labels` holds, then a handful by name: `2 renderings: a; b`."""
    count = len(labels)
    named = "; ".join(labels[:NAMED_RENDERINGS])
    rest = f"; and {count - NAMED_RENDERINGS} more" if count > NAMED_RENDERINGS else ""
    return f"{count} rendering{'s' if count != 1 else ''}: {named}{rest}"


def load_targets(path: Path) -> Targets:
    """Read and validate the targets file at `path`.

    Raises:
        TargetsError: the file is not valid TOML (including bytes that are not UTF-8, which
            `tomllib` reports as a `UnicodeDecodeError`), or its content is refused.
        OSError: the file cannot be read.
    """
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise TargetsError(f"{path} is not valid TOML: {error}") from error
    return parse_targets(document)


# fuzz-target
def parse_targets(document: Mapping[str, object]) -> Targets:
    """Validate a parsed targets document; every refusal is a `TargetsError` naming the key."""
    # Deferred: pass_redirects builds on this module's names, so it cannot be imported above.
    from pass_redirects import parse_redirects  # noqa: PLC0415 - see the comment above

    _refuse_unknown_keys(document, _DOCUMENT_KEYS, "the targets file")
    pages = _parse_pages(document.get("pages"))
    not_found = _parse_not_found(document.get("not_found"))
    return Targets(
        pages=pages,
        viewports=_parse_viewports(document.get("viewport")),
        base_url=_parse_base_url(document.get("base_url")),
        states=_parse_states(document.get("state")),
        not_found=not_found,
        redirects=parse_redirects(
            document.get("redirects"), pages=pages, not_found=not_found
        ),
    )


def _refuse_unknown_keys(
    table: Mapping[str, object], known: frozenset[str], where: str
) -> None:
    """Refuse any key outside `known`, naming it, so a typo cannot pass as a default."""
    unknown = sorted(set(table) - known)
    if unknown:
        raise TargetsError(f"{where} has unknown key(s): {', '.join(unknown)}")


def server_path(page: str) -> str:
    """The file path a server answers `page` from: without its `?query` or its `#fragment`.

    A fragment never leaves the browser. A query does, but a static server answers it from the
    file its path names, so that file is the one to confirm.
    """
    return page.split("#", 1)[0].split("?", 1)[0]


def served_pages(targets: Targets) -> tuple[str, ...]:
    """The server path of every page and every state's page, each once, in file order.

    These are the paths `--serve` confirms before anything renders.
    """
    pages = (*targets.pages, *(state.page for state in targets.states))
    return tuple(dict.fromkeys(map(server_path, pages)))


def missing_pages(targets: Targets, has_file: Callable[[str], bool]) -> frozenset[str]:
    """The served pages `has_file` finds no file for, when the targets file has a `not_found`.

    Each is then EXPECTED to answer 404 with that file. Without a `not_found` there are none,
    and `has_file` is not asked: a page with no file is refused when it is confirmed.
    """
    if targets.not_found is None:
        return frozenset()
    return frozenset(page for page in served_pages(targets) if not has_file(page))


def _has_dot_segment(page: str) -> bool:
    """Whether `page`'s server path holds a `.` or `..` segment.

    No page needs one, and a `..` can name a file outside the served folder, which is checked
    for and read before anything renders: so every server path the targets file names is
    refused with one. A dot inside a segment (`/a.html`, `/.well-known/`) is no segment of its
    own, and a query or fragment is not part of the server path. A backslash separates segments
    too, as it does wherever a server reads a path as a Windows one.
    """
    return not {".", ".."}.isdisjoint(server_path(page).replace("\\", "/").split("/"))


#: How every refusal of a dot segment ends.
_DOT_SEGMENT: Final = (
    "it has a . or .. segment, which could name a file outside the served folder"
)


def _parse_pages(value: object) -> tuple[str, ...]:
    """A non-empty list of server paths, duplicates dropped with order kept.

    A page may carry a `#fragment`: an anchored arrival lands scrolled to its target, which is a
    different reading of the same page (and the one a late-moving layout hurts most). It may
    carry a `?query` too, which the page's own scripts read: `/?panel=1` opens a panel.
    """
    if not isinstance(value, list) or not value:
        raise TargetsError("pages must be a non-empty list of server paths")
    for page in value:
        if not isinstance(page, str):
            raise TargetsError(f"pages holds {page!r}, which is not a server path")
        if not page.startswith("/"):
            raise TargetsError(f"pages holds {page!r}: start it with /")
        if _has_dot_segment(page):
            raise TargetsError(f"pages holds {page!r}: {_DOT_SEGMENT}")
    return tuple(dict.fromkeys(value))


def _parse_viewports(value: object) -> tuple[Viewport, ...]:
    """A non-empty array of `[[viewport]]` tables."""
    if not isinstance(value, list) or not value:
        raise TargetsError("viewport must be one or more [[viewport]] tables")
    return tuple(
        _parse_viewport(number, table) for number, table in enumerate(value, 1)
    )


def _parse_viewport(number: int, table: object) -> Viewport:
    """One `[[viewport]]` table: whole-pixel width and height, and an optional `rotates`."""
    where = f"viewport {number}"
    if not isinstance(table, dict):
        raise TargetsError(f"{where} is not a table")
    _refuse_unknown_keys(table, _VIEWPORT_KEYS, where)
    width = _pixels(table.get("width"), f"{where} width")
    height = _pixels(table.get("height"), f"{where} height")
    rotates = table.get("rotates", False)
    if not isinstance(rotates, bool):
        raise TargetsError(f"{where} rotates must be true or false")
    if rotates and width == height:
        raise TargetsError(
            f"{where} is square, so it has no second orientation to rotate to"
        )
    return Viewport(width, height, rotates)


def _pixels(value: object, where: str) -> int:
    """A positive whole number of CSS pixels. A bool is refused although Python counts it an int."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TargetsError(f"{where} must be a positive whole number of pixels")
    return value


def _parse_base_url(value: object) -> str | None:
    """An optional http(s) url, kept without a trailing slash so a page path joins it cleanly."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        raise TargetsError("base_url must be an http:// or https:// url")
    return value.rstrip("/")


def _parse_not_found(value: object) -> str | None:
    """An optional server path, such as `/404.html`, with exactly ONE leading slash.

    That is the shape the key is specified with: a path from the served tree's root, which only
    the server ever reads. A second slash would name the same file, since the server strips every
    leading one, so it is refused as a misspelling rather than kept as an alias. Whether a file
    answers to it is the server's to check, before it starts.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("/") or value[1:2] == "/":
        raise TargetsError(
            "not_found must be a server path with one leading /, such as /404.html"
        )
    if _has_dot_segment(value):
        raise TargetsError(f"not_found {value!r}: {_DOT_SEGMENT}")
    return value


def _parse_states(value: object) -> tuple[State, ...]:
    """The optional `[[state]]` tables, in file order, each name used once."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TargetsError("state must be [[state]] tables")
    states = tuple(_parse_state(number, table) for number, table in enumerate(value, 1))
    seen: set[str] = set()
    for number, state in enumerate(states, 1):
        if state.name in seen:
            raise TargetsError(f"state {number} name {state.name!r} is already used")
        seen.add(state.name)
    return states


def _parse_state(number: int, table: object) -> State:
    """One `[[state]]` table: a name and a page, with optional stored values and actions."""
    where = f"state {number}"
    if not isinstance(table, dict):
        raise TargetsError(f"{where} is not a table")
    _refuse_unknown_keys(table, _STATE_KEYS, where)
    name = table.get("name")
    if not isinstance(name, str) or _STATE_NAME.fullmatch(name) is None:
        raise TargetsError(
            f"{where} name must be lowercase letters, digits and hyphens, "
            "opening with a letter or a digit"
        )
    page = table.get("page")
    if not isinstance(page, str) or not page.startswith("/"):
        raise TargetsError(f"{where} page must be a server path: start it with /")
    if _has_dot_segment(page):
        raise TargetsError(f"{where} page {page!r}: {_DOT_SEGMENT}")
    return State(
        name=name,
        page=page,
        storage=_parse_storage(table.get("storage", {}), where),
        actions=_parse_actions(table.get("actions", []), where),
    )


def _parse_storage(value: object, where: str) -> tuple[tuple[str, str], ...]:
    """A state's `storage` table of `localStorage` keys to values, kept in file order.

    A key is checked as well as a value: TOML makes only string keys, but a caller handing a
    mapping straight to `parse_targets` can make any.
    """
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(stored, str)
        for key, stored in value.items()
    ):
        raise TargetsError(f"{where} storage must map strings to strings")
    return tuple(value.items())


def _parse_actions(value: object, where: str) -> tuple[Action, ...]:
    """A state's `actions` array, run in this order once its page has loaded."""
    if not isinstance(value, list):
        raise TargetsError(f"{where} actions must be an array of action tables")
    return tuple(
        _parse_action(item, f"{where} action {number}")
        for number, item in enumerate(value, 1)
    )


def _parse_action(item: object, where: str) -> Action:
    """One action: a table holding exactly one of `ACTION_KINDS`, and what it acts on."""
    if not isinstance(item, dict) or len(item) != 1 or set(item) - set(ACTION_KINDS):
        raise TargetsError(
            f"{where} must hold exactly one of {', '.join(ACTION_KINDS)}"
        )
    ((kind, argument),) = item.items()
    if kind == _WAIT_ACTION:
        return Action(kind, None, _wait_ms(argument, where))
    if not isinstance(argument, str) or not argument:
        raise TargetsError(f"{where} {kind} must be a non-empty selector")
    return Action(kind, argument, None)


def _wait_ms(value: object, where: str) -> int:
    """A `wait_ms` from `MIN_WAIT_MS` to `MAX_WAIT_MS`. A bool is refused, though an int."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_WAIT_MS <= value <= MAX_WAIT_MS
    ):
        raise TargetsError(
            f"{where} wait_ms must be a whole number of milliseconds "
            f"from {MIN_WAIT_MS} to {MAX_WAIT_MS}"
        )
    return value


def viewport_sizes(
    viewports: Iterable[Viewport], orientations: Sequence[str]
) -> list[tuple[int, int]]:
    """Every (width, height) to render, in file order; each size once however it was reached.

    `orientations` filters rotating viewports only: a fixed viewport always runs as written.
    """
    sizes: dict[tuple[int, int], None] = {}
    for viewport in viewports:
        if not viewport.rotates:
            sizes[(viewport.width, viewport.height)] = None
            continue
        short, long = sorted((viewport.width, viewport.height))
        shapes = {"portrait": (short, long), "landscape": (long, short)}
        for orientation in orientations:
            sizes[shapes[orientation]] = None
    return list(sizes)


def expand_matrix(
    targets: Targets,
    *,
    engines: Sequence[str],
    orientations: Sequence[str],
    modes: Sequence[str],
    scripts: Sequence[str],
) -> list[Rendering]:
    """Every rendering, engine outermost, so one browser launch serves a contiguous run.

    Within an engine the pages come first, then the states, each at every size and mode. A
    state renders with `STATE_SCRIPTS` only, so a run whose `scripts` leaves that out renders
    none of them.
    """
    sizes = viewport_sizes(targets.viewports, orientations)
    state_scripts = [STATE_SCRIPTS] if STATE_SCRIPTS in scripts else []
    readings: list[tuple[str, str | None, Sequence[str]]] = [
        *((page, None, scripts) for page in targets.pages),
        *((state.page, state.name, state_scripts) for state in targets.states),
    ]
    return [
        Rendering(engine, page, width, height, mode, script, state)
        for engine in engines
        for page, state, scripting in readings
        for width, height in sizes
        for mode in modes
        for script in scripting
    ]
