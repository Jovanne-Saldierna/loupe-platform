"""Tests for apps/metric_governance/persistence.py (Phase 6D)."""

from __future__ import annotations

from apps.metric_governance.persistence import (
    certify_definition,
    read_catalog,
    source_health_for_definition,
    trust_score_for_definition,
)
from shared.models import MetricDefinition
from tests.metric_governance.conftest import SequencedFakeBigQueryClient


def _revenue_definition() -> MetricDefinition:
    return MetricDefinition(
        name="revenue",
        owner="loupe-agent-team",
        description="Total booked revenue.",
        formula="SUM(order_items.sale_price)",
        measurement_grain="order_item",
        freshness_expectation="undeclared",
        certification_status="pending_validation",
        approved_source_tables=["order_items", "orders", "products"],
        version="v1-extracted",
    )


def _catalog_row(name: str, definition: MetricDefinition) -> dict:
    return {
        "name": definition.name,
        "owner": definition.owner,
        "certification_status": definition.certification_status,
        "last_reviewed_at": None,
        "version": definition.version,
        "description": definition.description,
        "formula": definition.formula,
        "measurement_grain": definition.measurement_grain,
        "freshness_expectation": definition.freshness_expectation,
        "approved_source_tables": definition.approved_source_tables,
        "required_filters": [],
        "downstream_dashboards": [],
    }


def test_read_catalog_reports_unavailable_when_persisted_storage_is_unreachable(fake_client):
    fake_client.query_exception = RuntimeError("no metric_catalog table")

    result = read_catalog(fake_client)

    assert result.catalog_unavailable is True
    assert result.definitions == []
    assert result.safe_error is not None


def test_read_catalog_never_partially_populates_when_one_metric_is_unreadable(fake_client):
    # A single resolve_current_definition() failure must make the WHOLE
    # catalog read report unavailable -- never a partial 4-of-5 list that
    # silently drops the failing one.
    revenue = _revenue_definition()
    client = SequencedFakeBigQueryClient(
        rows_per_call=[[_catalog_row("revenue", revenue)]],
    )
    client.query_exception = RuntimeError("transient failure on the second lookup")
    # First call succeeds via the queue; force every call after the queue
    # is drained to raise, simulating "the second metric's read failed."

    class _FlakyClient(SequencedFakeBigQueryClient):
        def __init__(self, rows_per_call):
            super().__init__(rows_per_call)
            self._calls = 0

        def query(self, sql, job_config=None):
            self._calls += 1
            if self._calls > 1:
                raise RuntimeError("transient failure")
            return super().query(sql, job_config=job_config)

    flaky = _FlakyClient([[_catalog_row("revenue", revenue)]])
    result = read_catalog(flaky)
    assert result.catalog_unavailable is True
    assert result.definitions == []


def test_read_catalog_omits_uncatalogued_metrics_without_error(fake_client):
    fake_client.next_rows = []  # every lookup finds nothing catalogued yet

    result = read_catalog(fake_client)

    assert result.catalog_unavailable is False
    assert result.definitions == []


def test_source_health_for_definition_returns_worst_status_and_evidence(fake_client):
    definition = _revenue_definition()
    # Three approved tables -> derive_source_health() + list_active_incidents_for_table()
    # each issue one query per table = 6 calls total, in table order:
    # order_items (healthy), orders (degraded), products (healthy).
    rows_per_call = [
        [],  # order_items: derive_source_health -> list_active_incidents_for_table query -> no active incidents
        [],  # order_items: list_active_incidents_for_table (same query again for the incidents list)
        [
            {
                "incident_id": "ds.orders.check.1", "created_at": "2026-07-12T00:00:00Z", "dataset": "ds",
                "table_id": "orders", "check_type": "check", "severity": "high", "status": "open",
            }
        ],
        [
            {
                "incident_id": "ds.orders.check.1", "created_at": "2026-07-12T00:00:00Z", "dataset": "ds",
                "table_id": "orders", "check_type": "check", "severity": "high", "status": "open",
            }
        ],
        [],  # products: healthy
        [],  # products: healthy (incidents list)
    ]
    client = SequencedFakeBigQueryClient(rows_per_call=rows_per_call)

    evidence = source_health_for_definition(client, definition)

    assert evidence.worst_health is not None
    assert evidence.worst_health.status == "critical"
    assert evidence.worst_health.table_id == "orders"
    assert len(evidence.active_incidents) == 1


def test_source_health_for_definition_degrades_honestly_when_unavailable(fake_client):
    fake_client.query_exception = RuntimeError("no incidents table")

    evidence = source_health_for_definition(fake_client, _revenue_definition())

    assert evidence.worst_health is None
    assert evidence.table_health == []
    assert evidence.active_incidents == []


def test_trust_score_for_definition_scores_zero_source_points_when_evidence_unavailable(fake_client):
    fake_client.query_exception = RuntimeError("no incidents table")

    result = trust_score_for_definition(fake_client, _revenue_definition())

    source_factor = next(f for f in result.trust.factors if f.name == "source_health")
    assert source_factor.points == 0


def test_certify_definition_allows_same_reviewer_and_creator_by_default(fake_client):
    fake_client.next_rows = [
        {"event_id": "evt_cert_revenue_v2", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    result = certify_definition(
        fake_client,
        name="revenue",
        new_version="v2-certified",
        expected_current_version="v1-extracted",
        description="Total booked revenue.",
        formula="SUM(order_items.sale_price)",
        measurement_grain="order_item",
        freshness_expectation="undeclared",
        approved_source_tables=["order_items", "orders", "products"],
        created_by="reviewer-bot",
        reviewer="reviewer-bot",
        validation_evidence="cross-checked",
        reviewed_at="2026-07-12T00:00:00Z",
        change_reason="first certification",
        event_id="evt_cert_revenue_v2",
        require_separation_of_duties=False,
    )
    assert result.reviewer == "reviewer-bot"
    assert result.created_by == "reviewer-bot"


def test_certify_definition_rejects_same_identity_when_strict_policy_requested(fake_client):
    import pytest

    with pytest.raises(ValueError):
        certify_definition(
            fake_client,
            name="revenue",
            new_version="v2-certified",
            expected_current_version="v1-extracted",
            description="Total booked revenue.",
            formula="SUM(order_items.sale_price)",
            measurement_grain="order_item",
            freshness_expectation="undeclared",
            approved_source_tables=["order_items", "orders", "products"],
            created_by="same-bot",
            reviewer="same-bot",
            validation_evidence="cross-checked",
            reviewed_at="2026-07-12T00:00:00Z",
            change_reason="first certification",
            event_id="evt_cert_revenue_v2",
            require_separation_of_duties=True,
        )
    assert fake_client.queries == []
