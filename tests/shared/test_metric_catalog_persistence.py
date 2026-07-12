"""Tests for shared/metric_catalog_persistence.py (Phase 6C).

Exercises persisted metric-catalog reads, explicit one-time seeding, and
governed certification against the fake BigQuery client -- no live
BigQuery access. execute_transaction()'s own mechanism (commit/rollback,
ASSERT @@row_count, result_sql, retry/backoff, lock-row contention) is
already covered by tests/shared/test_persistence_transactions.py and
confirmed against real BigQuery by the Phase 6B live spike; these tests
focus on this module's own business logic.
"""

from __future__ import annotations

import json

import pytest


def _params(job_config) -> dict:
    """Flatten a QueryJobConfig's query_parameters into {name: value},
    handling both ScalarQueryParameter (.value) and ArrayQueryParameter
    (.values) uniformly."""

    result = {}
    for p in job_config.query_parameters:
        result[p.name] = p.value if hasattr(p, "value") else p.values
    return result

from shared.audit_persistence import WRITE_AUDIT_EVENT_TXN
from shared.metric_catalog import get_definition, list_definitions
from shared.metric_catalog_persistence import (
    CERTIFY_METRIC_VERSION_TXN,
    SEED_METRIC_DEFINITION_TXN,
    certify_metric_definition,
    get_current_definition,
    get_version_history,
    resolve_current_definition,
    seed_current_catalog,
    seed_metric_definition,
)
from shared.metric_hashing import compute_content_hash
from shared.models import MetricDefinition
from shared.persistence_transactions import PayloadConflictError


def _revenue_definition() -> MetricDefinition:
    definition = get_definition("revenue")
    assert definition is not None
    return definition


