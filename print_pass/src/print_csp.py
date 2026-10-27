"""A state's Content Security Policy violations, heard through the shared collector `csp_watch`.

`CspWatch` installs `csp_watch`'s collector on a state's context before the context's first
page opens, so the first load is watched, and `lines` gives back each violation its documents
reported, one line each in arrival order: `style-src-attr blocked inline on /404.html`. A
refused URL on the served origin is named by its path and query, as every other network list
names one, so the served port is not recorded. The engine records the lines with the state's
network, and the judge makes each a finding.

The collector is an init script, which an engine runs only with a page's scripts on. A make
run never turns them off, so every state is measured: one that reports nothing is clean.

Every Playwright call the engine makes runs in a watchdog bound of its own, so the collector is
installed through `_BoundedContext`: the state's context, with each call made on it run inside
a bound named for that call.

The collector's binding is a global any script in the page can call, so a payload its
listener never sends is the page's own doing, not the instrument's. Each is screened out
before the collector sees it, with `csp_watch`'s own `violation_from`, and never handed back
to the page: the state goes on, and `lines` ends with one line counting them and giving the
first one's reason, cut to `MAX_REFUSAL_CHARS`, which the judge makes a finding like any
other. So the collector itself is never sent a payload it refuses, and never faults.

Entry point assumptions: `csp_watch` is the folder beside this tool's own, put on the path
and imported as this module loads; it imports no Playwright.
"""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Final

from print_playwright import ContextLike, request_label

# The shared collector: a folder beside this tool's own, named rather than searched for, so
# a same-named folder above the clone is never imported in its place, and put near the front
# of the path, ahead of every installed distribution, so none of the same name can shadow
# it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "csp_watch"))

import csp_watch

#: The watchdog's bound on one call of a state, named for the call: the state's `bound`.
type Bound = Callable[[str], AbstractContextManager[None]]

#: What a payload the listener never sends is recorded as, ahead of its count and reason.
REFUSED: Final = "a page sent the collector what its listener never sends"

#: The most characters of the first refusal's reason kept: it can quote a key the page chose.
MAX_REFUSAL_CHARS: Final = 200

#: What ends a reason cut to `MAX_REFUSAL_CHARS`.
_CUT: Final = "..."


class CspWatch:
    """The collector on one state's context, and every violation it has heard so far."""

    def __init__(self, context: ContextLike, bound: Bound, *, served: str) -> None:
        """Install the collector on `context`, before its first page.

        Each call the install makes on the context runs inside `bound`, named for itself.
        `served` is the served origin's base URL, which a refused URL is labeled against.
        """
        self._served = served
        self._refusals = _Refusals()
        bounded = _BoundedContext(context, bound, self._refusals)
        self._log = csp_watch.install(bounded)

    def lines(self) -> tuple[str, ...]:
        """Each violation heard so far, as one line, in the order they arrived.

        A page that sent the collector anything its listener never sends adds one line more,
        last, counting those payloads and giving the first one's reason.
        """
        heard = tuple(
            _line(violation, self._served) for violation in self._log.violations
        )
        return heard + self._refusals.lines()


def _line(violation: csp_watch.Violation, served: str) -> str:
    """The collector's own line for `violation`, a refused URL named as the report names one."""
    label = request_label(violation.blocked, served=served)
    return dataclasses.replace(violation, blocked=label).describe()


class _Refusals:
    """The payloads a page sent that the listener never sends: how many, and the first's reason."""

    def __init__(self) -> None:
        """None yet."""
        self._count = 0
        self._first: str | None = None

    def screen(
        self, callback: Callable[[object, object], None]
    ) -> Callable[[object, object], None]:
        """`callback`, handed only the payloads `violation_from` reads; the rest are counted."""

        def screened(source: object, payload: object) -> None:
            try:
                csp_watch.violation_from(payload)
            except csp_watch.CspWatchError as refusal:
                self._count += 1
                self._first = self._first or _cut(str(refusal))
                return
            callback(source, payload)

        return screened

    def lines(self) -> tuple[str, ...]:
        """One line for the refusals, or none when there were none."""
        if self._first is None:
            return ()
        return (f"{REFUSED} ({self._count}), the first: {self._first}",)


def _cut(reason: str) -> str:
    """`reason`, cut to `MAX_REFUSAL_CHARS` and ending in `_CUT` when it is longer."""
    if len(reason) <= MAX_REFUSAL_CHARS:
        return reason
    return reason[: MAX_REFUSAL_CHARS - len(_CUT)] + _CUT


class _BoundedContext:
    """A state's context, handed to the collector: each call on it inside a bound of its own."""

    def __init__(self, context: ContextLike, bound: Bound, refusals: _Refusals) -> None:
        """Hand each call on to `context`, inside `bound`; screen the binding by `refusals`."""
        self._context = context
        self._bound = bound
        self._refusals = refusals

    def expose_binding(
        self, name: str, callback: Callable[[object, object], None]
    ) -> None:
        """Expose `callback`, screened, to every page of the context as the global `name`."""
        with self._bound("context.expose_binding"):
            self._context.expose_binding(name, self._refusals.screen(callback))

    def add_init_script(self, script: str) -> None:
        """Run `script` in every document of the context, before the document's own."""
        with self._bound("context.add_init_script"):
            self._context.add_init_script(script)
