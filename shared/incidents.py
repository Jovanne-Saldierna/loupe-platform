"""Incident lifecycle rules shared by data_quality_triage (writer),
loupe_agent, and metric_governance (readers).

This module owns two related, still-BigQuery-free responsibilities:
transition validation, and classifying which statuses currently count as
"active" for source-health purposes. Both are pure functions of already-
known status strings. Actual persistence (reading/writing Incident rows,
deriving SourceHealth from real query results) lives in
shared/data_service.py (Phase 2), which imports and depends on this
module rather than duplicating its rules.

Status semantics
-----------------
"detected" is a persisted status, not a transient event. It represents an
unconfirmed raw signal captured by a deterministic check
(shared/checks.py / apps/data_quality_triage/anomaly_engine.py) before a
human or automated process has confirmed it as an actionable incident.
"open" is the first status that represents a confirmed, actionable,
tracked incident.

Active-status classification
-----------------------------
Whether a status currently degrades source health is decided by explicit
set membership in ACTIVE_INCIDENT_STATUSES below -- never by comparing
positions in the lifecycle sequence or by wording like "open and later."
That phrasing was used in an earlier draft of this module and was wrong:
it would have implied "resolved" (which sorts after "open" in the
documented sequence) counts as active, which is precisely backwards.
Resolved incidents must not continue degrading current source health,
and detected-but-unconfirmed signals must not degrade it either, since
they haven't yet been confirmed as real.

Reopen vs. new linked incident
--------------------------------
The "resolved" -> "open" transition is allowed, but it means one specific
thing: the *same* incident's resolution turns out to have been premature
or incorrect -- e.g. a verification recheck run shortly after resolving
reveals the underlying condition never actually cleared. Reopening in
this sense preserves the complete transition and audit history under the
original incident_id.

It does NOT mean "the same type of problem happened again later." A
later, independent recurrence of the same check_type on the same table
is a new occurrence -- it has its own detection timestamp, its own
observed_value, and its own deterministic check run -- and must be
persisted as a *new* Incident record. That new record should set
Incident.recurrence_of_incident_id to the prior (resolved) incident's ID
so the two remain linked for pattern analysis, without conflating two
distinct occurrences into one record's history.

This distinction is documented here, ahead of Phase 2's persistence
design, exactly as required: shared/data_service.py's incident-writing
functions must decide "reopen this record" vs. "create a new record
linked to this one" using this rule, not by transition validity alone
(validate_transition() only proves resolved->open is a legal state
change -- it does not decide which of the two real-world cases applies;
that decision requires context validate_transition() does not have, such
as how much time has passed or whether this is a fresh check run).
"""

from __future__ import annotations

from shared.models import IncidentStatus

# Allowed transitions, per docs/data-quality-triage.md's documented lifecycle
# (detected -> open -> acknowledged -> investigating -> mitigated -> resolved),
# plus two realistic exceptions:
#   - investigating -> resolved: a root-cause fix can resolve an incident
#     directly, without a separate intermediate "mitigated" state.
#   - resolved -> open: see "Reopen vs. new linked incident" above -- this
#     represents correcting a premature resolution of the SAME incident,
#     not a fresh recurrence (which should be a new, linked Incident).
# Every other transition -- including skipping stages, moving backward
# through the happy path, or transitioning a status to itself -- is
# rejected.
ALLOWED_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    "detected": {"open"},
    "open": {"acknowledged"},
    "acknowledged": {"investigating"},
    "investigating": {"mitigated", "resolved"},
    "mitigated": {"resolved"},
    "resolved": {"open"},
}

# Explicit membership set, not derived from ALLOWED_TRANSITIONS or from
# any notion of lifecycle ordering. "detected" is excluded: an unconfirmed
# signal must not yet propagate as an active, trust-degrading incident.
# "resolved" is excluded: a resolved incident must not continue degrading
# current source health.
ACTIVE_INCIDENT_STATUSES: set[IncidentStatus] = {
    "open",
    "acknowledged",
    "investigating",
    "mitigated",
}


class InvalidTransitionError(ValueError):
    """Raised when an incident status transition is not permitted."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Cannot transition incident from {current!r} to {target!r}. "
            f"Allowed targets from {current!r}: "
            f"{sorted(ALLOWED_TRANSITIONS.get(current, set()))}"
        )


def can_transition(current: IncidentStatus, target: IncidentStatus) -> bool:
    """Return True if moving from `current` to `target` is permitted."""

    return target in ALLOWED_TRANSITIONS.get(current, set())


def validate_transition(current: IncidentStatus, target: IncidentStatus) -> None:
    """Raise InvalidTransitionError if the transition is not permitted.

    Callers (e.g. shared/data_service.py's incident writer) should call
    this before persisting any status change.
    """

    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def is_active_status(status: IncidentStatus) -> bool:
    """Return True if `status` should currently count toward a degraded
    source-health read.

    This is an explicit membership test against ACTIVE_INCIDENT_STATUSES.
    It deliberately does not use enum ordering, string comparison, or any
    "later in the sequence" logic, because the lifecycle sequence and the
    active/inactive classification are not the same axis: "resolved" is
    last in the sequence but must read as inactive, and "detected" is
    first but must also read as inactive.
    """

    return status in ACTIVE_INCIDENT_STATUSES
