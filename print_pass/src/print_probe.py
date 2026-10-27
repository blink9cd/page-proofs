"""The driver's half of the in-page probe: its source, its argument, and the parse of its result.

`print_probe.js` is one bare arrow function the engine evaluates in a page, as
`page.evaluate(PROBE_SOURCE, argument)`. It reads the media emulation in force, how far loading
got, every image and whether it has a src attribute, the animations still running, the
targets' `[[read]]` values and the storage keys asked for, and changes nothing. This module
builds its argument from the targets' reads and turns what it returns into a typed
`ProbeResult`, refusing any shape the probe never returns -- so a probe and a parser that
drift apart fail here, loudly, instead of as a report that silently says less. The argument
carries the bounds the probe cuts a page's strings and lists to, `MAX_PROBE_TEXT` and
`MAX_PROBE_ITEMS`, and the parse refuses past them, so one constant here bounds both halves.

Entry point assumptions: the standard library only. The page is anything with Playwright's
`evaluate(expression, arg)` shape; bounding that call is the caller's job, since `evaluate`
takes no timeout of its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

#: The in-page probe, handed to the engine as source text, read once at import.
PROBE_SOURCE: Final[str] = (
    Path(__file__).with_name("print_probe.js").read_text(encoding="utf-8")
)

#: What `prefers-color-scheme` can read: the probe checks dark, then light.
COLOR_SCHEMES: Final = frozenset({"dark", "light", "no-preference"})

#: What `prefers-reduced-motion` can read.
REDUCED_MOTION: Final = frozenset({"reduce", "no-preference"})

#: A font set's own two statuses, and the probe's word for a document with none.
FONT_STATUSES: Final = frozenset({"loaded", "loading", "unavailable"})

#: What `document.readyState` can read.
READY_STATES: Final = frozenset({"loading", "interactive", "complete"})

#: An image's `loading`: empty with no attribute, else the IDL property, which reads only
#: these two whatever the attribute says.
IMAGE_LOADINGS: Final = frozenset({"", "lazy", "eager"})

#: The most characters of any string the probe returns, and of items in any list: the probe
#: cuts what a page supplies to them, so a page's megabytes are never polled or reported.
MAX_PROBE_TEXT: Final = 1_000
MAX_PROBE_ITEMS: Final = 5_000

#: How a refusal names the value at the top of the result.
_RESULT: Final = "probe result"

#: The keys each object the probe returns carries, exactly.
_RESULT_KEYS: Final = (
    "colorScheme",
    "reducedMotion",
    "readyState",
    "fontsStatus",
    "title",
    "images",
    "animations",
    "reads",
    "storage",
)
_IMAGE_KEYS: Final = ("src", "hasSrc", "complete", "loading", "naturalWidth")
_ANIMATION_KEYS: Final = ("name", "target")
_READ_KEYS: Final = ("name", "values")

#: A storage entry is a [key, value] pair.
_STORAGE_PAIR_LENGTH: Final = 2

#: The types a read's value may take: what the probe keeps of a DOM value as it is.
_READ_SCALARS: Final = (str, int, float, bool, type(None))

type ReadScalar = str | int | float | bool | None


class ProbeError(ValueError):
    """The probe returned a shape it never returns: the probe and this parser disagree."""


@dataclass(frozen=True)
class ImageState:
    """One image as the page held it: its source, loaded or not, and its lazy-loading ask.

    `src` is a path for a same-origin image, `data:<media type>` for a data URL, and the
    whole URL otherwise. `loading` is empty when the attribute is unset, else the browser's
    reading of it, `lazy` or `eager`; a `natural_width` of 0 on a complete image is broken.
    `has_src` is whether the element carries a src attribute at all, `src=""` included: one
    without requested nothing, so it is never requested rather than broken or loading. It
    has no default, so a record of the shape before it fails to build rather than reading
    every image as one that asked for its source.
    """

    src: str
    complete: bool
    loading: str
    natural_width: int
    has_src: bool


@dataclass(frozen=True)
class RunningAnimation:
    """An animation still running when the page was probed, and the element it moves."""

    name: str
    target: str


@dataclass(frozen=True)
class ReadValue:
    """One `[[read]]`'s values: one per match read, empty when nothing matched."""

    name: str
    values: tuple[ReadScalar, ...]


