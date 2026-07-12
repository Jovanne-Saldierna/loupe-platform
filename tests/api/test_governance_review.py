from dataclasses import dataclass

from api.services import governance_review
from shared.models import MetricDefinition, SourceHealth


@dataclass
class _Catalog:
    definitions: list
    catalog_unavailable: bool = False
    safe_error: str | None = None


@dataclass
class _Evidence:
    worst_health: SourceHealth | None
    active_incidents: list


def _definition():
    return MetricDefinition(
        name="revenue", owner="Analytics", description="Revenue", formula="SUM(sale_price)",
        measurement_grain="order_item", freshness_expectation="daily", certification_status="pending_validation",
        approved_source_tables=["order_items"], version="v1-extracted",
    )


def test_governance_review_is_deterministic_and_catalog_grounded(monkeypatch):
    monkeypatch.setattr(governance_review, "read_catalog", lambda client: _Catalog([_definition()]))
    monkeypatch.setattr(
        governance_review,
        "source_health_for_definition",
        lambda client, definition: _Evidence(SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="healthy"), []),
    )
    result = governance_review.build_governance_review(
        object(),
        "SELECT DATE(created_at), SUM(sale_price) FROM `thelook_ecommerce.order_items` WHERE created_at >= @start_date GROUP BY 1",
        "revenue",
    )
    assert result.metric.certification_status == "pending_validation"
    assert result.referenced_tables == ["order_items"]
    assert result.source_health == "healthy"
    assert result.trust_factors


def test_catalog_unavailable_never_falls_back_to_constants(monkeypatch):
    monkeypatch.setattr(governance_review, "read_catalog", lambda client: _Catalog([], catalog_unavailable=True))
    try:
        governance_review.list_governed_metrics(object())
    except governance_review.CatalogUnavailableError:
        pass
    else:
        raise AssertionError("catalog outage must be explicit")
