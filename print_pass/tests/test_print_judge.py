"""Suite: how the judge weighs what a state blocked, and the images it could not print.

Each case hands `state_problems` one state, a read-back and a network record built here, with
the off-loopback hosts the targets file expects, and compares the problems whole: their texts
are fixed, since a live suite asserts on them. A request blocked on an expected host is still
blocked (the engine aborted it) and is judged a warning; so is an image problem whose source
is on one. Everything else is judged as it always was. How a URL is matched against the hosts
is `test_print_offsite.py`'s.
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path
from typing import Any

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_judge  # noqa: E402 - the tool's private src/, via the insert above
from print_probe import ImageState, ProbeResult, RunningAnimation  # noqa: E402 - as above
from print_record import NetworkRecord, Problem  # noqa: E402 - as above
from print_targets import State  # noqa: E402 - as above

#: The one host most cases expect, and URLs on it and off it.
_EXPECTED = ("example.org",)
_ON = "https://img.example.org/cover.jpg"
_OFF = "https://badexample.org/cover.jpg"

#: A state that emulates nothing, so it adds no warning of its own.
_STATE = State("default", "native", "native", (), (), None)

#: A network record with nothing in it.
_QUIET = NetworkRecord(
    failed=(), http_errors=(), blocked=(), page_errors=(), csp_violations=()
)

#: A style attribute a page's policy refused, as the collector describes it.
_REFUSED = "style-src-attr blocked inline on /"

#: An image's natural width once it has loaded.
_LOADED_WIDTH = 64


def _finding(what: str) -> Problem:
    return Problem("finding", what)


def _warning(what: str) -> Problem:
    return Problem("warning", what)


def _blocked(*urls: str) -> NetworkRecord:
    """A network record of `urls` blocked, and nothing else."""
    return dataclasses.replace(_QUIET, blocked=urls)


def _probe(*images: ImageState) -> ProbeResult:
    """A settled read-back of a light page holding `images`, and nothing else to warn of."""
    return ProbeResult(
        color_scheme="light",
        reduced_motion="no-preference",
        ready_state="complete",
        fonts_status="loaded",
        title="Sample",
        images=images,
        animations=(),
        reads=(),
        storage=(),
    )


def _broken(src: str, *, has_src: bool = True) -> ImageState:
    """An image that completed with no natural width: broken, when it asked for a source."""
    return ImageState(src, True, "", 0, has_src=has_src)


def _incomplete(src: str, *, has_src: bool = True) -> ImageState:
    """An image not complete when the page printed: loading, when it asked for a source."""
    return ImageState(src, False, "lazy", 0, has_src=has_src)


class _JudgeCase(unittest.TestCase):
    """The one call every case makes."""

    def judged(
        self,
        network: NetworkRecord = _QUIET,
        probe: ProbeResult | None = None,
        expected: tuple[str, ...] = _EXPECTED,
    ) -> tuple[Problem, ...]:
        """The problems of the default state, with `probe` (a quiet one) and `network`."""
        return print_judge.state_problems(
            _STATE,
            _probe() if probe is None else probe,
            network,
            expected_hosts=expected,
            color_scheme_emulated=False,
        )


class BlockedRequestTests(_JudgeCase):
    """A blocked request is a warning on an expected host, and a finding off every one."""

    def test_a_blocked_request_on_an_expected_host_is_a_warning(self) -> None:
        self.assertEqual(
            self.judged(_blocked(_ON)),
            (_warning(f"off-loopback request blocked as expected: {_ON}"),),
        )

    def test_a_blocked_request_on_any_other_host_is_a_finding(self) -> None:
        socket = "wss://example.net/live"
        self.assertEqual(
            self.judged(_blocked(_OFF, socket)),
            (
                _finding(f"blocked off-loopback request: {_OFF}"),
                _finding(f"blocked off-loopback request: {socket}"),
            ),
        )

    def test_each_blocked_request_is_judged_by_its_own_host_in_order(self) -> None:
        also_on = "https://example.org/a.png"
        self.assertEqual(
            self.judged(_blocked(_ON, _OFF, also_on)),
            (
                _warning(f"off-loopback request blocked as expected: {_ON}"),
                _finding(f"blocked off-loopback request: {_OFF}"),
                _warning(f"off-loopback request blocked as expected: {also_on}"),
            ),
        )

    def test_with_no_host_expected_every_blocked_request_is_a_finding(self) -> None:
        self.assertEqual(
            self.judged(_blocked(_ON), expected=()),
            (_finding(f"blocked off-loopback request: {_ON}"),),
        )

    def test_a_state_stopped_before_its_read_back_judges_them_the_same(self) -> None:
        problems = print_judge.state_problems(
            _STATE,
            None,
            _blocked(_ON, _OFF),
            expected_hosts=_EXPECTED,
            color_scheme_emulated=False,
        )
        self.assertEqual(
            problems,
            (
                _warning(f"off-loopback request blocked as expected: {_ON}"),
                _finding(f"blocked off-loopback request: {_OFF}"),
            ),
        )


class ImageProblemTests(_JudgeCase):
    """An image problem keeps its text, and is a warning only on an expected host."""

    def test_an_image_problem_on_an_expected_host_is_a_warning(self) -> None:
        probe = _probe(_incomplete(_ON), _broken("https://example.org/b.png"))
        self.assertEqual(
            self.judged(probe=probe),
            (
                _warning(f"image not complete at print: {_ON}"),
                _warning("image broken: https://example.org/b.png"),
            ),
        )

    def test_an_image_problem_on_any_other_host_is_a_finding(self) -> None:
        probe = _probe(_incomplete(_OFF), _broken(_OFF))
        self.assertEqual(
            self.judged(probe=probe),
            (
                _finding(f"image not complete at print: {_OFF}"),
                _finding(f"image broken: {_OFF}"),
            ),
        )

    def test_a_same_origin_image_problem_is_still_a_finding(self) -> None:
        # A same-origin image is reported by its path, and a path may open with `//`: it
        # names no host, so it is on no expected one.
        path, doubled = "/a.png", "//img.example.org/a.png"
        probe = _probe(_incomplete(path), _broken(doubled))
        self.assertEqual(
            self.judged(probe=probe),
            (
                _finding(f"image not complete at print: {path}"),
                _finding(f"image broken: {doubled}"),
            ),
        )

    def test_an_image_the_browser_could_not_request_is_still_a_finding(self) -> None:
        # The browser never requests a source it cannot parse, and reports it as written:
        # nothing was blocked, so the image is broken on its own account, whatever host
        # its text names.
        src = "https://img.example.org:99999/a.png"
        self.assertEqual(
            self.judged(probe=_probe(_broken(src))),
            (_finding(f"image broken: {src}"),),
        )

    def test_a_loaded_image_on_an_expected_host_is_no_problem(self) -> None:
        probe = _probe(ImageState(_ON, True, "", _LOADED_WIDTH, has_src=True))
        self.assertEqual(self.judged(probe=probe), ())


class NeverRequestedTests(_JudgeCase):
    """An image with no src attribute requested nothing: neither broken nor incomplete.

    A carousel leaves its hidden slides without a source until they are needed, and prints
    only the one showing. Every image that does carry a src attribute, an empty one
    included, is judged exactly as before.
    """

    def test_a_complete_image_with_no_src_and_no_width_is_no_problem(self) -> None:
        probe = _probe(_broken("", has_src=False))
        self.assertEqual(self.judged(probe=probe), ())

    def test_an_incomplete_image_with_no_src_is_no_problem(self) -> None:
        probe = _probe(_incomplete("", has_src=False))
        self.assertEqual(self.judged(probe=probe), ())

    def test_an_empty_src_that_loaded_broken_is_still_a_finding(self) -> None:
        self.assertEqual(
            self.judged(probe=_probe(_broken(""))), (_finding("image broken: "),)
        )

    def test_a_src_still_loading_is_still_a_finding(self) -> None:
        self.assertEqual(
            self.judged(probe=_probe(_incomplete("/a.png"))),
            (_finding("image not complete at print: /a.png"),),
        )

    def test_a_src_that_loaded_broken_is_still_a_finding(self) -> None:
        self.assertEqual(
            self.judged(probe=_probe(_broken("/a.png"))),
            (_finding("image broken: /a.png"),),
        )

    def test_unrequested_slides_beside_a_broken_one_leave_only_its_finding(
        self,
    ) -> None:
        probe = _probe(
            _broken("", has_src=False),
            _broken("/shown.jpg"),
            _incomplete("", has_src=False),
        )
        self.assertEqual(
            self.judged(probe=probe), (_finding("image broken: /shown.jpg"),)
        )

    def test_an_image_record_without_has_src_fails_loudly(self) -> None:
        # Required, never defaulted: an old-shape record is refused at construction,
        # never judged as though it asked for its source.
        build: Any = ImageState
        with self.assertRaisesRegex(TypeError, "has_src"):
            build("/a.png", True, "", 0)


class EverythingElseTests(_JudgeCase):
    """What an expected host does not touch: every other problem, judged as before."""

    def test_other_network_problems_stay_findings(self) -> None:
        network = NetworkRecord(
            failed=("/font.woff2 (net::ERR_FAILED)",),
            http_errors=("404 /missing.png",),
            blocked=(_ON,),
            page_errors=("boom is not defined",),
            csp_violations=(_REFUSED,),
        )
        self.assertEqual(
            self.judged(network),
            (
                _finding("HTTP 404: /missing.png"),
                _finding("request failed: /font.woff2 (net::ERR_FAILED)"),
                _warning(f"off-loopback request blocked as expected: {_ON}"),
                _finding("page error: boom is not defined"),
                _finding(f"csp violation: {_REFUSED}"),
            ),
        )

    def test_the_fonts_and_emulated_dark_warnings_stand(self) -> None:
        dark = State("dark", "dark", "native", (), (), None)
        probe = ProbeResult(
            color_scheme="dark",
            reduced_motion="no-preference",
            ready_state="complete",
            fonts_status="loading",
            title="Sample",
            images=(),
            animations=(),
            reads=(),
            storage=(),
        )
        problems = print_judge.state_problems(
            dark, probe, _QUIET, expected_hosts=_EXPECTED, color_scheme_emulated=True
        )
        self.assertEqual(
            problems,
            (
                _warning("fonts not loaded at print: loading"),
                _warning(print_judge.EMULATED_DARK_WARNING),
            ),
        )


class CspViolationTests(_JudgeCase):
    """Each violation a page's policy reported is a finding, whatever host it names."""

    def test_each_violation_is_a_finding_in_the_order_it_arrived(self) -> None:
        image = "img-src blocked https://example.net/x.png on /about/"
        network = dataclasses.replace(
            _QUIET, csp_violations=(_REFUSED, image, _REFUSED)
        )
        self.assertEqual(
            self.judged(network),
            (
                _finding(f"csp violation: {_REFUSED}"),
                _finding(f"csp violation: {image}"),
                _finding(f"csp violation: {_REFUSED}"),
            ),
        )

    def test_a_violation_naming_an_expected_host_is_still_a_finding(self) -> None:
        # The page's own policy refused what the site is known to load: the policy and the
        # page disagree, which an expected host does not excuse.
        refused = f"img-src blocked {_ON} on /"
        network = dataclasses.replace(_QUIET, csp_violations=(refused,))
        self.assertEqual(self.judged(network), (_finding(f"csp violation: {refused}"),))

    def test_a_state_stopped_before_its_read_back_still_has_them_judged(self) -> None:
        network = dataclasses.replace(_QUIET, csp_violations=(_REFUSED,))
        problems = print_judge.state_problems(
            _STATE,
            None,
            network,
            expected_hosts=_EXPECTED,
            color_scheme_emulated=False,
        )
        self.assertEqual(problems, (_finding(f"csp violation: {_REFUSED}"),))