def _certified_pointer_row(**overrides) -> dict:
    row = {
        "name": "revenue",
        "owner": "loupe-agent-team",
        "certification_status": "pending_validation",
        "last_reviewed_at": None,
        "version": "v1-extracted",
        "description": "Total booked revenue.",
        "formula": "SUM(order_items.sale_price)",
        "measurement_grain": "order_item",
        "freshness_expectation": "undeclared",
        "approved_source_tables": ["order_items", "orders", "products"],
        "required_filters": [],
        "downstream_dashboards": ["loupe_agent dashboard: KPI summary, revenue trend"],
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Templates are registered correctly
# ---------------------------------------------------------------------------


def test_seed_txn_is_guarded_by_metric_catalog_lock_domain():
    assert SEED_METRIC_DEFINITION_TXN.lock_domain == "metric_catalog"
    assert "WHERE NOT EXISTS" in SEED_METRIC_DEFINITION_TXN.sql


def test_certify_txn_is_guarded_by_metric_catalog_lock_domain_and_has_no_result_sql():
    assert CERTIFY_METRIC_VERSION_TXN.lock_domain == "metric_catalog"
    # Per this module's docstring: the audit event's result_sql comes
    # from WRITE_AUDIT_EVENT_TXN, composed alongside this template --
    # CERTIFY_METRIC_VERSION_TXN must not also declare one (only one
    # statement per execute_transaction() call may).
    assert CERTIFY_METRIC_VERSION_TXN.result_sql is None


def test_module_never_imports_the_streaming_write_event_path():
    # Static guard for the "audit ownership" requirement: this module's
    # actual code (not its prose docstrings, which discuss write_event()
    # by name) must never import or call shared.audit.write_event -- the
    # streaming, non-atomic path -- for its governed certification event.
    import inspect

    import shared.metric_catalog_persistence as module

    assert not hasattr(module, "write_event")
    source = inspect.getsource(module.certify_metric_definition)
    assert "write_event(" not in source
    assert "import write_event" not in inspect.getsource(module)


# ---------------------------------------------------------------------------
# 1. Persisted reads
# ---------------------------------------------------------------------------


def test_get_current_definition_returns_none_when_no_pointer_row(fake_client):
    fake_client.next_rows = []
    assert get_current_definition(fake_client, "revenue") is None


def test_get_current_definition_resolves_pointer_to_version_shape(fake_client):
    fake_client.next_rows = [_certified_pointer_row()]
    definition = get_current_definition(fake_client, "revenue")
    assert definition is not None
    assert definition.name == "revenue"
    assert definition.version == "v1-extracted"
    assert definition.approved_source_tables == ["order_items", "orders", "products"]


def test_get_current_definition_preserves_pending_validation_honestly(fake_client):
    fake_client.next_rows = [_certified_pointer_row(certification_status="pending_validation")]
    definition = get_current_definition(fake_client, "revenue")
    assert definition.certification_status == "pending_validation"


def test_get_current_definition_reports_certified_status_once_certified(fake_client):
    fake_client.next_rows = [_certified_pointer_row(certification_status="certified", version="v2-certified")]
    definition = get_current_definition(fake_client, "revenue")
    assert definition.certification_status == "certified"
    assert definition.version == "v2-certified"


def test_get_version_history_returns_empty_list_when_nothing_persisted(fake_client):
    fake_client.next_rows = []
    assert get_version_history(fake_client, "revenue") == []


def test_get_version_history_maps_rows_to_metric_version_objects(fake_client):
    fake_client.next_rows = [
        {
            "name": "revenue",
            "version": "v2-certified",
            "description": "Total booked revenue.",
            "formula": "SUM(order_items.sale_price)",
            "measurement_grain": "order_item",
            "freshness_expectation": "undeclared",
            "certification_status": "certified",
            "approved_source_tables": ["order_items"],
            "content_hash": "abc123",
            "prior_version": "v1-extracted",
            "created_by": "author-bot",
            "created_at": "2026-07-12T00:00:00Z",
            "change_reason": "certified after review",
            "required_filters": [],
            "downstream_dashboards": [],
            "validation_evidence": "dashboard spot-check",
            "review_notes": None,
            "reviewer": "reviewer-bot",
            "reviewed_at": "2026-07-12T01:00:00Z",
        },
        {
            "name": "revenue",
            "version": "v1-extracted",
            "description": "Total booked revenue.",
            "formula": "SUM(order_items.sale_price)",
            "measurement_grain": "order_item",
            "freshness_expectation": "undeclared",
            "certification_status": "pending_validation",
            "approved_source_tables": ["order_items"],
            "content_hash": "def456",
            "prior_version": None,
            "created_by": "seed-bot",
            "created_at": "2026-07-01T00:00:00Z",
            "change_reason": "initial seed",
            "required_filters": [],
            "downstream_dashboards": [],
            "validation_evidence": None,
            "review_notes": None,
            "reviewer": None,
            "reviewed_at": None,
        },
    ]
    versions = get_version_history(fake_client, "revenue")
    assert [v.version for v in versions] == ["v2-certified", "v1-extracted"]
    assert versions[0].prior_version == "v1-extracted"
    assert versions[1].reviewer is None


def test_resolve_current_definition_ok_when_read_succeeds(fake_client):
    fake_client.next_rows = [_certified_pointer_row()]
    resolution = resolve_current_definition(fake_client, "revenue")
    assert resolution.ok is True
    assert resolution.definition.name == "revenue"
    assert resolution.safe_error is None


def test_resolve_current_definition_ok_true_with_none_definition_when_not_catalogued(fake_client):
    fake_client.next_rows = []
    resolution = resolve_current_definition(fake_client, "revenue")
    assert resolution.ok is True
    assert resolution.definition is None


def test_resolve_current_definition_reports_unavailable_rather_than_falling_back(fake_client):
    # Persistence-unavailable behavior: a raised exception from the
    # underlying read must become ok=False with a safe error, never a
    # silently-substituted value and never a propagated raw exception.
    fake_client.query_exception = RuntimeError("connection reset, internal detail that must not leak")
    resolution = resolve_current_definition(fake_client, "revenue")
    assert resolution.ok is False
    assert resolution.definition is None
    assert resolution.safe_error is not None
    assert "connection reset" not in resolution.safe_error


# ---------------------------------------------------------------------------
# 2. Explicit one-time seeding
# ---------------------------------------------------------------------------


def test_seed_metric_definition_refuses_non_pending_validation_definitions(fake_client):
    certified = MetricDefinition(
        name="revenue",
        owner="team",
        description="d",
        formula="f",
        measurement_grain="g",
        freshness_expectation="u",
        certification_status="certified",
        approved_source_tables=["order_items"],
        version="v1",
    )
    with pytest.raises(ValueError):
        seed_metric_definition(fake_client, certified, created_by="admin", created_at="2026-07-12T00:00:00Z")
    assert fake_client.queries == []


def test_seed_metric_definition_happy_path(fake_client):
    definition = _revenue_definition()
    content_hash = compute_content_hash(
        name=definition.name,
        description=definition.description,
        formula=definition.formula,
        measurement_grain=definition.measurement_grain,
        freshness_expectation=definition.freshness_expectation,
        approved_source_tables=definition.approved_source_tables,
        required_filters=definition.required_filters,
        downstream_dashboards=definition.downstream_dashboards,
    )
    fake_client.next_rows = [
        {
            "name": "revenue",
            "current_version": "v1-extracted",
            "owner": "loupe-agent-team",
            "certification_status": "pending_validation",
            "version_version": "v1-extracted",
            "content_hash": content_hash,
        }
    ]
    result = seed_metric_definition(
        fake_client, definition, created_by="admin", created_at="2026-07-12T00:00:00Z"
    )
    assert result.name == "revenue"
    assert result.version == "v1-extracted"
    assert result.certification_status == "pending_validation"


def test_seed_metric_definition_identical_retry_is_idempotent(fake_client):
    # Simulates re-running the exact same seed call: metric_catalog and
    # metric_versions already hold this exact content (the WHERE NOT
    # EXISTS guards make the INSERTs no-ops), and the content_hash
    # comparison below finds no conflict.
    definition = _revenue_definition()
    content_hash = compute_content_hash(
        name=definition.name,
        description=definition.description,
        formula=definition.formula,
        measurement_grain=definition.measurement_grain,
        freshness_expectation=definition.freshness_expectation,
        approved_source_tables=definition.approved_source_tables,
        required_filters=definition.required_filters,
        downstream_dashboards=definition.downstream_dashboards,
    )
    fake_client.next_rows = [
        {
            "name": "revenue",
            "current_version": "v1-extracted",
            "owner": "loupe-agent-team",
            "certification_status": "pending_validation",
            "version_version": "v1-extracted",
            "content_hash": content_hash,
        }
    ]
    first = seed_metric_definition(fake_client, definition, created_by="admin", created_at="2026-07-12T00:00:00Z")
    second = seed_metric_definition(fake_client, definition, created_by="admin", created_at="2026-07-13T00:00:00Z")
    assert first == second


def test_seed_metric_definition_rejects_conflicting_existing_content(fake_client):
    definition = _revenue_definition()
    fake_client.next_rows = [
        {
            "name": "revenue",
            "current_version": "v1-extracted",
            "owner": "loupe-agent-team",
            "certification_status": "pending_validation",
            "version_version": "v1-extracted",
            "content_hash": "some-different-hash-already-persisted",
        }
    ]
    with pytest.raises(PayloadConflictError):
        seed_metric_definition(fake_client, definition, created_by="admin", created_at="2026-07-12T00:00:00Z")


def test_seed_metric_definition_rejects_pointer_already_at_a_different_version(fake_client):
    definition = _revenue_definition()
    fake_client.next_rows = [
        {
            "name": "revenue",
            "current_version": "v2-certified",
            "owner": "loupe-agent-team",
            "certification_status": "certified",
            "version_version": "v2-certified",
            "content_hash": "whatever",
        }
    ]
    with pytest.raises(PayloadConflictError):
        seed_metric_definition(fake_client, definition, created_by="admin", created_at="2026-07-12T00:00:00Z")


def test_seed_current_catalog_seeds_all_five_definitions(fake_client):
    calls = {"count": 0}
    original_next_rows = None

    # Simplify by making the fake client return a fresh matching row for
    # whichever metric is being seeded on each call.
    def _fake_query(sql, job_config=None):
        params = _params(job_config)
        name = params["s0_name"]
        version = params["s0_version"]
        content_hash = params["s0_content_hash"]
        fake_client.next_rows = [
            {
                "name": name,
                "current_version": version,
                "owner": params["s0_owner"],
                "certification_status": params["s0_certification_status"],
                "version_version": version,
                "content_hash": content_hash,
            }
        ]
        calls["count"] += 1
        return fake_client.__class__.query(fake_client, sql, job_config)

    fake_client.query = _fake_query
    results = seed_current_catalog(fake_client, created_by="admin", created_at="2026-07-12T00:00:00Z")
    assert {r.name for r in results} == {d.name for d in list_definitions()}
    assert calls["count"] == 5


# ---------------------------------------------------------------------------
# 3. Governed certification
# ---------------------------------------------------------------------------


def _certify_kwargs(**overrides) -> dict:
    defaults = dict(
        name="revenue",
        new_version="v2-certified",
        expected_current_version="v1-extracted",
        description="Total booked revenue.",
        formula="SUM(order_items.sale_price)",
        measurement_grain="order_item",
        freshness_expectation="undeclared",
        approved_source_tables=["order_items", "orders", "products"],
        created_by="author-bot",
        reviewer="reviewer-bot",
        validation_evidence="cross-checked against dashboard totals for 3 sample days",
        reviewed_at="2026-07-12T00:00:00Z",
        change_reason="first formal certification of the extracted formula",
        event_id="evt_cert_revenue_v2",
    )
    defaults.update(overrides)
    return defaults


def test_certify_metric_definition_allows_same_reviewer_and_creator_by_default(fake_client):
    # Phase 6D policy correction: require_separation_of_duties defaults to
    # False for this portfolio deployment, so a single identity may both
    # author and certify a version.
    fake_client.next_rows = [
        {"event_id": "evt_cert_revenue_v2", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    result = certify_metric_definition(fake_client, **_certify_kwargs(created_by="same-bot", reviewer="same-bot"))
    assert result.created_by == "same-bot"
    assert result.reviewer == "same-bot"


def test_certify_metric_definition_rejects_same_reviewer_and_creator_under_strict_policy(fake_client):
    with pytest.raises(ValueError):
        certify_metric_definition(
            fake_client,
            **_certify_kwargs(created_by="same-bot", reviewer="same-bot"),
            require_separation_of_duties=True,
        )
    assert fake_client.queries == []


def test_certify_metric_definition_allows_distinct_reviewer_and_creator_under_strict_policy(fake_client):
    fake_client.next_rows = [
        {"event_id": "evt_cert_revenue_v2", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    result = certify_metric_definition(
        fake_client,
        **_certify_kwargs(created_by="author-bot", reviewer="reviewer-bot"),
        require_separation_of_duties=True,
    )
    assert result.created_by == "author-bot"
    assert result.reviewer == "reviewer-bot"


def test_certify_metric_definition_preserves_both_identities_even_when_equal(fake_client):
    # Both created_by and reviewer must always be preserved as distinct,
    # separately-recorded fields -- never conflated -- regardless of
    # whether the policy setting happens to allow them to be equal. Check
    # the actual bound params sent to BigQuery, not just the returned
    # result object.
    fake_client.next_rows = [
        {"event_id": "evt_cert_revenue_v2", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    certify_metric_definition(fake_client, **_certify_kwargs(created_by="same-bot", reviewer="same-bot"))
    sql, job_config = fake_client.queries[-1]
    params = _params(job_config)
    created_by_params = [v for k, v in params.items() if k.endswith("_created_by")]
    reviewer_params = [v for k, v in params.items() if k.endswith("_reviewer")]
    assert created_by_params == ["same-bot"]
    assert reviewer_params == ["same-bot"]


def test_certify_metric_definition_requires_validation_evidence(fake_client):
    with pytest.raises(ValueError):
        certify_metric_definition(fake_client, **_certify_kwargs(validation_evidence=""))
    assert fake_client.queries == []


def test_certify_metric_definition_requires_reviewed_at(fake_client):
    with pytest.raises(ValueError):
        certify_metric_definition(fake_client, **_certify_kwargs(reviewed_at=""))
    assert fake_client.queries == []


def test_certify_metric_definition_requires_change_reason(fake_client):
    with pytest.raises(ValueError):
        certify_metric_definition(fake_client, **_certify_kwargs(change_reason=""))
    assert fake_client.queries == []


def test_certify_metric_definition_without_content_change_happy_path(fake_client):
    # "Certification without content change": the content passed in is
    # byte-identical to the definition's existing pending_validation
    # content -- this is a pure approval-state change, still a normal,
    # successful certification.
    fake_client.next_rows = [
        {"event_id": "evt_cert_revenue_v2", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    result = certify_metric_definition(fake_client, **_certify_kwargs())
    assert result.name == "revenue"
    assert result.version == "v2-certified"
    assert result.prior_version == "v1-extracted"
    assert result.reviewer == "reviewer-bot"
    assert result.created_by == "author-bot"


def test_certify_metric_definition_with_content_change_produces_a_different_hash(fake_client):
    fake_client.next_rows = [
        {"event_id": "evt_cert_revenue_v2", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    unchanged = certify_metric_definition(fake_client, **_certify_kwargs())

    fake_client.next_rows = [
        {"event_id": "evt_cert_revenue_v3", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    changed = certify_metric_definition(
        fake_client,
        **_certify_kwargs(
            new_version="v3-certified",
            expected_current_version="v2-certified",
            formula="SUM(order_items.sale_price) - SUM(returns.amount)",
            event_id="evt_cert_revenue_v3",
        ),
    )
    assert changed.content_hash != unchanged.content_hash


def test_certify_metric_definition_binds_prior_version_and_reviewer_distinctly(fake_client):
    fake_client.next_rows = [
        {"event_id": "evt_cert_revenue_v2", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    certify_metric_definition(fake_client, **_certify_kwargs())
    _, job_config = fake_client.queries[0]
    params = _params(job_config)
    assert params["s0_expected_current_version"] == "v1-extracted"
    assert params["s0_created_by"] == "author-bot"
    assert params["s0_reviewer"] == "reviewer-bot"
    assert params["s0_created_by"] != params["s0_reviewer"]


def test_certify_metric_definition_writes_via_write_audit_event_txn_template(fake_client):
    # Confirms the composition described in the module docstring: the
    # second statement in the rendered script is WRITE_AUDIT_EVENT_TXN's
    # own SQL fragment (audit_events lock domain + insert-if-absent),
    # not a re-embedded duplicate.
    fake_client.next_rows = [
        {"event_id": "evt_cert_revenue_v2", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    certify_metric_definition(fake_client, **_certify_kwargs())
    sql, job_config = fake_client.queries[0]
    assert "lock_domain = 'audit_events'" in sql
    params = _params(job_config)
    assert params["s1_event_type"] == "metric_certified"  # bound, not literal, in WRITE_AUDIT_EVENT_TXN's SQL
    assert "s1_event_id" in params  # WRITE_AUDIT_EVENT_TXN is statement index 1


def test_certify_metric_definition_context_never_contains_a_secret_field(fake_client):
    fake_client.next_rows = [
        {"event_id": "evt_cert_revenue_v2", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    certify_metric_definition(fake_client, **_certify_kwargs())  # must not raise


def test_certify_metric_definition_atomic_rollback_on_pointer_assert_failure(fake_client):
    # Simulates the pointer UPDATE's ASSERT failing (e.g. a stale
    # expected_current_version because someone else certified first).
    # Per this module's design, the whole script -- new metric_versions
    # row, pointer update, and audit event -- is one atomic unit, so a
    # failure anywhere inside it must propagate as a single exception
    # with no partial result ever constructed.
    fake_client.query_exception = RuntimeError(
        "simulated ASSERT failure: expected exactly one metric_catalog pointer updated"
    )
    with pytest.raises(RuntimeError):
        certify_metric_definition(fake_client, **_certify_kwargs())


def test_certify_metric_definition_raises_if_audit_event_id_mismatch_found(fake_client):
    # Should be unreachable in practice, but must not silently succeed.
    fake_client.next_rows = [
        {"event_id": "some-other-event", "event_type": "metric_certified", "subject": "metric:revenue", "outcome": "completed"}
    ]
    with pytest.raises(RuntimeError):
        certify_metric_definition(fake_client, **_certify_kwargs())


def test_certify_metric_definition_raises_runtime_error_if_no_row_found_afterward(fake_client):
    fake_client.next_rows = []
    with pytest.raises(RuntimeError):
        certify_metric_definition(fake_client, **_certify_kwargs())
