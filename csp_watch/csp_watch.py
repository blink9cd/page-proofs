"""Collect a page's Content Security Policy violations through a Playwright binding.

A library with no CLI, shared by the tools here that drive a browser engine. `install` puts a
listener into every document of a browser context before the document's own scripts run, and
the listener hands each `securitypolicyviolation` event straight back to Python through a
binding, where a `CspLog` keeps it. Sending each one out at once is the point: a record kept in
the page is erased by the next navigation, and a meta refresh is one.

The listener is an init script, so it runs only with the page's scripts ON. With them off an
engine runs no init script, and the log stays empty whatever the page violates: a caller must
report that rendering as not measured, never as clean. `csp_watch.md` has what each engine
reports there instead.

Every page of the context reports to the one log, so a caller measuring one page among others
(a tab a browser extension opened beside it, say) hands `install` an `admit` that keeps only
the reports of the pages it measures, by the caller Playwright hands the binding.

`policy_of` reads the policy a page's markup declares, and works with scripts off as well, since
an engine still runs the code the driver sends. `plant` adds a policy of its own and then breaks
it, so a caller can show the collector catches what it is meant to (`plant_caught`); that needs
scripts on, since only the listener reports the plant.

The module never imports Playwright. It takes any context with the two calls `install` makes
(`ContextLike`) and any page with the one call `policy_of` and `plant` make (`PageLike`), which
Playwright's own `BrowserContext` and `Page` are, as they are: so a caller may hand it a wrapper
of its own, and importing it brings no browser driver with it.

Entry point assumptions: the standard library only, and the listener's source in `src/` beside
this file, read as the module loads. Callers reach this module by putting its folder on
`sys.path`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

#: The page global the binding is exposed as, and the name the listener calls.
BINDING: Final = "__cspWatch"

#: The policy `plant` adds: it refuses every style attribute and asks for its text back.
#: `'report-sample'` is what makes an engine put the attribute's text in the event's sample,
#: which is how `plant_caught` tells the plant's violation from any the page makes itself. It
#: sits in `style-src`, the attribute's fallback, rather than in `style-src-attr`: in the pinned
#: builds WebKit fills the sample only from `style-src`'s keyword, while Chromium and Firefox
#: take it from either, and all three still report the attribute as `style-src-attr`. `'self'`
#: keeps the plant from refusing a same-origin stylesheet the page loads after it.
PLANT_POLICY: Final = "style-src 'self' 'report-sample'"

#: The style attribute `plant` sets on the root element: a custom property, so it changes
#: nothing on the page even where no policy blocks it.
PLANT_SAMPLE: Final = "--csp-watch-plant: 1"

#: The listener, a classic script defining the one function the init script then calls.
_LISTENER_SOURCE: Final = (
    (Path(__file__).resolve().parent / "src" / "csp_listener.js")
    .read_text(encoding="utf-8")
    .rstrip()
)

#: Whether a report is kept, given the caller Playwright hands the binding: a mapping naming the
#: `context`, `page` and `frame` that called.
Admit = Callable[[object], bool]

#: The keys of every payload the listener sends, and nothing else.
_PAYLOAD_KEYS: Final = ("document", "directive", "blocked", "sample")

#: The most characters of a sample the listener sends: its own cut (`SAMPLE_LIMIT` in
#: `src/csp_listener.js`), counted by character as it cuts.
SAMPLE_LIMIT: Final = 40

#: The most characters the listener's other three values may hold. Each is a URL, a path and
#: query, or a directive's name, so this is sized for a URL: generous for any a page under test
#: loads, and far below the megabytes a page's own script could hand the binding directly.
URL_LIMIT: Final = 65536

#: Each payload key's bound, in characters.
_VALUE_LIMITS: Final = {
    "document": URL_LIMIT,
    "directive": URL_LIMIT,
    "blocked": URL_LIMIT,
    "sample": SAMPLE_LIMIT,
}

#: Run in the page: the content of the first policy meta in the document, or null. The
#: attribute selector's `i` matches the value ignoring case, as an engine does.
_POLICY_OF: Final = """() => {
  const meta = document.querySelector('meta[http-equiv="Content-Security-Policy" i]');
  return meta === null ? null : meta.getAttribute("content");
}"""

#: Run in the page: add the planted policy to the head, then break it on the root element. An
#: engine applies a policy meta from the moment it is inserted, so the attribute set after it is
#: checked against it.
_PLANT: Final = """([policy, sample]) => {
  const meta = document.createElement("meta");
  meta.httpEquiv = "Content-Security-Policy";
  meta.content = policy;
  document.head.append(meta);
  document.documentElement.setAttribute("style", sample);
}"""


class ContextLike(Protocol):
    """A browser context, as far as `install` uses one: the two calls it makes, and no more."""

    def expose_binding(
        self, name: str, callback: Callable[[object, object], None]
    ) -> object:
        """Expose `callback` to every page of the context as the page global `name`.

        A page's call hands it the caller (`source`) and the page's own argument.
        """

    def add_init_script(self, script: str) -> object:
        """Run `script` in every document of the context, before the document's own."""


class PageLike(Protocol):
    """A page, as far as `policy_of` and `plant` use one: the one call they make."""

    def evaluate(self, expression: str, arg: object = ...) -> object:
        """Evaluate `expression` in the page, calling it with `arg` when it is a function."""


class CspWatchError(Exception):
    """An answer from the page that the in-page code never gives: an instrument fault."""


