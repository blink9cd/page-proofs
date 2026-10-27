"""What each engine is launched with beyond its defaults: an extension, and the egress guard.

A site's Content Security Policy is measured against the extensions its visitors install, so a
run can load one: `--chromium-extension` an unpacked folder, `--firefox-extension` a signed
`.xpi`. This module reads each one's manifest and refuses, before any browser opens, what cannot
be measured -- a missing manifest, a Firefox add-on with no gecko id to file it under, a Chromium
one with no service worker to confirm its load by, one asking for the `proxy` permission, with
which it could route requests around the guard below, an engine `--engines` names that has no
extension (WebKit cannot load one at all), or an extension for an engine the run leaves out.

`--loopback-only` is the egress guard: requests off 127.0.0.1 fail, the extension's own included.
It is implied by an extension, and exists on its own so the no-extension BASELINE can be measured
under the same guard. Each engine has its own guard, each measured to fail an off-loopback page
request while loopback still loads; what each guards beyond that differs:

- **Chromium** resolves no name but 127.0.0.1 (`--host-resolver-rules`), sends no UDP that no
  proxy carries -- and there is no proxy -- so WebRTC sends none (measured: a STUN request to a
  loopback port stopped), and speaks no QUIC; and runs its FULL build (`channel="chromium"`)
  rather than the headless shell, which cannot load an extension -- so a baseline is measured in
  the very build its extension run is;
- **Firefox** sends every request to a proxy on a port nothing listens on, loopback excepted, and
  lets no add-on bypass it; and has no WebRTC (measured: `RTCPeerConnection` is gone), no
  WebTransport, no HTTP/3 and no DNS prefetch, each set by a pref;
- **WebKit** sends its requests through Playwright's own proxy option the same way, and that is
  all: Playwright gives WebKit no switch for its UDP, and WebRTC in it still sends a STUN request
  under the guard (measured), so a WebKit run is guarded for its page's requests, not its UDP.

Entry point assumptions: the standard library only, so the CLI can validate with no Playwright
installed; `pass_extension_launch` launches what is planned here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlsplit

from pass_markdown import plain_text

#: The engines that can load an extension. WebKit cannot.
EXTENSION_ENGINES = ("chromium", "firefox")

#: The one host the egress guard lets a request reach: the address `--serve` listens on.
LOOPBACK_HOST = "127.0.0.1"

#: A port nothing listens on (discard), where the guard sends every off-loopback request.
REFUSING_PORT = 9

#: Playwright's name for the full Chromium build, as against its headless shell.
CHROMIUM_CHANNEL = "chromium"

#: Chromium's guard. The resolver rule applies to IP literals as to names, so loopback is
#: excluded by its exact address.
#: WebRTC's sockets never ask the resolver, so its UDP is refused by its own policy, which this
#: build names `--webrtc-ip-handling-policy` (measured: the older `--force-` spelling stopped
#: nothing); QUIC is refused outright.
CHROMIUM_GUARD_ARGS = (
    f"--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE {LOOPBACK_HOST}",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-quic",
)

#: Firefox's guard: a manual proxy for every scheme, which loopback alone bypasses and no add-on
#: may; then every channel that would not go through it -- WebRTC, WebTransport, HTTP/3 (each
#: UDP), and the name lookups a page's links would prefetch.
FIREFOX_GUARD_PREFS: dict[str, str | int | bool] = {
    "network.proxy.type": 1,
    "network.proxy.http": LOOPBACK_HOST,
    "network.proxy.http_port": REFUSING_PORT,
    "network.proxy.ssl": LOOPBACK_HOST,
    "network.proxy.ssl_port": REFUSING_PORT,
    "network.proxy.no_proxies_on": "",
    "network.proxy.allow_hijacking_localhost": False,
    "network.proxy.socks_remote_dns": True,
    "network.proxy.allow_bypass": False,
    "media.peerconnection.enabled": False,
    "network.webtransport.enabled": False,
    "network.http.http3.enable": False,
    "network.dns.disablePrefetch": True,
}

#: WebKit's guard, through Playwright's proxy option, bypassed by loopback alone.
WEBKIT_GUARD_PROXY = {
    "server": f"http://{LOOPBACK_HOST}:{REFUSING_PORT}",
    "bypass": LOOPBACK_HOST,
}

MANIFEST_NAME = "manifest.json"

#: A manifest or messages file past this size is refused unread: real ones are a few KB.
MANIFEST_LIMIT_BYTES = 1_048_576

#: The permission with which an extension could set its own proxy, around the guard: Chromium
#: hands a request to an extension's proxy without resolving its host (measured: one setting a
#: proxy on 127.0.0.1 had the browser's own requests sent there by name), and Firefox lets an
#: add-on answer each request with a proxy of its own.
_PROXY_PERMISSION = "proxy"

#: Whether Chromium here names an unpacked extension by its path as Windows spells one.
ON_WINDOWS = os.name == "nt"

#: How many bytes of an unpacked extension's key or path hash make its id, and the letter 0
#: maps to.
_ID_BYTES, _ID_ALPHABET_START = 16, ord("a")

#: The manifest field holding the extension's public key, base64, which fixes its id wherever
#: it is loaded from: a published extension's unpacked copy carries one.
_KEY = "key"

#: A key may be PEM-wrapped, as Chromium also loads it: the text between the end of the
#: `-----BEGIN ... KEY-----` line and the `-----END` footer, its whitespace dropped.
_PEM_BEGIN, _PEM_HEADER_END, _PEM_FOOTER = "-----BEGIN", "KEY-----", "-----END"

#: A localized manifest string: `__MSG_<key>__`, looked up in the default locale's messages.
_MESSAGE_PREFIX, _MESSAGE_SUFFIX = "__MSG_", "__"


@dataclass(frozen=True)
class ExtensionFacts:
    """What the report records of an engine's extension (the gecko id for Firefox alone)."""

    engine: str
    name: str
    version: str
    gecko_id: str | None = None


