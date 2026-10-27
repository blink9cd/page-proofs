"""Grouped suite: the LIVE run beside the sample -- requests off loopback -- and the budget.

The live case here serves a site on loopback and runs the tool over it on the real engine:
one page that reaches off loopback for an image and for two WebSockets, under a policy
refusing its one style attribute, runs motion in shadow trees, and holds a carousel of lazy
slides clipped far outside the window. All of it shares that page, and its cases share one
run of it, because every live run's bound is spent from the aggregator's own ten-minute
budget: a run apiece would spend a bound each on what one run shows. The run is a
process of its own, killed at a bound, so a hang fails the cases that read it rather than
hanging the suite. Nothing it fetches off loopback names a real host: an RFC 6761 `.invalid`
name or an RFC 5737 documentation address, so even with every guard gone a request reaches
no one. Installed Chrome's run is `test_print_pass_live_chrome.py`'s. Beside them sit the
checks that need no browser: a run past its bound fails its case; each run's bound follows
its own timeout, and every live run's bound together fits the aggregator's ten-minute
budget; the installed packages are the ones the `print` extra pins, which the `test`
extra repeats; the sample README's command writes the image the suites serve; and a live
case skips with a notice, or with the opt-in fails, where poppler or the engine is absent.

The live case skips where poppler or the headless shell Playwright pins is not installed,
naming the install, or with `PAGE_PROOFS_REQUIRE_BROWSERS=1` fails; the rest run regardless.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.metadata
import io
import os
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import ClassVar, Final, cast
from unittest import mock

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
from _print_pass_live_fixtures import (
    CAROUSEL_FINDINGS,
    CAROUSEL_HTML,
    CHROME_OPTIONS,
    CHROME_TARGETS,
    EXIT_FINDINGS,
    MISSING_SLIDE,
    NOT_COMPLETE,
    PIXEL_NAME,
    PYTHON_TIMEOUT_SECONDS,
    REPORT_NAME,
    REQUIRE_BROWSERS_ENV,
    SAMPLE_README,
    SHADOW_SCRIPT,
    SKIP_NOTICE,
    TOOL_DIR,
    absent,
    at,
    builds_required,
    chrome_run_bound_seconds,
    load_json,
    make_run_process,
    pinned_shell,
    pixel_png,
    redirect_run_bound_seconds,
    problems,
    require_live_engine,
    require_poppler,
    run_bound_seconds,
    sample_run_bound_seconds,
    served_pages,
    state_entry,
)

from _gecko_live_fixtures import FIREFOX_RUN_BOUND_SECONDS  # this folder's, as above
from _print_pass_fixtures import Outcome  # this folder's fixtures, as above
import bounded_run  # the shared runner, a folder beside the tool's, via the CLI's own insert
import print_pass  # the CLI, via the fixtures' insert
import print_preflight  # the tool's private src/, as above
from print_judge import running_animations_warning  # the tool's private src/
from print_targets import adhoc_targets, parse_targets  # as above

#: An image off loopback, at a name that can never resolve.
_IMAGE_URL: Final = "https://print-pass.invalid/x.png"

#: The violation the page's policy reports, as the collector describes it: the page is
#: served at `/`.
_REFUSED_STYLE: Final = "style-src-attr blocked inline on /"

#: Two WebSockets off loopback: a documentation address, and a name that can never resolve.
_SOCKET_URLS: Final = ("ws://203.0.113.9/", "wss://print-pass.invalid/")

#: The warning the open roots' motion draws; the closed root's is out of the page's reach.
_SHADOW_WARNING: Final = (
    "warning",
    running_animations_warning(("shadow-open", "shadow-nested")),
)

#: One page reaching off loopback for both: the image, and the sockets its script opens,
#: under a policy refusing its one style attribute. The policy names styles alone, so the
#: image and the sockets are still requested, and blocked by the routes, not by the policy.
_OFF_LOOPBACK_PAGE: Final = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="style-src 'self'">
<title>off loopback</title></head>
<body><p style="color: red">An image and two sockets off loopback.</p>
<img src="{_IMAGE_URL}" alt="off loopback" width="16" height="16">
<script>const sockets = {list(_SOCKET_URLS)!r}.map((url) => new WebSocket(url));</script>
<script>{SHADOW_SCRIPT}</script>
{CAROUSEL_HTML}</body></html>
"""

