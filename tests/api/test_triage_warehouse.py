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


def test_warehouse_health_uses_persisted_incidents_and_real_source_health(monkeypatch):
    monkeypatch.setattr(triage_warehouse, "_governed_tables_and_metric_map", lambda client: (["order_items"], {}, {}))
    monkeypatch.setattr(triage_warehouse, "_active_incident_rows", lambda client, config: [])
    monkeypatch.setattr(
        triage_warehouse,
        "derive_source_health",
        lambda *args: SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="healthy"),
    )
    monkeypatch.setattr(triage_warehouse, "get_table_metadata", lambda *args: type("M", (), {"modified_at": None})())
    result = triage_warehouse.build_warehouse_health(object(), type("C", (), {})())
    assert result.healthy_tables == 1
    assert result.open_incidents == 0
    assert result.tables[0].status == "healthy"
    assert result.lineage[0].table_id == "order_items"
    assert result.lineage[0].governed_metrics == []
