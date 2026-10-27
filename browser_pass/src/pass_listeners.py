"""What the browser reports while a page loads, and the one answer a page with no file is excused.

`Listeners` collects every console error, uncaught page error, HTTP >= 400 response and failed
request from the first byte, for the page-load detectors (`pass_engine`).

A targets file's `not_found` serves a tree the way GitHub Pages serves a site: a request with no
file is answered with the site's own 404 page, status 404. A page with no file is then EXPECTED
to answer 404, so that one answer is no finding (`DocumentMiss`): its own document's response,
and the console line Chromium and WebKit echo it as, each once. Nothing else is excused. A
subresource such a page cannot have is a finding as on any page, the `--prove` plant among them,
and so is the document answering any other status. Answering 200 is not judged here: `--serve`
has already confirmed that the page answers 404 with the not-found file before anything renders.

Entry point assumptions: Playwright is importable; `Listeners` is attached before the page loads.
"""

from __future__ import annotations

from collections.abc import Sequence
from http import HTTPStatus

import pass_csp
from pass_checks import Observation, display_url, observation_from
from pass_targets import server_path
from playwright.sync_api import ConsoleMessage, Page, Request, Response
from playwright.sync_api import Error as PlaywrightError

#: The console line Chromium and WebKit log for a response that answers 404, a document's own
#: included, at line 0 and column 0 of the response's url; Firefox logs none. Measured in the
#: pinned builds. Matched whole, so an engine that rewords it leaves the echo a finding again
#: -- loudly, never a 404 hidden.
NOT_FOUND_ECHO = (
    "Failed to load resource: the server responded with a status of 404 (Not Found)"
)


class DocumentMiss:
    """The one answer a page with no file is excused: its own document, answering 404.

    The response is the main frame's navigation to the page's own path, its query aside; the
    echo is the console's line for a 404 at that url. Each is excused once, so a second load of
    the same document -- a reload, say -- is a finding again.
    """

    def __init__(self, base_url: str, page: str | None) -> None:
        """`page` is the page being loaded when it has no file under the served tree, else None."""
        self._base_url = base_url
        self._path = None if page is None else server_path(page)
        self._response_due = self._echo_due = page is not None

    def excuses_response(self, response: Response) -> bool:
        """Whether `response` is the document's own 404, not yet excused."""
        if not self._response_due or response.status != HTTPStatus.NOT_FOUND:
            return False
        request = response.request
        if (
            not request.is_navigation_request()
            or request.frame.parent_frame is not None
        ):
            return False
        if not self._names_document(response.url):
            return False
        self._response_due = False
        return True

    def excuses_echo(self, message: ConsoleMessage) -> bool:
        """Whether `message` is the console's echo of the document's own 404, not yet excused."""
        if not self._echo_due or message.text != NOT_FOUND_ECHO:
            return False
        place = message.location
        if (place["lineNumber"], place["columnNumber"]) != (0, 0):
            return False
        if not self._names_document(place["url"]):
            return False
        self._echo_due = False
        return True

    def _names_document(self, url: str) -> bool:
        """Whether `url` is the document's, on the measured origin, its query and fragment aside."""
        return server_path(display_url(url, self._base_url)) == self._path


class Listeners:
    """Everything the browser reports while a page loads and runs, collected as it arrives."""

    def __init__(
        self,
        page: Page,
        base_url: str,
        csp: pass_csp.CspWatch = pass_csp.UNWATCHED,
        *,
        missing: str | None = None,
    ) -> None:
        """Start listening on `page`; `base_url` shortens the urls it reports.

        `csp` is what the page's context watches of its Content Security Policy. `missing` is
        the page being loaded when it has no file under the served tree, so its own 404 is
        expected and excused (`DocumentMiss`); None for a page that has one.
        """
        self.base_url = base_url
        self.csp = csp
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.http_errors: list[str] = []
        self._miss = DocumentMiss(base_url, missing)
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    def _on_console(self, message: ConsoleMessage) -> None:
        if message.type == "error" and not self._miss.excuses_echo(message):
            self.console_errors.append(message.text)

    def _on_page_error(self, error: PlaywrightError) -> None:
        self.page_errors.append(f"{error.name}: {error.message}")

    def _on_response(self, response: Response) -> None:
        if response.status >= 400 and not self._miss.excuses_response(response):
            self.http_errors.append(
                f"{response.status} {display_url(response.url, self.base_url)}"
            )

    def _on_request_failed(self, request: Request) -> None:
        where = display_url(request.url, self.base_url)
        self.http_errors.append(f"failed {where} ({request.failure})")

    def observation(self, layout: object, *, csp: Sequence[str] = ()) -> Observation:
        """Join a layout probe's record with everything collected so far.

        `csp` is the CSP detector's findings, read when the page is measured
        (`pass_csp.findings`); a poll for the plants' arrival reads none.
        """
        return observation_from(
            layout,
            console_errors=self.console_errors,
            page_errors=self.page_errors,
            http_errors=self.http_errors,
            csp_violations=csp,
        )
