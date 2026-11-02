"""Stand-ins for browser_pass's CSP suite: a browser that logs what is asked of it, and reports.

`test_pass_csp.py` drives `pass_engine.render` through `FakeBrowser`. Each context it hands out
logs every call made of it, in order, so a test can read the ORDER of the wiring: the collector
exposed before the first page, the CSP plant after every other read. Each page answers the probe
with records that measure (a faithful read-back, a still page, every plant taken, a late-script
record that is presentable), and delivers the violations its `Script` names through the exposed
binding, only where the listener's init script would run: never with scripts off.

Entry point assumptions: imported by a suite in this folder; it puts the tool's `src/` on
`sys.path`, and `pass_csp`'s own insert puts the shared collector there.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pass_checks  # noqa: E402 - the tool's private src/, via the insert above
import pass_csp  # noqa: E402 - the tool's private src/, via the insert above

import csp_watch  # noqa: E402 - after pass_csp, whose insert puts it on sys.path
import pass_engine  # noqa: E402 - the tool's private src/, via the insert above
import pass_probe  # noqa: E402 - the tool's private src/, via the insert above
import pass_report  # noqa: E402 - the tool's private src/, via the insert above
from pass_motion import NO_MOTION, motion_record  # noqa: E402 - the tool's private src/
from pass_targets import Rendering, State  # noqa: E402 - the tool's private src/
from playwright.sync_api import Error as PlaywrightError  # noqa: E402 - after the insert

BASE_URL = "http://127.0.0.1:8123"

#: Short, because a poll that cannot succeed here (the listener plants nobody reports, a plant
#: the log never sees) busy-waits it out against a pause that does not sleep.
TIMEOUT = 0.05

#: WebKit, so the rendering opens one context: only Chromium gets a late-script load.
ON = Rendering("webkit", "/csp.html", 390, 844, "light", "on")
OFF = replace(ON, scripts="off")
CHROMIUM = replace(ON, engine="chromium")


def payload(directive: str, blocked: str, sample: str = "") -> dict[str, str]:
    """One violation as the listener sends it, from the sample page."""
    return {
        "document": "/csp.html",
        "directive": directive,
        "blocked": blocked,
        "sample": sample,
    }


STYLE = payload("style-src-attr", "inline")
EVAL = payload("script-src", "eval")
PLANTED = payload("style-src-attr", "inline", csp_watch.PLANT_SAMPLE)
STYLE_LINE = "style-src-attr blocked inline on /csp.html"
EVAL_LINE = "script-src blocked eval on /csp.html"

#: The finding `--require-csp` makes, as `pass_csp` spells it.
NO_POLICY = pass_csp.NO_POLICY

#: A layout record with nothing in it.
NO_LAYOUT = {
    "overflow": 0,
    "pastRightEdge": [],
    "hiddenRendered": [],
    "smallTargets": [],
}

#: A late-script record that measures: painted before the release, presentable at it, no shift.
_PRESENTABLE = {
    "supported": True,
    "released": 10.0,
    "paints": [5.0],
    "presentableAt": 10.0,
    "blockingHeld": False,
    "entries": [],
}


@dataclass(frozen=True)
class Script:
    """What one context's page reports, and when; and the policy its markup declares."""

    on_load: tuple[object, ...] = ()
    on_probe_plant: tuple[object, ...] = ()
    on_csp_plant: tuple[object, ...] = ()
    on_shift_plant: tuple[object, ...] = ()
    #: What another page of the context reports as this one loads: a tab an extension opened.
    from_elsewhere: tuple[object, ...] = ()
    #: What frames of this very page report as it loads, each payload beside its frame's url:
    #: an iframe of the page's own, or one an extension injected into it.
    from_frames: tuple[tuple[str, object], ...] = ()
    #: What the policy read answers: text, nothing, or what no page would answer.
    policy: object = None
    #: The url the page's main frame is at once loaded: the measured page's own origin, which a
    #: redirect can move off the base url's.
    main_frame_url: str = f"{BASE_URL}/csp.html"


@dataclass(frozen=True)
class FakeFrame:
    """The frame a binding's caller names: its url is all the collector reads of it."""

    url: str


class FakeContext:
    """A browser context that logs every call made of it, in order, and hands out pages."""

    def __init__(self, options: Mapping[str, object], script: Script) -> None:
        """A context opened with `options`, whose pages follow `script`."""
        self.options = options
        self.script = script
        self.calls: list[str] = []
        self._binding: Callable[[object, object], None] | None = None

    def expose_binding(
        self, name: str, callback: Callable[[object, object], None]
    ) -> None:
        """Keep the callback the listener would call."""
        self.calls.append(f"expose_binding {name}")
        self._binding = callback

    def add_init_script(self, script: str) -> None:
        """Log the init script; the page delivers what the listener would have sent."""
        self.calls.append("add_init_script")

    def new_page(self) -> FakePage:
        """A page of this context."""
        self.calls.append("new_page")
        return FakePage(self)

    def close(self) -> None:
        """Log the close."""
        self.calls.append("close")

    def new_cdp_session(self, page: object) -> object:
        """Refuse a DevTools session, as Playwright does but in Chromium: none is scripted."""
        raise PlaywrightError(
            "BrowserContext.new_cdp_session: CDP session is only available in Chromium"
        )

    def report(
        self,
        payloads: Sequence[object],
        page: FakePage,
        *,
        frame_url: str | None = None,
    ) -> None:
        """Hand each payload to the binding as `page`'s listener would: only where it runs.

        The binding is handed its caller as Playwright hands it: a source naming the page, and
        the frame, at `frame_url`, whose document reported; by default the page's main frame.
        """
        if self._binding is None or not self.options["java_script_enabled"]:
            return
        frame = page.main_frame if frame_url is None else FakeFrame(frame_url)
        source = {"context": self, "page": page, "frame": frame}
        for each in payloads:
            # As Playwright does: a binding callback's exception goes back to the PAGE, never to
            # the code measuring, so the log's own later reads are the only way it surfaces.
            with contextlib.suppress(csp_watch.CspWatchError):
                self._binding(source, each)


