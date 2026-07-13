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


def _definition(**overrides):
    defaults = dict(
        name="revenue", owner="Analytics", description="Revenue", formula="SUM(sale_price)",
        measurement_grain="order_item", freshness_expectation="daily", certification_status="pending_validation",
        approved_source_tables=["order_items"], version="v1-extracted",
    )
    defaults.update(overrides)
    return MetricDefinition(**defaults)


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


# ---------------------------------------------------------------------------
# Product-depth additions: richer catalog detail, change risk, recommendations
# ---------------------------------------------------------------------------


def test_review_response_carries_full_catalog_detail_and_downstream_assets(monkeypatch):
    definition = _definition(downstream_dashboards=["loupe_agent dashboard: KPI summary, revenue trend"])
    monkeypatch.setattr(governance_review, "read_catalog", lambda client: _Catalog([definition]))
    monkeypatch.setattr(
        governance_review,
        "source_health_for_definition",
        lambda client, d: _Evidence(SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="healthy"), []),
    )
    result = governance_review.build_governance_review(object(), "SELECT SUM(sale_price) FROM order_items", "revenue")

    assert result.metric.owner == "Analytics"
    assert result.metric.description == "Revenue"
    assert result.metric.formula == "SUM(sale_price)"
    assert result.metric.approved_source_tables == ["order_items"]
    assert result.downstream_assets == ["loupe_agent dashboard: KPI summary, revenue trend"]


def test_review_response_carries_change_risk_and_recommendations(monkeypatch):
    definition = _definition()
    monkeypatch.setattr(governance_review, "read_catalog", lambda client: _Catalog([definition]))
    monkeypatch.setattr(
        governance_review,
        "source_health_for_definition",
        lambda client, d: _Evidence(SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="healthy"), []),
    )
    result = governance_review.build_governance_review(object(), "SELECT * FROM order_items", "revenue")

    assert [c.category for c in result.change_risk] == [
        "Calculation drift",
        "Source table mismatch",
        "Grain mismatch",
        "Filter/status mismatch",
        "Freshness/SLA mismatch",
    ]
    # SELECT * triggers a real "Projection" finding -> calculation drift risk.
    calc_drift = next(c for c in result.change_risk if c.category == "Calculation drift")
    assert calc_drift.status == "risk"
    assert result.recommendations
    assert all(r.priority in ("info", "required", "blocking") for r in result.recommendations)


def test_active_incidents_surface_a_blocking_resolve_incident_recommendation(monkeypatch):
    from shared.models import Incident

    definition = _definition()
    monkeypatch.setattr(governance_review, "read_catalog", lambda client: _Catalog([definition]))
    monkeypatch.setattr(
        governance_review,
        "source_health_for_definition",
        lambda client, d: _Evidence(
            SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="degraded"),
            [Incident(
                incident_id="inc-1", created_at="2026-07-01T00:00:00Z", dataset="thelook_ecommerce",
                table_id="order_items", check_type="freshness_delay", severity="high", status="open",
            )],
        ),
    )
    result = governance_review.build_governance_review(object(), "SELECT SUM(sale_price) FROM order_items", "revenue")

    assert result.active_incident_ids == ["inc-1"]
    rec = next(r for r in result.recommendations if r.action == "Resolve source incident")
    assert rec.priority == "blocking"
    assert "inc-1" in rec.rationale


def test_list_governed_metrics_returns_rich_catalog_detail_with_evidence(monkeypatch):
    definition = _definition(downstream_dashboards=["loupe_agent dashboard: KPI summary"])
    monkeypatch.setattr(governance_review, "read_catalog", lambda client: _Catalog([definition]))
    monkeypatch.setattr(
        governance_review,
        "source_health_for_definition",
        lambda client, d: _Evidence(SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="healthy"), []),
    )
    response = governance_review.list_governed_metrics(object())

    assert len(response.metrics) == 1
    metric = response.metrics[0]
    assert metric.owner == "Analytics"
    assert metric.downstream_dashboards == ["loupe_agent dashboard: KPI summary"]
    assert metric.source_health == "healthy"
    assert metric.active_incident_ids == []


def test_list_governed_metrics_returns_governance_completeness(monkeypatch):
    definition = _definition(certification_status="certified", downstream_dashboards=["loupe_agent dashboard: KPI summary"])
    monkeypatch.setattr(governance_review, "read_catalog", lambda client: _Catalog([definition]))
    monkeypatch.setattr(
        governance_review,
        "source_health_for_definition",
        lambda client, d: _Evidence(SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="healthy"), []),
    )
    response = governance_review.list_governed_metrics(object())
    metric = response.metrics[0]

    assert len(metric.completeness) == 7
    assert all(c.passed for c in metric.completeness)
    assert metric.completeness_score == 1.0


def test_review_response_metric_carries_completeness_reflecting_review_evidence(monkeypatch):
    definition = _definition(certification_status="proposed")
    monkeypatch.setattr(governance_review, "read_catalog", lambda client: _Catalog([definition]))
    monkeypatch.setattr(
        governance_review,
        "source_health_for_definition",
        lambda client, d: _Evidence(SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="healthy"), []),
    )
    result = governance_review.build_governance_review(object(), "SELECT SUM(sale_price) FROM order_items", "revenue")

    labels = {c.label: c.passed for c in result.metric.completeness}
    assert labels["Has certified definition"] is False
    assert 0.0 < result.metric.completeness_score < 1.0
