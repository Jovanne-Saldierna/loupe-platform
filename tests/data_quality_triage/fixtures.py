"""FICTIONAL example incident/check data, preserved as test fixtures only.

Ported from the original data-quality-incident-triage-agent's
src/sample_data.py (read-only reference; that repository is not modified),
which the original app.py loaded as a live fallback whenever its BigQuery
path raised (`except Exception: state = load_sample_data()`). Per the
Phase 4 migration spec ("Preserve useful sample incidents only as test
fixtures, never production runtime data"), that fallback has been removed
(see apps/data_quality_triage/main.py's build_state(), which returns an
honest data_available=False state instead) and this content now exists
ONLY here, for exercising remediation.py / explanations.py / chat.py
against realistic-shaped data without requiring live BigQuery access.

Nothing in this module is imported by anything under apps/ -- see
test_fixtures.py's test_fixtures_module_is_never_imported_by_the_app,
which proves that by scanning apps/data_quality_triage/'s own source for
any reference to this module.

table_id values below ("fct_orders", "dim_customers") are the original
sample data's dbt-style model names, not real bigquery-public-data.
thelook_ecommerce table names -- consistent with them being illustrative
fixtures, not real production data shapes.
"""

from __future__ import annotations

from apps.data_quality_triage.models import CheckDefinition, TableFinding
from shared.models import Incident

# ---------------------------------------------------------------------------
# Sample incidents (ported from src/sample_data.py's INC-1042 / INC-1043)
# ---------------------------------------------------------------------------
#
# Behavioral note: the original sample incidents used status="investigating"
# directly for the high-severity example. Per checks.py's documented
# status-collapse fix (build_incident_from_finding() always starts new
# incidents at "open"), these fixtures use "open" too, so they stay
# representative of what the migrated pipeline actually produces -- not a
# frozen snapshot of the original app's now-superseded behavior.

SAMPLE_INCIDENT_1042 = Incident(
    incident_id="INC-1042",
    created_at="2026-07-01T08:15:00Z",
    dataset="fictional_example_dataset",
    table_id="fct_orders",
    check_type="duplicate_key_ratio",
    severity="high",
    status="open",
    observed_value=0.031,
    expected_value=0.01,
    sql_template=None,
    affected_metrics=["revenue", "margin"],
    affected_dashboards=["Revenue Trend", "KPI Summary"],
    playbook=None,
    rule_version="fictional-example",
)

SAMPLE_INCIDENT_1043 = Incident(
    incident_id="INC-1043",
    created_at="2026-07-02T14:40:00Z",
    dataset="fictional_example_dataset",
    table_id="dim_customers",
    check_type="null_ratio",
    severity="medium",
    status="open",
    observed_value=0.045,
    expected_value=0.02,
    sql_template=None,
    affected_metrics=[],
    affected_dashboards=["Customer 360"],
    playbook=None,
    rule_version="fictional-example",
)

SAMPLE_FINDING_1042 = TableFinding(
    table_id="fct_orders",
    check_name="duplicate_key_ratio",
    status="fail",
    severity="high",
    observed_value=0.031,
    threshold=0.01,
    summary="fct_orders.order_id has a 3.10% duplicate-key ratio, well above the 1% threshold.",
    likely_root_cause="A backfill job appears to have re-run without deduplication over the last 24 hours.",
    affected_assets=["Revenue Trend", "KPI Summary"],
)

SAMPLE_FINDING_1043 = TableFinding(
    table_id="dim_customers",
    check_name="null_ratio",
    status="warn",
    severity="medium",
    observed_value=0.045,
    threshold=0.02,
    summary="dim_customers.email has a 4.50% null ratio, above the 2% warning threshold.",
    likely_root_cause="A recent signup-flow change may have made the email field optional upstream.",
    affected_assets=["Customer 360"],
)

SAMPLE_INCIDENTS = [SAMPLE_INCIDENT_1042, SAMPLE_INCIDENT_1043]
SAMPLE_FINDINGS = [SAMPLE_FINDING_1042, SAMPLE_FINDING_1043]

# ---------------------------------------------------------------------------
# Sample check catalog (ported from src/sample_data.py's 4 MetricCheck
# entries). Superseded in production by checks.GUARDRAILS_CATALOG (6
# entries, reflecting what actually runs today) -- these are kept only to
# exercise CheckDefinition-shaped data in tests.
# ---------------------------------------------------------------------------

SAMPLE_CHECK_DEFINITIONS = [
    CheckDefinition(
        name="Row Count Drop",
        description="Fictional example: flags a sudden drop in daily row count.",
        threshold="fictional-example threshold",
        severity="high",
    ),
    CheckDefinition(
        name="Null Spike",
        description="Fictional example: flags a rising null ratio on a key column.",
        threshold="fictional-example threshold",
        severity="medium",
    ),
    CheckDefinition(
        name="Duplicate Key Growth",
        description="Fictional example: flags a rising duplicate-key ratio.",
        threshold="fictional-example threshold",
        severity="high",
    ),
    CheckDefinition(
        name="Freshness Delay",
        description="Fictional example: flags a table that hasn't refreshed on schedule.",
        threshold="fictional-example threshold",
        severity="medium",
    ),
]
