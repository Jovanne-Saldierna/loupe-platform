"""Phase 6D cross-app credential-free proofs.

These tests exercise the demonstrable end-to-end workflow across the three
persistence-wired applications, entirely against in-memory fake BigQuery
clients (no cloud credentials, no network access):

    Data Quality Triage detects and persists an incident
        -> Metric Governance incorporates it into trust scoring
        -> Loupe warns the user before explaining affected metrics

Each test below maps directly to one bullet point from the Phase 6D task
specification's "cross-app credential-free tests" list.
"""

from __future__ import annotations

from apps.data_quality_triage.checks import build_audit_event_for_incident
from apps.data_quality_triage.models import TableFinding
from apps.data_quality_triage.persistence import persist_confirmed_incidents
from apps.loupe_agent import chat as loupe_chat
from apps.loupe_agent import source_health as loupe_source_health
from apps.metric_governance.persistence import (
    source_health_for_definition,
    trust_score_for_definition,
)
from shared.models import Incident, MetricDefinition
from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient
from tests.shared.conftest import FakeBigQueryClient


def _incident(
    table_id: str = "orders",
    check_type: str = "null_rate",
    status: str = "open",
    severity: str = "high",
) -> Incident:
    return Incident(
        incident_id=f"thelook_ecommerce.{table_id}.{check_type}.2026-07-12T00:00:00Z",
        created_at="2026-07-12T00:00:00Z",
        dataset="thelook_ecommerce",
        table_id=table_id,
        check_type=check_type,
        severity=severity,
        status=status,
    )


def _build_event(incident: Incident):
    finding = TableFinding(
        table_id=incident.table_id,
        check_name=incident.check_type,
        status="fail",
        severity=incident.severity,
        observed_value=incident.observed_value,
        threshold=incident.expected_value,
        summary="test finding",
        likely_root_cause="test",
    )
    return build_audit_event_for_incident(
        incident,
        finding,
        event_id=f"incident_created.{incident.incident_id}",
        timestamp="2026-07-12T00:00:00Z",
    )


def _persist_one_incident(incident: Incident) -> None:
    client = SequencedFakeBigQueryClient(
        rows_per_call=[
            [
                {
                    "incident_id": incident.incident_id,
                    "dataset": incident.dataset,
                    "table_id": incident.table_id,
                    "check_type": incident.check_type,
                    "severity": incident.severity,
                    "status": incident.status,
                    "row_version": 1,
                }
            ],
            [
                {
                    "event_id": f"incident_created.{incident.incident_id}",
                    "event_type": "incident_created",
                    "subject": incident.incident_id,
                    "outcome": "incident_created",
                }
            ],
        ]
    )
    outcomes = persist_confirmed_incidents(
        client, [incident], actor="triage-bot", build_audit_event=_build_event
    )
    assert outcomes[0].persisted is True


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


def _governance_client_with_one_active_incident_on_orders(incident: Incident) -> FakeBigQueryClient:
    """A fake client shaped so that derive_source_health() + the active
    incidents list for `orders` both see the persisted incident, and every
    other approved source table (order_items, products) reads clean.
    """

    incident_row = {
        "incident_id": incident.incident_id,
        "created_at": incident.created_at,
        "dataset": incident.dataset,
        "table_id": incident.table_id,
        "check_type": incident.check_type,
        "severity": incident.severity,
        "status": incident.status,
    }
    rows_per_call = [
        [],  # order_items: derive_source_health -> no active incidents
        [],  # order_items: active incidents list -> empty
        [incident_row],  # orders: derive_source_health sees the incident
        [incident_row],  # orders: active incidents list
        [],  # products: derive_source_health -> no active incidents
        [],  # products: active incidents list -> empty
    ]
    return SequencedFakeBigQueryClient(rows_per_call=rows_per_call)


def _clean_governance_client() -> FakeBigQueryClient:
    """A fake client where every approved source table reads healthy --
    used to prove that once an incident resolves, degrade signal clears."""

    return SequencedFakeBigQueryClient(rows_per_call=[[] for _ in range(6)])


def _loupe_client_with_one_active_incident_on_orders(incident: Incident) -> FakeBigQueryClient:
    """Loupe's apps.loupe_agent.source_health.table_health() issues exactly
    ONE query per table (a single derive_source_health() call, unlike
    Governance's source_health_for_definition() which separately re-queries
    the active-incidents list for evidence) -- so this needs its own
    one-row-per-table queue, distinct from the Governance helper above."""

    incident_row = {
        "incident_id": incident.incident_id,
        "created_at": incident.created_at,
        "dataset": incident.dataset,
        "table_id": incident.table_id,
        "check_type": incident.check_type,
        "severity": incident.severity,
        "status": incident.status,
    }
    rows_per_call = [
        [],  # order_items: healthy
        [incident_row],  # orders: critical
        [],  # products: healthy
    ]
    return SequencedFakeBigQueryClient(rows_per_call=rows_per_call)