#: What the image fails its state with: it never arrives, and its request was blocked.
_IMAGE_FINDINGS: Final = (
    ("finding", f"image broken: {_IMAGE_URL}"),
    ("finding", f"blocked off-loopback request: {_IMAGE_URL}"),
)

#: What each socket fails its state with, in the order of `_SOCKET_URLS`.
_SOCKET_FINDINGS: Final = tuple(
    ("finding", f"blocked off-loopback request: {url}") for url in _SOCKET_URLS
)

#: What the refused style attribute fails its state with: the collector heard it.
_CSP_FINDING: Final = ("finding", f"csp violation: {_REFUSED_STYLE}")

#: The network record's lists the page leaves empty: a blocked request is not a failed one,
#: and nothing on the page throws.
_EMPTY_NETWORK_LISTS: Final = ("failed", "page_errors")

#: The one error answer the page draws: the carousel's missing slide.
_HTTP_ERRORS: Final = [f"404 {MISSING_SLIDE}"]

#: The README's command that writes the sample's image: a program run with `-c`, its
#: output sent to `pixel.png`.
_PIXEL_COMMAND: Final = re.compile(r"-c '([^']*)' > \"\$SITE/pixel\.png\"")

#: A page with nothing to fetch, and a bound no run can meet: a millisecond.
_PLAIN_PAGE: Final = "<!doctype html><title>plain</title><p>Nothing to fetch.</p>\n"
_NO_TIME: Final = 0.001

#: How many times the default `--timeout` a run's bound is checked to scale with.
_TIMEOUT_SCALE: Final = 3

#: The milliseconds in a second: the report's timings are milliseconds.
_MS_PER_SECOND: Final = 1000

#: Bound on a run given no targets file: the CLI prints its default page, in its one
#: default state.
_PAGE_RUN_BOUND_SECONDS: Final = run_bound_seconds(
    adhoc_targets([print_pass.DEFAULT_PAGE]),
    timeout=print_pass.DEFAULT_TIMEOUT_SECONDS,
)

#: The aggregator's own budget: ten minutes for the one process that runs every suite of
#: this tool, the live ones included, so that a CI or coverage timeout of that length still
#: sees each hung case fail by name. A runner that bounds it tighter must change this.
_GATE_UNIT_TIMEOUT_SECONDS: Final = 600.0

#: What the aggregator's other suites are left of that bound. Measured under coverage:
#: about 110 s of a 133 s run at a load average near 160, and 8 to 32 s at loads of 29 to
#: 53; re-measured after the suites grew (the 17 shadowing interpreters, the PDF worker's
#: isolation and memory runs, the per-page renders), 25 s for 1,415 tests at a load near 9.
#: No high load was at hand to re-measure, so the room stays a quarter again the longest
#: figure, and has no slack left: a suite that grows must shorten a live bound first. The
#: room also carries bounds no live entry counts -- the canary proofs, the shim's
#: behavioral run, the shadowing interpreters, and the Firefox class's process listings and
#: its watcher's join -- each measured under a second, so a stall there spends the room,
#: not a live bound.
_OTHER_SUITES_ROOM_SECONDS: Final = 140.0

#: The one live run the budget leaves out: the bound test's own, given no time at all.
_UNBUDGETED_RUNS: Final = 1


def _live_run_bounds() -> dict[str, float]:
    """Each live run the live suites make, named by its case, and the bound it runs at."""
    return {
        "the sample": sample_run_bound_seconds(),
        "the page off loopback": _PAGE_RUN_BOUND_SECONDS,
        "Chrome's native dark": chrome_run_bound_seconds(),
        "the redirect stub": redirect_run_bound_seconds(),
        "the fenced Firefox run": FIREFOX_RUN_BOUND_SECONDS,
    }


def _make_run_calls() -> int:
    """How many direct calls to `make_run_process` the suites here hold, in source: sites."""
    suites = Path(__file__).resolve().parent.glob("test_*.py")
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == make_run_process.__name__
        for suite in suites
        for node in ast.walk(ast.parse(suite.read_text(encoding="utf-8")))
    )