@dataclass(frozen=True)
class ProbeResult:
    """Everything one probe of a page read, typed."""

    color_scheme: str
    reduced_motion: str
    ready_state: str
    fonts_status: str
    title: str
    images: tuple[ImageState, ...]
    animations: tuple[RunningAnimation, ...]
    reads: tuple[ReadValue, ...]
    storage: tuple[tuple[str, str | None], ...]

    @property
    def settled(self) -> bool:
        """Whether what the page prints had arrived: every image complete, fonts loaded.

        An image with no src attribute requested nothing, so nothing is on its way and it
        never holds the settle, whatever its `complete` reads.
        """
        return self.fonts_status == "loaded" and all(
            image.complete or not image.has_src for image in self.images
        )


class ReadSpec(Protocol):
    """What the probe needs of one `[[read]]`; the targets file's read records have it.

    Read-only members, so a frozen dataclass satisfies it as well as a plain object.
    """

    @property
    def name(self) -> str:
        """The label the read's values are reported under."""

    @property
    def selector(self) -> str:
        """The CSS selector whose matches are read."""

    @property
    def kind(self) -> str:
        """What is read of a match: `attribute`, `property` or `text`."""

    @property
    def key(self) -> str | None:
        """The attribute or property name; None for a text read."""

    @property
    def all(self) -> bool:
        """Whether every match is read, rather than the first."""


class Evaluator(Protocol):
    """A page, as far as the probe is concerned: Playwright's `Page.evaluate`."""

    def evaluate(self, expression: str, arg: object = ...) -> object:
        """Evaluate `expression` in the page; call it with `arg` when it is a function."""


def probe_argument(
    reads: Sequence[ReadSpec], storage_keys: Sequence[str]
) -> dict[str, object]:
    """The probe's argument: each read's five fields, the storage keys, and the bounds."""
    return {
        "reads": [
            {
                "name": read.name,
                "selector": read.selector,
                "kind": read.kind,
                "key": read.key,
                "all": read.all,
            }
            for read in reads
        ],
        "storageKeys": list(storage_keys),
        "maxText": MAX_PROBE_TEXT,
        "maxItems": MAX_PROBE_ITEMS,
    }


def _record(raw: object, keys: tuple[str, ...], where: str) -> Mapping[str, object]:
    """`raw` as an object holding exactly `keys`; a `ProbeError` naming `where` otherwise."""
    if not isinstance(raw, dict):
        raise ProbeError(f"{where} is not an object")
    missing = [key for key in keys if key not in raw]
    unexpected = sorted(str(key) for key in raw if key not in keys)
    if missing or unexpected:
        raise ProbeError(f"{where}: missing {missing}, unexpected {unexpected}")
    return raw


def _string(record: Mapping[str, object], key: str, where: str) -> str:
    """The string at `key`, refused when it is anything else."""
    value = record[key]
    if not isinstance(value, str):
        raise ProbeError(f"{where}.{key} is not a string")
    return _bounded_text(value, f"{where}.{key}")


def _bounded_text(value: str, where: str) -> str:
    """`value`, refused when it is longer than the probe cuts any string to."""
    if len(value) > MAX_PROBE_TEXT:
        raise ProbeError(f"{where} is longer than {MAX_PROBE_TEXT:,} characters")
    return value


def _choice(
    record: Mapping[str, object], key: str, where: str, allowed: frozenset[str]
) -> str:
    """The string at `key`, refused unless it is one of `allowed`."""
    value = _string(record, key, where)
    if value not in allowed:
        raise ProbeError(f"{where}.{key} is {value!r}, not one of {sorted(allowed)}")
    return value


def _boolean(record: Mapping[str, object], key: str, where: str) -> bool:
    """The boolean at `key`, refused when it is anything else."""
    value = record[key]
    if not isinstance(value, bool):
        raise ProbeError(f"{where}.{key} is not a boolean")
    return value


