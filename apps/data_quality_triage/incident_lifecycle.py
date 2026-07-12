"""Incident lifecycle operations for Data Quality Triage.

A thin wrapper over shared.data_service's incident-persistence functions
(get_incident, list_active_incidents_for_table, derive_source_health,
apply_incident_transition) and shared.incidents' transition rules. This
module defines no lifecycle logic of its own: per the Phase 4 constraint
"Use shared Incident and SourceHealth contracts," Triage does not maintain
a second, parallel state machine or its own notion of which transitions are
legal -- shared/incidents.py's ALLOWED_TRANSITIONS is the only source of
truth for that, exactly as it already is for Loupe and Governance.

*** Persistence behavior ***
In persisted mode the UI-facing lifecycle functions call the shared,
transactional incident persistence service and record durable transition
history. In constants mode they validate and return a session-only outcome.

This module does not swallow that gap. IncidentNotFoundError,
ConcurrentModificationError, and InvalidTransitionError (all raised by
shared/data_service.py or shared/incidents.py) are real, meaningful
application errors -- e.g. "you tried to resolve a detected incident" -- and
are allowed to propagate unchanged, so a caller can show the user what
actually went wrong. Any *other* exception (e.g. what a real BigQuery
client raises when loupe_platform.incidents does not exist) is wrapped in
LivePersistenceUnavailableError, giving apps/data_quality_triage/ui.py and
main.py exactly one exception type to catch in order to render an honest
"incident lifecycle is not connected yet" state -- never a fabricated empty
list or a silently-assumed-healthy status. See docs/data-quality-triage.md:
"the resulting source status must be available to both Loupe responses and
governance SQL reviews" -- an unavailable status must read as unavailable,
not as healthy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from shared.config import PersistenceMode, PlatformConfig
from shared.data_service import (
    BigQueryClientLike,
    ConcurrentModificationError,
    IncidentNotFoundError,
    apply_incident_transition,
    derive_source_health,
    get_incident,
    list_active_incidents_for_table,
)
from shared.incident_persistence import (
    get_incident_state,
    record_incident_transition,
)
from shared.incidents import ALLOWED_TRANSITIONS, InvalidTransitionError, validate_transition
from shared.models import Incident, IncidentStatus, SourceHealth
from shared.persistence_transactions import (
    ConcurrentModificationError as PersistedConcurrentModificationError,
)
from shared.persistence_transactions import PayloadConflictError

# Two distinct exception classes both named ConcurrentModificationError
# exist in this codebase (shared.data_service's, raised by the
# constants-mode/read-side apply_incident_transition() path below, and
# shared.persistence_transactions', raised by execute_transaction() after
# a persisted transition's retry budget is exhausted). Both represent the
# same real-world condition -- a caller acted on state that is no longer
# current -- so both are treated as known, meaningful application errors
# here, never wrapped into LivePersistenceUnavailableError. PayloadConflictError
# is included for the same reason: a real, meaningful outcome a caller
# should see and handle, not an infrastructure failure.
_KNOWN_APPLICATION_ERRORS = (
    IncidentNotFoundError,
    ConcurrentModificationError,
    InvalidTransitionError,
    PersistedConcurrentModificationError,
    PayloadConflictError,
)


class LivePersistenceUnavailableError(RuntimeError):
    """Raised when a lifecycle operation fails for a reason other than one
    of the three well-understood application errors above -- in practice,
    today, this means "there is no live loupe_platform.incidents table to
    query yet" (Phase 6 has not happened). Wraps and chains the original
    exception via `__cause__` so nothing about the underlying failure is
    lost, but gives callers one exception type to catch for the honest
    "not connected yet" UI state.
    """


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except _KNOWN_APPLICATION_ERRORS:
        raise
    except Exception as exc:  # noqa: BLE001 -- deliberately broad; see class docstring
        raise LivePersistenceUnavailableError(
            "Incident lifecycle persistence is unavailable. Refresh the "
            "incident state and try again."
        ) from exc


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def fetch_incident(client: "BigQueryClientLike", incident_id: str) -> Optional[Incident]:
    """Look up one incident by id."""

    return _call(get_incident, client, incident_id)


def list_open_incidents(client: "BigQueryClientLike", dataset: str, table_id: str) -> list[Incident]:
    """List every currently-active incident for one table, per
    shared.incidents.ACTIVE_INCIDENT_STATUSES."""

    return _call(list_active_incidents_for_table, client, dataset, table_id)


def source_health_for(client: "BigQueryClientLike", dataset: str, table_id: str) -> SourceHealth:
    """Derive current SourceHealth for one table from its active incidents."""

    return _call(derive_source_health, client, dataset, table_id)


def next_allowed_statuses(current_status: IncidentStatus) -> list[IncidentStatus]:
    """Pure helper for ui.py to know which transition actions to offer for
    an incident's current status, without duplicating
    shared.incidents.ALLOWED_TRANSITIONS locally. Never touches a client --
    this cannot fail with LivePersistenceUnavailableError."""

    return sorted(ALLOWED_TRANSITIONS.get(current_status, set()))


# ---------------------------------------------------------------------------
# Transitions
#
# Each named wrapper below is the ONE lifecycle-transition entry point
# both the real Triage UI (apps/data_quality_triage/ui.py) and
# tools/phase6e_ops/live_integration_validation.py call -- there is no
# second, separate implementation. Two modes:
#
#   mode="constants" (the default, unchanged from before this correction):
#     a read+validate-only call over shared.data_service.apply_incident_transition().
#     No write of any kind happens; the returned outcome is explicitly
#     session_only=True/persisted=False, matching this module's original,
#     narrower "contract-only until persistence exists" scope. Any caller
#     (e.g. a BigQuery-backed script that just wants transition validation
#     against a live incidents table without writing anything) that
#     already depended on this module's original behavior is unaffected.
#
#   mode="persisted": reads the incident's CURRENT persisted state via
#     shared.incident_persistence.get_incident_state() (never an
#     in-memory/possibly-stale object the caller happened to be holding),
#     requires the caller to supply `expected_current_status` (the status
#     it last displayed) so a genuine concurrent change is caught as
#     ConcurrentModificationError rather than silently overwritten, and
#     then calls shared.incident_persistence.record_incident_transition()
#     with that freshly-read row_version -- the same function
#     tools/phase6e_ops/live_integration_validation.py's resolution step
#     calls, via this exact wrapper (see that script's run_validation()).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleTransitionOutcome:
    """The outcome of a lifecycle transition action.

    `persisted`/`session_only` are mutually exclusive and always set
    consistently with `mode`: a persisted-mode call that succeeds always
    has persisted=True, session_only=False; a constants-mode call always
    has persisted=False, session_only=True. Callers (ui.py) must use
    these -- never assume based on which function was called -- to decide
    whether to label a transition as durable or as "this run only."
    """

    incident_id: str
    status: IncidentStatus
    persisted: bool
    session_only: bool
    row_version: Optional[int] = None
    resolution_notes: Optional[str] = None


def _transition(
    client: "BigQueryClientLike",
    incident_id: str,
    target_status: IncidentStatus,
    *,
    expected_current_status: Optional[IncidentStatus],
    resolution_notes: Optional[str],
    mode: PersistenceMode,
    actor: str,
    config: Optional[PlatformConfig],
    transition_id: Optional[str],
) -> LifecycleTransitionOutcome:
    if mode == "persisted":
        if expected_current_status is None:
            raise ValueError(
                "expected_current_status is required when mode='persisted' -- "
                "pass the status currently displayed to the user (or, for a "
                "script, the status it just read) so a genuine concurrent "
                "change is caught rather than silently overwritten."
            )
        return _persisted_transition(
            client,
            incident_id,
            target_status,
            expected_current_status=expected_current_status,
            resolution_notes=resolution_notes,
            actor=actor,
            config=config,
            transition_id=transition_id,
        )

    updated = _call(
        apply_incident_transition,
        client,
        incident_id,
        target_status,
        expected_current_status=expected_current_status,
        resolution_notes=resolution_notes,
    )
    return LifecycleTransitionOutcome(
        incident_id=updated.incident_id,
        status=updated.status,
        persisted=False,
        session_only=True,
        row_version=None,
        resolution_notes=updated.resolution_notes,
    )


def _persisted_transition(
    client: "BigQueryClientLike",
    incident_id: str,
    target_status: IncidentStatus,
    *,
    expected_current_status: IncidentStatus,
    resolution_notes: Optional[str],
    actor: str,
    config: Optional[PlatformConfig],
    transition_id: Optional[str],
) -> LifecycleTransitionOutcome:
    current = _call(get_incident_state, client, incident_id, config=config)
    if current is None:
        raise IncidentNotFoundError(f"No persisted incident found with incident_id={incident_id!r}")

    if current.status != expected_current_status:
        raise ConcurrentModificationError(
            f"Incident {incident_id!r} status changed since it was last "
            f"displayed: expected {expected_current_status!r}, but the "
            f"currently persisted status is {current.status!r}. Re-fetch "
            "the incident and retry."
        )

    # Fail fast against the SAME rule record_incident_transition() itself
    # enforces (shared.incidents.validate_transition), so an invalid
    # target is rejected before spending the row_version we just read --
    # not a second, divergent copy of the transition rule.
    validate_transition(current.status, target_status)

    resolved_transition_id = transition_id or f"{incident_id}.{target_status}.{uuid.uuid4().hex[:12]}"

    result = _call(
        record_incident_transition,
        client,
        incident_id=incident_id,
        from_status=current.status,
        to_status=target_status,
        row_version_before=current.row_version,
        actor=actor,
        transition_id=resolved_transition_id,
        resolution_notes=resolution_notes,
        config=config,
    )
    return LifecycleTransitionOutcome(
        incident_id=result.incident_id,
        status=result.status,
        persisted=True,
        session_only=False,
        row_version=result.row_version,
        resolution_notes=resolution_notes,
    )


def acknowledge_incident(
    client: "BigQueryClientLike",
    incident_id: str,
    *,
    expected_current_status: Optional[IncidentStatus] = None,
    mode: PersistenceMode = "constants",
    actor: str = "data_quality_triage.ui",
    config: Optional[PlatformConfig] = None,
    transition_id: Optional[str] = None,
) -> LifecycleTransitionOutcome:
    return _transition(
        client,
        incident_id,
        "acknowledged",
        expected_current_status=expected_current_status,
        resolution_notes=None,
        mode=mode,
        actor=actor,
        config=config,
        transition_id=transition_id,
    )


def begin_investigation(
    client: "BigQueryClientLike",
    incident_id: str,
    *,
    expected_current_status: Optional[IncidentStatus] = None,
    mode: PersistenceMode = "constants",
    actor: str = "data_quality_triage.ui",
    config: Optional[PlatformConfig] = None,
    transition_id: Optional[str] = None,
) -> LifecycleTransitionOutcome:
    return _transition(
        client,
        incident_id,
        "investigating",
        expected_current_status=expected_current_status,
        resolution_notes=None,
        mode=mode,
        actor=actor,
        config=config,
        transition_id=transition_id,
    )


def mark_mitigated(
    client: "BigQueryClientLike",
    incident_id: str,
    *,
    expected_current_status: Optional[IncidentStatus] = None,
    mode: PersistenceMode = "constants",
    actor: str = "data_quality_triage.ui",
    config: Optional[PlatformConfig] = None,
    transition_id: Optional[str] = None,
) -> LifecycleTransitionOutcome:
    return _transition(
        client,
        incident_id,
        "mitigated",
        expected_current_status=expected_current_status,
        resolution_notes=None,
        mode=mode,
        actor=actor,
        config=config,
        transition_id=transition_id,
    )


def resolve_incident(
    client: "BigQueryClientLike",
    incident_id: str,
    *,
    resolution_notes: str,
    expected_current_status: Optional[IncidentStatus] = None,
    mode: PersistenceMode = "constants",
    actor: str = "data_quality_triage.ui",
    config: Optional[PlatformConfig] = None,
    transition_id: Optional[str] = None,
) -> LifecycleTransitionOutcome:
    return _transition(
        client,
        incident_id,
        "resolved",
        expected_current_status=expected_current_status,
        resolution_notes=resolution_notes,
        mode=mode,
        actor=actor,
        config=config,
        transition_id=transition_id,
    )


def reopen_incident(
    client: "BigQueryClientLike",
    incident_id: str,
    *,
    expected_current_status: Optional[IncidentStatus] = None,
    mode: PersistenceMode = "constants",
    actor: str = "data_quality_triage.ui",
    config: Optional[PlatformConfig] = None,
    transition_id: Optional[str] = None,
) -> LifecycleTransitionOutcome:
    """Reopen a resolved incident -- see shared/incidents.py's "Reopen vs.
    new linked incident" section: this is for correcting a premature
    resolution of the SAME incident, not a fresh recurrence. A fresh
    recurrence is a new Incident with recurrence_of_incident_id set,
    decided at detection time by checks.py/anomaly_engine.py, not here.
    """

    return _transition(
        client,
        incident_id,
        "open",
        expected_current_status=expected_current_status,
        resolution_notes=None,
        mode=mode,
        actor=actor,
        config=config,
        transition_id=transition_id,
    )