class _LiveCase(unittest.TestCase):
    """A temp folder outside the repository, removed after the case."""

    def setUp(self) -> None:
        """Make the folder a case serves its site from and writes its run into."""
        folder = tempfile.TemporaryDirectory(prefix="print_pass-live-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.out = self.root / "out"


class OffLoopbackTest(unittest.TestCase):
    """What one page reaches for off loopback is blocked, reported, and never sent.

    The page asks for an image and opens two WebSockets, all off loopback, under a policy
    refusing its one style attribute, runs motion in shadow trees, holds a carousel of lazy
    slides, and is run once for every case here: a run per case would spend a live bound
    apiece on what one run shows. Each case reads its own part of that run's report, and one
    reads the whole problem list, so that nothing beside the image, the sockets, the refused
    style and the missing slide fails the state, and only the open roots' motion warns.
    """

    outcome: ClassVar[Outcome]
    state: ClassVar[dict[str, object]]

    @classmethod
    def setUpClass(cls) -> None:
        """Serve the page and run the tool over it once, in its one default state.

        The folder is removed by a class cleanup, which runs even when this raises. A
        socket route's handler that deadlocks the synchronous API spins its dispatcher
        forever, outside every bound the run keeps: the run's own process is killed at
        `_PAGE_RUN_BOUND_SECONDS`, and the class fails naming it, before any case runs.
        Without poppler or the real engine, it skips every case here, naming the install.
        """
        require_live_engine()
        folder = tempfile.TemporaryDirectory(prefix="print_pass-live-")
        cls.addClassCleanup(folder.cleanup)
        root = Path(folder.name)
        out = root / "out"
        site = served_pages(root, {"index.html": _OFF_LOOPBACK_PAGE})
        (site / PIXEL_NAME).write_bytes(pixel_png())
        cls.outcome = make_run_process(site, out, timeout=_PAGE_RUN_BOUND_SECONDS)
        report_file = out / REPORT_NAME
        if not report_file.is_file():
            raise RuntimeError(
                f"the run wrote no report (exit {cls.outcome.code}):\n"
                f"{cls.outcome.stderr}"
            )
        cls.state = state_entry(load_json(report_file), "default")

    def test_a_blocked_image_fails_its_state(self) -> None:
        """The image's request is blocked, reported as two findings, and fails the run."""
        self.assertEqual(self.outcome.code, EXIT_FINDINGS, self.outcome.stderr)
        self.assertEqual(at(self.state, "engine"), "shell")
        found = problems(self.state)
        for image_finding in _IMAGE_FINDINGS:
            self.assertIn(image_finding, found)
        # Blocked, not failed: a request that reached the resolver would fail by name.
        network = cast(dict[str, list[str]], at(self.state, "network"))
        self.assertIn(_IMAGE_URL, network["blocked"])
        for kind in _EMPTY_NETWORK_LISTS:
            self.assertEqual(network[kind], [], kind)
        self.assertEqual(network["http_errors"], _HTTP_ERRORS)

    def test_a_refused_style_is_a_csp_violation_finding(self) -> None:
        """The style attribute the page's policy refuses is heard, recorded and a finding.

        The collector hears it from the first load, so the record lists exactly the one
        violation, by the collector's own line, and the judge makes it a finding.
        """
        self.assertIn(_CSP_FINDING, problems(self.state))
        self.assertEqual(at(self.state, "network", "csp_violations"), [_REFUSED_STYLE])

    def test_an_off_loopback_websocket_is_reported_without_hanging(self) -> None:
        """Both sockets are blocked and reported, and the page printed inside the timeout.

        The socket route records each socket among the blocked requests, as the request
        route records the image, so each is a finding of the same text.
        """
        found = problems(self.state)
        blocked = cast(list[str], at(self.state, "network", "blocked"))
        for url, socket_finding in zip(_SOCKET_URLS, _SOCKET_FINDINGS, strict=True):
            with self.subTest(socket=url):
                self.assertIn(socket_finding, found)
                self.assertIn(url, blocked)
        printed_at_ms = cast(int, at(self.state, "timings", "printed_at_ms"))
        self.assertLess(
            printed_at_ms, print_pass.DEFAULT_TIMEOUT_SECONDS * _MS_PER_SECOND
        )

    def test_the_states_problems_are_exactly_the_pages_own(self) -> None:
        """The state's problems are the image's, sockets', style's and slide's, and one warning.

        The warning is the open shadow roots' motion; a warning does not fail the state.

        Compared in any order: the request route, the socket route and the collector each
        record what they are handed as it arrives, and nothing orders the image's request
        against the sockets.
        """
        self.assertCountEqual(
            problems(self.state),
            [
                *_IMAGE_FINDINGS,
                *_SOCKET_FINDINGS,
                _CSP_FINDING,
                *CAROUSEL_FINDINGS,
                _SHADOW_WARNING,
            ],
        )

    def test_lazy_slides_far_outside_the_window_arrive_before_the_print(self) -> None:
        """Every lazy slide clipped off the window is complete, and the missing one broken.

        The engine widens Chromium's lazy margin, so each slide loads as an eager image
        would: none is read still loading, and the one no site serves is still a finding,
        fetched and answered 404, so broken.
        """
        found = problems(self.state)
        self.assertEqual([p for p in found if str(p[1]).startswith(NOT_COMPLETE)], [])
        for slide_finding in CAROUSEL_FINDINGS:
            self.assertIn(slide_finding, found)

    def test_motion_in_an_open_shadow_root_is_warned_of(self) -> None:
        """An open root's animation, nested or not, is named in the warning; a closed one's is not.

        `document.getAnimations()` lists none of them in any engine, so the probe walks every
        open root; a closed root is out of the page's reach, the documented limit.
        """
        self.assertIn(_SHADOW_WARNING, problems(self.state))


class RunBoundTest(_LiveCase):
    """A live run is a process of its own, so a run that outlives its bound fails its case."""

    def test_a_run_past_its_bound_fails_its_case_naming_the_bound(self) -> None:
        """A run given no time at all is ended, and the case fails with the bound named.

        No interpreter starts, let alone prints, inside `_NO_TIME`, so the run is past its
        bound every time: the same outcome a run that hangs meets at a real bound.
        """
        site = served_pages(self.root, {"index.html": _PLAIN_PAGE})
        with self.assertRaisesRegex(AssertionError, f"did not end within {_NO_TIME} s"):
            make_run_process(site, self.out, timeout=_NO_TIME)

    def test_a_runs_bound_follows_the_timeout_it_is_given(self) -> None:
        """A run given a larger `--timeout` is given a larger bound, in proportion.

        The bound counts the run's own timeouts, so a case that raises `--timeout` raises
        its bound with it, rather than keeping the one the default timeout gives.
        """
        targets = adhoc_targets([print_pass.DEFAULT_PAGE])
        default = print_pass.DEFAULT_TIMEOUT_SECONDS
        self.assertEqual(
            run_bound_seconds(targets, timeout=_TIMEOUT_SCALE * default),
            _TIMEOUT_SCALE * run_bound_seconds(targets, timeout=default),
        )

    def test_the_chrome_run_is_bounded_at_its_raised_timeout(self) -> None:
        """The Chrome case passes a `--timeout` above the default, and its bound follows it.

        Installed Chrome's close takes no timeout of its own, so the engine bounds it at
        twice the run's `--timeout`, and a loaded run's two closes spend much of its outer
        bound: the case raises that setting, the tool's own for a slow host, and counts its
        outer bound at the timeout its run is really given.
        """
        self.assertIn("--timeout", CHROME_OPTIONS)
        timeout = float(CHROME_OPTIONS[CHROME_OPTIONS.index("--timeout") + 1])
        self.assertGreater(timeout, print_pass.DEFAULT_TIMEOUT_SECONDS)
        chrome_targets = parse_targets(tomllib.loads(CHROME_TARGETS))
        self.assertEqual(
            chrome_run_bound_seconds(),
            run_bound_seconds(chrome_targets, timeout=timeout),
        )


class RunBudgetTest(unittest.TestCase):
    """Every live run's bound, together, leaves the aggregator inside its ten-minute budget."""

    def test_the_live_bounds_and_the_other_suites_fit_the_budget(self) -> None:
        """Were every live run to hang at once, each would still fail its own case.

        A CI or coverage timeout of ten minutes on the one process that runs every suite of
        this tool would, were the live bounds past what the other suites leave of it, end the
        whole run, with no case named, before the last of them ran out.
        """
        bounds = _live_run_bounds()
        self.assertLessEqual(
            sum(bounds.values()) + _OTHER_SUITES_ROOM_SECONDS,
            _GATE_UNIT_TIMEOUT_SECONDS,
            bounds,
        )

    def test_the_budget_counts_every_live_run(self) -> None:
        """Each call site that makes a live run, in any suite here, has its bound counted.

        It counts the direct calls to `make_run_process` in the suites' source, not the runs
        they make: a call in a loop, or behind a helper of its own, is one site however many
        runs it makes, and escapes this count, so its runs must be counted by hand.
        """
        self.assertEqual(
            _make_run_calls(),
            len(_live_run_bounds()) + _UNBUDGETED_RUNS,
            "every make_run_process call site needs its run's bound in _live_run_bounds(),"
            " or a place in _UNBUDGETED_RUNS",
        )


class SampleFactsTest(unittest.TestCase):
    """What the live runs rest on that needs no browser: the pins, and the sample's image."""

    def test_installed_versions_match_the_print_pin(self) -> None:
        """Each `print` pin is exact, installed, repeated by `test`, and has `browser`'s."""
        # The repository root's, the folder this tool's own folder sits in.
        with (TOOL_DIR.parent / "pyproject.toml").open("rb") as handle:
            extras = tomllib.load(handle)["project"]["optional-dependencies"]
        pins = extras["print"]
        self.assertLessEqual(set(extras["browser"]), set(pins))
        for pin in pins:
            name, exact, version = cast(str, pin).partition("==")
            with self.subTest(package=name):
                self.assertEqual(exact, "==")
                self.assertEqual(importlib.metadata.version(name), version)
                self.assertIn(pin, extras["test"])

    def test_the_readme_writes_the_image_the_suites_serve(self) -> None:
        """The README's one-line command writes exactly the PNG the live suites write."""
        readme = SAMPLE_README.read_text(encoding="utf-8")
        match = _PIXEL_COMMAND.search(readme)
        self.assertIsNotNone(match, "the README holds no command writing pixel.png")
        program = cast(re.Match[str], match).group(1)
        answer = bounded_run.run_bounded_bytes(
            [sys.executable, "-I", "-c", program], timeout=PYTHON_TIMEOUT_SECONDS
        )
        self.assertEqual(answer.returncode, 0, answer.stderr)
        self.assertEqual(answer.stdout, pixel_png())


class LiveSkipTest(unittest.TestCase):
    """Without poppler or the engine a live case skips with a notice, or fails if required."""

    def setUp(self) -> None:
        """Clear the opt-in and Playwright's variable, restored after the case."""
        patched = mock.patch.dict(os.environ)
        patched.start()
        self.addCleanup(patched.stop)
        os.environ.pop(REQUIRE_BROWSERS_ENV, None)
        os.environ.pop(print_preflight.BROWSERS_ENV, None)

    def test_the_opt_in_is_off_unset_or_empty_on_at_1_and_refused_otherwise(
        self,
    ) -> None:
        self.assertFalse(builds_required())
        for value, on in (("", False), ("1", True)):
            os.environ[REQUIRE_BROWSERS_ENV] = value
            self.assertIs(builds_required(), on)
        os.environ[REQUIRE_BROWSERS_ENV] = "yes"
        with self.assertRaisesRegex(RuntimeError, "is 'yes': set it to 1"):
            builds_required()

    def test_absent_skips_with_one_line_on_stderr_or_fails_when_required(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertIsInstance(absent("x missing"), unittest.SkipTest)
            os.environ[REQUIRE_BROWSERS_ENV] = "1"
            self.assertIsInstance(absent("x missing"), RuntimeError)
        self.assertEqual(stderr.getvalue(), f"{SKIP_NOTICE}x missing\n")

    def test_missing_poppler_or_shell_skips_naming_the_install(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with mock.patch.object(
                print_preflight, "missing_poppler", return_value="p"
            ):
                self.assertRaisesRegex(unittest.SkipTest, "^p$", require_poppler)
            with (
                mock.patch.object(
                    print_preflight, "missing_poppler", return_value=None
                ),
                mock.patch.object(Path, "is_dir", return_value=False),
            ):
                os.environ[print_preflight.BROWSERS_ENV] = "nowhere"
                with self.assertRaisesRegex(unittest.SkipTest, "--only-shell chromium"):
                    require_live_engine()
                with mock.patch("importlib.util.find_spec", return_value=None):
                    self.assertRaisesRegex(
                        RuntimeError, "not installed", require_live_engine
                    )

    def test_the_shell_is_looked_for_where_the_variable_points(self) -> None:
        os.environ[print_preflight.BROWSERS_ENV] = "builds"
        self.assertEqual(pinned_shell().parent, Path("builds"))
        # 0 is Playwright's hermetic mode: the builds inside its own installed package.
        os.environ[print_preflight.BROWSERS_ENV] = "0"
        self.assertEqual(pinned_shell().parent.name, ".local-browsers")


if __name__ == "__main__":
    unittest.main()
