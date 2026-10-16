"""S3: unit tests for the waiting-time service (pure functions)."""

import pytest

from app.services.waiting_time_service import (
    ESTIMATE_NOTE,
    estimate_wait_minutes,
    recommend_virtual_queue,
)


def test_spec_example_12_people_4_min_2_counters():
    assert estimate_wait_minutes(12, 4.0, 2) == 24.0


def test_zero_counters_never_divides_by_zero():
    assert estimate_wait_minutes(12, 4.0, 0) is None


def test_empty_queue_or_zero_service_is_zero_wait():
    assert estimate_wait_minutes(0, 4.0, 2) == 0.0
    assert estimate_wait_minutes(12, 0.0, 2) == 0.0


def test_missing_inputs_are_not_computable():
    assert estimate_wait_minutes(None, 4.0, 2) is None
    assert estimate_wait_minutes(12, None, 2) is None
    assert estimate_wait_minutes(12, 4.0, None) is None


def test_negative_inputs_rejected():
    with pytest.raises(ValueError):
        estimate_wait_minutes(-1, 4.0, 2)
    with pytest.raises(ValueError):
        estimate_wait_minutes(5, -1.0, 2)
    with pytest.raises(ValueError):
        estimate_wait_minutes(5, 4.0, -1)


def test_people_ahead_counts_active_tokens_not_physical():
    # The session view counts eligible customers in the system. The
    # confirmed physical count is capacity/backdrop, never added.
    from app.services.queue_service import queue_ahead_estimate

    ahead, wait = queue_ahead_estimate(
        [("waiting", None), ("waiting", None)], 5.0, 1)
    assert (ahead, wait) == (2, 10.0)
    ahead, wait = queue_ahead_estimate([], 5.0, 1)
    assert (ahead, wait) == (0, 0.0)


def test_recommendation_is_advisory_only():
    below, _ = recommend_virtual_queue(3, False, threshold=10)
    assert below is False
    above, reason = recommend_virtual_queue(12, False, threshold=10)
    assert above is True
    assert "threshold" in reason
    # Already active -> no recommendation (owner already decided).
    active, _ = recommend_virtual_queue(50, True, threshold=10)
    assert active is False


def test_estimate_note_labels_approximate():
    assert "pproximate" in ESTIMATE_NOTE
    assert "guarantee" in ESTIMATE_NOTE.lower()