@dataclass(frozen=True)
class Extension:
    """An extension to load: its facts, where it is, and (Chromium) the worker confirming it."""

    facts: ExtensionFacts
    path: Path
    #: The manifest's background service worker, a path inside the extension; Chromium only.
    worker: str | None = None
    #: The id Chromium gives the unpacked extension, its worker's url's host: derived from the
    #: manifest's key where it has one, else from the folder's path (`chromium_id`).
    extension_id: str | None = None


@dataclass(frozen=True)
class Isolation:
    """What the run's engines are launched with beyond their defaults."""

    loopback_only: bool = False
    extensions: tuple[Extension, ...] = ()

    def extension(self, engine: str) -> Extension | None:
        """`engine`'s extension, or None when it runs without one."""
        return next((e for e in self.extensions if e.facts.engine == engine), None)

    @property
    def facts(self) -> tuple[ExtensionFacts, ...]:
        """Each extension's facts, in engine order, as the report records them."""
        return tuple(extension.facts for extension in self.extensions)


#: No extension, and no guard: every engine launched as it always was.
NO_ISOLATION = Isolation()

#: Reads a file inside an extension by its relative path: its bytes, or None when absent.
_Reader = Callable[[str], bytes | None]


class _Refused(Exception):
    """A manifest that cannot be measured with, and why."""


