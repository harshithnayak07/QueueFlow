"""S5: unit tests for the queue/token service (pure functions)."""

from datetime import datetime, timezone

import pytest

from app.services.queue_service import (
    recommended_return_at,
    token_people_ahead,
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


def test_token_people_ahead_counts_physical_plus_earlier():
    assert token_people_ahead(8, 2, "waiting") == 10
    assert token_people_ahead(8, 2, "called") == 10
    assert token_people_ahead(0, 0, "waiting") == 0


def test_terminal_tokens_have_nobody_ahead():
    assert token_people_ahead(8, 5, "served") == 0
    assert token_people_ahead(8, 5, "cancelled") == 0


def test_token_people_ahead_rejects_bad_input():
    with pytest.raises(ValueError):
        token_people_ahead(-1, 0, "waiting")
    with pytest.raises(ValueError):
        token_people_ahead(0, -1, "waiting")
    with pytest.raises(ValueError):
        token_people_ahead(0, 0, "bogus")


def test_recommended_return_at_is_now_plus_wait():
    now = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    assert recommended_return_at(24.0, now) == datetime(
        2026, 10, 14, 10, 24, tzinfo=timezone.utc
    )
    assert recommended_return_at(None, now) is None
    with pytest.raises(ValueError):
        recommended_return_at(-1.0, now)
