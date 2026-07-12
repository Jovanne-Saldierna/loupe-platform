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
    )
    monkeypatch.setattr(triage_warehouse, "read_catalog", lambda client: _Catalog([definition]))
    assert triage_warehouse._governed_tables(object()) == ["order_items", "products"]


def test_warehouse_health_uses_persisted_incidents_and_real_source_health(monkeypatch):
    monkeypatch.setattr(triage_warehouse, "_governed_tables", lambda client: ["order_items"])
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
