"""S5: unit tests for the queue/token service (pure functions)."""

from datetime import datetime, timezone

import pytest

from app.services.queue_service import (
    queue_ahead_estimate,
    recommended_return_at,
    validate_status_transition,
)


def test_forward_transitions_allowed():
    assert validate_status_transition("waiting", "called") is True
    assert validate_status_transition("waiting", "served") is True
    assert validate_status_transition("waiting", "cancelled") is True
    assert validate_status_transition("called", "served") is True
    assert validate_status_transition("called", "cancelled") is True


def test_same_status_is_idempotent_noop():
    for s in ("waiting", "called", "served", "cancelled"):
        assert validate_status_transition(s, s) is False


def test_terminal_states_never_leave():
    for terminal in ("served", "cancelled"):
        for target in ("waiting", "called", "served", "cancelled"):
            if target == terminal:
                continue
            with pytest.raises(ValueError):
                validate_status_transition(terminal, target)


def test_no_backwards_or_unknown_moves():
    with pytest.raises(ValueError):
        validate_status_transition("called", "waiting")
    with pytest.raises(ValueError):
        validate_status_transition("waiting", "bogus")
    with pytest.raises(ValueError):
        validate_status_transition("bogus", "waiting")


def test_ahead_counts_eligible_tokens_only():
    # Eligible customers ahead, nothing else. Terminal tokens and the
    # tracked token itself are never included by callers.
    ahead, wait = queue_ahead_estimate(
        [("waiting", None), ("called", None)], 3.0, 1)
    assert (ahead, wait) == (2, 6.0)


def test_ahead_uses_remaining_service_of_in_service_tokens():
    from datetime import timedelta

    now = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
    # #1 called with window ending in 2 min (1 min already served),
    # #2 still waiting: 2 + 3 = 5 min on one counter.
    ahead, wait = queue_ahead_estimate(
        [("called", now + timedelta(minutes=2)), ("waiting", None)],
        3.0, 1, now,
    )
    assert ahead == 2
    assert wait == 5.0


def test_ahead_respects_counters_and_zero_guard():
    from datetime import timedelta

    now = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
    ahead, wait = queue_ahead_estimate(
        [("called", now + timedelta(minutes=2)), ("waiting", None)],
        4.0, 2, now,
    )
    assert (ahead, wait) == (2, 3.0)  # (2 + 4) / 2 counters
    assert queue_ahead_estimate([("waiting", None)], 4.0, 0)[1] is None
    assert queue_ahead_estimate([("waiting", None)], 4.0, None)[1] is None


def test_own_in_service_wait_is_own_remaining():
    from datetime import timedelta

    now = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
    ahead, wait = queue_ahead_estimate(
        [("waiting", None)], 3.0, 1, now,
        own_service_ends_at=now + timedelta(minutes=1, seconds=30),
    )
    assert ahead == 1
    assert wait == 1.5


def test_ahead_rejects_bad_input():
    with pytest.raises(ValueError):
        queue_ahead_estimate([("waiting", None)], -1.0, 1)
    with pytest.raises(ValueError):
        queue_ahead_estimate([("waiting", None)], 3.0, -1)


def test_recommended_return_at_is_now_plus_wait():
    now = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    assert recommended_return_at(24.0, now) == datetime(
        2026, 10, 14, 10, 24, tzinfo=timezone.utc
    )
    assert recommended_return_at(None, now) is None
    with pytest.raises(ValueError):
        recommended_return_at(-1.0, now)


# --- E2 expiry: 30-minute post-call validity window ---


def test_expired_is_terminal_and_has_nobody_ahead():
    from datetime import timedelta

    from app.services.queue_service import (
        TOKEN_VALIDITY_MINUTES_AFTER_CALLED,
        is_token_expired,
        token_expiry_time,
    )

    assert TOKEN_VALIDITY_MINUTES_AFTER_CALLED == 30
    # Expired (like served/cancelled) is terminal: callers never include
    # such tokens in ahead lists, so they contribute no wait.
    assert queue_ahead_estimate([], 3.0, 1) == (0, 0.0)
    # Expired tokens are terminal and system-only: no manual moves allowed.
    with pytest.raises(ValueError):
        validate_status_transition("expired", "waiting")
    with pytest.raises(ValueError):
        validate_status_transition("called", "expired")

    now = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    assert token_expiry_time(now) == datetime(
        2026, 10, 14, 10, 30, tzinfo=timezone.utc
    )
    assert token_expiry_time(None) is None
    assert is_token_expired("called", now - timedelta(minutes=1), now) is True
    assert is_token_expired("called", now + timedelta(minutes=1), now) is False
    assert is_token_expired("waiting", now - timedelta(minutes=99), now) is False
    assert is_token_expired("served", now - timedelta(minutes=99), now) is False


def test_validity_equals_estimated_wait():
    # Validity IS the estimated wait (whole minutes, min 1) — no separate
    # hardcoded duration. Uncomputable waits fall back to the setting.
    from app.services.queue_service import validity_for_estimate

    assert validity_for_estimate(5.0, 30) == 5
    assert validity_for_estimate(2.0, 30) == 2
    assert validity_for_estimate(4.2, 30) == 5  # ceil, matches display
    assert validity_for_estimate(0.0, 30) == 1  # minimum, never zero
    assert validity_for_estimate(None, 10) == 10  # fallback (e.g. C=0)
