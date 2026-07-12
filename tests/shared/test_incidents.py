"""Tests for shared/incidents.py -- incident lifecycle transition rules.

Per the migration spec: "Add tests for every permitted and prohibited
transition." ALL_ALLOWED_PAIRS below is a hand-written ground truth,
independent of shared.incidents.ALLOWED_TRANSITIONS, so this test suite
actually catches a wrong entry in the implementation rather than just
mirroring it back.
"""

from __future__ import annotations

import itertools

import pytest

from shared.incidents import (
    ACTIVE_INCIDENT_STATUSES,
    InvalidTransitionError,
    can_transition,
    is_active_status,
    validate_transition,
)

ALL_STATUSES = (
    "detected",
    "open",
    "acknowledged",
    "investigating",
    "mitigated",
    "resolved",
)

# Ground truth, written independently from the implementation.
ALL_ALLOWED_PAIRS = {
    ("detected", "open"),
    ("open", "acknowledged"),
    ("acknowledged", "investigating"),
    ("investigating", "mitigated"),
    ("investigating", "resolved"),
    ("mitigated", "resolved"),
    ("resolved", "open"),
}

ALL_PAIRS = set(itertools.product(ALL_STATUSES, ALL_STATUSES))
ALL_PROHIBITED_PAIRS = ALL_PAIRS - ALL_ALLOWED_PAIRS


@pytest.mark.parametrize("current,target", sorted(ALL_ALLOWED_PAIRS))
def test_permitted_transition_is_allowed_and_does_not_raise(current, target):
    assert can_transition(current, target) is True
    validate_transition(current, target)  # must not raise


@pytest.mark.parametrize("current,target", sorted(ALL_PROHIBITED_PAIRS))
def test_prohibited_transition_is_rejected(current, target):
    assert can_transition(current, target) is False
    with pytest.raises(InvalidTransitionError):
        validate_transition(current, target)


def test_self_transitions_are_all_prohibited():
    for status in ALL_STATUSES:
        assert can_transition(status, status) is False


def test_full_happy_path_is_walkable_in_order():
    happy_path = [
        "detected",
        "open",
        "acknowledged",
        "investigating",
        "mitigated",
        "resolved",
    ]
    for current, target in zip(happy_path, happy_path[1:]):
        validate_transition(current, target)  # must not raise


def test_shortcut_from_investigating_directly_to_resolved_is_allowed():
    validate_transition("investigating", "resolved")


def test_reopening_a_resolved_incident_is_allowed():
    validate_transition("resolved", "open")


def test_reopening_a_resolved_incident_cannot_skip_back_to_acknowledged():
    with pytest.raises(InvalidTransitionError):
        validate_transition("resolved", "acknowledged")


def test_error_message_reports_current_target_and_allowed_targets():
    with pytest.raises(InvalidTransitionError) as excinfo:
        validate_transition("detected", "resolved")
    message = str(excinfo.value)
    assert "detected" in message
    assert "resolved" in message
    assert "open" in message  # the one allowed target from "detected"


def test_every_status_pair_is_covered_by_ground_truth_or_prohibited():
    # Sanity check on the test data itself: every one of the 36 (status, status)
    # pairs must be classified as exactly one of allowed / prohibited.
    assert len(ALL_ALLOWED_PAIRS) + len(ALL_PROHIBITED_PAIRS) == len(ALL_PAIRS)
    assert ALL_ALLOWED_PAIRS.isdisjoint(ALL_PROHIBITED_PAIRS)


# ---------------------------------------------------------------------------
# Active-status classification (required correction: explicit set, not
# enum ordering / "open and later" wording)
# ---------------------------------------------------------------------------

# Ground truth, written independently from ACTIVE_INCIDENT_STATUSES.
EXPECTED_ACTIVE = {"open", "acknowledged", "investigating", "mitigated"}
EXPECTED_INACTIVE = {"detected", "resolved"}


@pytest.mark.parametrize("status", sorted(EXPECTED_ACTIVE))
def test_active_statuses_are_classified_as_active(status):
    assert is_active_status(status) is True


@pytest.mark.parametrize("status", sorted(EXPECTED_INACTIVE))
def test_detected_and_resolved_are_not_classified_as_active(status):
    assert is_active_status(status) is False


def test_detected_incident_does_not_produce_an_active_degraded_source_status():
    # A raw, unconfirmed signal must not count toward source degradation.
    assert is_active_status("detected") is False


def test_resolved_incident_does_not_produce_an_active_degraded_source_status():
    # A fixed incident must not continue degrading current source health,
    # even though "resolved" is the last status in the documented sequence.
    assert is_active_status("resolved") is False


def test_active_status_set_covers_every_status_exactly_once():
    assert ACTIVE_INCIDENT_STATUSES == EXPECTED_ACTIVE
    assert EXPECTED_ACTIVE | EXPECTED_INACTIVE == set(ALL_STATUSES)
    assert EXPECTED_ACTIVE.isdisjoint(EXPECTED_INACTIVE)


def test_active_status_classification_does_not_depend_on_lifecycle_ordering():
    # Regression guard for the exact bug this correction fixes: "resolved"
    # sorts after "open" in the documented lifecycle sequence, so any
    # ordering-based or "open and later" implementation would incorrectly
    # classify it as active. Assert the opposite explicitly.
    happy_path = [
        "detected",
        "open",
        "acknowledged",
        "investigating",
        "mitigated",
        "resolved",
    ]
    open_index = happy_path.index("open")
    resolved_index = happy_path.index("resolved")
    assert resolved_index > open_index  # resolved genuinely sorts after open
    assert is_active_status("resolved") is False  # yet must not be active
