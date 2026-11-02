"""Record which Chrome measured a run.

Two facts decide whether a later run compares with this one: the commit of the tree that was
served, and the Chrome that rendered it. The commit is the shared `commit_facts`' to read;
the Chrome is this module's. It is located the way
Lighthouse locates it (chrome-launcher, resolved from Lighthouse's own install), and the
runner hands that path to Lighthouse through `CHROME_PATH`, so the version recorded is the
browser used by construction rather than a second guess. The report's user agent is no
substitute: Chrome reduces it to the major version.

Entry point assumptions: the caller has already located `node`, or found it absent; nothing
here searches `PATH`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# The shared tree-killing subprocess runner, a folder at the repository root beside this
# tool's own folder. It goes second, just after the tool's own src/ that every importer puts
# first, and so ahead of every installed distribution: none of the same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run

#: Lighthouse's own package manifest, relative to the project root. chrome-launcher is
#: resolved from here, so the answer holds wherever npm places that dependency.
LIGHTHOUSE_PACKAGE_JSON = Path("node_modules") / "lighthouse" / "package.json"

#: Prints the Chrome chrome-launcher would launch. Resolved through a require rooted at
#: Lighthouse's manifest (argv[1]) rather than a bare import, which would find only a copy npm
#: happened to hoist to the top of node_modules.
_LOCATE_CHROME_SCRIPT = (
    'import {createRequire} from "node:module";'
    'import {pathToFileURL} from "node:url";'
    'const located = createRequire(process.argv[1]).resolve("chrome-launcher");'
    "const launcher = await import(pathToFileURL(located).href);"
    "process.stdout.write(launcher.getChromePath());"
)


@dataclass(frozen=True)
class ChromeFacts:
    """The Chrome executable Lighthouse is handed, and the version it reports."""

    path: str
    version: str


class FactError(RuntimeError):
    """Chrome could not be located, or could not report its version."""


def chrome_facts(root: Path, *, node: str | None, timeout: float) -> ChromeFacts:
    """Locate the Chrome Lighthouse would launch from `root`, and read its version.

    Raises:
        FactError: `node` is absent, the locator failed or printed nothing, or Chrome did not
            report a version.
        OSError: an executable could not be started.
        subprocess.TimeoutExpired: an executable did not finish within `timeout`.
    """
    if node is None:
        raise FactError(
            "node is not on PATH, and locating Chrome as Lighthouse does needs it"
        )
    located = bounded_run.run_bounded(
        [
            node,
            "--input-type=module",
            "-e",
            _LOCATE_CHROME_SCRIPT,
            # Absolute: node's createRequire refuses a relative filename, and `--root .` is
            # the documented invocation.
            str((root / LIGHTHOUSE_PACKAGE_JSON).absolute()),
        ],
        cwd=root,
        timeout=timeout,
    )
    path = located.stdout.strip()
    if located.returncode != 0 or not path:
        reason = located.stderr.strip() or "no path printed"
        raise FactError(f"chrome-launcher located no Chrome: {reason}")
    probed = bounded_run.run_bounded([path, "--version"], timeout=timeout)
    version = probed.stdout.strip()
    if probed.returncode != 0 or not version:
        raise FactError(f"{path} --version reported no version")
    return ChromeFacts(path=path, version=version)