class FakePage:
    """A page answering the probe with records that measure, and reporting what its script says."""

    def __init__(self, context: FakeContext) -> None:
        """A page of `context`."""
        self.context = context
        self.main_frame = FakeFrame(context.script.main_frame_url)

    def on(self, event: str, handler: object) -> None:
        """Take a listener; nothing is reported to it."""

    def route(self, pattern: str, handler: object) -> None:
        """Take a route handler; no request is made through it."""

    def goto(self, url: str, **_options: object) -> None:
        """Load: report what the script says the load reports."""
        self.context.calls.append("goto")
        self.context.report(self.context.script.on_load, self)
        self.context.report(self.context.script.from_elsewhere, FakePage(self.context))
        for frame_url, each in self.context.script.from_frames:
            self.context.report((each,), self, frame_url=frame_url)

    def wait_for_timeout(self, milliseconds: float) -> None:
        """A pause that does not sleep."""

    def wait_for_load_state(self, state: str, **_options: object) -> None:
        """Loaded already."""

    def evaluate(self, source: str, argument: object = None) -> object:
        """`policy_of` passes no argument, `plant` its policy and sample, the probe an operation."""
        script = self.context.script
        if argument is None:
            self.context.calls.append("policy_of")
            return script.policy
        if argument == [csp_watch.PLANT_POLICY, csp_watch.PLANT_SAMPLE]:
            self.context.calls.append("csp plant")
            self.context.report(script.on_csp_plant, self)
            return None
        assert isinstance(argument, list)
        operation = str(argument[0])
        self.context.calls.append(operation)
        caused = {"plant": script.on_probe_plant, "plantShift": script.on_shift_plant}
        self.context.report(caused.get(operation, ()), self)
        return self._record(operation)

    def _record(self, operation: str) -> object:
        """The probe's answer: a faithful read-back, a still page, every plant taken."""
        viewport = self.context.options["viewport"]
        assert isinstance(viewport, dict)
        width, height = viewport["width"], viewport["height"]
        return {
            "stillMotion": motion_record(NO_MOTION),
            "signature": [width, height, 3, "loaded", 0],
            "state": {
                "innerWidth": width,
                "innerHeight": height,
                "clientWidth": width,
                "scriptingNone": not self.context.options["java_script_enabled"],
                "dark": self.context.options["color_scheme"] == "dark",
                "landscape": width > height,
                "reducedMotion": False,
            },
            "layout": NO_LAYOUT,
            "digest": [],
            "plant": dict.fromkeys(pass_checks.DETECTORS, True),
            "holdState": {"painted": True, "fontsLoaded": True, "blocked": False},
            "markRelease": True,
            "lateShifts": _PRESENTABLE,
            "plantShift": True,
            "plantMoved": True,
        }[operation]


class FakeBrowser:
    """Hands out one `FakeContext` per `new_context`, each with the next script in turn."""

    def __init__(self, *scripts: Script) -> None:
        """A browser whose contexts follow `scripts`, in the order they are opened."""
        self._scripts = iter(scripts)
        self.contexts: list[FakeContext] = []

    def new_context(self, **options: object) -> FakeContext:
        """A fresh context with the next script."""
        context = FakeContext(options, next(self._scripts))
        self.contexts.append(context)
        return context


def render(
    rendering: Rendering,
    *scripts: Script,
    prove: bool = False,
    require_csp: bool = False,
    state: State | None = None,
) -> tuple[pass_report.RenderingResult, FakeBrowser]:
    """`pass_engine.render` in a fake browser whose contexts follow `scripts` in turn."""
    browser = FakeBrowser(*scripts)
    # A quiet window of nothing: the fake page never moves, and its pause never sleeps.
    with mock.patch.object(pass_probe, "SETTLE_QUIET_SECONDS", 0.0):
        result = pass_engine.render(
            browser,  # type: ignore[arg-type] # a scripted stand-in for the browser
            rendering,
            BASE_URL,
            timeout=TIMEOUT,
            prove=prove,
            state=state,
            require_csp=require_csp,
        )
    return result, browser


def facts(*engines: str, require_csp: bool = False) -> pass_report.RunFacts:
    """A run's facts, naming `engines`."""
    return pass_report.RunFacts(
        started="20261006T000000Z",
        commit=None,
        tree_dirty=None,
        served=None,
        base_url=BASE_URL,
        playwright="1.63.0",
        engines=dict.fromkeys(engines, "1"),
        prove=False,
        require_csp=require_csp,
    )