class WarningTextTests(_JudgeCase):
    """The warnings about how a print looks claim only what the tool measured.

    Each text is pinned whole here, its specification; every other suite reads the judge's
    own definition, so the text is written once in the source.
    """

    def test_running_animations_are_one_warning_naming_what_the_screen_ran(
        self,
    ) -> None:
        # The probe reads the SCREEN's animations: a print stylesheet may show each one
        # finished, so the warning says how to check rather than what the paper caught.
        running = (
            RunningAnimation("spin", "div.spinner"),
            RunningAnimation("fade", "p"),
        )
        probe = dataclasses.replace(_probe(), animations=running)
        text = (
            "2 animation(s) running on screen at print (spin, fade): a print stylesheet"
            " may still show them finished; compare with a reduced-motion state"
        )
        self.assertEqual(self.judged(probe=probe), (_warning(text),))
        self.assertEqual(print_judge.running_animations_warning(("spin", "fade")), text)

    def test_the_emulated_dark_warning_claims_a_difference_only_where_one_is(
        self,
    ) -> None:
        # An emulated-dark PDF can print exactly as a light one does, where the page's
        # print styles force light: only where the two differ is it unlike a reader's.
        self.assertEqual(
            print_judge.EMULATED_DARK_WARNING,
            "EMULATED dark: Playwright's emulated color scheme persists into page.pdf(),"
            " while a real dark OS prints light (Chrome 153, measured); where this PDF"
            " differs from a light print, no reader's print looks like it",
        )


if __name__ == "__main__":
    unittest.main()
