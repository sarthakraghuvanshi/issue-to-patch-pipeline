"""Terminal run states.

Principle 2 from ``IMPLEMENTATION_PLAN.md``: *the system must never silently claim
success*. Every run finishes in exactly one of these states, and nothing else.
"""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    """The only ways a run is allowed to end."""

    PATCH_VALIDATED = "PATCH_VALIDATED"
    PATCH_REQUIRES_HUMAN_REVIEW = "PATCH_REQUIRES_HUMAN_REVIEW"
    PATCH_REJECTED = "PATCH_REJECTED"
    INVESTIGATION_INCONCLUSIVE = "INVESTIGATION_INCONCLUSIVE"


TERMINAL_STATES: frozenset[RunState] = frozenset(RunState)
"""All run states are terminal; there is no in-progress sentinel here.

In-progress tracking lives on the run row (``finished_at IS NULL``), not in this
enum, so a half-finished run can never be mistaken for a real outcome.
"""


def is_terminal(state: str) -> bool:
    """Return True if ``state`` is a recognised terminal run state."""
    return state in RunState.__members__ or state in {s.value for s in RunState}
