"""Grouped suite: a page with no file, answered with the site's own 404 page (`not_found`).

The targets file's key and its refusals, which served pages have no file, and the one load such a
page is excused: its own document answering 404, as the engines report it (the response, and
the console line Chromium and WebKit echo it as). Everything else stays a finding: a subresource
the page cannot have, the `--prove` plant among them, any other status, any other document, and
the same answer a second time. Responses and console lines are stand-ins carrying what
Playwright hands over; the engine's wiring goes through `_csp_fixtures.py`'s scripted browser,
and the real engines report through it in `test_pass_not_found_live.py`.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ on sys.path
# for every import after it.
from _csp_fixtures import BASE_URL, OFF, ON, TIMEOUT, FakeBrowser, Script

import contextlib
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pass_checks
import pass_engine
import pass_listeners
import pass_probe
import pass_report
import pass_shift
import pass_targets
from pass_motion import NO_MOTION
from pass_targets import Rendering, State

#: A page with no file as a targets file names it, with the query and fragment it is loaded with.
_MISSING = "/future?from=test#top"

#: The url its document's response carries: the fragment never leaves the browser.
_DOCUMENT_URL = f"{BASE_URL}/future?from=test"

#: A minimal valid targets document, as `tomllib` would hand it over.
_VALID: dict[str, object] = {
    "pages": ["/", _MISSING],
    "viewport": [{"width": 390, "height": 844}],
}

#: A layout record with nothing in it, for reading what the listeners collected.
_NO_LAYOUT = {
    "overflow": 0,
    "pastRightEdge": [],
    "hiddenRendered": [],
    "smallTargets": [],
}


def _response(
    url: str, *, status: int = 404, navigation: bool = True, main_frame: bool = True
) -> mock.Mock:
    """A response as Playwright hands it over: its url, its status and the request it answers."""
    response = mock.Mock(url=url, status=status)
    response.request.is_navigation_request.return_value = navigation
    response.request.frame.parent_frame = None if main_frame else mock.Mock()
    return response


def _console(
    url: str,
    *,
    text: str = pass_listeners.NOT_FOUND_ECHO,
    line: int = 0,
    column: int = 0,
    kind: str = "error",
) -> mock.Mock:
    """A console line as Playwright hands it over: its type, its text and where it was logged."""
    message = mock.Mock(type=kind, text=text)
    message.location = {"url": url, "lineNumber": line, "columnNumber": column}
    return message


class _Listening:
    """`Listeners` on a stand-in page, with a way to report to each handler it attached."""

    def __init__(self, missing: str | None) -> None:
        """Attach `Listeners` for a page that is `missing` (its page) or has a file (None)."""
        page = mock.Mock()
        self.listeners = pass_listeners.Listeners(page, BASE_URL, missing=missing)
        self._handlers = {call.args[0]: call.args[1] for call in page.on.call_args_list}

    def report(self, event: str, *payloads: object) -> None:
        """Hand each of `payloads` to the handler for `event`, as the engine would."""
        for payload in payloads:
            self._handlers[event](payload)

    def observation(self) -> pass_checks.Observation:
        """What the listeners collected so far, as the detectors read it."""
        return self.listeners.observation(_NO_LAYOUT)


class NotFoundKeyTests(unittest.TestCase):
    """The targets file's `not_found`: one server path, or nothing."""

    def test_reads_a_not_found_path_and_its_absence(self) -> None:
        named = pass_targets.parse_targets({**_VALID, "not_found": "/404.html"})
        self.assertEqual(named.not_found, "/404.html")
        self.assertIsNone(pass_targets.parse_targets(_VALID).not_found)

    def test_refuses_a_not_found_that_is_not_one_server_path(self) -> None:
        for bad in ("404.html", "//404.html", "", 404, ["/404.html"]):
            with (
                self.subTest(bad=bad),
                self.assertRaisesRegex(
                    pass_targets.TargetsError, "not_found must be a server path"
                ),
            ):
                pass_targets.parse_targets({**_VALID, "not_found": bad})


class MissingPagesTests(unittest.TestCase):
    """Which served pages have no file: judged by server path, and only with a `not_found`."""

    def test_every_page_and_state_page_is_served_once_by_its_server_path(self) -> None:
        state = State(name="opened", page="/future?from=state", storage=(), actions=())
        targets = replace(
            pass_targets.parse_targets(
                {**_VALID, "pages": ["/", _MISSING, "/#latest"]}
            ),
            states=(state,),
        )
        self.assertEqual(pass_targets.served_pages(targets), ("/", "/future"))

    def test_without_not_found_no_page_is_missing_and_no_file_is_looked_for(
        self,
    ) -> None:
        def has_file(page: str) -> bool:
            raise AssertionError(f"looked for the file of {page}")

        targets = pass_targets.parse_targets(_VALID)
        self.assertEqual(pass_targets.missing_pages(targets, has_file), frozenset())

    def test_with_not_found_the_pages_with_no_file_are_missing(self) -> None:
        asked: list[str] = []

        def has_file(page: str) -> bool:
            asked.append(page)
            return page == "/"

        targets = pass_targets.parse_targets({**_VALID, "not_found": "/404.html"})
        self.assertEqual(
            pass_targets.missing_pages(targets, has_file), frozenset({"/future"})
        )
        self.assertEqual(asked, ["/", "/future"])


