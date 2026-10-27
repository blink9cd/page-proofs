#!/usr/bin/env python3
"""Own the one ``--help`` formatter this project's CLIs use.

Why this exists
---------------
Each CLI's ``--help`` shows every optional argument's default, derived
from the live ``default=`` rather than hardcoded into ``help=`` text that drifts.
``argparse.ArgumentDefaultsHelpFormatter`` does that -- but on CPython < 3.13 it also
stamps a misleading ``(default: None)`` onto *required* options, which have no default a
caller could ever rely on. The documented workaround is a subclass whose
``_get_help_string`` returns ``action.help`` unchanged for a required action.

This module is that subclass, kept in one place rather than copied into each CLI.

The version trap this module is really about
--------------------------------------------
**The bug is fixed upstream, and an interpreter newer than the fix hides it.** Measured
on 3.14: the stock formatter already returns ``action.help`` unchanged for a required
action, with either ``default=None`` or a real default. So on 3.13 and later this shim is
a no-op, and any test that asserts on rendered ``--help`` output passes just as well with
the override deleted.

It is still needed. The OLDEST runtime the artifact must run on, this project's 3.12
floor, is below the fix. The shim earns its keep only on the older interpreters, which is why it
must be pinned by a test that does not depend on the local interpreter's behavior. See
``test_help_formatter.py``: the required-option contract is asserted against a stand-in
for old argparse, not against CPython's current output.

Entry point assumptions
-----------------------
Importers bind :class:`ReqAwareDefaultsFormatter` -- or :class:`RawNoneHidingFormatter`,
for a CLI whose epilog is laid out by hand and whose ``None`` defaults mean "not given",
or :class:`UnsetHidingFormatter`, for a prose epilog and ``None`` or empty-list defaults
that mean the same -- as their parser's ``formatter_class`` and nothing else. The module
is pure -- no I/O, no filesystem access, no config -- so it carries no ``stdio_utf8``
dependency and cannot fail at import time.
"""

from __future__ import annotations

import argparse


class ReqAwareDefaultsFormatter(argparse.ArgumentDefaultsHelpFormatter):
    """Show optionals' defaults in ``--help``; drop the bogus one on required options.

    Behaves identically to its base class on CPython >= 3.13, where the required-option
    bug is already fixed; the override is what keeps ``--help`` honest on the 3.12
    floor.
    """

    def _get_help_string(self, action: argparse.Action) -> str | None:
        """Return *action*'s help text, suppressing the default note when required.

        A required option cannot fall back to its default, so advertising one is
        actively misleading. Everything else delegates, keeping the base class's
        ``SUPPRESS`` handling and its ``%(default)s`` interpolation intact.
        """
        if action.required:
            return action.help
        return super()._get_help_string(action)


class RawNoneHidingFormatter(
    ReqAwareDefaultsFormatter, argparse.RawDescriptionHelpFormatter
):
    """Show real defaults; hide them for required options and a ``None`` default.

    Also keeps the description's and epilog's line breaks, for an epilog that lays out
    prerequisites or exit codes as a table. The two bases override different methods, so
    they compose. A default of ``None`` is no value a person could type, so it is not
    shown: each such option's help should say instead what leaving it out means.
    """

    def _get_help_string(self, action: argparse.Action) -> str | None:
        """Return *action*'s help, with its default appended unless that is ``None``.

        A non-``None`` default delegates, so the required-option override still applies.
        """
        if action.default is None:
            return action.help
        return super()._get_help_string(action)


class UnsetHidingFormatter(ReqAwareDefaultsFormatter):
    """Show real defaults; hide them for required options and for an unset default.

    The non-raw counterpart of :class:`RawNoneHidingFormatter`, for a CLI whose epilog is
    prose argparse may rewrap. "Unset" means ``None`` or an EMPTY list or tuple: the first is
    what an option left out holds, and the second is what an ``append`` or ``extend`` option
    holds before any value is given. Neither is a value a person could type, so
    ``(default: None)`` and ``(default: [])`` would say nothing true; each such option's help
    should say instead what leaving it out means. An empty string is still shown, because
    ``--flag ''`` can be typed.
    """

    def _get_help_string(self, action: argparse.Action) -> str | None:
        """Return *action*'s help, with its default appended unless that default is unset.

        A set default delegates, so the required-option override still applies.
        """
        default = action.default
        if default is None or (isinstance(default, (list, tuple)) and not default):
            return action.help
        return super()._get_help_string(action)
