"""Stand-ins for browser_pass's DevTools-session suite: a session that answers with scripted records.

`test_pass_inert.py` drives the late-script load's settle (`pass_late_load._settled`) through
`FakePage`, whose context opens a `FakeSession` -- or refuses one, as Playwright does for any
engine but Chromium. The session answers each call the stilling makes as the protocol would: the
probe's listing as a handle on a record holding the listed array and the asks beside it, each
ask's fields -- a null for an animation the listing asks nothing about -- a node for each target
and each pseudo-element, the `@keyframes` rules the style resolver applies to a node, and the
stilling's record. It refuses the calls a test names, as the protocol refuses one about an object
or node it no longer has, and logs every call, in order, so a test can read what the stilling was
handed.

Entry point assumptions: imported by a suite in this folder; it puts the tool's `src/` on
`sys.path`.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pass_late_load  # noqa: E402 - the tool's private src/, via the insert above
import pass_probe  # noqa: E402 - the tool's private src/, via the insert above
from pass_motion import NO_MOTION, Motion, motion_record  # noqa: E402 - the tool's src/
from playwright.sync_api import Error as PlaywrightError  # noqa: E402 - after the insert

#: Never spent: every settle here is scripted to read a set number of times.
TIMEOUT = 30.0

#: The handles the fake session gives the probe's listing: the record it returns, the array of
#: running animations in it, which the stilling is called over, and the asks beside them.
RECORD = "record"
LISTED = "listed"
ASKS = "asks"

#: Where each kind of pseudo-element's backend ids start, beside its originating element's id:
#: every pseudo-element of a node has an id of its own, so one read in another's place reads
#: another's rule.
_PSEUDO_BASES = {"before": 900, "after": 800}

#: What the probe's listing is evaluated as: the probe itself, called with its operation.
LISTING = f"({pass_probe.PROBE_SOURCE})({json.dumps(['listMotion', pass_probe.PROBE_CONFIG])})"


def pseudo_node(node: int, kind: str) -> int:
    """The backend id of `node`'s pseudo-element `kind` (`before`), and the node it is pushed to."""
    return _PSEUDO_BASES[kind] + node


def rule(name: str, *keyframes: tuple[str, ...]) -> dict[str, object]:
    """A `@keyframes` rule as the protocol reports one: each keyframe's declared names."""
    return {
        "animationName": {"text": name},
        "keyframes": [
            {
                "style": {
                    "cssProperties": [{"name": each, "value": "0"} for each in names]
                }
            }
            for names in keyframes
        ],
    }


@dataclass(frozen=True)
class Listed:
    """One animation as the probe lists it, and what it asks the engine about it.

    `name` is None where the listing asks nothing (its rule could not decide its verdict), `node`
    the node its target resolves to (None: no target; 0: one the protocol cannot place), and
    `pseudo` the pseudo-element of it the effect targets.
    """

    name: str | None = "drift"
    node: int | None = 1
    pseudo: str | None = None


def _remote(value: object) -> dict[str, object]:
    """A primitive as the protocol hands one back by value."""
    if value is None:
        return {"type": "object", "subtype": "null", "value": None}
    return {"type": "string", "value": value}


class FakeSession:
    """A DevTools session answering with scripted records, and refusing where it is told to.

    `rules` maps a node to the `@keyframes` rules the style resolver applies to it, `pseudos` a
    node to its pseudo-elements, and `refuses` names (method, subject) pairs the protocol refuses,
    the subject being the object or node the call is about.
    """

    def __init__(
        self,
        listed: tuple[Listed, ...],
        *,
        rules: Mapping[int, list[dict[str, object]]],
        pseudos: Mapping[int, tuple[str, ...]] | None = None,
        refuses: frozenset[tuple[str, object]] = frozenset(),
        stilled: Motion = NO_MOTION,
        throws: str = "",
    ) -> None:
        self._listed = listed
        self._rules = rules
        self._pseudos = pseudos or {}
        self._refuses = refuses
        self._stilled = stilled
        self._throws = throws
        #: Every call made, in order, with its parameters.
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.detached = 0

    def send(self, method: str, params: dict[str, object] | None = None) -> object:
        """Answer one call, or refuse it as the protocol would."""
        given = dict(params or {})
        self.calls.append((method, given))
        if (method, _subject(given)) in self._refuses:
            raise PlaywrightError(
                f"CDPSession.send: Protocol error ({method}): refused"
            )
        return self._answers()[method](given)

    def detach(self) -> None:
        """End the session."""
        self.detached += 1

    def _answers(self) -> dict[str, Callable[[dict[str, object]], object]]:
        """What each method answers, by name."""
        return {
            "DOM.enable": lambda _: {},
            "CSS.enable": lambda _: {},
            "DOM.getDocument": lambda _: {"root": {"nodeId": 1}},
            "Runtime.evaluate": lambda _: self._returning(
                "listMotion", {"type": "object", "objectId": RECORD}
            ),
            "Runtime.getProperties": self._properties,
            "DOM.requestNode": self._requested,
            "DOM.describeNode": self._described,
            "DOM.pushNodesByBackendIdsToFrontend": lambda given: {
                "nodeIds": given["backendNodeIds"]
            },
            "CSS.getMatchedStylesForNode": lambda given: {
                "cssKeyframesRules": self._rules.get(_int(given["nodeId"]), [])
            },
            "Runtime.callFunctionOn": lambda _: self._returning(
                "stillMotion", {"type": "object", "value": motion_record(self._stilled)}
            ),
            "Runtime.releaseObjectGroup": lambda _: {},
        }

    def _returning(
        self, operation: str, result: dict[str, object]
    ) -> dict[str, object]:
        """What a probe call returns, or the exception it threw where it is told to throw."""
        if operation != self._throws:
            return {"result": result}
        thrown = {
            "type": "object",
            "subtype": "error",
            "description": f"Error: {operation}",
        }
        return {
            "result": thrown,
            "exceptionDetails": {"text": "Uncaught", "exception": thrown},
        }

    def _properties(self, given: dict[str, object]) -> dict[str, object]:
        """The listing's own fields, its asks, or one ask's fields; none for any other object."""
        handle = str(given["objectId"])
        if handle == RECORD:
            return {"result": [_field("listed", LISTED), _field("asks", ASKS)]}
        if handle == ASKS:
            asks = [
                {"name": str(index), "value": _ask(index, listed)}
                for index, listed in enumerate(self._listed)
            ]
            length = {
                "name": "length",
                "value": {"type": "number", "value": len(asks)},
            }
            return {"result": [*asks, length]}
        if handle.startswith("ask:"):
            index = int(handle.removeprefix("ask:"))
            return {"result": _fields(index, self._listed[index])}
        return {"result": []}

    def _requested(self, given: dict[str, object]) -> dict[str, object]:
        """The node a target's handle resolves to; 0 where the protocol cannot place it."""
        index = int(str(given["objectId"]).removeprefix("target:"))
        return {"nodeId": self._listed[index].node}

    def _described(self, given: dict[str, object]) -> dict[str, object]:
        """A node and its pseudo-elements, each with a backend id of its own."""
        node = _int(given["nodeId"])
        pseudos = [
            {"pseudoType": kind, "backendNodeId": pseudo_node(node, kind)}
            for kind in self._pseudos.get(node, ())
        ]
        return {"node": {"nodeId": node, "pseudoElements": pseudos}}


