"""A page that is a meta refresh: the targets file's `redirects`, and following one.

A page whose markup is a 0-second `<meta http-equiv="refresh">` navigates away as it loads, so a
browser told to measure it evaluates script in a page being torn down, and the run dies. Such a
page is read over loopback BEFORE any browser opens it, and:

- listed in the targets file's `redirects`, it must refresh at once to a page the same server
  serves, and that page is measured in its place: the year page an archive stub points at, say,
  so no year is written in a targets file and none goes stale each January;
- not listed, the run stops naming it, because a redirect nobody declared is one that could
  hide a page quietly.

A page with no file is never read (it answers 404), and a page with a `#fragment` or a `?query`
is read at its server path, the file a static server answers it from.

Entry point assumptions: nothing here opens a socket itself. The server's two answers (a
page's meta refresh, and whether a path is served) come from the `site` a caller hands over:
the CLI hands over `loopback_site` itself, and the tests a fake.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence, Set
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from pass_markdown import code_span
from pass_targets import Rendering, Targets, TargetsError, served_pages, server_path


class RedirectRefused(Exception):
    """A page is a meta refresh the targets file does not declare, or one that cannot be followed."""


class Refresh(Protocol):
    """A page's meta refresh as the reader returns it: seconds to wait, and an absolute url."""

    @property
    def delay(self) -> float:
        """Seconds the page waits before it navigates."""
        ...

    @property
    def destination(self) -> str:
        """The absolute url it navigates to."""
        ...


class Site(Protocol):
    """The part of `loopback_site` a redirect is followed through: a seam the tests replace."""

    @property
    def SiteServeError(self) -> type[Exception]:  # noqa: N802 - mirrors the module's class name
        """What `confirm_serves` raises when the server answers with another tree's bytes."""
        ...

    def read_meta_refresh(self, page_url: str, *, timeout: float) -> Refresh | None:
        """The meta refresh `page_url` declares, or None."""
        ...

    def confirm_serves(
        self, base_url: str, directory: Path, page: str, *, timeout: float
    ) -> None:
        """Raise OSError or `SiteServeError` unless the server answers `page` with its file."""
        ...


@dataclass(frozen=True)
class Redirect:
    """A declared page, by its server path, and the page measured in its place."""

    page: str
    destination: str


def parse_redirects(
    value: object, *, pages: Sequence[str], not_found: str | None
) -> tuple[str, ...]:
    """The optional `redirects`: pages the targets file also lists, each once, not `not_found`.

    Raises:
        TargetsError: naming the key, and the page when one is at fault.
    """
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(page, str) for page in value):
        raise TargetsError("redirects must be a list of pages, each one pages lists")
    for number, page in enumerate(value):
        if page not in pages:
            raise TargetsError(f"redirects holds {page!r}, which pages does not list")
        if page in value[:number]:
            raise TargetsError(f"redirects holds {page!r} twice")
        if server_path(page) == not_found:
            raise TargetsError(
                f"redirects holds {page!r}, the not_found page, which answers 404"
            )
    return tuple(value)


@dataclass(frozen=True)
class Served:
    """Where pages are read: the site, the url it serves at, its folder, and each call's bound."""

    site: Site
    base_url: str
    folder: Path
    timeout: float

    def refresh(self, path: str) -> Refresh | None:
        """The meta refresh the page at server path `path` declares, or None."""
        return self.site.read_meta_refresh(self.base_url + path, timeout=self.timeout)


#: The redirects one run followed, in the order their pages are served.
Followed = tuple[Redirect, ...]


def follow(targets: Targets, served: Served, missing_pages: Set[str]) -> Followed:
    """Read every page `served` holds for a meta refresh; each declared one, followed.

    A page in `missing_pages` has no file, so it is not read.

    Raises:
        RedirectRefused: an undeclared page is a meta refresh, or a declared one cannot be
            followed, naming the page and why.
    """
    declared = {server_path(page) for page in targets.redirects}
    followed: list[Redirect] = []
    for page in served_pages(targets):
        if page in missing_pages:
            if page in declared:
                raise RedirectRefused(
                    f"{page} is listed in redirects but has no file, so it is no meta refresh"
                )
            continue
        refresh = served.refresh(page)
        if page in declared:
            followed.append(_followed_to(page, refresh, served))
        elif refresh is not None:
            raise RedirectRefused(
                f"{page} is a meta refresh to {refresh.destination}: list it in the targets "
                "file's redirects to measure the page it points at"
            )
    return tuple(followed)


def _followed_to(page: str, refresh: Refresh | None, served: Served) -> Redirect:
    """The declared `page`'s destination, once it is proven a page this server serves."""
    if refresh is None:
        raise RedirectRefused(
            f"{page} is listed in redirects but declares no meta refresh"
        )
    if refresh.delay > 0:
        raise RedirectRefused(
            f"{page} is listed in redirects but refreshes after {refresh.delay:g} seconds, "
            "not at once"
        )
    destination = _same_origin_page(refresh.destination, served.base_url)
    if destination is None:
        raise RedirectRefused(
            f"{page} is a meta refresh to {refresh.destination}, off the served site"
        )
    site, path = served.site, server_path(destination)
    try:
        site.confirm_serves(
            served.base_url, served.folder, path, timeout=served.timeout
        )
    except (OSError, site.SiteServeError) as error:
        raise RedirectRefused(
            f"{page} is a meta refresh to {destination}, which is not served: {error}"
        ) from error
    # One hop only: a destination that refreshes again would race the browser the same way.
    if served.refresh(path) is not None:
        raise RedirectRefused(
            f"{page} is a meta refresh to {destination}, itself a meta refresh: "
            "only one is followed"
        )
    return Redirect(page, destination)


def _same_origin_page(url: str, base_url: str) -> str | None:
    """`url` as a page of `base_url`'s server (path, query, fragment), or None when off it."""
    parts = urlsplit(url)
    if (parts.scheme, parts.netloc) != urlsplit(base_url)[:2]:
        return None
    query = f"?{parts.query}" if parts.query else ""
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    return f"{parts.path or '/'}{query}{fragment}"


def retarget(
    renderings: Iterable[Rendering], redirects: Iterable[Redirect]
) -> tuple[Rendering, ...]:
    """Each rendering of a followed page moved to its destination, every rendering once.

    A destination the targets file lists as well would otherwise be measured twice.
    """
    destinations = {redirect.page: redirect.destination for redirect in redirects}
    moved = (
        replace(r, page=destinations.get(server_path(r.page), r.page))
        for r in renderings
    )
    return tuple(dict.fromkeys(moved))


def report_lines(redirects: Sequence[Redirect]) -> list[str]:
    """The report's facts line naming each redirect followed, or none when there were none."""
    if not redirects:
        return []
    pairs = "; ".join(
        f"{code_span(r.page)} to {code_span(r.destination)}" for r in redirects
    )
    return [f"- Redirects followed: {pairs}"]
