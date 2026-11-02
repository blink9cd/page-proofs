"""The checks a targets file's values pass, each refusing with the path to the value.

`print_targets` knows every key a targets file may hold; what a value must be (a table, an
array, a non-empty string, one of a set, a whole number, a boolean) is checked here, with no
key of the file known. Each refusal is a `TargetsError` whose message opens with the PATH to
the value, `target[1].state[2].color_scheme`, arrays counted from zero in file order, because
that path is the whole of what an operator is shown.

Entry point assumptions: the standard library only. Each check takes a value as `tomllib`
made it, or as a Python caller handed it in, and the path its caller built to it.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from urllib.parse import unquote


class TargetsError(ValueError):
    """A targets file, or a command-line value standing in for one, this tool cannot use."""


def at(where: str, key: str) -> str:
    """The path to `key` inside the table at `where`; the top level has an empty path."""
    return f"{where}.{key}" if where else key


def require_keys(
    table: Mapping[str, object],
    where: str,
    *,
    required: Sequence[str],
    allowed: Collection[str],
) -> None:
    """Refuse the first key outside `allowed`, then the first of `required` that is absent."""
    for key in table:
        if key not in allowed:
            raise TargetsError(f"{at(where, key)}: unknown key")
    for key in required:
        if key not in table:
            raise TargetsError(f"{at(where, key)}: required")


def optional[T](
    table: Mapping[str, object],
    where: str,
    key: str,
    parse: Callable[[object, str], T],
    absent: T,
) -> T:
    """`parse` the value at `key` in the table at `where`, or `absent` when it is left out.

    Presence is `key in table`, never a None test: TOML has no null, so a None comes only
    from a Python caller, and it is parsed, and refused, like any other wrong type.
    """
    return parse(table[key], at(where, key)) if key in table else absent


def table(value: object, where: str) -> Mapping[str, object]:
    """`value` as a table, or a refusal."""
    if not isinstance(value, Mapping):
        raise TargetsError(f"{where}: must be a table")
    return value


def array_of[T](
    parse: Callable[[object, str], T],
) -> Callable[[object, str], tuple[T, ...]]:
    """A parser of an array of what `parse` parses, each item at its own `where[index]`."""

    def parse_array(value: object, where: str) -> tuple[T, ...]:
        if not isinstance(value, list):
            raise TargetsError(f"{where}: must be an array")
        items = enumerate(value)
        return tuple(parse(item, f"{where}[{index}]") for index, item in items)

    return parse_array


def text(value: object, where: str) -> str:
    """A non-empty string."""
    if not isinstance(value, str) or not value:
        raise TargetsError(f"{where}: must be a non-empty string")
    return value


def one_of(allowed: Sequence[str]) -> Callable[[object, str], str]:
    """A parser of one of `allowed`, for `optional`."""

    def parse(value: object, where: str) -> str:
        if not isinstance(value, str) or value not in allowed:
            raise TargetsError(f"{where}: must be one of {', '.join(allowed)}")
        return value

    return parse


def int_at_least(value: object, where: str, minimum: int) -> int:
    """A whole number no smaller than `minimum`.

    `type(...) is int`, not `isinstance`: TOML's `true` arrives as a `bool`, which Python
    counts as an int.
    """
    if type(value) is not int or value < minimum:
        raise TargetsError(f"{where}: must be a whole number of at least {minimum}")
    return value


def int_between(value: object, where: str, minimum: int, maximum: int) -> int:
    """A whole number from `minimum` to `maximum`, both included; a boolean is refused."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise TargetsError(
            f"{where}: must be a whole number from {minimum} to {maximum}"
        )
    return value


def boolean(value: object, where: str) -> bool:
    """`true` or `false`."""
    if not isinstance(value, bool):
        raise TargetsError(f"{where}: must be true or false")
    return value


def exactly_one[K: str](
    table: Mapping[str, object], where: str, kinds: Sequence[K]
) -> K:
    """The one key of `kinds` the table at `where` holds; none, or several, is refused."""
    held = [kind for kind in kinds if kind in table]
    if len(held) != 1:
        raise TargetsError(f"{where}: must hold exactly one of {', '.join(kinds)}")
    (kind,) = held
    return kind


def has_dot_segment(page: str) -> bool:
    """Whether `page`'s server path, before any `?query` or `#fragment`, has a `.` or `..` segment.

    A dot inside a segment (`/a.html`, `/.well-known/`) is no segment of its own. A backslash
    separates segments too, as it does wherever a server reads a path as a Windows one. The
    path is percent-decoded first: a browser resolves `/a/%2e%2e/b` to `/b`, so the PDF would
    be of another page than the one named.
    """
    path = unquote(page.split("#", 1)[0].split("?", 1)[0])
    return not {".", ".."}.isdisjoint(path.replace("\\", "/").split("/"))
