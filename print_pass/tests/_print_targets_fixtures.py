"""Shared by the print_targets suites: the minimal targets documents their cases build on.

Each builder returns a plain dictionary shaped as `tomllib` would produce it (one valid
target, named `home` and served at `/`), with the caller's keys layered on, so a case
states only the one key it is about. Nothing here imports the subject, so this module
imports alone and needs no `sys.path` insert of its own.
"""

from __future__ import annotations


def doc(**target_keys: object) -> dict[str, object]:
    """A targets document holding one minimal target, with `target_keys` layered onto it."""
    return {"target": [{"name": "home", "page": "/", **target_keys}]}


def state_doc(**state_keys: object) -> dict[str, object]:
    """A targets document whose one target holds one state, with `state_keys` layered on."""
    return doc(state=[{"name": "s", **state_keys}])


def read_doc(read: object) -> dict[str, object]:
    """A targets document with one file-level `[[read]]` table."""
    return {"read": [read], **doc()}


def viewport_doc(viewport: object) -> dict[str, object]:
    """A targets document carrying `viewport`."""
    return {"viewport": viewport, **doc()}


def hosts_doc(listed: object) -> dict[str, object]:
    """A targets document carrying `listed` as its expected off-loopback hosts."""
    return {"expected_offsite_hosts": listed, **doc()}
