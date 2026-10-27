"""Suite: the engine's one `--blink-settings` switch keeps Playwright's own headless settings.

Playwright's headless launch passes a `--blink-settings` of its own -- a hovering, fine
pointer -- before the caller's arguments, and Chromium honors only the last such switch. So
the engine's switch repeats Playwright's settings ahead of its lazy-image margin, and this
suite pins the copy against the installed Playwright: an upgrade that changes Playwright's
default fails here, loudly, rather than silently dropping half of it from every launch.

No browser is launched: the default is read from the JavaScript bundle Playwright's Python
package ships its driver in, found from the package's own location at run time.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; discovery's top-level directory puts the shared fixtures beside this module on
the path, and the tool's interpreter has Playwright installed.
"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path
from typing import Final

from _print_engine_fixtures import PLAYWRIGHT_HEADLESS_SETTINGS

#: Where, under the Playwright package, the driver's JavaScript lives.
_DRIVER_LIB: Final = ("driver", "package", "lib")

#: A `--blink-settings` value written into the driver's JavaScript as a string literal.
_BLINK_LITERAL: Final = re.compile(r"""["'`]--blink-settings=([^"'`]*)["'`]""")


def _playwright_blink_settings() -> set[str]:
    """Every `--blink-settings` value the installed Playwright's driver passes.

    Raises:
        AssertionError: Playwright is not installed, or its driver has no JavaScript.
    """
    spec = importlib.util.find_spec("playwright")
    if spec is None or spec.origin is None:
        raise AssertionError("Playwright is not installed for this interpreter")
    lib = Path(spec.origin).parent.joinpath(*_DRIVER_LIB)
    scripts = sorted(lib.rglob("*.js"))
    if not scripts:
        raise AssertionError(f"no driver JavaScript under {lib.name}/ in Playwright")
    return {
        value
        for script in scripts
        for value in _BLINK_LITERAL.findall(script.read_text(encoding="utf-8"))
    }


class PlaywrightHeadlessSettingsTests(unittest.TestCase):
    """The fixture's copy is exactly what Playwright's headless launch passes.

    The engine's launch arguments are pinned to the fixture's switch, which opens with this
    copy, in `test_print_engine`; this suite closes the chain at the installed driver.
    """

    def test_playwright_passes_exactly_the_settings_the_engine_repeats(self) -> None:
        self.assertEqual(_playwright_blink_settings(), {PLAYWRIGHT_HEADLESS_SETTINGS})


if __name__ == "__main__":
    unittest.main()
