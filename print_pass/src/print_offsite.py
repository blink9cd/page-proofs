"""Hosts off loopback a site expects: which a targets file may list, and which URLs each covers.

Every request off loopback is aborted, whatever this module says: nothing leaves the machine.
A site whose pages load images from a vendor's host would still be red on every run, so a
targets file may list the hosts it expects, and the judge reports what is blocked on them as a
warning rather than a finding. Two rules make that safe. A listed name is a real hostname of
two labels or more, never an address, a wildcard or loopback, so no entry can cover a whole
top-level domain or excuse a request the served site should have answered. And
`on_expected_host` is the ONE place a URL is matched against the list: a name covers itself
and the names under it, and nothing else.

Entry point assumptions: `expected_hosts` is handed the value of the targets file's key, as
`tomllib` made it, and the path to it; `on_expected_host` takes a URL a page asked for or an
image reported, and a list `expected_hosts` made. It reads each URL as the browser would
request it, and fails closed where no request could have been made: a source the browser
could not parse is reported as written, was never requested, and so was not blocked.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Collection
from typing import Final
from urllib.parse import urlsplit

import print_targets_checks as checks
from print_targets_checks import TargetsError

#: The schemes a page's request to a host is made with: HTTP(S) and the WebSockets. Any
#: other URL (`file:`, `javascript:`, `data:`) asks no host for anything.
NETWORK_SCHEMES: Final = frozenset({"http", "https", "ws", "wss"})

# A label: lowercase letters, digits and hyphens, 1 to 63 of them, with no hyphen at either
# end. A hostname is labels joined by one dot each, matched WHOLE: a `$` anchor would let a
# trailing newline through, and nothing else (a scheme, port, path, wildcard, space or
# trailing dot) matches at all.
_LABEL: Final = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME: Final = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")

# The longest name DNS carries, checked before the pattern ever runs.
_MAX_HOSTNAME_LENGTH: Final = 253

# A last label a browser reads as a number, so the host as an IPv4 address: all decimal
# digits, or `0x` and hex digits (the URL standard's "ends in a number" check). `1.2.3` and
# `0x7f.1` are addresses to it, never names.
_NUMERIC_LABEL: Final = re.compile(r"[0-9]+|0x[0-9a-f]*")

# The loopback name, and every name under it (RFC 6761): the host resolves them to itself.
_LOOPBACK_NAME: Final = "localhost"

# What an IPv6 literal is wrapped in inside a URL.
_IPV6_BRACKETS: Final = "[]"

# A URL's host as the matcher reads it: labels of lowercase letters, digits, hyphens and
# underscores, one dot between each, matched whole. The browser requests such a host as it
# is written (an underscore, a hyphen at a label's end and a label past 63 included), and
# none of these characters ends a host to it or to `urlsplit`, so the host read here is the
# browser's own. It requests other hosts too (`a$b`, `a%20b`, an empty label): those fail
# closed, on no expected host, and each request stays a finding.
_URL_HOST: Final = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*")

# What never stands in the authority of a URL the browser could have requested from an
# expected host: an IPv6 literal's bracket, since no listed name is an address; and a
# backslash, which the browser reads as a slash, ending the host before it where `urlsplit`
# reads on past it.
_NOT_IN_AUTHORITY: Final = ("[", "\\")


def expected_hosts(value: object, where: str) -> tuple[str, ...]:
    """The targets file's expected hosts: an array of hostnames, each listed once.

    Raises:
        TargetsError: naming the path to the array, or to the entry refused, and why.
    """
    hosts = checks.array_of(_hostname)(value, where)
    for index, host in enumerate(hosts):
        if host in hosts[:index]:
            raise TargetsError(f"{where}[{index}]: {host!r} is already listed")
    return hosts


# fuzz-target
def on_expected_host(url: str, hosts: Collection[str]) -> bool:
    """Whether a page's request for `url` would go to one of `hosts`, or a name under one.

    `img.example.org` is on `example.org`; `badexample.org` is not. Only a URL of one of
    `NETWORK_SCHEMES` is requested from a host: a `file:` or `javascript:` URL, and one with
    no scheme (a same-origin path, one opening `//` included) is on none. Case is ignored
    on both sides, and a port the browser accepts (an empty one included), a path, user info
    or a leading space changes nothing. On none, too, is a URL that does not parse, one the
    browser refuses or reads another way (a port past 65535 or not a number, a bracket or a
    backslash in its authority), and one whose host is outside the label alphabet.
    """
    host = _requested_host(url)
    if host is None:
        return False
    names = (name.lower() for name in hosts)
    return any(host == name or host.endswith(f".{name}") for name in names)


def _requested_host(url: str) -> str | None:
    """The host the browser would have requested `url` from, lowercase, or None."""
    try:
        parts = urlsplit(url)
        # Read for its check alone: a port the browser refuses raises here.
        _ = parts.port
    except ValueError:
        return None
    host = parts.hostname  # lowercased by urlsplit, as the scheme is
    if parts.scheme not in NETWORK_SCHEMES or host is None:
        return None
    if any(mark in parts.netloc for mark in _NOT_IN_AUTHORITY):
        return None
    return host if _URL_HOST.fullmatch(host) else None


def _hostname(value: object, where: str) -> str:
    """One expected host: a lowercase hostname of two labels or more, not an address or loopback."""
    host = checks.text(value, where)
    if len(host) > _MAX_HOSTNAME_LENGTH:
        raise TargetsError(
            f"{where}: must be at most {_MAX_HOSTNAME_LENGTH} characters"
        )
    if _is_ip_address(host):
        raise TargetsError(f"{where}: must be a hostname, not an IP address")
    if _HOSTNAME.fullmatch(host) is None:
        raise TargetsError(
            f"{where}: must be a lowercase hostname, labels of ASCII letters, digits and"
            " inner hyphens joined by dots, an internationalized name in its xn-- form,"
            " with no scheme, port, path or wildcard"
        )
    if host == _LOOPBACK_NAME or host.endswith(f".{_LOOPBACK_NAME}"):
        raise TargetsError(f"{where}: is a loopback name, never a host off loopback")
    if "." not in host:
        raise TargetsError(
            f"{where}: must hold two labels or more, so it cannot cover a whole"
            " top-level domain"
        )
    return host


def _is_ip_address(host: str) -> bool:
    """Whether `host` is an IP address: a literal, bracketed or not, or a numeric last label."""
    try:
        ipaddress.ip_address(host.strip(_IPV6_BRACKETS))
    except ValueError:
        return _NUMERIC_LABEL.fullmatch(host.rpartition(".")[2]) is not None
    return True
