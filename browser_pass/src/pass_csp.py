"""The CSP violation detector's wiring: the shared collector, installed, read and planted.

`csp_watch`, a library in a folder beside this tool's, collects every Content Security Policy
violation a page's engine reports, through a binding its listener calls. This module puts it
where a rendering needs it, so the engine's own modules each make one call into it:

- **Installed** on a rendering's context before that context's first page, with scripts on,
  keeping only the reports of the pages the pass opens through `open_page`, from their
  documents on the measured origin -- the one the page's main frame is at as it reports,
  wherever a redirect of the base url landed it, in every run: a tab a browser extension opens
  in the context, or an iframe one injects into the measured page, reports to the same binding,
  and its violations are the extension's own, never the measured page's. A frame of the
  page's own on another origin (an embed) is held to that origin's policy, not the site's,
  and is not kept either.
  The listener is an init script, and an engine runs none with the page's scripts off, so a
  scripts-off rendering is NOT MEASURED for this detector, never clean (`pass_checks.csp_watched`).
  Every engine still logs each violation as a console error, scripts on and off, so the
  `console_error` detector reports it too: twice with scripts on, and only there with them off.
- **Read** once the page is measured, before any plant: each distinct violation is one finding,
  the line `Violation.describe()` gives it, in the order each first arrived, with a refused url on
  the measured origin named by its path, as an HTTP error's is. With `--require-csp`,
  a page as it loads (never a state) whose markup declares no policy gets one more, `NO_POLICY`.
  The policy is read through the driver, so that half works with scripts off as well.
- **Planted** last on a rendering, with `--prove`. The planted policy stays in force for the rest
  of the document and refuses every style attribute and inline `<style>` added after it. The
  other plants are CSSOM writes, which no policy governs, so last is insurance: nothing the page
  restyles while they are read is refused. Its verdict is whether the log reports its own sample.

A payload the listener never sends is an instrument fault, never a finding: the collector's log
raises it as `CspWatchError` from every read after it, and `csp_fault` is the fault the caller
then gives its rendering. So is a policy read answering what no page answers, raised as
`PolicyReadError`, which `csp_fault` names as the read's rather than the collector's.

Entry point assumptions: Playwright is importable (the repository's `browser` extra), and
`csp_watch` is the folder beside this tool's own that the insert below names.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

# The shared collector, a folder beside this tool's own, named rather than searched for, so a
# same-named folder above the clone is never imported in its place. It goes second, just after
# the tool's own src/ that every importer puts first, and so ahead of every installed
# distribution: none of the same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "csp_watch"))

import csp_watch
from csp_watch import CspLog, CspWatchError
from pass_checks import CSP_VIOLATION, csp_watched, display_url, poll_until
from pass_probe import POLL_INTERVAL_SECONDS, pause
from pass_targets import Rendering
from playwright.sync_api import BrowserContext, Page

#: The finding a page load gets, with `--require-csp`, when its markup declares no policy.
NO_POLICY = "page carries no Content-Security-Policy meta"

#: A frame's url scheme for a document whose origin and policy are its maker's: srcdoc, blank.
_INHERITING_SCHEME = "about"

#: What a `blob:` url wraps around the url of the origin that made it.
_BLOB_PREFIX = "blob:"


@dataclass(frozen=True)
class CspWatch:
    """What one rendering watches of its Content Security Policy."""

    #: The collector's log, or None where nothing could report to it: scripts off.
    log: CspLog | None
    #: Whether a page that declares no policy is a finding: `--require-csp`, on a page load.
    require: bool
    #: The run's base url, which a refused url on the measured origin is named without.
    base_url: str
    #: The pages the pass opened in the context (`open_page`): the only ones whose reports the
    #: log keeps. Grown in place, since a page is opened after the log that must admit it.
    pages: list[Page] = field(default_factory=list)


class PolicyReadError(CspWatchError):
    """The policy read answered what no page answers: the read's fault, not the collector's."""


#: Watching nothing: no collector, and no policy required.
UNWATCHED = CspWatch(None, require=False, base_url="")


def watch(
    context: BrowserContext,
    rendering: Rendering,
    base_url: str,
    *,
    require_csp: bool = False,
) -> CspWatch:
    """Install the collector on `context` where it can report: before the context's first page.

    `base_url` is the run's, against which a refused url is named. The log keeps only the
    reports of the pages opened through `open_page`, from their documents on the origin each
    page's main frame is at as it reports: where a redirect of `base_url` landed it.
    """
    pages: list[Page] = []
    log = (
        csp_watch.install(context, admit=lambda source: _opened(source, pages))
        if csp_watched(rendering)
        else None
    )
    require = require_csp and rendering.state is None
    return CspWatch(log, require=require, base_url=base_url, pages=pages)


def open_page(context: BrowserContext, watched: CspWatch) -> Page:
    """A new page of `context`, whose reports `watched`'s log keeps: the page the pass measures.

    Nothing is recorded where nothing is watched, so the shared `UNWATCHED` never grows.
    """
    page = context.new_page()
    if watched.log is not None:
        watched.pages.append(page)
    return page


def _opened(source: object, pages: list[Page]) -> bool:
    """Whether the binding's caller, `source`, is a document of the measured page's own.

    Its page must be one of `pages` -- the same object, not an equal one -- and its frame on
    the origin that page's main frame is at now. That is the measured origin, and not the
    base url's: a base url that redirects (http to https, a bare host to www) lands the page
    on another, as can one differing only in its host's case. A frame's page is its
    top-level page, so an iframe an extension injects into the measured page reports from
    that page too, under the extension's own policy; its document is on the extension's
    origin, and so is never kept. A document that takes its origin and policy from the page
    that made it is the page's own: an `about:` frame (`about:srcdoc`, `about:blank`) or a
    `blob:` url on the measured origin.
    """
    if not isinstance(source, dict):
        return False
    caller, frame_url = source.get("page"), getattr(source.get("frame"), "url", None)
    if not isinstance(frame_url, str):
        return False
    page = next((page for page in pages if caller is page), None)
    return page is not None and _on_origin(frame_url, page.main_frame.url)


def _on_origin(url: str, page_url: str) -> bool:
    """Whether a frame at `url` is on `page_url`'s origin, or takes its origin from its maker."""
    parts = urlsplit(url.removeprefix(_BLOB_PREFIX))
    if parts.scheme == _INHERITING_SCHEME:
        return True
    page = urlsplit(page_url)
    return (parts.scheme, parts.netloc) == (page.scheme, page.netloc)