class ExcusedResponseTests(unittest.TestCase):
    """The response excused: the missing page's own document, answering 404, once."""

    def test_the_missing_page_s_own_404_is_no_http_error(self) -> None:
        listening = _Listening(_MISSING)
        listening.report("response", _response(_DOCUMENT_URL))
        self.assertEqual(listening.observation().http_errors, ())

    def test_it_is_excused_once(self) -> None:
        listening = _Listening(_MISSING)
        listening.report("response", _response(_DOCUMENT_URL), _response(_DOCUMENT_URL))
        self.assertEqual(
            listening.observation().http_errors, ("404 /future?from=test",)
        )

    def test_a_subresource_it_cannot_have_is_still_one(self) -> None:
        listening = _Listening(_MISSING)
        listening.report(
            "response",
            _response(_DOCUMENT_URL),
            _response(f"{BASE_URL}/gone.png", navigation=False),
        )
        self.assertEqual(listening.observation().http_errors, ("404 /gone.png",))

    def test_a_subresource_at_the_page_s_own_path_is_still_one(self) -> None:
        # Only the navigation is the document: a fetch of the same url is a request the page
        # made, and it neither is excused nor spends the document's one excuse.
        listening = _Listening(_MISSING)
        listening.report("response", _response(_DOCUMENT_URL, navigation=False))
        self.assertEqual(
            listening.observation().http_errors, ("404 /future?from=test",)
        )
        listening.report("response", _response(_DOCUMENT_URL))
        self.assertEqual(
            listening.observation().http_errors, ("404 /future?from=test",)
        )

    def test_the_prove_plant_is_still_caught(self) -> None:
        # The plant asks for a path no site serves, and a not-found page still answers it 404.
        listening = _Listening(_MISSING)
        listening.report("response", _response(_DOCUMENT_URL))
        before = listening.observation()
        plant = f"{BASE_URL}{pass_checks.MISSING_PLANT_PATH}"
        listening.report("response", _response(plant, navigation=False))
        verdicts = pass_checks.plant_verdicts(
            before,
            listening.observation(),
            dict.fromkeys(pass_checks.DETECTORS, True),
            scripts_on=True,
            csp_caught=True,
        )
        self.assertEqual(verdicts["http_error"], "caught")

    def test_its_document_answering_any_other_error_is_still_one(self) -> None:
        for status in (410, 500):
            with self.subTest(status=status):
                listening = _Listening(_MISSING)
                listening.report("response", _response(_DOCUMENT_URL, status=status))
                self.assertEqual(
                    listening.observation().http_errors,
                    (f"{status} /future?from=test",),
                )

    def test_a_document_in_a_child_frame_is_still_one(self) -> None:
        listening = _Listening(_MISSING)
        listening.report("response", _response(_DOCUMENT_URL, main_frame=False))
        self.assertEqual(
            listening.observation().http_errors, ("404 /future?from=test",)
        )

    def test_another_document_is_still_one(self) -> None:
        elsewhere = "http://198.51.100.7/future?from=test"
        listening = _Listening(_MISSING)
        listening.report(
            "response", _response(f"{BASE_URL}/elsewhere"), _response(elsewhere)
        )
        self.assertEqual(
            listening.observation().http_errors, ("404 /elsewhere", f"404 {elsewhere}")
        )

    def test_a_page_with_a_file_is_excused_nothing(self) -> None:
        listening = _Listening(None)
        listening.report("response", _response(_DOCUMENT_URL))
        listening.report("console", _console(f"{_DOCUMENT_URL}#top"))
        observation = listening.observation()
        self.assertEqual(observation.http_errors, ("404 /future?from=test",))
        self.assertEqual(observation.console_errors, (pass_listeners.NOT_FOUND_ECHO,))

    def test_a_response_below_400_is_no_http_error(self) -> None:
        listening = _Listening(None)
        listening.report("response", _response(_DOCUMENT_URL, status=304))
        self.assertEqual(listening.observation().http_errors, ())


