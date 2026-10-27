"""Grouped suite: the run's progress lines, and where its pages are measured from.

`pass_progress` builds the two lines the CLI prints as it goes; `pass_serve` serves `--serve`'s
tree on loopback and confirms its pages, or hands back the targets file's `base_url`. Both are
driven end to end by the CLI suites; this one pins each on its own, with a real loopback server
over a scratch tree.
"""

from __future__ import annotations

import sys
import tempfile
import tomllib
import unittest
import urllib.request
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# The shared runner and loopback server, each a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place, and
# put just after src/, ahead of every installed distribution, so none of the same name can
# shadow it.
sys.path.insert(1, str(_TOOL_DIR.parent / "bounded_run"))
sys.path.insert(1, str(_TOOL_DIR.parent / "loopback_site"))

import loopback_site  # noqa: E402 - a folder beside the tool's, via the insert above
import pass_progress  # noqa: E402 - the tool's private src/, via the insert above
import pass_serve  # noqa: E402 - the tool's private src/, via the insert above
import pass_shift  # noqa: E402 - the tool's private src/, via the insert above
import pass_targets  # noqa: E402 - the tool's private src/, via the insert above
from pass_motion import NO_MOTION  # noqa: E402 - the tool's private src/
from pass_report import RenderingResult  # noqa: E402 - the tool's private src/
from pass_targets import Rendering  # noqa: E402 - the tool's private src/

_TIMEOUT = 10.0
_LOADED = Rendering("webkit", "/", 390, 844, "light", "on")


def _result(
    findings: dict[str, tuple[str, ...]] | None = None,
    verdicts: dict[str, str] | None = None,
    faults: tuple[str, ...] = (),
) -> RenderingResult:
    late = pass_shift.LateShift(pass_shift.NOT_APPLICABLE)
    return RenderingResult(
        _LOADED, faults, findings or {}, verdicts or {}, {}, late, NO_MOTION
    )


def _targets(text: str) -> pass_targets.Targets:
    return pass_targets.parse_targets(tomllib.loads(text))


_TARGETS = """pages = ["/"]
[[viewport]]
width = 390
height = 844
"""


class AnnouncementTests(unittest.TestCase):
    """A rendering's line: only one with something to say gets one."""

    def test_a_silent_rendering_says_nothing(self) -> None:
        self.assertIsNone(pass_progress.announcement(_result(verdicts={"x": "caught"})))

    def test_faults_findings_and_uncaught_plants_are_named(self) -> None:
        line = pass_progress.announcement(
            _result(
                findings={"small_target": ("a",)},
                verdicts={"page_error": "missed", "late_shift": "n/a"},
                faults=("the layout was still changing",),
            )
        )
        self.assertEqual(
            line,
            f"{_LOADED.label}: the layout was still changing, small_target, "
            "page_error missed",
        )


class StatesLeftOutTests(unittest.TestCase):
    """A state the run renders none of is counted and named."""

    def test_nothing_is_said_when_every_state_renders(self) -> None:
        self.assertIsNone(pass_progress.states_left_out(_targets(_TARGETS), [_LOADED]))

    def test_each_state_left_out_is_named(self) -> None:
        targets = _targets(
            _TARGETS
            + '[[state]]\nname = "a"\npage = "/"\n[[state]]\nname = "b"\npage = "/"\n'
        )
        self.assertEqual(
            pass_progress.states_left_out(targets, [_LOADED]),
            "2 states not rendered: a state renders with scripts on only (a, b)",
        )
        opened = Rendering("webkit", "/", 390, 844, "light", "on", "a")
        self.assertEqual(
            pass_progress.states_left_out(targets, [opened]),
            "1 state not rendered: a state renders with scripts on only (b)",
        )


class PagesAtTests(unittest.TestCase):
    """`--serve`'s tree served and confirmed, or the targets file's own url."""

    def test_without_serve_the_base_url_is_measured_as_it_answers(self) -> None:
        targets = _targets('base_url = "http://127.0.0.1:8123"\n' + _TARGETS)
        with pass_serve.pages_at(targets, None, frozenset(), timeout=_TIMEOUT) as at:
            self.assertEqual(at, ("http://127.0.0.1:8123", ()))

    def test_a_served_tree_is_confirmed_then_measured(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder)
            (site / "index.html").write_text(
                "<p>home</p>\n", encoding="utf-8", newline="\n"
            )
            targets = _targets(_TARGETS)
            with pass_serve.pages_at(targets, site, frozenset(), timeout=_TIMEOUT) as (
                base_url,
                redirects,
            ):
                self.assertEqual(redirects, ())
                with urllib.request.urlopen(base_url + "/", timeout=_TIMEOUT) as answer:
                    self.assertEqual(answer.read(), b"<p>home</p>\n")

    def test_a_page_the_tree_lacks_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            targets = _targets(_TARGETS)
            with (
                self.assertRaises((OSError, loopback_site.SiteServeError)),
                pass_serve.pages_at(
                    targets, Path(folder), frozenset(), timeout=_TIMEOUT
                ),
            ):
                pass


if __name__ == "__main__":
    unittest.main()
