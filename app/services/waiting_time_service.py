"""S3: waiting-time estimation service (pure functions, no DB imports).

Baseline formula (spec section 4, step 4):
    wait_min ~= people_ahead * average_service_minutes / active_counters

Rules enforced here:
- Never divide by zero: zero/negative counters -> None (not computable).
- Invalid inputs (negatives) raise ValueError; missing (None) -> None.
- Results are APPROXIMATE, never guaranteed; callers must surface the
  estimate note to users (see ESTIMATE_NOTE).
- Recommendation never auto-activates anything; the owner decides.
"""

ESTIMATE_NOTE = (
    "Approximate estimate only — based on the owner-confirmed queue size "
    "and current service settings, not a guaranteed waiting time."
)

# MVP default for the virtual-queue recommendation (assumption, S3).
# No universal value is justified by the spec; 10 people ahead is a starting
# point the owner can interpret. Made a parameter everywhere it is used so
# tests and future settings can override it without code changes.
VIRTUAL_QUEUE_THRESHOLD_DEFAULT = 10

# Token statuses that still occupy queue positions ahead of a newcomer.
# Served/cancelled tokens no longer count. (S5 owns transitions; S3 only reads.)
ACTIVE_TOKEN_STATUSES = ("waiting", "called")


def estimate_wait_minutes(
    people_ahead: int | None,
    average_service_minutes: float | None,
    active_counters: int | None,
) -> float | None:
    """Return approximate wait in minutes, or None when not computable.

    None means "cannot be estimated right now" (missing inputs or no active
    counters) — callers must render it as unavailable, never as 0.
    """
    if (
        people_ahead is None
        or average_service_minutes is None
        or active_counters is None
    ):
        return None
    if people_ahead < 0:
        raise ValueError("people_ahead must be >= 0")
    if average_service_minutes < 0:
        raise ValueError("average_service_minutes must be >= 0")
    if active_counters < 0:
        raise ValueError("active_counters must be >= 0")
    if active_counters == 0:
        return None
    if people_ahead == 0 or average_service_minutes == 0:
        return 0.0
    return round(people_ahead * average_service_minutes / active_counters, 2)


def recommend_virtual_queue(
    people_ahead: int,
    virtual_queue_enabled: bool,
    threshold: int = VIRTUAL_QUEUE_THRESHOLD_DEFAULT,
) -> tuple[bool, str]:
    """Recommend (never auto-activate) the virtual queue.

    Returns (recommended, reason). Recommendation is advisory only.
    """
    if virtual_queue_enabled:
        return False, "Virtual queue is already active."
    if people_ahead >= threshold:
        return True, (
            f"Approximately {people_ahead} people ahead "
            f"(threshold {threshold}); consider activating the virtual queue."
        )
    return False, (
        f"Queue is below the advisory threshold "
        f"({people_ahead} < {threshold}); virtual queue not recommended yet."
    )