def plan_isolation(
    engines: Sequence[str],
    *,
    chromium: Path | None,
    firefox: Path | None,
    loopback_only: bool,
    base_url: str | None,
) -> Isolation | str:
    """The run's isolation, or the reason to refuse it.

    `base_url` is the targets file's when the run serves nothing itself; under the guard it must
    be on 127.0.0.1, the one host the guard lets through.
    """
    extensions: list[Extension] = []
    for path, read in ((chromium, read_unpacked), (firefox, read_xpi)):
        if path is not None:
            extension = read(path)
            if isinstance(extension, str):
                return extension
            extensions.append(extension)
    refusal = _engines_refusal(engines, extensions) if extensions else None
    guarded = loopback_only or bool(extensions)
    host = urlsplit(base_url).hostname if guarded and base_url is not None else None
    if refusal is None and host not in (None, LOOPBACK_HOST):
        refusal = (
            f"--loopback-only lets through {LOOPBACK_HOST} alone, and the targets file's "
            f"base_url is on {host}: pass --serve"
        )
    if refusal is not None:
        return refusal
    return Isolation(loopback_only=guarded, extensions=tuple(extensions))


def _engines_refusal(
    engines: Sequence[str], extensions: Sequence[Extension]
) -> str | None:
    """Why `engines` cannot run with `extensions`: one lacks an extension, or one goes unused."""
    have = {extension.facts.engine for extension in extensions}
    for engine in engines:
        if engine in have:
            continue
        if engine not in EXTENSION_ENGINES:
            return (
                f"--engines names {engine}, which cannot load an extension: leave {engine} "
                "out of --engines to measure with one"
            )
        return (
            f"--engines names {engine}, which has no extension: pass --{engine}-extension, "
            f"or leave {engine} out of --engines"
        )
    unused = next((engine for engine in sorted(have) if engine not in engines), None)
    if unused is not None:
        return f"--{unused}-extension is given, but --engines does not name {unused}"
    return None


def read_unpacked(folder: Path) -> Extension | str:
    """`--chromium-extension`: an unpacked folder's facts and worker, or why it is refused."""
    option = f"--chromium-extension {folder}"
    if not (folder / MANIFEST_NAME).is_file():
        return f"{option} is not a folder holding {MANIFEST_NAME}"

    def read(relative: str) -> bytes | None:
        path = folder / relative
        return _bounded_bytes(path) if path.is_file() else None

    # Chromium loads the folder by its absolute path with every link resolved, and names it so.
    path = folder.resolve()
    try:
        manifest = _manifest(read)
        _refuse_proxy(manifest)
        worker = _worker_of(manifest)
        name, version = _name_and_version(manifest, read)
        extension_id = _unpacked_id(manifest, path)
    except _Refused as refusal:
        return f"{option}: {refusal}"
    facts = ExtensionFacts("chromium", name, version)
    return Extension(facts, path, worker, extension_id)


def _unpacked_id(manifest: dict[str, Any], path: PurePath) -> str:
    """The id Chromium gives the unpacked extension at `path`: by its manifest's key, if any.

    Raises:
        _Refused: the manifest gives a `key` that is not base64 text, bare or PEM-wrapped,
            with which Chromium would not load it, and from which no id can be derived.
    """
    if _KEY not in manifest:
        return chromium_id(path, windows=ON_WINDOWS)
    key = manifest[_KEY]
    try:
        decoded = base64.b64decode(_unwrapped(key), validate=True)
    except ValueError:
        # binascii.Error is one, and a non-ASCII string raises the plain kind.
        decoded = b""
    if not decoded:
        raise _Refused(
            f'{MANIFEST_NAME}\'s "{_KEY}" is not base64 text, so the id Chromium gives the '
            "extension is unknown"
        )
    return _lettered_digest(decoded)


def _unwrapped(key: object) -> str:
    """A manifest key's base64 text: a PEM wrapping's inside, whitespace dropped; else as is.

    Not a string, or a wrapping with no inside, is no text at all, which nothing decodes to.
    """
    if not isinstance(key, str):
        return ""
    if not key.startswith(_PEM_BEGIN):
        return key
    start, end = key.find(_PEM_HEADER_END, len(_PEM_BEGIN)), key.rfind(_PEM_FOOTER)
    inside = key[start + len(_PEM_HEADER_END) : end] if 0 <= start < end else ""
    return "".join(inside.split())


