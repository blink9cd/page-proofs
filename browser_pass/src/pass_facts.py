"""Where the engine builds live by default: where Playwright itself puts them.

`python -m playwright install` puts the engine builds in a cache folder of its own, per user, and
the default here is that same folder, so a run after a plain install finds them with nothing to
set. Playwright's own `PLAYWRIGHT_BROWSERS_PATH`, when set, wins, as it does for Playwright.

Entry point assumptions: the standard library only; the caller hands over the environment, the
platform and the home folder, so nothing here reads the host itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

#: Playwright's own variable naming where its engine builds are.
BROWSERS_ENV = "PLAYWRIGHT_BROWSERS_PATH"

#: The folder Playwright keeps its engine builds in, inside the platform's cache folder.
PLAYWRIGHT_CACHE_NAME = "ms-playwright"

#: Each platform's cache folder as Playwright finds it: the variable that names it, if any, and
#: the path under the home folder it falls back to.
_CACHE_ROOTS: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "linux": ("XDG_CACHE_HOME", (".cache",)),
    "darwin": (None, ("Library", "Caches")),
    "win32": ("LOCALAPPDATA", ("AppData", "Local")),
}


def home_folder() -> Path | None:
    """The user's home folder, or None where the platform cannot name one."""
    try:
        return Path.home()
    except RuntimeError:
        return None


def playwright_cache(
    env: Mapping[str, str], *, platform: str, home: Path | None
) -> Path | None:
    """The folder a plain `playwright install` puts the builds in, or None where it has none.

    None for a platform Playwright does not install builds on, and for a cache folder that falls
    back to a home folder there is none of.
    """
    root = _CACHE_ROOTS.get(platform)
    if root is None:
        return None
    variable, under_home = root
    named = env.get(variable) if variable is not None else None
    if named:
        return Path(named) / PLAYWRIGHT_CACHE_NAME
    if home is None:
        return None
    return home.joinpath(*under_home, PLAYWRIGHT_CACHE_NAME)


def default_browsers_path(
    env: Mapping[str, str], *, platform: str, home: Path | None
) -> Path | None:
    """Where the engine builds are: Playwright's variable if set, else its own cache folder."""
    named = env.get(BROWSERS_ENV)
    if named:
        return Path(named)
    return playwright_cache(env, platform=platform, home=home)