def violations(watched: CspWatch) -> tuple[str, ...]:
    """Each distinct violation in the log so far, as one line, in the order it first arrived.

    A refused url on the measured origin is named by its path, as an HTTP error's is
    (`pass_checks.display_url`), so a run's loopback port never reaches a finding; violations
    are told apart only once they are named that way.

    Raises:
        CspWatchError: the log was sent a payload its listener never sends.
    """
    if watched.log is None:
        return ()
    lines = (
        replace(violation, blocked=display_url(violation.blocked, watched.base_url))
        for violation in watched.log.violations
    )
    return tuple(dict.fromkeys(violation.describe() for violation in lines))


def findings(page: Page, watched: CspWatch) -> tuple[str, ...]:
    """The rendering's findings for the detector: its violations, then a policy it lacks.

    Raises:
        PolicyReadError: the policy read answered something other than text or nothing.
        CspWatchError: the log was sent a payload its listener never sends.
    """
    missing = watched.require and _policy_of(page) is None
    return (*violations(watched), *((NO_POLICY,) if missing else ()))


def _policy_of(page: Page) -> str | None:
    """The page's policy meta, as `csp_watch.policy_of` reads it, its failure named as its own."""
    try:
        return csp_watch.policy_of(page)
    except CspWatchError as error:
        raise PolicyReadError(str(error)) from error


def prove(page: Page, watched: CspWatch, *, timeout: float) -> bool:
    """Plant a broken policy, LAST, and whether the log reported it within `timeout`.

    False without a log, and nothing planted: with scripts off nothing could report it, which
    `pass_checks.plant_verdicts` calls `n/a`. The report arrives after the plant returns, as
    every violation does, so it is polled for rather than read once.

    Raises:
        CspWatchError: the log was sent a payload its listener never sends.
    """
    log = watched.log
    if log is None:
        return False
    since = log.mark()
    csp_watch.plant(page)
    return poll_until(
        lambda: csp_watch.plant_caught(log, since),
        pause=pause(page),
        clock=time.monotonic,
        within=timeout,
        interval=POLL_INTERVAL_SECONDS,
    )


def csp_fault(error: CspWatchError) -> str:
    """The instrument fault a rendering gets when the detector's instrument answered wrongly.

    A failed policy read says so itself; anything else is a payload the collector was sent.
    """
    if isinstance(error, PolicyReadError):
        return f"{CSP_VIOLATION}: {error}"
    return f"{CSP_VIOLATION}: the collector was sent what its listener never sends: {error}"
