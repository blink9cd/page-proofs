"""The late-script load's stilling: layout-inert motion alone, each CSS animation judged by its rule.

The late load counts layout shifts, so it stills only an animation PROVABLY layout-inert: a seek
of one moving layout would itself be a layout shift, charged to the scripts. The probe judges that
(`page_probe_motion.js`); what it cannot read for itself is what a CSS animation moves. Chromium
leaves custom properties out of a CSS animation's `getKeyframes()`, so a `--m` driving
`margin-top` reads there as moving nothing, and no page script can find the rule that DOES name
it: a same-named rule it reaches may be a decoy, while the one the cascade applies sits in an
imported sheet, the target's own shadow root (`:host`), the root it is slotted into (`::slotted`),
or a CLOSED shadow root. Chromium's own style resolver knows: `CSS.getMatchedStylesForNode`
answers, for a node, the one rule per animation name the cascade applies, with the names each of
its keyframes declares, custom properties included. The late load runs in Chromium alone, so the
DevTools session it is asked over lives only there; the page-load settle stills every animation
that advances, whatever it moves, and opens none.

ONE POLL (`still_inert`): the probe's `listMotion` lists the running animations -- the document's,
and every open shadow root's, whose targets live inside the root -- into ONE array the page
keeps, of which the driver holds a handle, not a copy, and beside each an ask where its rule could
decide its verdict: a CSS animation that advances with time, whose `getKeyframes()` names nothing
but layout-inert properties, or nothing. One whose `getKeyframes()` names another
property is left running whatever its rule adds, and any other animation has no rule, so neither
is asked about. For each ask, the animation's target -- or the pseudo-element of it -- is
resolved to a node and its applied rule read; then the probe's `stillMotion` is called over that
SAME array, handed the names, one entry per animation, None where nothing was asked. The poll's
handles are let go however it ends, and the session however the settle does.

THE RACE. Scripts run between any two of those calls, adding and removing animations, so nothing
here enumerates twice and matches the two by index or name: the names go back beside the array
they were read from, and the probe judges each animation by the entry beside it. One started
since the listing is not in the array, so it is left alone, and the next poll lists it; one
stopped since is skipped by its play state, as at any poll. What no handle pins is a script
rewriting a `@keyframes` rule between the reading and the stilling -- a window as wide as the
reading of every rule asked for: the probe's union with `getKeyframes()` catches any property but
a custom one, and the next poll reads the rule anew.

COST. Nothing read is kept from poll to poll, since a script can insert a rule mid-load. So a
poll makes a fixed handful of calls, and a handful more for each animation asked about (more for
a pseudo-element); one not asked about costs it nothing of its own, however many run. A stilled
animation no longer runs, so after the first poll, what is asked about is what its rule alone
left running: a custom property moving layout, say.

UNKNOWN IS LEFT RUNNING. A rule not returned, a target or pseudo-element that cannot be placed, a
call the protocol refuses: each leaves that animation's names unknown (None), and the probe leaves
it running, which costs the page its measurement only if that motion really keeps the layout
changing. Where no session can be had, every CSS animation's names are unknown. The listing and
the stilling are the probe's own calls, so a failure there is the run's, as a failing
`page.evaluate` is.

BOUNDS. `CDPSession.send` takes no timeout, as `page.evaluate` takes none: every call here runs
inside the rendering's watchdog bound (`pass_watchdog`), as every probe call does, and the
settle's own deadline bounds the polls between them.

Entry point assumptions: Playwright is importable (the repository's `browser` extra).
"""

from __future__ import annotations

from pass_checks import ProbeError
from pass_probe import PROBE_CONFIG, PROBE_SOURCE, probe, probe_expression
from playwright.sync_api import CDPSession, Page
from playwright.sync_api import Error as PlaywrightError

#: The object group a poll's handles live in, let go together however the poll ends.
_OBJECT_GROUP = "browser_pass-motion"


def open_session(page: Page) -> CDPSession | None:
    """A DevTools session on `page`, ready to read rules; None where the engine offers none.

    Playwright refuses one for any engine but Chromium, which leaves every CSS animation's rule
    unknown, and so running: the late load is measured in Chromium alone.
    """
    try:
        session = page.context.new_cdp_session(page)
    except PlaywrightError:
        return None
    session.send("DOM.enable")
    session.send("CSS.enable")
    return session


def close_session(session: CDPSession | None) -> None:
    """End `session`, so nothing of it outlives the settle it served."""
    if session is not None:
        session.detach()


def still_inert(page: Page, session: CDPSession | None) -> object:
    """Still `page`'s provably layout-inert motion once; returns the probe's motion record."""
    if session is None:
        return probe(page, "stillMotion", layoutInertOnly=True)
    # A node id is the session's for one document: one a script replaced drops them all.
    session.send("DOM.getDocument", {"depth": 0})
    try:
        listed, asks = _listing(session)
        declared = [
            None if ask is None else _applied(session, ask)
            for ask in _entries(session, asks)
        ]
        stilled = session.send(
            "Runtime.callFunctionOn",
            {
                "functionDeclaration": PROBE_SOURCE,
                "objectId": listed,
                "arguments": [
                    {"value": ["stillMotion", _stilling_config(declared)]},
                    {"objectId": listed},
                ],
                "returnByValue": True,
            },
        )
        return _at(_returned(stilled, "stillMotion"), "value")
    finally:
        # However the poll ended -- a probe that threw, a call refused -- its handles go with it.
        session.send("Runtime.releaseObjectGroup", {"objectGroup": _OBJECT_GROUP})


