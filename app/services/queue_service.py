"""S5: virtual-queue / token service (pure functions, no DB imports).

Owns the token lifecycle rules so route handlers stay thin:

- Status transitions are forward-only. ``served`` / ``cancelled`` are
  terminal; a token can never leave them. Same-status updates are a
  no-op success (idempotent), never an error.
- Per-token people-ahead: confirmed physical remainder + earlier ACTIVE
  virtual tokens (lower token_number, status waiting/called). Served /
  cancelled tokens occupy no queue position, and a served/cancelled
  token itself has nobody ahead.
- ``recommended_return_at`` is ``now + estimated_wait``; None when the
  wait is not computable. Callers pass None through as "unavailable".

Wait math itself lives in waiting_time_service; this module only
decides WHO counts as ahead.
"""

from datetime import datetime, timedelta, timezone

from app.models.token import (
    TOKEN_STATUS_CALLED,
    TOKEN_STATUS_CANCELLED,
    TOKEN_STATUS_SERVED,
    TOKEN_STATUS_WAITING,
)

TOKEN_STATUSES = (
    TOKEN_STATUS_WAITING,
    TOKEN_STATUS_CALLED,
    TOKEN_STATUS_SERVED,
    TOKEN_STATUS_CANCELLED,
)

TERMINAL_TOKEN_STATUSES = (TOKEN_STATUS_SERVED, TOKEN_STATUS_CANCELLED)

# Forward-only transitions (S5 assumption: no requeue from called back to
# waiting; the owner cancels and the customer takes a fresh token).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    TOKEN_STATUS_WAITING: frozenset(
        {TOKEN_STATUS_CALLED, TOKEN_STATUS_SERVED, TOKEN_STATUS_CANCELLED}
    ),
    TOKEN_STATUS_CALLED: frozenset(
        {TOKEN_STATUS_SERVED, TOKEN_STATUS_CANCELLED}
    ),
    TOKEN_STATUS_SERVED: frozenset(),
    TOKEN_STATUS_CANCELLED: frozenset(),
}


def validate_status_transition(current: str, new: str) -> bool:
    """Validate an owner-requested status change.

    Returns True when the status actually changes, False when it is a
    same-status no-op. Raises ValueError describing the problem for
    anything else (unknown status or illegal transition); routes map
    that to HTTP 409.
    """
    if new not in TOKEN_STATUSES:
        raise ValueError(f"Unknown token status: {new!r}.")
    if current not in TOKEN_STATUSES:
        raise ValueError(f"Unknown current token status: {current!r}.")
    if current == new:
        return False
    if new not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(
            f"Cannot move token from {current!r} to {new!r}. "
            f"{current!r} tokens can only move to "
            f"{sorted(ALLOWED_TRANSITIONS[current]) or 'nowhere (terminal)'}."
        )
    return True


def token_people_ahead(
    physical_people_remaining: int,
    earlier_active_count: int,
    own_status: str,
) -> int:
    """People ahead of one token holder.

    Terminal tokens (served/cancelled) have nobody ahead — their wait is
    over. Otherwise: confirmed physical remainder + earlier active
    virtual tokens.
    """
    if physical_people_remaining < 0:
        raise ValueError("physical_people_remaining must be >= 0")
    if earlier_active_count < 0:
        raise ValueError("earlier_active_count must be >= 0")
    if own_status not in TOKEN_STATUSES:
        raise ValueError(f"Unknown token status: {own_status!r}.")
    if own_status in TERMINAL_TOKEN_STATUSES:
        return 0
    return physical_people_remaining + earlier_active_count


def recommended_return_at(
    estimated_wait_minutes: float | None,
    now: datetime | None = None,
) -> datetime | None:
    """Return-time hint = now + estimated wait; None when not computable."""
    if estimated_wait_minutes is None:
        return None
    if estimated_wait_minutes < 0:
        raise ValueError("estimated_wait_minutes must be >= 0")
    now = now or datetime.now(timezone.utc)
    return now + timedelta(minutes=estimated_wait_minutes)
