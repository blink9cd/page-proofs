"""A state's actions: what a targets file may ask a page to do after it loads, before it prints.

An action clicks an element, waits for one to be visible, waits a set time, or sets one
attribute on an element. `set_attribute` exists for a page whose own "print this way" button
sets an attribute and then calls `window.print()`: under Firefox, WebDriver's click on such a
button never returns while the real print dialog is open, so the tool takes the button's first
step itself and prints as it always does. It runs once, with no wait: the element must be
there when it runs (a `wait_for` before it waits for one that arrives late), and a selector
matching nothing stops the state with the same finding a failed click does.

Entry point assumptions: `parse_action` is handed one action's table by `print_targets`,
with that module's parser of a time in milliseconds; `set_attribute` is handed a page that
evaluates a function expression on one argument, as Playwright's page does and as
`gecko_page.ProbePage` does over WebDriver. Nothing here starts a browser.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, Protocol

import print_targets_checks as checks

type ActionKind = Literal["click", "wait_for", "wait_ms", "set_attribute"]

#: The script a `set_attribute` runs: true once it set the attribute, false when the
#: selector matched nothing. A function expression on one argument, the selector, the name
#: and the value, so each engine runs it as it runs the probe.
SET_ATTRIBUTE_SOURCE: Final = """([selector, name, value]) => {
  const element = document.querySelector(selector);
  if (element === null) {
    return false;
  }
  element.setAttribute(name, value);
  return true;
}"""

# The kinds that act on a selector alone, the kind that waits a set time, and the one that
# sets an attribute: every kind an action may hold, each one its table's only key.
_SELECTOR_ACTIONS: Final[tuple[ActionKind, ...]] = ("click", "wait_for")
_WAIT_ACTION: Final[ActionKind] = "wait_ms"
_SET_ATTRIBUTE: Final[ActionKind] = "set_attribute"
_ACTION_KINDS: Final = (*_SELECTOR_ACTIONS, _WAIT_ACTION, _SET_ATTRIBUTE)

# A `set_attribute` table's keys: the value is optional and an empty one by default, which
# is what a bare HTML attribute such as `hidden` is.
_SET_ATTRIBUTE_KEYS: Final = ("selector", "name", "value")
_DEFAULT_VALUE: Final = ""

# The attribute names `setAttribute` takes, in their ASCII form: XML's Name production,
# which a browser checks, throwing on any other. Refused when the file is read, rather than
# as a finding once the run has started. Matched WHOLE: a `$` would let a newline through.
_ATTRIBUTE_NAME: Final = re.compile(r"[A-Za-z_:][A-Za-z0-9_:.-]*")

# Unicode's category Cc, and only it: the C0 controls (newline and tab among them), DEL
# and the C1 controls, refused in a selector or a value. Other characters that can break or
# disguise a line, such as U+2028 or a bidirectional override, are not in Cc and pass.
_CONTROL: Final = "Cc"


@dataclass(frozen=True)
class Action:
    """One step run after load: click or wait for a selector, wait, or set an attribute."""

    kind: ActionKind
    selector: str | None  # click / wait_for / set_attribute
    ms: int | None  # wait_ms
    attribute: tuple[str, str] | None = None  # set_attribute: its name, then its value

    def text(self) -> str:
        """`click #start`, `wait_for #ready`, `wait_ms 300` or `set_attribute :root a=""`.

        A `set_attribute`'s value is written as a JSON string, so a quote inside it cannot
        be read as its end. A non-ASCII character is written as itself, not escaped, so the
        value stays readable; a control character never reaches here, refused when read.
        """
        if self.attribute is not None:
            name, value = self.attribute
            quoted = json.dumps(value, ensure_ascii=False)
            return f"{self.kind} {self.selector} {name}={quoted}"
        return f"{self.kind} {self.selector if self.ms is None else self.ms}"

    def failure(self) -> str:
        """The finding's text when this action does not complete, the same in either engine."""
        argument = self.selector if self.selector is not None else self.ms
        return f'action did not complete: {self.kind} "{argument}"'


class ActionError(Exception):
    """An action ran and the page did not do it: a `set_attribute` matched nothing."""


class Evaluates(Protocol):
    """A page that calls a function expression on one argument and returns what it returns."""

    def evaluate(self, expression: str, arg: object = ...) -> object:
        """Evaluate `expression` in the page, calling it with `arg`."""


def parse_action(
    value: object, where: str, *, ms: Callable[[object, str], int]
) -> Action:
    """One action: a table holding exactly one of `click`, `wait_for`, `wait_ms`, `set_attribute`.

    `ms` parses a `wait_ms`'s time, refusing it at its key as `print_targets` refuses every
    time in a targets file.
    """
    table = checks.table(value, where)
    checks.require_keys(table, where, required=(), allowed=_ACTION_KINDS)
    kind = checks.exactly_one(table, where, _ACTION_KINDS)
    argument, at = table[kind], checks.at(where, kind)
    if kind == _WAIT_ACTION:
        return Action(kind, None, ms(argument, at))
    if kind == _SET_ATTRIBUTE:
        return _set_attribute_action(argument, at)
    return Action(kind, _selector(argument, at), None)


def _set_attribute_action(value: object, where: str) -> Action:
    """A `set_attribute` table: its selector, its attribute's name, and its optional value."""
    table = checks.table(value, where)
    required = _SET_ATTRIBUTE_KEYS[:2]
    checks.require_keys(table, where, required=required, allowed=_SET_ATTRIBUTE_KEYS)
    selector = _selector(table["selector"], checks.at(where, "selector"))
    name = _attribute_name(table["name"], checks.at(where, "name"))
    attribute_value = checks.optional(table, where, "value", _string, _DEFAULT_VALUE)
    return Action(_SET_ATTRIBUTE, selector, None, (name, attribute_value))


def _attribute_name(value: object, where: str) -> str:
    """An attribute name a page can set: XML's Name production, in ASCII."""
    if not isinstance(value, str) or _ATTRIBUTE_NAME.fullmatch(value) is None:
        raise checks.TargetsError(f"{where}: must be an attribute name, as data-x is")
    return value


def _selector(value: object, where: str) -> str:
    """A non-empty selector holding no control character."""
    return _without_control(checks.text(value, where), where)


def _string(value: object, where: str) -> str:
    """Any string holding no control character, the empty one included."""
    if not isinstance(value, str):
        raise checks.TargetsError(f"{where}: must be a string")
    return _without_control(value, where)


def _without_control(value: str, where: str) -> str:
    """`value`, refused when it holds a control character, a newline or a tab included."""
    for character in value:
        if unicodedata.category(character) == _CONTROL:
            raise checks.TargetsError(
                f"{where}: must not hold a control character, a newline or a tab"
                f" included (found U+{ord(character):04X})"
            )
    return value


def set_attribute(page: Evaluates, action: Action) -> None:
    """Set `action`'s attribute on the first element its selector matches, once.

    Raises:
        ActionError: the page answered anything but a plain `true`: nothing matched.
        ValueError: `action` is not a `set_attribute`, which no targets file can make so.
    """
    if action.attribute is None or action.selector is None:
        raise ValueError(f"not a set_attribute a targets file can hold: {action!r}")
    name, value = action.attribute
    if page.evaluate(SET_ATTRIBUTE_SOURCE, [action.selector, name, value]) is not True:
        raise ActionError(f"{action.selector} matched nothing")
