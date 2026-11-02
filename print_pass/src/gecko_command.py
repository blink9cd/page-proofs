"""What geckodriver is started with: the platform it runs on, where it is, its environment, its command.

Two facts shape it, both measured on macOS, the one platform the engine runs on. Gecko resolves
its per-user application-data folder (the one holding the reader's own `profiles.ini`)
before it uses the profile it was handed, so a throwaway profile alone does not keep it out of
the reader's Library; `CFFIXED_USER_HOME`, `MOZ_APP_DATA` and `MOZ_LOCAL_APP_DATA` do, and the
first of those is a macOS mechanism. And geckodriver drops `moz:firefoxOptions.env`, so those
variables go on geckodriver's own environment, which the Firefox it starts inherits, with
`TMPDIR` pointing at the scratch too. The engine never names, reads or touches the reader's
own Firefox folder, and on any other platform it refuses to run rather than risk it. One
folder is known to escape the redirect: Firefox's crash helper is handed `TemporaryItems` in
the system's per-user temporary folder, which `TMPDIR` does not redirect (measured), and a
crash may write there.

One variable there is a tripwire rather than a redirect. `MOZ_DISABLE_NONLOCAL_CONNECTIONS`
makes Firefox refuse a direct TCP connection to an address it counts as non-local, and end,
so a leak past the prefs ends the run loudly instead of reaching the network; `isolation_breach`
reads the refused host from the log. It also makes a release Firefox honor the local stand-in
Marionette names for Remote Settings, which it otherwise ignores (both measured). It is a
backstop, not the fence: it acts at the connection, after the lookup, which is why the prefs
also keep every lookup on loopback. It covers TCP alone, so only the prefs keep UDP off the
network, and which addresses Firefox counts as local was not measured.

Entry point assumptions: the standard library and this folder's `gecko_prefs`, `gecko_scratch`
and `print_record`. geckodriver locates the installed Firefox itself, so no path to Firefox is
written here, and geckodriver's own is whatever `PATH` finds.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable, Mapping
from typing import Final

from gecko_prefs import LOOPBACK
from gecko_scratch import Scratch
from print_record import EngineError

#: The WebDriver server's name on `PATH`.
GECKODRIVER: Final = "geckodriver"

#: The one platform, as `sys.platform` names it, where the isolation of Firefox's own folders
#: was measured.
_MEASURED_PLATFORM: Final = "darwin"

#: Firefox's non-local guard: set to 1, a direct TCP connection it counts as non-local ends it.
_NON_LOCAL_GUARD: Final = "MOZ_DISABLE_NONLOCAL_CONNECTIONS"

#: What the caller's environment may not hand Firefox: its own switches and NSPR's logging,
#: by prefix (a log file of Firefox's, a crash report's destination, its content sandbox or
#: process separation off), and the file TLS session keys would be written to.
_DROPPED_PREFIXES: Final = ("MOZ_", "NSPR_LOG_")
_DROPPED_KEYS: Final = frozenset({"SSLKEYLOGFILE"})


def require_measured_platform(platform: str = sys.platform) -> None:
    """Refuse to run anywhere but macOS, the one platform the engine's isolation was measured on.

    `CFFIXED_USER_HOME`, which keeps Gecko out of the reader's own Library, is a macOS
    mechanism, and `DeadPort`'s hold on its port was measured there; elsewhere the reader's
    own Firefox folders could be reached. `platform` is a `sys.platform` value.

    Raises:
        EngineError: `platform` is not macOS's.
    """
    if platform != _MEASURED_PLATFORM:
        raise EngineError(
            "the Firefox engine runs on macOS only: its isolation of Firefox's own folders "
            f"is measured there alone, so it refuses to run on {platform!r} rather than "
            "risk the reader's own Firefox folders"
        )


def find_geckodriver(which: Callable[[str], str | None] = shutil.which) -> str | None:
    """Where geckodriver is installed, found on `PATH` by `which`; None when it is not."""
    return which(GECKODRIVER)


def driver_env(base: Mapping[str, str], scratch: Scratch) -> dict[str, str]:
    """`base`, copied, with Firefox's folders moved into `scratch` and its non-local guard set.

    These go on geckodriver's environment, which the Firefox it starts inherits: geckodriver
    drops the session's own `moz:firefoxOptions.env`. Each is set after `base`, so nothing in
    the caller's environment can undo a redirect or lift the guard; and every Firefox switch,
    NSPR log setting and `SSLKEYLOGFILE` in `base` is dropped first, so none changes what
    Firefox does or where it writes.
    """
    kept = {
        key: value
        for key, value in base.items()
        if key not in _DROPPED_KEYS and not key.startswith(_DROPPED_PREFIXES)
    }
    return {
        **kept,
        "CFFIXED_USER_HOME": str(scratch.home),
        "MOZ_APP_DATA": str(scratch.app_data),
        "MOZ_LOCAL_APP_DATA": str(scratch.local_app_data),
        "TMPDIR": str(scratch.root),
        _NON_LOCAL_GUARD: "1",
    }


def driver_command(executable: str, scratch: Scratch) -> list[str]:
    """The command geckodriver runs: loopback, a port the system picks, profiles in the scratch.

    Port 0 lets the system pick a free port, which geckodriver then names in its log.
    """
    return [
        executable,
        "--host",
        LOOPBACK,
        "--port",
        "0",
        "--profile-root",
        str(scratch.profiles),
        "--log",
        "info",
    ]