def _count(record: Mapping[str, object], key: str, where: str) -> int:
    """The non-negative integer at `key`; a boolean is refused, though Python calls it one."""
    value = record[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProbeError(f"{where}.{key} is not a non-negative integer")
    return value


def _items(value: object, where: str) -> list[object]:
    """`value` as a list, refused when it is anything else."""
    if not isinstance(value, list):
        raise ProbeError(f"{where} is not a list")
    if len(value) > MAX_PROBE_ITEMS:
        raise ProbeError(f"{where} holds more than {MAX_PROBE_ITEMS:,} items")
    return value


def _each[T](
    record: Mapping[str, object], key: str, parse: Callable[[object, str], T]
) -> tuple[T, ...]:
    """Every entry of the list at `key`, each parsed by `parse` under its own index."""
    entries = _items(record[key], key)
    return tuple(parse(entry, f"{key}[{index}]") for index, entry in enumerate(entries))


def _image(raw: object, where: str) -> ImageState:
    """One entry of `images`."""
    record = _record(raw, _IMAGE_KEYS, where)
    return ImageState(
        src=_string(record, "src", where),
        complete=_boolean(record, "complete", where),
        loading=_choice(record, "loading", where, IMAGE_LOADINGS),
        natural_width=_count(record, "naturalWidth", where),
        has_src=_boolean(record, "hasSrc", where),
    )


def _animation(raw: object, where: str) -> RunningAnimation:
    """One entry of `animations`."""
    record = _record(raw, _ANIMATION_KEYS, where)
    return RunningAnimation(
        name=_string(record, "name", where), target=_string(record, "target", where)
    )


def _scalar(raw: object, where: str) -> ReadScalar:
    """One value of a read: a string, number, boolean or null, never a nested value."""
    if not isinstance(raw, _READ_SCALARS):
        raise ProbeError(f"{where} is not a string, number, boolean or null")
    return _bounded_text(raw, where) if isinstance(raw, str) else raw


def _read(raw: object, where: str) -> ReadValue:
    """One entry of `reads`."""
    record = _record(raw, _READ_KEYS, where)
    values = _items(record["values"], f"{where}.values")
    return ReadValue(
        name=_string(record, "name", where),
        values=tuple(
            _scalar(value, f"{where}.values[{index}]")
            for index, value in enumerate(values)
        ),
    )


def _stored(raw: object, where: str) -> tuple[str, str | None]:
    """One entry of `storage`: a [key, value] pair, the value null when nothing is stored."""
    pair = _items(raw, where)
    if len(pair) != _STORAGE_PAIR_LENGTH:
        raise ProbeError(f"{where} is not a [key, value] pair")
    key, value = pair
    if not isinstance(key, str) or not (value is None or isinstance(value, str)):
        raise ProbeError(f"{where} is not a string key with a string or null value")
    return key, None if value is None else _bounded_text(value, f"{where}[1]")


# fuzz-target
def parse_probe_result(raw: object) -> ProbeResult:
    """The probe's return value as a `ProbeResult`; `ProbeError` for any shape it never returns."""
    record = _record(raw, _RESULT_KEYS, _RESULT)
    return ProbeResult(
        color_scheme=_choice(record, "colorScheme", _RESULT, COLOR_SCHEMES),
        reduced_motion=_choice(record, "reducedMotion", _RESULT, REDUCED_MOTION),
        ready_state=_choice(record, "readyState", _RESULT, READY_STATES),
        fonts_status=_choice(record, "fontsStatus", _RESULT, FONT_STATUSES),
        title=_string(record, "title", _RESULT),
        images=_each(record, "images", _image),
        animations=_each(record, "animations", _animation),
        reads=_each(record, "reads", _read),
        storage=_each(record, "storage", _stored),
    )


def run_probe(
    page: Evaluator, reads: Sequence[ReadSpec], storage_keys: Sequence[str]
) -> ProbeResult:
    """Probe `page` once, reading `reads` and `storage_keys`, and parse what it returns."""
    return parse_probe_result(
        page.evaluate(PROBE_SOURCE, probe_argument(reads, storage_keys))
    )
