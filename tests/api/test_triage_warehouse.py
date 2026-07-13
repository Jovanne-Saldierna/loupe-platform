from dataclasses import dataclass

from api.services import triage_warehouse
from shared.models import MetricDefinition, SourceHealth


@dataclass
class _Catalog:
    definitions: list
    catalog_unavailable: bool = False


def test_governed_tables_come_from_persisted_catalog(monkeypatch):
    definition = MetricDefinition(
        name="revenue", owner="Analytics", description="Revenue", formula="SUM(sale_price)",
        measurement_grain="order_item", freshness_expectation="daily", certification_status="pending_validation",
        approved_source_tables=["order_items", "products"], version="v1",
        downstream_dashboards=["Executive Revenue Dashboard"],
    )
    monkeypatch.setattr(triage_warehouse, "read_catalog", lambda client: _Catalog([definition]))
    tables, metrics_by_table, lineage_by_table = triage_warehouse._governed_tables_and_metric_map(object())
    assert tables == ["order_items", "products"]
    assert metrics_by_table == {"order_items": ["revenue"], "products": ["revenue"]}
    assert lineage_by_table["order_items"][0].name == "revenue"
    assert lineage_by_table["order_items"][0].downstream_dashboards == ["Executive Revenue Dashboard"]
    assert lineage_by_table["products"][0].downstream_dashboards == ["Executive Revenue Dashboard"]


def test_incidents_carry_governed_metric_names_from_their_table(monkeypatch):
    definition = MetricDefinition(
        name="revenue", owner="Analytics", description="Revenue", formula="SUM(sale_price)",
        measurement_grain="order_item", freshness_expectation="daily", certification_status="pending_validation",
        approved_source_tables=["order_items"], version="v1",
        downstream_dashboards=["Executive Revenue Dashboard"],
    )
    monkeypatch.setattr(triage_warehouse, "read_catalog", lambda client: _Catalog([definition]))
    monkeypatch.setattr(
        triage_warehouse,
        "_active_incident_rows",
        lambda client, config: [
            {
                "incident_id": "inc-1", "table_id": "order_items", "check_type": "freshness",
                "severity": "high", "status": "open", "created_at": "2024-01-01T00:00:00Z",
                "observed_value": None, "expected_value": None, "affected_metrics": [], "owner": None,
            }
        ],
    )
    monkeypatch.setattr(
        triage_warehouse,
        "derive_source_health",
        lambda *args: SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="degraded"),
    )
    monkeypatch.setattr(triage_warehouse, "get_table_metadata", lambda *args: type("M", (), {"modified_at": None})())
    result = triage_warehouse.build_warehouse_health(object(), type("C", (), {})())
    assert result.incidents[0].governed_metric_names == ["revenue"]
    assert result.incidents[0].downstream_assets == ["Executive Revenue Dashboard"]
    steps = [entry.step for entry in result.incidents[0].audit_trail]
    assert steps == ["metadata_loaded", "check_evaluated", "incident_generated"]
    assert result.incidents[0].audit_trail[2].description.startswith("Incident inc-1 generated")
    assert result.lineage[0].table_id == "order_items"
    assert result.lineage[0].governed_metrics[0].name == "revenue"


def test_warehouse_health_seeds_one_incident_when_none_are_persisted(monkeypatch):
    # The live incidents table is genuinely empty -- no real incident has
    # ever been persisted. build_warehouse_health falls back to the backend
    # seed (apps/data_quality_triage/seed_incidents.py) so the product story
    # (incident -> playbook -> lineage -> audit trail -> helper) stays
    # demoable, rather than silently returning zero incidents everywhere.
    monkeypatch.setattr(triage_warehouse, "_governed_tables_and_metric_map", lambda client: (["order_items"], {}, {}))
    monkeypatch.setattr(triage_warehouse, "_active_incident_rows", lambda client, config: [])
    monkeypatch.setattr(
        triage_warehouse,
        "derive_source_health",
        lambda *args: SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="healthy"),
    )
    monkeypatch.setattr(triage_warehouse, "get_table_metadata", lambda *args: type("M", (), {"modified_at": None})())
    result = triage_warehouse.build_warehouse_health(object(), type("C", (), {})())
    assert result.open_incidents == 1
    assert result.tables[0].active_incident_count == 1
    incident = result.incidents[0]
    assert incident.table_id == "order_items"
    assert incident.check_type == "freshness_delay"
    assert incident.status == "open"
    # Seeded, not a live detection -- audit trail says so honestly.
    assert "seed" in (incident.audit_trail[2].source or "").lower()
    assert "seed" in (incident.audit_trail[1].source or "").lower()
    assert result.lineage[0].table_id == "order_items"
    assert result.lineage[0].governed_metrics == []


def test_warehouse_health_does_not_seed_when_a_real_incident_is_persisted(monkeypatch):
    # A real persisted incident always wins -- the seed must never run
    # alongside or override live data.
    monkeypatch.setattr(triage_warehouse, "_governed_tables_and_metric_map", lambda client: (["order_items"], {}, {}))
    monkeypatch.setattr(
        triage_warehouse,
        "_active_incident_rows",
        lambda client, config: [
            {
                "incident_id": "inc-real", "table_id": "order_items", "check_type": "row_count_empty",
                "severity": "critical", "status": "open", "created_at": "2024-01-01T00:00:00Z",
                "observed_value": 0.0, "expected_value": None, "affected_metrics": ["revenue"], "owner": "data-eng",
            }
        ],
    )
    monkeypatch.setattr(
        triage_warehouse,
        "derive_source_health",
        lambda *args: SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="critical"),
    )
    monkeypatch.setattr(triage_warehouse, "get_table_metadata", lambda *args: type("M", (), {"modified_at": None})())
    result = triage_warehouse.build_warehouse_health(object(), type("C", (), {})())
    assert result.open_incidents == 1
    assert result.incidents[0].incident_id == "inc-real"
    assert "seed" not in (result.incidents[0].audit_trail[2].source or "").lower()
