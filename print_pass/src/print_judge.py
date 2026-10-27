"""What counts as a problem in a printed state: its read-back, its network, its emulation.

Pure judgment over what a state recorded; nothing here touches a browser. A finding is
something a reader's print would get wrong, and fails the run; a warning is reported only.
The finding texts are fixed, since a live suite asserts on them.

A request off loopback was aborted whatever is judged here. When its host is one the
targets file expects, the block is a warning, and so is an image that could not print from
such a host: the site is known to load from it, so a run is not red for that alone.

Entry point assumptions: `print_probe`'s read-back, `print_record`'s records,
`print_targets`' states and its `native-dark` scheme name, and `print_offsite`'s matcher,
the one place a URL is matched against the expected hosts.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Final

from print_offsite import on_expected_host
from print_probe import ProbeResult
from print_record import NetworkRecord, Problem
from print_targets import NATIVE_DARK, State

#: The warning every emulated-dark state carries. A page whose print styles force light
#: prints emulated dark exactly as it prints light, so the warning names the comparison
#: that decides it rather than claiming a difference it never measured.
EMULATED_DARK_WARNING: Final = (
    "EMULATED dark: Playwright's emulated color scheme persists into page.pdf(), while a real "
    "dark OS prints light (Chrome 153, measured); where this PDF differs from a light print, "
    "no reader's print looks like it"
)

# Dark, as a state requests it and as a page reads it back; and the fonts status that is settled.
_DARK: Final = "dark"
_FONTS_LOADED: Final = "loaded"


def running_animations_warning(names: Sequence[str]) -> str:
    """The warning's text for animations `names`, in order, running when the page printed.

    The probe reads the SCREEN's animations, just before the print, and a print stylesheet
    may lay each one out finished, so the text says what ran and how to check, never that
    the paper caught one mid-way.
    """
    return (
        f"{len(names)} animation(s) running on screen at print ({', '.join(names)}): a print "
        "stylesheet may still show them finished; compare with a reduced-motion state"
    )


def finding(what: str) -> Problem:
    """A finding: something a reader's print would get wrong."""
    return Problem("finding", what)


def warning(what: str) -> Problem:
    """A warning: reported, never failing the run."""
    return Problem("warning", what)


def state_problems(
    state: State,
    probe: ProbeResult | None,
    network: NetworkRecord,
    *,
    expected_hosts: Collection[str],
    color_scheme_emulated: bool,
) -> tuple[Problem, ...]:
    """Every problem a state's read-back, network record and emulation show.

    `probe` is None for a state stopped before its read-back: its network and emulation are
    still judged. `expected_hosts` are the targets file's hosts off loopback: a request
    blocked on one, or an image problem whose source is on one, is a warning.
    `color_scheme_emulated` is whether the state's color scheme was emulated, as its record
    says: the emulated-dark warning is Playwright's emulation persisting into the print, so
    a dark scheme the browser was set to some other way (Firefox's own preference) is no
    such state.
    """
    emulated = (
        (warning(EMULATED_DARK_WARNING),)
        if color_scheme_emulated and state.color_scheme == _DARK
        else ()
    )
    if probe is None:
        return (*_network_problems(network, expected_hosts), *emulated)
    return (
        *_image_problems(probe, expected_hosts),
        *_network_problems(network, expected_hosts),
        *_animation_problems(probe),
        *emulated,
        *_native_dark_problems(state, probe),
    )


def _network_problems(
    network: NetworkRecord, expected_hosts: Collection[str]
) -> list[Problem]:
    """One problem per HTTP error, failed request, blocked request, page error and violation.

    Each is a finding but a request blocked on an expected host, which is a warning. A CSP
    violation naming an expected host is still a finding: the site is known to load from
    there, and its own policy refused it.
    """
    # A status is digits, so the first space in "<status> <path>" ends it.
    statuses = (entry.partition(" ") for entry in network.http_errors)
    return [
        *(finding(f"HTTP {status}: {path}") for status, _, path in statuses),
        *(finding(f"request failed: {entry}") for entry in network.failed),
        *(_blocked_problem(url, expected_hosts) for url in network.blocked),
        *(finding(f"page error: {line}") for line in network.page_errors),
        *(finding(f"csp violation: {line}") for line in network.csp_violations),
    ]


def _blocked_problem(url: str, expected_hosts: Collection[str]) -> Problem:
    """A request aborted off loopback: a warning on an expected host, else a finding."""
    if on_expected_host(url, expected_hosts):
        return warning(f"off-loopback request blocked as expected: {url}")
    return finding(f"blocked off-loopback request: {url}")


def _image_problems(
    probe: ProbeResult, expected_hosts: Collection[str]
) -> list[Problem]:
    """Images not complete at print, fonts not loaded, and images that loaded broken.

    Only an image with a src attribute is judged, `src=""` included. One without requested
    nothing -- a carousel's slide left sourceless until it is needed, while the carousel
    prints only the slide showing -- so it is never requested, neither broken nor loading.
    A static image missing its src is an HTML validator's to catch.
    """
    requested = [image for image in probe.images if image.has_src]
    problems = [
        _image_problem(
            f"image not complete at print: {image.src}", image.src, expected_hosts
        )
        for image in requested
        if not image.complete
    ]
    if probe.fonts_status != _FONTS_LOADED:
        problems.append(warning(f"fonts not loaded at print: {probe.fonts_status}"))
    problems.extend(
        _image_problem(f"image broken: {image.src}", image.src, expected_hosts)
        for image in requested
        if image.complete and image.natural_width == 0
    )
    return problems


def _image_problem(what: str, src: str, expected_hosts: Collection[str]) -> Problem:
    """An image that could not print: a warning when `src` is on an expected host.

    Every request off loopback is aborted, so such an image never arrives: its problem is
    the block's, and weighs what the block weighs. A source the browser could not have
    requested is on no expected host, since nothing was blocked. Any other is a finding.
    """
    return warning(what) if on_expected_host(src, expected_hosts) else finding(what)


def _animation_problems(probe: ProbeResult) -> tuple[Problem, ...]:
    """A warning when animations were still running on screen as the page printed."""
    if not probe.animations:
        return ()
    names = [animation.name for animation in probe.animations]
    return (warning(running_animations_warning(names)),)


def _native_dark_problems(state: State, probe: ProbeResult) -> tuple[Problem, ...]:
    """A finding when a native-dark state's page does not read dark."""
    if state.color_scheme != NATIVE_DARK or probe.color_scheme == _DARK:
        return ()
    return (finding(f"native dark not honored: the page reads {probe.color_scheme}"),)
