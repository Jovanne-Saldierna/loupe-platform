"""Tests for apps/data_quality_triage/models.py."""

from __future__ import annotations

import pytest

from apps.data_quality_triage.models import (
    CheckDefinition,
    IncidentExplanation,
    SchemaSnapshot,
    TableFinding,
)


def test_table_finding_constructs_with_all_fields():
    finding = TableFinding(
        table_id="order_items",
        check_name="duplicate_key_ratio",
        status="fail",
        severity="critical",
        observed_value=0.08,
        threshold=0.05,
        summary="8% duplicate keys on order_items.id.",
        likely_root_cause="Upstream ingestion job re-ran without dedup.",
        affected_assets=["fct_orders", "revenue dashboard"],
    )
    assert finding.table_id == "order_items"
    assert finding.severity == "critical"
    assert finding.affected_assets == ["fct_orders", "revenue dashboard"]


def test_table_finding_rejects_invalid_severity():
    with pytest.raises(ValueError):
        TableFinding(
            table_id="t",
            check_name="c",
            status="fail",
            severity="extreme",  # type: ignore[arg-type]
            observed_value=None,
            threshold=None,
            summary="s",
            likely_root_cause="r",
        )


def test_table_finding_rejects_invalid_status():
    with pytest.raises(ValueError):
        TableFinding(
            table_id="t",
            check_name="c",
            status="broken",  # type: ignore[arg-type]
            severity="low",
            observed_value=None,
            threshold=None,
            summary="s",
            likely_root_cause="r",
        )


def test_table_finding_defaults_affected_assets_to_empty_list():
    finding = TableFinding(
        table_id="t",
        check_name="c",
        status="pass",
        severity="low",
        observed_value=None,
        threshold=None,
        summary="s",
        likely_root_cause="r",
    )
    assert finding.affected_assets == []


def test_check_definition_constructs():
    definition = CheckDefinition(
        name="Duplicate Key Growth",
        description="Flags rising duplicate-key ratio on a table's primary candidate.",
        threshold="critical at >=5% duplicate rate",
        severity="high",
    )
    assert definition.name == "Duplicate Key Growth"


def test_check_definition_rejects_invalid_severity():
    with pytest.raises(ValueError):
        CheckDefinition(name="n", description="d", threshold="t", severity="extreme")  # type: ignore[arg-type]


def test_incident_explanation_constructs():
    explanation = IncidentExplanation(
        incident_id="inc_1", narrative="Duplicate keys detected on order_items.", used_claude=False
    )
    assert explanation.incident_id == "inc_1"
    assert explanation.used_claude is False


# ---------------------------------------------------------------------------
# CheckStatus: "error" and "not_evaluated" (Phase 4 correction)
# ---------------------------------------------------------------------------


def test_table_finding_accepts_error_status_for_query_exceptions():
    finding = TableFinding(
        table_id="order_items",
        check_name="query_exception",
        status="error",
        severity="high",
        observed_value=None,
        threshold=None,
        summary="s",
        likely_root_cause="r",
    )
    assert finding.status == "error"


def test_table_finding_accepts_not_evaluated_status_for_missing_preconditions():
    finding = TableFinding(
        table_id="order_items",
        check_name="schema_drift",
        status="not_evaluated",
        severity="low",
        observed_value=None,
        threshold=None,
        summary="s",
        likely_root_cause="r",
    )
    assert finding.status == "not_evaluated"


# ---------------------------------------------------------------------------
# SchemaSnapshot
# ---------------------------------------------------------------------------


def test_schema_snapshot_constructs():
    snapshot = SchemaSnapshot(
        table_id="order_items",
        captured_at="2026-07-01T00:00:00Z",
        columns={"id": "INTEGER", "status": "STRING"},
    )
    assert snapshot.table_id == "order_items"
    assert snapshot.columns == {"id": "INTEGER", "status": "STRING"}
