"""The lines a run prints as it goes: a rendering with something to say, and states left out.

The CLI prints each one as it arrives; these build them, so what a line says is decided once,
here, and tested without a run.

Entry point assumptions: pure functions over a finished rendering's result and a validated
targets file.
"""

from __future__ import annotations

from collections.abc import Sequence

from pass_report import FAULT_VERDICT, MISS_VERDICT, RenderingResult
from pass_targets import STATE_SCRIPTS, Rendering, Targets

#: Proof verdicts worth a line as they arrive: a detector that missed its plant, and a plant
#: that never took. `n/a` and `not measured` had nothing that could have caught them. Taken from
#: the report's own two rather than spelled again, so a third cannot start going unannounced.
UNCAUGHT_VERDICTS = (MISS_VERDICT, FAULT_VERDICT)


def announcement(result: RenderingResult) -> str | None:
    """A finished rendering's line -- its faults, findings and uncaught plants -- or None."""
    missed = [
        d for d, verdict in result.verdicts.items() if verdict in UNCAUGHT_VERDICTS
    ]
    notes = [
        *result.faults,
        *result.findings,
        *(f"{d} {result.verdicts[d]}" for d in missed),
    ]
    return f"{result.rendering.label}: {', '.join(notes)}" if notes else None


def states_left_out(targets: Targets, renderings: Sequence[Rendering]) -> str | None:
    """How many of `targets`' states `renderings` render none of, and why; None if none.

    A state renders with scripts on only, so a run whose `--scripts` leaves that out renders
    none -- counted and said rather than dropped in silence.
    """
    rendered = {r.state for r in renderings}
    left_out = [s.name for s in targets.states if s.name not in rendered]
    if not left_out:
        return None
    count = len(left_out)
    return (
        f"{count} state{'s' if count != 1 else ''} not rendered: a state renders with "
        f"scripts {STATE_SCRIPTS} only ({', '.join(left_out)})"
    )