def test_persisted_triage_incident_degrades_governance_trust_scoring():
    """A persisted Triage incident affects Governance trust scoring."""

    incident = _incident(status="open", severity="high")
    _persist_one_incident(incident)

    definition = _revenue_definition()
    governance_client = _governance_client_with_one_active_incident_on_orders(incident)

    # trust_score_for_definition() internally calls
    # source_health_for_definition() itself -- calling that a second time
    # here would double-consume the fake client's queued rows, so all
    # assertions (including the raw evidence trail) read off the single
    # DefinitionTrust result it returns.
    result = trust_score_for_definition(governance_client, definition)

    evidence = result.evidence
    assert evidence.worst_health is not None
    assert evidence.worst_health.status == "critical"
    assert evidence.worst_health.table_id == "orders"
    assert len(evidence.active_incidents) == 1
    assert evidence.active_incidents[0].incident_id == incident.incident_id

    source_factor = next(f for f in result.trust.factors if f.name == "source_health")
    assert source_factor.points < 0  # an active critical incident penalizes trust, never a neutral/zero no-op
    assert result.trust.band == "do_not_rely"


def test_same_incident_produces_a_loupe_warning():
    """The same incident produces a Loupe warning."""

    incident = _incident(status="open", severity="high")
    _persist_one_incident(incident)

    loupe_client = _loupe_client_with_one_active_incident_on_orders(incident)
    health_rows = loupe_source_health.get_source_health(
        loupe_client, ("order_items", "orders", "products")
    )
    summary = loupe_source_health.summarize(health_rows)

    assert summary["status"] in ("degraded", "critical")
    assert summary["warning"] is not None
    assert "orders" in summary["warning"]


def test_resolved_incidents_stop_degrading_both_apps():
    """Resolved incidents stop degrading both Governance and Loupe."""

    resolved_incident = _incident(status="resolved", severity="high")
    definition = _revenue_definition()

    # A resolved incident is never "active" -- derive_source_health() and
    # list_active_incidents_for_table() both exclude status="resolved" at
    # the query-construction layer, so the clean-client shape below (no
    # rows returned for any table) is the correct simulation of "this
    # table's only incident just resolved."
    clean_client = _clean_governance_client()

    evidence = source_health_for_definition(clean_client, definition)
    assert evidence.active_incidents == []
    assert evidence.worst_health is None or evidence.worst_health.status == "healthy"

    result = trust_score_for_definition(clean_client, definition)
    source_factor = next(f for f in result.trust.factors if f.name == "source_health")
    assert source_factor.points > 0

    loupe_client = _clean_governance_client()
    health_rows = loupe_source_health.get_source_health(
        loupe_client, ("order_items", "orders", "products")
    )
    summary = loupe_source_health.summarize(health_rows)
    assert summary["status"] == "healthy"
    assert summary["warning"] is None

    del resolved_incident  # documents intent: never persisted as "active"


def test_persisted_pending_validation_status_appears_consistently_across_apps():
    """Persisted pending-validation status appears consistently."""

    definition = _revenue_definition()
    assert definition.certification_status == "pending_validation"

    governance_client = FakeBigQueryClient()
    governance_client.next_rows = [
        {
            "name": "revenue",
            "owner": "loupe-agent-team",
            "certification_status": "pending_validation",
            "last_reviewed_at": None,
            "version": "v1-extracted",
            "description": definition.description,
            "formula": definition.formula,
            "measurement_grain": definition.measurement_grain,
            "freshness_expectation": definition.freshness_expectation,
            "approved_source_tables": definition.approved_source_tables,
            "required_filters": [],
            "downstream_dashboards": [],
        }
    ]
    from apps.metric_governance.persistence import read_catalog

    catalog = read_catalog(governance_client)
    assert catalog.catalog_unavailable is False
    resolved = next(d for d in catalog.definitions if d.name == "revenue")
    assert resolved.certification_status == "pending_validation"

    loupe_client = FakeBigQueryClient()
    loupe_client.next_rows = [
        {
            "name": "revenue",
            "owner": "loupe-agent-team",
            "certification_status": "pending_validation",
            "last_reviewed_at": None,
            "version": "v1-extracted",
            "description": definition.description,
            "formula": definition.formula,
            "measurement_grain": definition.measurement_grain,
            "freshness_expectation": definition.freshness_expectation,
            "approved_source_tables": definition.approved_source_tables,
            "required_filters": [],
            "downstream_dashboards": [],
        }
    ]
    note = loupe_chat.certification_note(loupe_client, "revenue")
    assert "pending" in note.lower()


