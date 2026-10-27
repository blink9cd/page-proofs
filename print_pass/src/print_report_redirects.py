"""The make report's `redirects`: each target whose page is a meta refresh, and what was done.

`print_report.make_report` shapes the states and PDFs; `with_redirects` adds the one key
this run's redirects need, and counts each unfollowed one among the findings. An entry pairs
the page the target names with the destination its refresh leads to, says whether it was
followed, and carries its problems: none when followed, since its states are printed at the
destination and report their own; one finding when not, naming the key that follows it,
since none of its states was printed. The destination is the page's own words, so it is
scrubbed as every text is.

Entry point assumptions: a make report as `make_report` returns it, and the redirects
`print_redirects.resolve_redirects` met.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from pdf_privacy import Identity
from print_record import Problem
from print_redirects import Redirect
from print_report_output import FINDING
from print_report_scrub import scrub


def undeclared_finding(redirect: Redirect) -> Problem:
    """The finding an undeclared redirect is: its states were not printed, and why."""
    return Problem(
        FINDING,
        f"{redirect.page} is a meta refresh to {redirect.destination}, so its states "
        "were not printed: set follows_redirect = true on its target to print the page "
        "it leads to",
    )


def with_redirects(
    report: Mapping[str, object],
    redirects: Sequence[Redirect],
    *,
    out_dir: Path,
    identity: Identity,
) -> dict[str, object]:
    """A copy of the make `report` with its `redirects`, each unfollowed one a finding.

    `out_dir` and `identity` are the ones the report was scrubbed with.
    """
    clean = functools.partial(scrub, out_dir=out_dir, identity=identity)
    entries = [_redirect_json(redirect, clean) for redirect in redirects]
    totals = dict(cast(Mapping[str, int], report["totals"]))
    totals["findings"] += sum(not redirect.followed for redirect in redirects)
    return {**report, "redirects": entries, "totals": totals}


def _redirect_json(
    redirect: Redirect, clean: Callable[[str], str]
) -> dict[str, object]:
    """One redirect as the report writes it, its texts scrubbed."""
    problems = () if redirect.followed else (undeclared_finding(redirect),)
    return {
        "target": redirect.target,
        "page": redirect.page,
        "destination": clean(redirect.destination),
        "followed": redirect.followed,
        "problems": [
            {"severity": problem.severity, "what": clean(problem.what)}
            for problem in problems
        ],
    }
