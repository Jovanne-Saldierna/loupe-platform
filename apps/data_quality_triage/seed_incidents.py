"""Backend seed for exactly one realistic active incident on `order_items`,
used ONLY when the persisted `loupe_platform.incidents` table has zero
active rows.

Why this exists: /api/v1/triage/warehouse (api/services/triage_warehouse.py)
queries a real, persisted BigQuery table. That table has never actually had
a row written to it in the live/dev environment -- LOUPE_PERSISTENCE_MODE
defaults to "constants" (shared/config.py), under which Data Quality
Triage's detection run never calls create_incident() at all, and no
separate seeding process has ever run in persisted mode against
loupe_platform.incidents. The result is a genuinely, honestly empty table --
not a bug in the query -- which breaks the product story (failed
deterministic check -> persisted incident -> AI playbook -> lineage ->
audit trail -> Loupe helper) because there is never anything to select.

This module is NOT a general-purpose fallback and it never masks a real
failure: api/services/triage_warehouse.py only calls seed_row_if_needed()
with the rows the live query actually returned, and appends a seed row ONLY
when that list is empty. A single real persisted incident anywhere in the
active set means this seed never activates. The seeded incident is also
labeled distinctly (not as a live detection) in its audit trail -- see
api/services/triage_warehouse.py::_incident_audit_trail -- so nobody
mistakes it for something the deterministic checks actually found live.

The values below are not invented: they mirror exactly what
apps/data_quality_triage/checks.py::check_stale_freshness already computes
for a table that has gone stale past its SLA (STALE_AFTER_MINUTES = 60*24*2
= 2880 minutes), including its severity ("high") and its check_name
("freshness_delay") -- the real, existing check vocabulary this app already
uses, not a fabricated one invented for this seed."""

from __future__ import annotations

from datetime import datetime, timezone

# Mirrors apps/data_quality_triage/checks.py::check_stale_freshness exactly:
# check_name="freshness_delay", severity="high", and the same
# STALE_AFTER_MINUTES=2880 threshold that check compares against.
SEED_INCIDENT_ID = "seed-order_items-freshness_delay"
SEED_TABLE_ID = "order_items"
SEED_CHECK_TYPE = "freshness_delay"
SEED_SEVERITY = "high"
SEED_STATUS = "open"
SEED_OBSERVED_MINUTES = 4320.0  # ~3 days stale
SEED_EXPECTED_MINUTES = 2880.0  # checks.py STALE_AFTER_MINUTES (2 days)
# order_items is the approved_source_table for both of these in
# shared/metric_catalog.py -- a real detector attaching affected_metrics to
# a freshness incident on order_items would plausibly tag exactly these.
SEED_AFFECTED_METRICS = ["revenue", "margin"]


def seed_row_if_needed(rows: list[dict], *, now: datetime | None = None) -> list[dict]:
    """Return `rows` unchanged whenever at least one active incident already
    exists -- a real persisted incident always takes priority and this seed
    never runs alongside or instead of it. Only when `rows` is genuinely
    empty does this return a single deterministic, clearly-labeled
    (`_seeded: True`) row shaped exactly like a BigQuery incidents-table row
    (see api/services/triage_warehouse.py::_active_incident_rows), so the
    caller can treat it identically except for audit-trail wording."""
    if rows:
        return rows
    created_at = (now or datetime.now(timezone.utc)).isoformat()
    return [
        {
            "incident_id": SEED_INCIDENT_ID,
            "table_id": SEED_TABLE_ID,
            "check_type": SEED_CHECK_TYPE,
            "severity": SEED_SEVERITY,
            "status": SEED_STATUS,
            "created_at": created_at,
            "observed_value": SEED_OBSERVED_MINUTES,
            "expected_value": SEED_EXPECTED_MINUTES,
            "affected_metrics": list(SEED_AFFECTED_METRICS),
            "owner": None,
            "_seeded": True,
        }
    ]
