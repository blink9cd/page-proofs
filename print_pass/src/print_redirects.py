"""Each target's meta refresh, read once per run, and the targets the engines are handed.

A page that is a meta refresh navigates as it loads, so an engine that evaluates script in it
races the navigation and loses. A target follows one only when the targets file DECLARES it
(`follows_redirect = true`); then it is printed at the destination. Before any
engine runs, `resolve_redirects` reads every target's page over loopback, as the targets name
it, and decides:

- Declared, a refresh at once (delay 0) to a page on the served origin that is served as its
  own file, and is not itself a refresh: followed. The target's `page` becomes the
  destination, so neither engine knows a redirect was there.
- Declared, anything else: `RedirectError`, naming the target and why. The run cannot print
  what the file says it should, so it is refused as a targets file would be.
- Not declared, a refresh: recorded unfollowed, and the target left out of what the engines
  print; the report makes it a finding naming the key.
- Not declared and no refresh, or a page that cannot be read: left as it is, for the engine,
  whose own load reports a page that does not answer.

Entry point assumptions: the served tree's base url, as `loopback_site.serve_directory`
yields it, and the folder it serves. Every read is a loopback GET, bounded by
`READ_TIMEOUT_SECONDS`.
"""

from __future__ import annotations

import dataclasses
import http.client
import urllib.error
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit, urlunsplit

# The shared loopback server: a folder beside this tool's own, named rather than searched
# for, so a same-named folder above the clone is never imported in its place, and put near
# the front of the path, ahead of every installed distribution, so none of the same name can
# shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "loopback_site"))

import loopback_site
from loopback_site import MetaRefresh, SiteServeError
from print_targets import Target, TargetsFile

#: Bound on each loopback read of a page, in seconds: a tiny static file from a server in
#: this process, so a read that takes longer is a server that will not answer.
READ_TIMEOUT_SECONDS: Final = 10.0

#: The only delay followed: a refresh at once. One that waits shows its own page first,
#: which is a page to print, not a redirect.
_AT_ONCE: Final = 0

#: The root path, for a destination naming the origin with no path.
_ROOT: Final = "/"

#: What a loopback read can raise for a page that does not answer: an error or redirect
#: status, a refused connection or a timeout (all `OSError`), or a malformed reply.
_UNREADABLE: Final = (OSError, http.client.HTTPException)


class RedirectError(Exception):
    """A declared redirect that cannot be followed honestly: the run is refused."""


@dataclass(frozen=True)
class Redirect:
    """One target whose page is a meta refresh, and whether the run followed it.

    `destination` is a server path and query when on the served origin, else the whole URL.
    """

    target: str
    page: str
    destination: str
    followed: bool


@dataclass(frozen=True)
class Resolution:
    """What the engines print, and every redirect met on the way.

    In `targets`, each followed target's `page` is its destination, and each unfollowed
    redirect's target is left out.
    """

    targets: TargetsFile
    redirects: tuple[Redirect, ...]


def server_path(destination: str, base_url: str) -> str | None:
    """`destination`'s path and query when it is on `base_url`'s origin, else None.

    The fragment is dropped: it names a place in the page the server answers, not a page.
    """
    parts, origin = urlsplit(destination), urlsplit(base_url)
    if (parts.scheme, parts.netloc) != (origin.scheme, origin.netloc):
        return None
    return urlunsplit(("", "", parts.path or _ROOT, parts.query, ""))


def resolve_redirects(
    targets: TargetsFile,
    base_url: str,
    served: Path,
    *,
    timeout: float = READ_TIMEOUT_SECONDS,
) -> Resolution:
    """Read every target's page at `base_url` and decide what each engine prints.

    `served` is the folder `base_url` serves, which a followed destination's file must be in.

    Raises:
        RedirectError: a declared redirect cannot be followed: the page is no refresh, waits,
            leaves the origin, leads to a page not served as its own file, or leads to
            another refresh; or the page cannot be read.
    """
    kept: list[Target] = []
    redirects: list[Redirect] = []
    for index, target in enumerate(targets.targets):
        where = f"target[{index}].follows_redirect: target {target.name!r}"
        refresh = _read(base_url, target, where, timeout)
        if refresh is None:
            kept.append(target)
            continue
        path = server_path(refresh.destination, base_url)
        redirect = Redirect(
            target.name,
            target.page,
            refresh.destination if path is None else path,
            followed=target.follows_redirect,
        )
        redirects.append(redirect)
        if target.follows_redirect:
            _require_followable(refresh, path, base_url, served, where, timeout)
            kept.append(dataclasses.replace(target, page=redirect.destination))
    return Resolution(
        dataclasses.replace(targets, targets=tuple(kept)), tuple(redirects)
    )


def _close(error: BaseException) -> None:
    """Close the reply an `HTTPError` holds open; any other error holds none."""
    if isinstance(error, urllib.error.HTTPError):
        error.close()


def _read(
    base_url: str, target: Target, where: str, timeout: float
) -> MetaRefresh | None:
    """The refresh `target`'s page declares, or None.

    A page that cannot be read is None when undeclared, left to the engine to load and
    report; when declared, the redirect cannot be checked, so it is refused.

    Raises:
        RedirectError: a declared page has no refresh, or cannot be read.
    """
    try:
        refresh = loopback_site.read_meta_refresh(
            base_url + target.page, timeout=timeout
        )
    except _UNREADABLE as error:
        _close(error)
        if target.follows_redirect:
            raise RedirectError(
                f"{where}: {target.page} could not be read: {error}"
            ) from error
        return None
    if refresh is None and target.follows_redirect:
        raise RedirectError(f"{where}: {target.page} is not a meta refresh")
    return refresh


def _require_followable(
    refresh: MetaRefresh,
    path: str | None,
    base_url: str,
    served: Path,
    where: str,
    timeout: float,
) -> None:
    """Refuse a declared refresh that waits, leaves the origin, or does not lead to a page.

    Raises:
        RedirectError: naming `where` and the reason.
    """
    if refresh.delay != _AT_ONCE:
        raise RedirectError(
            f"{where}: the refresh is after {refresh.delay:g} s, not at once"
        )
    if path is None:
        raise RedirectError(
            f"{where}: the refresh leads to {refresh.destination}, off the served origin"
        )
    try:
        # The path alone names the file; the query is the page's, not the server's.
        loopback_site.confirm_serves(
            base_url,
            served,
            urlsplit(path).path,
            timeout=timeout,
            follow_redirects=False,
        )
        again = loopback_site.read_meta_refresh(base_url + path, timeout=timeout)
    except (*_UNREADABLE, SiteServeError) as error:
        _close(error)
        raise RedirectError(f"{where}: {path} is not served: {error}") from error
    if again is not None:
        raise RedirectError(f"{where}: {path} is itself a meta refresh")