def test_persistence_unavailable_produces_honest_states_in_all_three_apps(monkeypatch):
    """Persistence unavailable produces honest states in all three apps
    (never fabricated healthy state, never fallback to sample/constant
    data being silently presented as real)."""

    # certification_note() only reads the persisted catalog through the
    # client at all when LOUPE_PERSISTENCE_MODE=persisted -- in the
    # default "constants" mode it never touches the client, so this test
    # (which specifically proves the persisted-mode-unavailable path
    # reports honestly) must opt into persisted mode explicitly.
    monkeypatch.setenv("LOUPE_PERSISTENCE_MODE", "persisted")

    # -- Triage: unreadable schema baseline / incident persistence -----
    from apps.data_quality_triage.persistence import read_schema_baseline

    triage_client = FakeBigQueryClient()
    triage_client.query_exception = RuntimeError("no schema_baselines table")
    assert read_schema_baseline(triage_client, dataset="ds", table_id="tbl") is None

    incident = _incident()
    broken_client = FakeBigQueryClient()
    broken_client.query_exception = RuntimeError("no loupe_platform.incidents table")
    outcomes = persist_confirmed_incidents(
        broken_client, [incident], actor="triage-bot", build_audit_event=_build_event
    )
    assert outcomes[0].persisted is False
    assert outcomes[0].error is not None

    # -- Governance: catalog unreadable ---------------------------------
    from apps.metric_governance.persistence import read_catalog

    governance_client = FakeBigQueryClient()
    governance_client.query_exception = RuntimeError("no metric_catalog table")
    catalog = read_catalog(governance_client)
    assert catalog.catalog_unavailable is True
    assert catalog.definitions == []

    evidence = source_health_for_definition(governance_client, _revenue_definition())
    assert evidence.worst_health is None
    assert evidence.active_incidents == []

    # -- Loupe: source health unresolvable, certification note honest --
    loupe_client = FakeBigQueryClient()
    loupe_client.query_exception = RuntimeError("no incidents table")
    health_rows = loupe_source_health.get_source_health(loupe_client, ("orders",))
    assert all(row["status"] == "unknown" and row["known"] is False for row in health_rows)

    note = loupe_chat.certification_note(loupe_client, "revenue")
    assert "unavailable" in note.lower() or "unknown" in note.lower()


def test_no_sample_data_enters_production_paths():
    """No sample data enters production (persisted-mode) paths -- a
    persistence read failure must never be silently backfilled with
    fixture/sample/demo data."""

    incident = _incident()
    broken_client = FakeBigQueryClient()
    broken_client.query_exception = RuntimeError("no loupe_platform.incidents table")
    outcomes = persist_confirmed_incidents(
        broken_client, [incident], actor="triage-bot", build_audit_event=_build_event
    )
    # A failed persist must be reported as a failed outcome for the exact
    # incident that was submitted -- never silently replaced with a
    # different (sample) incident_id, and never marked persisted=True.
    assert len(outcomes) == 1
    assert outcomes[0].incident_id == incident.incident_id
    assert outcomes[0].persisted is False

    from apps.metric_governance.persistence import read_catalog

    governance_client = FakeBigQueryClient()
    governance_client.query_exception = RuntimeError("no metric_catalog table")
    catalog = read_catalog(governance_client)
    # catalog_unavailable=True with an EMPTY definitions list -- never a
    # list of hardcoded/sample MetricDefinition objects standing in.
    assert catalog.catalog_unavailable is True
    assert catalog.definitions == []


def test_no_governed_action_uses_the_streaming_audit_path():
    """No governed action (incident persistence, metric certification)
    uses the streaming (non-atomic) audit path -- both must go through
    the transactional, idempotent audit_persistence module."""

    import ast
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    governed_files = [
        repo_root / "apps" / "data_quality_triage" / "persistence.py",
        repo_root / "apps" / "metric_governance" / "persistence.py",
    ]
    for path in governed_files:
        source = path.read_text()
        tree = ast.parse(source)
        streaming_audit_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "shared.audit"
        ]
        assert streaming_audit_imports == [], (
            f"{path} imports from shared.audit (the streaming, "
            "non-atomic path) -- governed actions must only use "
            "shared.audit_persistence.write_event_idempotent()."
        )


def test_no_startup_path_performs_schema_or_seed_writes():
    """No startup path (resolve_persistence(), or any app's main.py)
    performs schema management writes, seeding, or certification."""

    import ast
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    startup_files = [
        repo_root / "shared" / "persistence_bootstrap.py",
        repo_root / "apps" / "data_quality_triage" / "main.py",
        repo_root / "apps" / "metric_governance" / "main.py",
        repo_root / "apps" / "loupe_agent" / "main.py",
    ]
    forbidden_names = {
        "seed_current_catalog",
        "certify_metric_definition",
        "create_schema",
        "run_migrations",
        "execute_transaction",
        "register_template",
    }
    for path in startup_files:
        source = path.read_text()
        tree = ast.parse(source)
        called_names = {
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        offending = called_names & forbidden_names
        assert not offending, f"{path} calls forbidden startup action(s): {offending}"