def _listing(session: CDPSession) -> tuple[str, str]:
    """Handles on the probe's listing: the running animations, and what to ask beside each."""
    answer = session.send(
        "Runtime.evaluate",
        {
            "expression": probe_expression("listMotion"),
            "objectGroup": _OBJECT_GROUP,
            "returnByValue": False,
        },
    )
    record = _handle(_returned(answer, "listMotion"))
    parts = {} if record is None else _fields(session, record)
    listed, asks = _handle(parts.get("listed")), _handle(parts.get("asks"))
    if listed is None or asks is None:
        raise ProbeError("the probe's listMotion returned no list")
    return listed, asks


def _stilling_config(declared: list[list[str] | None]) -> dict[str, object]:
    """The late load's stilling options: inert motion alone, judged by `declared`."""
    return {**PROBE_CONFIG, "layoutInertOnly": True, "declared": declared}


def _at(record: object, key: str) -> object:
    """`record[key]` in a protocol answer, or None where it holds no such field."""
    return record.get(key) if isinstance(record, dict) else None


def _items(record: object, key: str) -> list[object]:
    """The list `record[key]` in a protocol answer, or none where it holds no list there."""
    found = _at(record, key)
    return found if isinstance(found, list) else []


def _handle(remote: object) -> str | None:
    """The handle on a remote object; None for a value with none, such as null."""
    found = _at(remote, "objectId")
    return found if isinstance(found, str) else None


def _node(found: object) -> int | None:
    """A node id the protocol answered; None for its 0, a node it could not place."""
    return found if isinstance(found, int) and found > 0 else None


def _returned(answer: object, operation: str) -> object:
    """What a probe call over the session returned; a `ProbeError` if the probe threw."""
    thrown = _at(answer, "exceptionDetails")
    if thrown is not None:
        raise ProbeError(
            f"the probe's {operation} threw: "
            f"{_at(_at(thrown, 'exception'), 'description')}"
        )
    return _at(answer, "result")


def _entries(session: CDPSession, array: str) -> list[str | None]:
    """A handle on each entry of the remote `array`, in its order; None for a null one."""
    answer = session.send(
        "Runtime.getProperties", {"objectId": array, "ownProperties": True}
    )
    by_index: dict[int, str | None] = {}
    for found in _items(answer, "result"):
        index = _at(found, "name")
        # Its own properties are its entries, by index, and its `length`.
        if isinstance(index, str) and index.isdigit():
            by_index[int(index)] = _handle(_at(found, "value"))
    return [by_index.get(index) for index in range(len(by_index))]


def _fields(session: CDPSession, remote: str) -> dict[str, object]:
    """A remote object's own fields, each as the protocol hands back a remote value."""
    answer = session.send(
        "Runtime.getProperties", {"objectId": remote, "ownProperties": True}
    )
    return {
        str(_at(found, "name")): _at(found, "value")
        for found in _items(answer, "result")
    }


def _applied(session: CDPSession, ask: str) -> list[str] | None:
    """The names declared by the rule applied to the animation the listing's `ask` is about.

    None where they are unknown, which leaves that animation running.
    """
    try:
        fields = _fields(session, ask)
        node = _target_node(session, fields)
        name = _at(fields.get("name"), "value")
        return None if node is None else _declared(session, node, name)
    except PlaywrightError:
        # The protocol refused: a node gone since the listing, say. Unknown, so left running.
        return None


def _target_node(session: CDPSession, fields: dict[str, object]) -> int | None:
    """The node an entry's rule applies to: its target, or the pseudo-element of it."""
    target = _handle(fields.get("target"))
    if target is None:
        return None
    node = _node(_at(session.send("DOM.requestNode", {"objectId": target}), "nodeId"))
    pseudo = _at(fields.get("pseudo"), "value")
    if node is None or not isinstance(pseudo, str):
        return node
    return _pseudo_node(session, node, pseudo)


def _pseudo_node(session: CDPSession, node: int, pseudo: str) -> int | None:
    """The node of `node`'s pseudo-element `pseudo` (`::before`), which holds its own rules."""
    described = _at(
        session.send("DOM.describeNode", {"nodeId": node, "depth": 1}), "node"
    )
    backend = next(
        (
            _at(each, "backendNodeId")
            for each in _items(described, "pseudoElements")
            if f"::{_at(each, 'pseudoType')}" == pseudo
        ),
        None,
    )
    if not isinstance(backend, int):
        return None
    pushed = session.send(
        "DOM.pushNodesByBackendIdsToFrontend", {"backendNodeIds": [backend]}
    )
    return next((_node(each) for each in _items(pushed, "nodeIds")), None)


def _declared(session: CDPSession, node: int, name: object) -> list[str] | None:
    """Every name declared in every keyframe of the rule called `name` applied to `node`.

    None where no rule of that name applies, as for a `name` the listing gave as no text. A
    declared name the protocol does not give as text is kept as the text of what it gave, which
    names no property, so it never reads as inert.
    """
    answer = session.send("CSS.getMatchedStylesForNode", {"nodeId": node})
    rules = [
        rule
        for rule in _items(answer, "cssKeyframesRules")
        if _at(_at(rule, "animationName"), "text") == name
    ]
    if not rules:
        return None
    return [
        str(_at(declared, "name"))
        for rule in rules
        for keyframe in _items(rule, "keyframes")
        for declared in _items(_at(keyframe, "style"), "cssProperties")
    ]
