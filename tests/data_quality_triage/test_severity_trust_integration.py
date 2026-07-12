"""End-to-end proof of the severity/source-health/trust-score pipeline
(Phase 4 correction item 2: "Confirm severity mapping end to end").

This test module exists specifically to answer the user's four bullet
points as executable assertions, in one continuous chain, rather than
relying on separate unit tests of each stage plus an analytical argument
that they compose correctly:

  1. a local "critical" TableFinding becomes a shared "high"-severity
     Incident (checks.build_incident_from_finding / collapse_severity),
  2. that high-severity *active* incident derives the platform's most
     severe source-health state ("critical") via
     shared.data_service.derive_source_health(),
  3. that "critical" source-health state activates the trust-score
     do_not_rely override via shared.trust_scoring.compute_trust_score(),
  4. the original local "critical" severity is retained in structured
     audit-event context (checks.build_audit_event_for_incident()) even
     after step 1's collapse.

It also proves there is no unreachable "critical incident" branch left in
trust_scoring.py after the collapse: the do_not_rely override is keyed on
SourceHealth.status == "critical" (a distinct, correctly-computed 3-value
enum from derive_source_health(), never on Incident.severity =="critical",
which cannot exist post-collapse since shared.models.Severity only has
high/medium/low) -- the trust-scoring override was never reachable via
Incident.severity in the first place, so nothing became unreachable when
the local 4-level CheckSeverity got collapsed to the shared 3-level one.
"""

from __future__ import annotations

from apps.data_quality_triage.checks import build_audit_event_for_incident, build_incident_from_finding
from apps.data_quality_triage.models import TableFinding
from shared.data_service import derive_source_health
from shared.trust_scoring import compute_trust_score
from tests.shared.conftest import FakeBigQueryClient

DATASET = "thelook_ecommerce"
TABLE_ID = "order_items"


def _incident_row(**overrides) -> dict:
    row = dict(
        incident_id="thelook_ecommerce.order_items.row_count_empty.2026-07-11T00:00:00Z",
        created_at="2026-07-11T00:00:00Z",
        dataset=DATASET,
        table_id=TABLE_ID,
        check_type="row_count_empty",
        severity="high",
        status="open",
        observed_value=0.0,
        expected_value=0.0,
    )
    row.update(overrides)
    return row


def test_local_critical_finding_to_do_not_rely_trust_band_end_to_end():
    # Step 1: a local "critical" TableFinding is promoted into a shared
    # Incident. collapse_severity() maps "critical" -> "high" -- the
    # shared vocabulary has no "critical" tier.
    finding = TableFinding(
        table_id=TABLE_ID,
        check_name="row_count_empty",
        status="fail",
        severity="critical",
        observed_value=0.0,
        threshold=0.0,
        summary=f"{TABLE_ID} currently has zero rows.",
        likely_root_cause="Upstream load job may have failed, or the table was truncated.",
    )
    incident = build_incident_from_finding(finding, dataset=DATASET, created_at="2026-07-11T00:00:00Z")
    assert incident.severity == "high"
    assert incident.status == "open"

    # Step 2: that high-severity, active incident (as it would be read
    # back from loupe_platform.incidents) derives "critical" source
    # health -- the platform's most severe source-health state.
    client = FakeBigQueryClient()
    client.next_rows = [
        _incident_row(
            incident_id=incident.incident_id,
            check_type=incident.check_type,
            severity=incident.severity,
            status=incident.status,
        )
    ]
    source_health = derive_source_health(client, DATASET, TABLE_ID)
    assert source_health.status == "critical"
    assert source_health.active_incident_ids == [incident.incident_id]

    # Step 3: that "critical" source-health state activates the
    # trust-score do_not_rely override, regardless of how favorably the
    # arithmetic score would otherwise land (definition=None and every
    # other factor at its best-case default here).
    result = compute_trust_score(definition=None, source_health=source_health)
    assert result.band == "do_not_rely"
    assert result.override_reason is not None
    assert "critical" in result.override_reason.lower()

    # Step 4: the original local "critical" severity is retained in
    # structured audit-event context, distinct from the collapsed "high"
    # now on the incident itself -- nothing about the finer-grained local
    # classification was discarded by the collapse.
    event = build_audit_event_for_incident(
        incident, finding, event_id="evt_1", timestamp="2026-07-11T00:00:00Z"
    )
    assert event.context["local_severity"] == "critical"
    assert event.context["collapsed_severity"] == "high"


def test_do_not_rely_override_is_keyed_on_source_health_never_on_a_nonexistent_critical_incident_severity():
    # There is no unreachable "critical incident" branch in trust_scoring
    # after the collapse: the override checks source_health.status ==
    # "critical" (SourceHealthStatus, a distinct enum), never
    # incident.severity == "critical" (which cannot occur --
    # shared.models.Severity is high/medium/low only, by construction).
    # A "high"-severity active incident with a source_health that has
    # NOT yet been escalated to "critical" (e.g. read before
    # derive_source_health() ran, or a caller who forgot to pass it) must
    # NOT trigger the override on severity alone -- only source_health,
    # or an explicit high_severity_finding_count, ever can.
    from shared.models import SourceHealth

    degraded_health = SourceHealth(
        dataset=DATASET, table_id=TABLE_ID, status="degraded", active_incident_ids=["some_incident"]
    )
    result = compute_trust_score(definition=None, source_health=degraded_health)
    assert result.band != "do_not_rely"
    assert result.override_reason is None

    # Passing high_severity_finding_count explicitly (the other, separate
    # override trigger) still forces do_not_rely regardless of
    # source_health -- proving the override has exactly two independent,
    # reachable causes, both keyed off already-computed platform state,
    # never off a raw Incident.severity value.
    result_with_finding = compute_trust_score(
        definition=None, source_health=degraded_health, high_severity_finding_count=1
    )
    assert result_with_finding.band == "do_not_rely"
    assert result_with_finding.override_reason is not None


def test_a_medium_severity_finding_never_reaches_do_not_rely_via_source_health_alone():
    # Sanity check on the other end of the chain: a "medium" local
    # finding collapses to "medium" (unchanged), which never makes
    # source health "critical" (only an active "high" does), so the
    # do_not_rely override must not fire from this path.
    finding = TableFinding(
        table_id=TABLE_ID,
        check_name="null_ratio",
        status="warn",
        severity="medium",
        observed_value=0.03,
        threshold=0.02,
        summary="s",
        likely_root_cause="r",
    )
    incident = build_incident_from_finding(finding, dataset=DATASET, created_at="2026-07-11T00:00:00Z")
    assert incident.severity == "medium"

    client = FakeBigQueryClient()
    client.next_rows = [
        _incident_row(
            incident_id=incident.incident_id,
            check_type=incident.check_type,
            severity=incident.severity,
            status=incident.status,
        )
    ]
    source_health = derive_source_health(client, DATASET, TABLE_ID)
    assert source_health.status == "degraded"

    result = compute_trust_score(definition=None, source_health=source_health)
    assert result.band != "do_not_rely"
