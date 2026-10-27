"""The fixtures loopback_site's grouped suites are built on.

Shared across the grouped suites, by import or by inheritance alike. They live here
rather than inside one suite because a fixture reached ACROSS suites is what makes
the decomposition honest: the alternative is near-copies that drift, and a drifted
copy fails in whichever suite touches it first, pointing at the wrong subject.

Every helper is PUBLIC. A leading underscore would claim these are private to one
module, which stopped being true the moment a second suite imported them.
"""

from __future__ import annotations

import signal
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


#: Bound on every request a test makes to a server it started.
TIMEOUT = 5.0


#: A generic tree: a root index, a directory index, and a page requested by file name.
PAGES: Mapping[str, str] = {
    "index.html": "<!doctype html><title>home</title>\n",
    "archive/index.html": "<!doctype html><title>archive</title>\n",
    "404.html": "<!doctype html><title>not found</title>\n",
}


def scratch_tree(case: unittest.TestCase, name: str = "site") -> Path:
    """Write the generic pages under a fresh scratch `name/` removed when `case` ends."""
    scratch = tempfile.TemporaryDirectory()
    case.addCleanup(scratch.cleanup)
    tree = Path(scratch.name) / name
    for relative, text in PAGES.items():
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    return tree


def restore_stop_dispositions(case: unittest.TestCase) -> None:
    """Put SIGINT's and SIGTERM's handlers back as they are now once `case` ends.

    `main` installs its own handlers for both, so every suite that calls it needs this.
    """
    for signum in (signal.SIGINT, signal.SIGTERM):
        case.addCleanup(signal.signal, signum, signal.getsignal(signum))
