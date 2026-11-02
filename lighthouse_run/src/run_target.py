"""Name the tree a run measures, and where its pages are fetched from.

Two kinds of run measure the same way and differ only here. A LOOPBACK run serves a folder on
127.0.0.1 itself: `src/` under `--root`, or the folder `--serve` names. A HOSTED run starts no
server: it fetches a copy a real host serves (`--url`), and `--expect` names the local folder
holding the bytes that host must serve: a built site of the deployed commit. Either way,
every page is confirmed byte-for-byte against that folder before any run, through the same
shared check, so the tree measured is the tree named whoever serves it. A hosted page must
answer at its own address: one the host redirects is refused, wherever the redirect points,
since every run would otherwise measure through a hop no summary records.

Entry point assumptions: the CLI's bootstrap has put the shared `loopback_site/` on
`sys.path`, as it does for the runner itself, and `run_options` has already refused any
combination of target options that names no single tree.
"""

from __future__ import annotations

import http.client
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import loopback_site

#: The served tree when `--serve` names none, relative to `--root`.
PAGES_SUBDIR = "src"

#: What fetching and comparing one hosted page can raise. `HTTPException` is not an `OSError`:
#: a connection a real network closes mid-body raises `IncompleteRead`, which must refuse the
#: run like any other unconfirmed page rather than escape as a traceback.
_CONFIRM_FAILURES = (
    OSError,
    http.client.HTTPException,
    loopback_site.SiteServeError,
)


class HostedPageError(loopback_site.SiteServeError):
    """A hosted page was not confirmed against the expected folder, whatever the reason.

    A `SiteServeError` because it means the same thing a loopback refusal does: the pages are
    not the tree named, so nothing is measured.
    """


@dataclass(frozen=True)
class Target:
    """What one run measures.

    `tree` is the folder every page's bytes must match. `named` is that folder as the operator
    gave it (`--serve` or `--expect`), `None` for the default `src/`, and the summary records it
    that way. `url` is the hosted base, `None` on a loopback run.
    """

    tree: Path
    named: Path | None
    url: str | None

    @property
    def where(self) -> str:
        """What a refusal names as the thing measured: the hosted base, else the tree."""
        return str(self.tree) if self.url is None else self.url


def resolve_target(
    root: Path, *, serve: Path | None, url: str | None, expect: Path | None
) -> Target:
    """The target the options name: hosted when `url` is set (with its `expect`)."""
    if url is not None and expect is not None:
        return Target(tree=expect, named=expect, url=url)
    return Target(
        tree=root / PAGES_SUBDIR if serve is None else serve, named=serve, url=None
    )


def tree_problem(root: Path, target: Target) -> str | None:
    """Why the target's tree is not a folder to confirm pages against, or `None`."""
    if target.tree.is_dir():
        return None
    # Named as the operator named it: a missing default means the root is wrong.
    if target.named is None:
        return f"{root} is not a web project root: no {PAGES_SUBDIR}/"
    option = "--serve" if target.url is None else "--expect"
    return f"{option} {target.named} is not a folder"


def confirm_hosted(
    base: str, tree: Path, pages: Sequence[str], *, timeout: float
) -> None:
    """Require the host at `base` to answer every page with exactly its bytes under `tree`.

    Raises:
        HostedPageError: a page answered other bytes, a redirect or another non-2xx status,
            has no file under `tree`, or could not be fetched within `timeout`, named once
            in the message.
    """
    for page in pages:
        try:
            loopback_site.confirm_serves(
                base, tree, page, timeout=timeout, follow_redirects=False
            )
        except _CONFIRM_FAILURES as error:
            # The shared check's own refusals open with the page, which this message names
            # first: dropped there, it is named once.
            reason = str(error).removeprefix(f"{page}: ")
            raise HostedPageError(f"{page} was not confirmed: {reason}") from error


@contextmanager
def confirmed_base(
    target: Target, pages: Sequence[str], *, timeout: float
) -> Iterator[str]:
    """Yield the base url every page is measured at, each page already confirmed.

    A loopback run serves the tree on 127.0.0.1 for the length of the block; a hosted run
    starts nothing and yields its own base.

    Raises:
        OSError: (loopback) a page has no file, or the server could not be reached.
        loopback_site.SiteServeError: the server answered from another tree; on a hosted
            run, the `HostedPageError` naming the page that was not confirmed.
    """
    if target.url is not None:
        confirm_hosted(target.url, target.tree, pages, timeout=timeout)
        yield target.url
        return
    with loopback_site.serve_directory(target.tree) as served_base:
        for page in pages:
            loopback_site.confirm_serves(
                served_base, target.tree, page, timeout=timeout
            )
        yield served_base