@dataclass(frozen=True)
class Violation:
    """One violation, as the reporting document's engine described it."""

    #: The reporting document's path and query.
    document: str
    #: The directive that refused the resource (the event's `effectiveDirective`).
    directive: str
    #: What was refused, as the engine names it: "inline", "eval", a URL, or "".
    blocked: str
    #: The refused text's first 40 characters; "" unless the policy asks for `'report-sample'`.
    sample: str

    def describe(self) -> str:
        """One line naming the directive, what it blocked and where; then the sample, if any."""
        text = f"{self.directive} blocked {self.blocked} on {self.document}"
        return f"{text}: {self.sample}" if self.sample else text


# fuzz-target
def violation_from(payload: object) -> Violation:
    """The `Violation` a listener payload describes.

    Raises:
        CspWatchError: `payload` is not an object of exactly the four keys, each a string no
            longer than its bound (`SAMPLE_LIMIT`, else `URL_LIMIT`).
    """
    if not isinstance(payload, dict):
        raise CspWatchError(
            f"the listener sent {type(payload).__name__}, not a violation"
        )
    missing = [key for key in _PAYLOAD_KEYS if key not in payload]
    extra = sorted(repr(key) for key in payload if key not in _PAYLOAD_KEYS)
    if missing or extra:
        raise CspWatchError(
            f"a violation from the listener has exactly the keys {list(_PAYLOAD_KEYS)}: "
            f"this one lacks {missing} and adds {extra}"
        )
    wrong = [key for key in _PAYLOAD_KEYS if not isinstance(payload[key], str)]
    if wrong:
        raise CspWatchError(f"the listener sent a violation whose {wrong} are not text")
    # The fault names the keys only: repeating an over-long value would carry it into the
    # report anyway.
    long = [key for key in _PAYLOAD_KEYS if len(payload[key]) > _VALUE_LIMITS[key]]
    if long:
        raise CspWatchError(
            f"the listener sent a violation whose {long} are longer than it sends"
        )
    return Violation(*(payload[key] for key in _PAYLOAD_KEYS))


class CspLog:
    """Every violation one context's pages reported, in the order they arrived.

    A payload the listener could not have sent is an instrument fault. Raising it from `record`
    alone would lose it: Playwright hands a binding callback's exception back to the PAGE, as
    the binding call's rejection, so it never reaches the code measuring. So the log keeps the
    first fault and raises it from every later read.

    With `admit`, only the reports whose caller it admits are kept; any other is dropped
    unread, so nor is its payload a fault.
    """

    def __init__(self, admit: Admit | None = None) -> None:
        """An empty log, with no fault, keeping what `admit` admits (by default, everything)."""
        self._violations: list[Violation] = []
        self._fault: CspWatchError | None = None
        self._admit = admit

    @property
    def violations(self) -> tuple[Violation, ...]:
        """Every violation so far.

        Raises:
            CspWatchError: a payload was refused since the log was made.
        """
        self._raise_any_fault()
        return tuple(self._violations)

    def record(self, source: object, payload: object) -> None:
        """The binding's callback: keep the violation `payload` describes.

        In the sync API this runs on Playwright's dispatcher, which is a greenlet on the
        caller's own thread, and only while the caller is inside a Playwright call. So it never
        runs beside a read of the log, and the append needs no lock. `source` (the page and
        frame that called) decides only whether the report is kept, by the log's `admit`: the
        payload names its own document.

        Raises:
            CspWatchError: `payload` is not one the listener sends; the log keeps the fault.
        """
        if self._admit is not None and not self._admit(source):
            return
        try:
            violation = violation_from(payload)
        except CspWatchError as fault:
            self._fault = self._fault or fault
            raise
        self._violations.append(violation)

    def mark(self) -> int:
        """How many violations so far: split the log before and after a plant with it.

        Raises:
            CspWatchError: a payload was refused since the log was made.
        """
        self._raise_any_fault()
        return len(self._violations)

    def _raise_any_fault(self) -> None:
        """Raise the first refused payload's fault, if there was one."""
        if self._fault is not None:
            raise self._fault


def install(context: ContextLike, *, admit: Admit | None = None) -> CspLog:
    """Collect every violation `context`'s pages report from now on, into the log returned.

    Call it before the context's first `new_page()`, so the first load is watched. The binding
    is exposed before the listener is added, so the listener never runs without it to call.
    With `admit`, the log keeps only the reports whose caller it admits (`CspLog`).
    """
    log = CspLog(admit)
    context.expose_binding(BINDING, log.record)
    context.add_init_script(
        f"{_LISTENER_SOURCE}\ncspWatch(window, {json.dumps(BINDING)});"
    )
    return log


def policy_of(page: PageLike) -> str | None:
    """The content of the page's first Content-Security-Policy meta, or None when it has none.

    Raises:
        CspWatchError: the page answered something other than text or nothing.
    """
    answer = page.evaluate(_POLICY_OF)
    if answer is None or isinstance(answer, str):
        return answer
    raise CspWatchError(
        f"the policy read answered {type(answer).__name__}, not text or nothing"
    )


def plant(page: PageLike) -> None:
    """Add `PLANT_POLICY` to the page, then set `PLANT_SAMPLE` as its root's style attribute.

    The attribute breaks the planted policy, so a watched page with scripts on reports one
    violation whose sample is `PLANT_SAMPLE`. The report arrives after this returns, as every
    violation event does: poll `plant_caught` for it.

    The planted policy stays in force for the life of the document, refusing every style
    attribute and inline `<style>` the page adds after it, so plant last on a rendering, once
    its measurement is taken.
    """
    page.evaluate(_PLANT, [PLANT_POLICY, PLANT_SAMPLE])


def plant_caught(log: CspLog, since: int) -> bool:
    """Whether a violation after the first `since` in `log` carries the planted sample."""
    return any(PLANT_SAMPLE in violation.sample for violation in log.violations[since:])
