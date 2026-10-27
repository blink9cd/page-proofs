"""The driver's half of the in-page probe: its source, the values it shares, and how it is called.

The in-page probe is one function the driver evaluates in the page, written in three files:
`page_probe.js`, a function of the other two; `page_probe_motion.js`, the stilling both settles
run; and `page_probe_trees.js`, the one walk over the page's elements, each open shadow root's
included, that every read makes. Their source is composed here, the first applied to the other
two, so an engine receives one function as before. Two loads call it -- the page-load detectors'
(`pass_engine`) and the late-script load's (`pass_late_load`), whose stilling also calls it over a
DevTools session (`pass_inert`) -- so its source, the configuration handed over on every call and
the pace of every poll live here, once.

Entry point assumptions: Playwright is importable (the repository's `browser` extra).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from pass_checks import (
    CONSOLE_PLANT_TEXT,
    MISSING_PLANT_PATH,
    PAGE_ERROR_PLANT_TEXT,
    PLANT_MARK,
)
from playwright.sync_api import Page


def _expression(name: str) -> str:
    """The function expression the probe file `name` holds, trimmed and without its semicolon.

    Each file is one expression statement, and the semicolon ending it may not sit inside the
    parentheses that compose the three.
    """
    text = Path(__file__).with_name(name).read_text(encoding="utf-8")
    return text.strip().removesuffix(";")


#: The in-page probe, handed to the engine as source text: `page_probe.js` applied to its motion
#: half, `page_probe_motion.js`, and its tree walker, `page_probe_trees.js`. The result is the
#: one function the engine calls with each operation; `page_probe_test_helpers.mjs` composes the
#: same three files for the JS suites.
PROBE_SOURCE = (
    f"({_expression('page_probe.js')})"
    f"({_expression('page_probe_motion.js')}, {_expression('page_probe_trees.js')})"
)

#: The attribute a planted element carries, naming the detector it was planted for.
PLANT_ATTRIBUTE = "data-browser-pass-plant"

#: The page global the late-script load's layout-shift observer records into.
SHIFT_LOG = "__browserPassShifts"

#: Everything the probe shares with this side, handed over on every call.
PROBE_CONFIG: dict[str, object] = {
    "plantAttribute": PLANT_ATTRIBUTE,
    "plantMark": PLANT_MARK,
    "consoleText": CONSOLE_PLANT_TEXT,
    "pageErrorText": PAGE_ERROR_PLANT_TEXT,
    "missingPath": MISSING_PLANT_PATH,
    "shiftLog": SHIFT_LOG,
}

#: How long a layout must hold still to count as settled. The page-load settle stills every CSS
#: and Web Animations animation before each poll (`pass_motion`), so this window guards what
#: that cannot reach: a page still building itself, and motion a script drives, which a pause
#: shorter than the window keeps from reading as settled. A script pacing its motion with longer
#: pauses is still read at its first pause, and lengthening the window is no cure for that: a
#: page carrying an endless animation would time out instead.
SETTLE_QUIET_SECONDS = 1.0

#: How often a poll reads the page again.
POLL_INTERVAL_SECONDS = 0.1

MS_PER_SECOND = 1000.0


def probe(page: Page, operation: str, **extra: object) -> object:
    """Run one operation of the in-page probe; `extra` joins the shared configuration."""
    result: object = page.evaluate(PROBE_SOURCE, [operation, {**PROBE_CONFIG, **extra}])
    return result


def probe_expression(operation: str) -> str:
    """The probe called with `operation` and the shared configuration, as source to evaluate."""
    call = json.dumps([operation, PROBE_CONFIG])
    return f"({PROBE_SOURCE})({call})"


def init_script(operation: str) -> str:
    """The probe as an init script: `operation` runs before any of the page's own code."""
    return f"{probe_expression(operation)};"


def pause(page: Page) -> Callable[[float], None]:
    """A pause that keeps Playwright delivering events, which a bare `time.sleep` would not."""
    return lambda seconds: page.wait_for_timeout(seconds * MS_PER_SECOND)
