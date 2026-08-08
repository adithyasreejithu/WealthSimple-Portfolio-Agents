"""The run lifecycle state machine.

Kept as data rather than scattered `if` checks so the legal shape of a run's
history is readable in one place, and so an illegal transition fails loudly
instead of leaving a run in a state no later step knows how to handle.
"""

from __future__ import annotations

from .paths import WorkspaceError

CREATED = "created"
IN_PROGRESS = "in_progress"
AWAITING_INPUT = "awaiting_input"
AWAITING_HUMAN_REVIEW = "awaiting_human_review"
COMPLETED = "completed"
FAILED = "failed"
ARCHIVED = "archived"

STATUSES: tuple[str, ...] = (
    CREATED, IN_PROGRESS, AWAITING_INPUT, AWAITING_HUMAN_REVIEW, COMPLETED, FAILED, ARCHIVED,
)

# Terminal states a run may be archived from. A run is never deleted, so
# `failed` and `awaiting_*` runs remain on disk; archiving them is allowed
# because "keep it, but move it out of the active set" is a valid outcome for
# an abandoned run. Only `archived` itself has no exit.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    CREATED: frozenset({IN_PROGRESS, AWAITING_INPUT, FAILED, ARCHIVED}),
    IN_PROGRESS: frozenset({AWAITING_INPUT, AWAITING_HUMAN_REVIEW, COMPLETED, FAILED}),
    AWAITING_INPUT: frozenset({IN_PROGRESS, FAILED, ARCHIVED}),
    AWAITING_HUMAN_REVIEW: frozenset({IN_PROGRESS, COMPLETED, FAILED}),
    COMPLETED: frozenset({ARCHIVED}),
    FAILED: frozenset({IN_PROGRESS, ARCHIVED}),
    ARCHIVED: frozenset(),
}

# Reaching one of these means no further work is expected without an explicit
# reopen. Used by `validate` to decide whether an empty manifest is a problem.
TERMINAL_STATUSES = frozenset({COMPLETED, FAILED, ARCHIVED})


class InvalidTransitionError(WorkspaceError):
    """A status change the lifecycle does not permit."""


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    """Raise unless `current -> target` is a legal move.

    The error names the legal alternatives, because the common cause is a
    caller that skipped an intermediate state rather than one that picked a
    nonexistent status.
    """
    if target not in STATUSES:
        raise InvalidTransitionError(
            f"unknown status {target!r}; expected one of {', '.join(STATUSES)}"
        )
    if current == target:
        return
    if not can_transition(current, target):
        allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
        allowed_text = ", ".join(sorted(allowed)) if allowed else "(none -- terminal state)"
        raise InvalidTransitionError(
            f"cannot move a run from {current!r} to {target!r}; allowed from {current!r}: {allowed_text}"
        )