class ExcusedEchoTests(unittest.TestCase):
    """The console line excused: the engine's echo of that same 404, once."""

    def test_the_echo_of_the_missing_page_s_own_404_is_no_console_error(self) -> None:
        listening = _Listening(_MISSING)
        listening.report("console", _console(f"{_DOCUMENT_URL}#top"))
        self.assertEqual(listening.observation().console_errors, ())

    def test_it_is_excused_once(self) -> None:
        echo = _console(f"{_DOCUMENT_URL}#top")
        listening = _Listening(_MISSING)
        listening.report("console", echo, echo)
        self.assertEqual(
            listening.observation().console_errors, (pass_listeners.NOT_FOUND_ECHO,)
        )

    def test_any_other_line_or_place_is_still_one(self) -> None:
        others = {
            "a subresource's echo": _console(f"{BASE_URL}/gone.png"),
            "other words": _console(_DOCUMENT_URL, text="Uncaught Error: boom"),
            "a line of the page": _console(_DOCUMENT_URL, line=3),
            "a column of the page": _console(_DOCUMENT_URL, column=5),
        }
        for what, message in others.items():
            with self.subTest(what):
                listening = _Listening(_MISSING)
                listening.report("console", message)
                self.assertEqual(
                    listening.observation().console_errors, (message.text,)
                )

    def test_a_line_that_is_not_an_error_is_not_collected(self) -> None:
        listening = _Listening(None)
        listening.report("console", _console(BASE_URL, kind="log"))
        self.assertEqual(listening.observation().console_errors, ())


class OtherReportsTests(unittest.TestCase):
    """What else the listeners collect, as they did before a page could be missing."""

    def test_an_uncaught_error_and_a_failed_request_are_collected(self) -> None:
        listening = _Listening(_MISSING)
        error = mock.Mock(message="boom")
        error.name = "TypeError"
        failed = mock.Mock(url=f"{BASE_URL}/late.js", failure="net::ERR_ABORTED")
        listening.report("pageerror", error)
        listening.report("requestfailed", failed)
        observation = listening.observation()
        self.assertEqual(observation.page_errors, ("TypeError: boom",))
        self.assertEqual(
            observation.http_errors, ("failed /late.js (net::ERR_ABORTED)",)
        )


class EngineWiringTests(unittest.TestCase):
    """The engine tells each rendering's listeners whether its page is a missing one."""

    def _listened_for(self, rendering: Rendering, *, missing: bool) -> list[object]:
        """What `pass_engine.render` told the listeners it made was missing."""
        told: list[object] = []
        made = pass_listeners.Listeners

        def recording(*args: object, **options: object) -> pass_listeners.Listeners:
            told.append(options.get("missing"))
            return made(*args, **options)  # type: ignore[arg-type] # passed straight through

        with (
            mock.patch.object(pass_engine, "Listeners", recording),
            mock.patch.object(pass_probe, "SETTLE_QUIET_SECONDS", 0.0),
        ):
            pass_engine.render(
                FakeBrowser(Script()),  # type: ignore[arg-type] # a scripted stand-in
                rendering,
                BASE_URL,
                timeout=TIMEOUT,
                prove=False,
                missing=missing,
            )
        return told

    def test_render_names_the_page_to_its_listeners_only_when_it_is_missing(
        self,
    ) -> None:
        rendering = replace(OFF, page=_MISSING)
        self.assertEqual(self._listened_for(rendering, missing=True), [_MISSING])
        self.assertEqual(self._listened_for(rendering, missing=False), [None])

    def test_run_matrix_finds_each_rendering_s_page_among_the_missing_ones(
        self,
    ) -> None:
        told: list[bool] = []

        def recording_render(
            browser: object, rendering: Rendering, *_args: object, **options: object
        ) -> pass_report.RenderingResult:
            told.append(bool(options["missing"]))
            late = pass_shift.LateShift(pass_shift.NOT_APPLICABLE)
            return pass_report.RenderingResult(
                rendering, (), {}, {}, {}, late, NO_MOTION
            )

        renderings = [replace(ON, page=_MISSING), replace(ON, page="/")]
        with (
            mock.patch.object(
                pass_engine,
                "sync_playwright",
                lambda: contextlib.nullcontext(mock.Mock()),
            ),
            mock.patch.object(pass_engine, "launch", return_value=mock.Mock()),
            mock.patch.object(pass_engine, "render", recording_render),
        ):
            pass_engine.run_matrix(
                renderings,
                BASE_URL,
                timeout=TIMEOUT,
                prove=False,
                scratch=Path("unused"),
                progress=lambda _result: None,
                missing_pages=frozenset({"/future"}),
            )
        self.assertEqual(told, [True, False])


if __name__ == "__main__":
    unittest.main()