def chromium_id(path: PurePath, *, windows: bool) -> str:
    """The id Chromium gives the unpacked extension at the absolute `path`, keyed by no key.

    Chromium's own rule: SHA-256 of the path's bytes as the platform stores them (UTF-8 here,
    UTF-16LE on Windows, its drive letter upper-cased first), its first 16 bytes in hex, each
    digit 0-f written as a letter a-p.
    """
    text = str(path)
    if windows and text[1:2] == ":":
        text = text[0].upper() + text[1:]
    return _lettered_digest(text.encode("utf-16-le") if windows else os.fsencode(text))


def _lettered_digest(data: bytes) -> str:
    """An extension id from `data`: its SHA-256's first 16 bytes in hex, 0-f lettered a-p.

    The bytes are a manifest key's, decoded, or else the unpacked folder's path's.
    """
    digest = hashlib.sha256(data).hexdigest()[: 2 * _ID_BYTES]
    return "".join(chr(_ID_ALPHABET_START + int(digit, 16)) for digit in digest)


def read_xpi(path: Path) -> Extension | str:
    """`--firefox-extension`: an `.xpi`'s facts and gecko id, or why it is refused."""
    option = f"--firefox-extension {path}"
    if not path.is_file():
        return f"{option} is not a file"
    try:
        with zipfile.ZipFile(path) as archive:
            facts = _xpi_facts(archive)
    except zipfile.BadZipFile:
        return f"{option} is not an .xpi (a zip archive)"
    except _Refused as refusal:
        return f"{option}: {refusal}"
    return Extension(facts, path.resolve())


def _xpi_facts(archive: zipfile.ZipFile) -> ExtensionFacts:
    """The facts of the add-on `archive` holds.

    Raises:
        _Refused: its manifest is missing or unreadable, or names no usable gecko id.
    """
    names = set(archive.namelist())

    def read(relative: str) -> bytes | None:
        if relative not in names:
            return None
        # The declared size refuses a big entry unopened; the bounded read refuses one whose
        # declared size understates it, so the bound never rests on the archive's word.
        too_big = _Refused(f"{relative} is larger than {MANIFEST_LIMIT_BYTES} bytes")
        if archive.getinfo(relative).file_size > MANIFEST_LIMIT_BYTES:
            raise too_big
        with archive.open(relative) as entry:
            data = entry.read(MANIFEST_LIMIT_BYTES + 1)
        if len(data) > MANIFEST_LIMIT_BYTES:
            raise too_big
        return data

    if MANIFEST_NAME not in names:
        raise _Refused(f"holds no {MANIFEST_NAME}")
    manifest = _manifest(read)
    _refuse_proxy(manifest)
    gecko_id = _gecko_id(manifest)
    name, version = _name_and_version(manifest, read)
    return ExtensionFacts("firefox", name, version, gecko_id)


def _bounded_bytes(path: Path) -> bytes:
    """`path`'s bytes, refused unread past the size a manifest ever needs.

    Raises:
        _Refused: the file is larger than `MANIFEST_LIMIT_BYTES`.
    """
    if path.stat().st_size > MANIFEST_LIMIT_BYTES:
        raise _Refused(f"{path.name} is larger than {MANIFEST_LIMIT_BYTES} bytes")
    return path.read_bytes()


