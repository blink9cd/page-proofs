"""Where a run's pages are measured from: `--serve`'s tree, confirmed, or the targets file's url.

With `--serve`, the tree is served on 127.0.0.1 through the shared `loopback_site`, on a port the
OS picks, and every page's bytes are confirmed against disk before anything renders, so the
tree measured is the tree named. A page with no file is confirmed to answer 404 with the targets
file's `not_found` instead, and each declared meta refresh is followed (`pass_redirects`).
Without it, the targets file's `base_url` is measured as it answers, and nothing is followed.

Entry point assumptions: `loopback_site` is importable, which the CLI's bootstrap arranges.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Set
from pathlib import Path

import loopback_site
import pass_redirects
from pass_targets import Targets, served_pages


@contextlib.contextmanager
def pages_at(
    targets: Targets, serve: Path | None, missing_pages: Set[str], *, timeout: float
) -> Iterator[tuple[str, pass_redirects.Followed]]:
    """The base url to measure, and the redirects `serve`'s confirmed pages are followed by.

    `timeout` bounds confirming or reading each served page.

    Raises:
        OSError, loopback_site.SiteServeError: a page is not served with its own file.
        pass_redirects.RedirectRefused: a meta refresh is undeclared, or cannot be followed.
    """
    if serve is None:
        assert targets.base_url is not None  # plan_run refuses a run with neither
        yield targets.base_url, ()
        return
    not_found = targets.not_found
    with loopback_site.serve_directory(serve, not_found=not_found) as base_url:
        for page in served_pages(targets):
            loopback_site.confirm_serves(
                base_url, serve, page, timeout=timeout, not_found=not_found
            )
        site = pass_redirects.Served(loopback_site, base_url, serve, timeout)
        yield base_url, pass_redirects.follow(targets, site, missing_pages)
