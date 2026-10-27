"""Which file a server path names, and the proof that a server answers with it.

Private to `loopback_site`: it lives in that folder's `src/`, and nothing outside that folder
imports it. Its name carries the tool's prefix, so it cannot shadow another tool's module
on `sys.path`. `loopback_site` re-exports `SiteServeError`, `INDEX_DOCUMENT`, `page_file` and
`confirm_serves`, which is how every caller reaches them.

This half reads the disk and fetches over HTTP; the serving itself stays in `loopback_site`.

Entry point assumptions: imported by `loopback_site` alone. Standard library only.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from http import HTTPStatus
from http.client import HTTPMessage
from pathlib import Path
from typing import IO

#: The file a static server answers a directory url with.
INDEX_DOCUMENT = "index.html"


class SiteServeError(RuntimeError):
    """The tree cannot be served honestly: a page answered wrong, or a not-found file is absent."""


def page_file(directory: Path, page: str) -> Path:
    """The file under `directory` a static server answers the server path `page` with.

    The containment check is lexical, as `not_found_file`'s is: a `..` climbing out of
    `directory` names a file no request can reach, since the request handler drops every `..`
    past the root, so it is refused before any caller checks or reads that file.

    Raises:
        SiteServeError: `page` names a file outside `directory`.
    """
    relative = page.lstrip("/")
    if not relative or relative.endswith("/"):
        relative += INDEX_DOCUMENT
    candidate = directory / relative
    if not Path(os.path.abspath(candidate)).is_relative_to(os.path.abspath(directory)):
        raise SiteServeError(f"the page {page} names a file outside {directory.name}/")
    return candidate


def _not_found_path(directory: Path, not_found: str) -> Path:
    """The one path the server path `not_found` names under `directory`, `..` resolved lexically.

    The request handler normalizes a request path the same way, without asking the disk, so
    this is the file a request for `not_found` itself is answered with. Left to the OS, a `..`
    after a symlinked directory would climb from the link's target instead.
    """
    return Path(os.path.abspath(page_file(directory, not_found)))


def not_found_file(directory: Path, not_found: str) -> Path:
    """The file under `directory` that the server path `not_found` names.

    The containment check is lexical, on the one path that is then checked, served and
    confirmed against, so the file it names is the file served and it sits inside the tree.

    Raises:
        SiteServeError: it names no file, or a file outside `directory`.
    """
    candidate = _not_found_path(directory, not_found)
    inside = candidate.is_relative_to(os.path.abspath(directory))
    if not (inside and candidate.is_file()):
        raise SiteServeError(
            f"the not-found page {not_found} names no file in {directory.name}/"
        )
    return candidate


class _KeepRedirects(urllib.request.HTTPRedirectHandler):
    """Hand a redirect back as the answer, rather than fetching the page it points at.

    A subclass of the redirect handler, so `build_opener` installs it in place of the one that
    follows. Every redirect status is declined before its address is read: the base handler
    parses `Location` before it would ask `redirect_request`, and a malformed address makes
    that parse raise a `ValueError` quoting part of the header. Declined, the redirect falls
    through to the opener's default, which raises it as an `HTTPError` naming its status,
    never its address.
    """

    def http_error_302(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> None:
        """Follow nothing, and read nothing of where the redirect points."""
        return None

    # The same statuses the base handler follows, each declined the same way.
    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _no_proxy() -> urllib.request.ProxyHandler:
    """A proxy handler that proxies nothing.

    `build_opener` otherwise adds one that reads `http_proxy` / `HTTP_PROXY` and the system
    proxy settings, and `proxy_bypass` does not exempt `127.0.0.1` once a proxy is set: a
    loopback read would be sent to the proxy host. An explicit empty one replaces it.
    """
    return urllib.request.ProxyHandler({})


#: Fetches a page without following a redirect. A page that must answer 404 does not, if it
#: answers a 301 to a page that does: a browser would land on another address. Nor does a
#: page confirmed with `follow_redirects` false, whatever the address it points at serves.
#: Public within `src/`: `loopbacksite_refresh` reads pages through it too, so no redirect can
#: lead a loopback read off loopback.
NO_REDIRECTS = urllib.request.build_opener(_no_proxy(), _KeepRedirects)

#: Fetches a page following redirects, as a browser does, but never through a proxy: urllib's
#: global `urlopen` opener would honor one.
FOLLOWS_REDIRECTS = urllib.request.build_opener(_no_proxy())


def _fetch_any_status(url: str, *, timeout: float) -> tuple[int, bytes]:
    """GET `url`, following no redirect; return its status and body, an error status's included.

    The opener raises an error status, a redirect included, as `HTTPError`, which still carries
    the body.
    """
    try:
        with NO_REDIRECTS.open(url, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()


def _is_redirect(status: int) -> bool:
    """Whether `status` is a 3xx: an answer pointing at another address, not the page."""
    return HTTPStatus.MULTIPLE_CHOICES <= status < HTTPStatus.BAD_REQUEST


def _fetch_refusing_redirects(url: str, page: str, *, timeout: float) -> bytes:
    """GET `url` and return its body; a redirect refuses `page` rather than being followed.

    Raises:
        SiteServeError: the server answered with a redirect. The message names the status
            only, never the address the redirect points at.
        OSError: any other error status, as the opener raises it.
    """
    try:
        with NO_REDIRECTS.open(url, timeout=timeout) as reply:
            body: bytes = reply.read()
    except urllib.error.HTTPError as error:
        if not _is_redirect(error.code):
            raise
        with error:
            raise SiteServeError(
                f"{page}: the server answered {error.code}, a redirect, not the page"
            ) from error
    return body


def _fetch_page(
    url: str, page: str, *, timeout: float, follow_redirects: bool
) -> bytes:
    """GET `url` and return its body, following a redirect only when told to."""
    if not follow_redirects:
        return _fetch_refusing_redirects(url, page, timeout=timeout)
    with FOLLOWS_REDIRECTS.open(url, timeout=timeout) as reply:
        followed: bytes = reply.read()
    return followed


def _confirm_answers_not_found(
    base_url: str, directory: Path, page: str, *, timeout: float, not_found: str
) -> None:
    """Require `page`, which has no file, to answer 404 with exactly the not-found file.

    Raises `OSError` when that file is missing: the path is resolved without checking it.
    """
    not_found_path = _not_found_path(directory, not_found)
    expected = not_found_path.read_bytes()
    status, served = _fetch_any_status(base_url + page, timeout=timeout)
    if (status, served) != (HTTPStatus.NOT_FOUND, expected):
        raise SiteServeError(
            f"{page}: the server did not answer 404 with {not_found_path.name} "
            f"from {directory.name}/ (it answered {status})"
        )


def confirm_serves(
    base_url: str,
    directory: Path,
    page: str,
    *,
    timeout: float,
    not_found: str | None = None,
    follow_redirects: bool = True,
) -> None:
    """Fetch `page` and require exactly the bytes of its file under `directory`.

    With `not_found`, a page that has no file must instead answer 404 with exactly the bytes of
    the file `not_found` names, as `serve_directory` given the same path does. That answer is
    fetched without following a redirect, so a directory named without its slash is refused as
    the 301 it answers.

    With `follow_redirects` false, a page that has its file is fetched the same way: a redirect
    refuses it, even to an address answering the very same bytes, so the page confirmed is the
    address named. The default follows, as a browser does.

    Raises:
        OSError: the page has no file on disk (with `not_found`: the not-found file has none),
            the server could not be reached, or it answered a page that has its file with an
            error status.
        SiteServeError: the server answered with other bytes, or with another status for a
            page that has no file -- it is serving another tree; without `follow_redirects`,
            it answered with a redirect. Or `page` names a file outside `directory`, refused
            before anything is read or fetched.
    """
    expected_file = page_file(directory, page)
    if not_found is not None and not expected_file.is_file():
        _confirm_answers_not_found(
            base_url, directory, page, timeout=timeout, not_found=not_found
        )
        return
    expected = expected_file.read_bytes()
    served = _fetch_page(
        base_url + page, page, timeout=timeout, follow_redirects=follow_redirects
    )
    if served != expected:
        raise SiteServeError(
            f"{page}: the server did not answer with {expected_file.name} "
            f"from {directory.name}/"
        )