def _int(value: object) -> int:
    """A node id the code under test passed, as the integer it must be."""
    assert isinstance(value, int), value
    return value


def _subject(given: dict[str, object]) -> object:
    """What a call is about: the object, the node or the backend node it names."""
    backend = given.get("backendNodeIds")
    if isinstance(backend, list):
        return backend[0]
    return given.get("objectId", given.get("nodeId"))


def _field(name: str, handle: str) -> dict[str, object]:
    """An object's own field holding the remote object `handle`."""
    return {"name": name, "value": {"type": "object", "objectId": handle}}


def _ask(index: int, listed: Listed) -> dict[str, object]:
    """The ask beside the animation at `index`: a handle on it, or a null where there is none."""
    if listed.name is None:
        return _remote(None)
    return {"type": "object", "objectId": f"ask:{index}"}


def _fields(index: int, listed: Listed) -> list[dict[str, object]]:
    """One ask's fields: what finding the rule applied to its animation takes."""
    target = (
        {"type": "object", "subtype": "node", "objectId": f"target:{index}"}
        if listed.node is not None
        else _remote(None)
    )
    return [
        {"name": "name", "value": _remote(listed.name)},
        {"name": "target", "value": target},
        {"name": "pseudo", "value": _remote(listed.pseudo)},
    ]


class _FakeContext:
    """Opens the scripted session, or refuses as Playwright does for an engine with none."""

    def __init__(self, session: FakeSession | None) -> None:
        self._session = session
        self.opened = 0

    def new_cdp_session(self, page: object) -> FakeSession:
        """The session, or Playwright's refusal where the engine is not Chromium."""
        self.opened += 1
        if self._session is None:
            raise PlaywrightError(
                "BrowserContext.new_cdp_session: CDP session is only available in Chromium"
            )
        return self._session


class FakePage:
    """A page that answers the probe's own calls with a still layout and stills nothing."""

    def __init__(self, session: FakeSession | None) -> None:
        self.context = _FakeContext(session)
        #: Every operation evaluated through the page, with what its call added.
        self.asked: list[tuple[object, dict[str, object]]] = []

    def evaluate(self, source: str, argument: list[object]) -> object:
        """Answer `stillMotion` and `signature`, as a page holding still would."""
        operation, config = argument
        assert isinstance(config, dict)
        extra = {k: v for k, v in config.items() if k not in pass_probe.PROBE_CONFIG}
        self.asked.append((operation, extra))
        if operation == "stillMotion":
            return motion_record(NO_MOTION)
        assert operation == "signature", operation
        return [0, 0, 0, "loaded", 0]

    def wait_for_timeout(self, milliseconds: float) -> None:
        """A pause that does not sleep."""


def reading(times: int) -> Callable[..., bool]:
    """A settle that reads the page `times` times, then settles."""

    def settle(read: Callable[[], object], **_timing: object) -> bool:
        for _ in range(times):
            read()
        return True

    return settle


def late_settle(page: FakePage, *, polls: int = 1) -> tuple[bool, Motion]:
    """The late-script load's settle of `page`, reading it `polls` times."""
    with mock.patch.object(pass_late_load, "wait_until_settled", reading(polls)):
        return pass_late_load._settled(
            page,  # type: ignore[arg-type] # a scripted stand-in for the page
            timeout=TIMEOUT,
        )