def _manifest(read: _Reader) -> dict[str, Any]:
    """The manifest `read` gives, as a JSON object.

    Raises:
        _Refused: it is not JSON, or not an object.
    """
    data = read(MANIFEST_NAME) or b""
    try:
        manifest = json.loads(data)
    except ValueError as error:
        raise _Refused(f"{MANIFEST_NAME} is not JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise _Refused(f"{MANIFEST_NAME} is not a JSON object")
    return manifest


def _text(value: object) -> str | None:
    """`value` when it is non-empty text, else None."""
    return value if isinstance(value, str) and value else None


def _table(value: object) -> dict[str, Any]:
    """`value` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def _name_and_version(manifest: dict[str, Any], read: _Reader) -> tuple[str, str]:
    """The manifest's name, localized where it is a message, and its version.

    Raises:
        _Refused: either is missing or not text.
    """
    name, version = _text(manifest.get("name")), _text(manifest.get("version"))
    if name is None:
        raise _Refused(f'{MANIFEST_NAME} gives no "name"')
    if version is None:
        raise _Refused(f'{MANIFEST_NAME} gives no "version"')
    return _localized(name, _text(manifest.get("default_locale")), read), version


def _localized(text: str, locale: str | None, read: _Reader) -> str:
    """`text`, or the default locale's message it names; kept as written when none is found."""
    named = text.startswith(_MESSAGE_PREFIX) and text.endswith(_MESSAGE_SUFFIX)
    if not named or locale is None:
        return text
    key = text[len(_MESSAGE_PREFIX) : -len(_MESSAGE_SUFFIX)].lower()
    try:
        messages = json.loads(read(f"_locales/{locale}/messages.json") or b"{}")
    except ValueError:
        return text
    by_key = {str(k).lower(): v for k, v in _table(messages).items()}
    return _text(_table(by_key.get(key)).get("message")) or text


def _refuse_proxy(manifest: dict[str, Any]) -> None:
    """Refuse an extension asking for the `proxy` permission, now or when it runs.

    Raises:
        _Refused: `permissions` or `optional_permissions` names it.
    """
    for key in ("permissions", "optional_permissions"):
        asked = manifest.get(key)
        if isinstance(asked, list) and _PROXY_PERMISSION in asked:
            raise _Refused(
                f'asks for the "{_PROXY_PERMISSION}" permission, with which it could route '
                "requests around --loopback-only"
            )


def _worker_of(manifest: dict[str, Any]) -> str:
    """The manifest's background service worker, by which Chromium's load is confirmed.

    Raises:
        _Refused: it declares none.
    """
    worker = _text(_table(manifest.get("background")).get("service_worker"))
    if worker is None:
        raise _Refused(
            "declares no background service worker, so whether it loaded cannot be confirmed"
        )
    return worker


def _gecko_id(manifest: dict[str, Any]) -> str:
    """The add-on's gecko id, which names its file in the profile.

    Raises:
        _Refused: there is none, or it is not a plain file name.
    """
    settings = _table(manifest.get("browser_specific_settings")) or _table(
        manifest.get("applications")
    )
    gecko_id = _text(_table(settings.get("gecko")).get("id"))
    if gecko_id is None:
        raise _Refused(
            f"{MANIFEST_NAME} names no gecko id (browser_specific_settings.gecko.id)"
        )
    if Path(gecko_id).name != gecko_id or gecko_id.startswith("."):
        raise _Refused(f"its gecko id {gecko_id!r} is not a plain file name")
    return gecko_id


def guard_options(engine: str, isolation: Isolation) -> dict[str, Any]:
    """The launch options `engine` takes for the egress guard; none when the run is unguarded.

    A fresh copy each call, so a caller extending one changes no other launch.
    """
    if not isolation.loopback_only:
        return {}
    if engine == "chromium":
        return {"channel": CHROMIUM_CHANNEL, "args": list(CHROMIUM_GUARD_ARGS)}
    if engine == "firefox":
        return {"firefox_user_prefs": dict(FIREFOX_GUARD_PREFS)}
    return {"proxy": dict(WEBKIT_GUARD_PROXY)}


def isolation_lines(
    loopback_only: bool, extensions: Sequence[ExtensionFacts]
) -> list[str]:
    """The report header's lines: whether the guard was on, then each engine's extension."""
    guard = "refused (--loopback-only)" if loopback_only else "allowed"
    lines = [f"- Off-loopback requests: {guard}"]
    for facts in extensions:
        named = f"{facts.name} {facts.version}"
        if facts.gecko_id is not None:
            named += f" ({facts.gecko_id})"
        lines.append(f"- Extension in {facts.engine}: {plain_text(named)}")
    return lines
