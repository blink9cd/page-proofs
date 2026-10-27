"""What geckodriver's log says that a run acts on: the port it listens on, and an isolation breach.

geckodriver writes its own lines and Firefox's console to one log in the scratch. A run reads
it as text it does not control the whole of, so each reader here is a pure function of that
text, fuzzed, which never raises on what it is handed.

The breach is Firefox's own non-local guard firing. With `MOZ_DISABLE_NONLOCAL_CONNECTIONS`
set, Firefox refuses a direct TCP connection to an address it counts as non-local, writes the
refusal to its stderr, which the log carries, and ends (measured on macOS). The session then
fails on the browser's exit, which says nothing of why; `isolation_breach` reads the why from
the log, and `IsolationBreach` is the error that carries it, apart from every other engine
failure. The log also carries the page's console, so the sentence alone proves nothing: it is
read only from a state or a run that failed.

Entry point assumptions: the standard library and this folder's `gecko_prefs` and
`print_record` only.
"""

from __future__ import annotations

import re
from typing import Final

from gecko_prefs import HIGHEST_PORT
from print_record import EngineError

#: geckodriver's line naming its port, matched only once its newline is written: a line caught
#: mid-write would otherwise give the first digits of the port as the port. ASCII digits only,
#: and no leading zero, which geckodriver never writes.
_LISTENING: Final = re.compile(r"Listening on 127\.0\.0\.1:([1-9][0-9]{0,4})\r?\n")

#: The non-local guard's refusal: the host Firefox was asked for, then the address it resolved
#: to (measured: `FATAL ERROR: ... 192.0.2.1 (192.0.2.1) was made.`). Matched only at a line's
#: start and with the guard's own prefix, so neither a page error quoting it (`JavaScript
#: error: <url>, line <n>: ...`) nor a console message's later line holding the bare sentence
#: is it; each name only in printable ASCII but a space or a parenthesis, so a
#: terminal escape is never carried; and only once the sentence's end is written, so a line
#: caught mid-write never names a prefix of either.
_NON_LOCAL_REFUSAL: Final = re.compile(
    r"^FATAL ERROR: Non-local network connections are disabled and a connection "
    r"attempt to ([!-'*-~]+) \(([!-'*-~]*)\) was made\.",
    re.MULTILINE,
)


class IsolationBreach(EngineError):
    """Firefox tried a direct TCP connection its non-local guard refused, ending the browser."""


# fuzz-target
def listening_port(log_text: str) -> int | None:
    """The port geckodriver named in its log, or None while it has named none.

    The first whole `Listening on 127.0.0.1:<port>` line naming a port from 1 to 65535
    decides. Any other host, a line whose newline is not written yet, and a port out of
    range or written with a leading zero name nothing.
    """
    for match in _LISTENING.finditer(log_text):
        port = int(match[1])
        if port <= HIGHEST_PORT:
            return port
    return None


# fuzz-target
def isolation_breach(log_text: str) -> str | None:
    """One line naming the first connection Firefox's non-local guard refused, or None.

    The guard ends the browser at its refusal, so the first refusal logged is what ended it.
    Neither the host nor the address it names can hold whitespace or a control character, so
    the line is one line whatever else the log holds. A page can write the same sentence to
    the log on a line of its own, through its console, so a caller asks only once a state or
    the run has failed: the guard ends the browser, and a run that went on was not refused.
    """
    match = _NON_LOCAL_REFUSAL.search(log_text)
    if match is None:
        return None
    host, address = match.groups()
    return (
        f"Firefox tried to connect directly to {host} ({address}); "
        "its non-local guard refused it and ended the browser"
    )
