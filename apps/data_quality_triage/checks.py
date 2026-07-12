"""Deterministic checks: severity classification, metadata-only checks, the
Guardrails catalog, and the one place a TableFinding is promoted into a
shared.models.Incident.

Nothing in this module touches BigQuery. Metadata-only checks operate
purely on an already-fetched TableProfile (apps/data_quality_triage/
profiling.py); the live ratio-query checks that need a client live in
anomaly_engine.py instead, per the "keep checks, anomaly evaluation, ...
in focused modules" constraint. This split mirrors the original app's own
checks.py / anomaly_engine.py split.

Per docs/data-quality-triage.md: "AI does not decide whether data is
broken. The check result, threshold, and deterministic rule make that
decision." Nothing here calls an LLM. explanations.py (a later Phase 4
module) may narrate an already-built Incident, but it never runs through
this file.

--- Behavioral change vs. the original app ---
The original data-quality-incident-triage-agent's src/checks.py defined
build_incidents_from_profiles() (the empty-table / stale-freshness /
no-primary-key-candidate checks below) but its own src/data_service.py's
load_live_state() never called it -- only the ratio-based checks in
anomaly_engine.py ran against live data. That was dead code, not an
intentional design choice: the docs' "Freshness delays" and "Row-count ...
drift" categories are explicitly in scope, and an empty table is exactly
the kind of failure a row-count check exists to catch. run_metadata_checks()
below is wired into the live path (see anomaly_engine.py's
evaluate_profiles()), so these checks now actually run.

--- Phase 4 correction: schema drift ---
check_schema_drift() below fills the last metadata-only gap from
docs/data-quality-triage.md's six documented check categories. It is
deterministic (a pure comparison of two already-fetched TableProfile /
SchemaSnapshot column-type maps, no LLM involvement) and never claims
drift occurred when it has nothing to compare against: with no baseline
snapshot supplied, it returns an explicit status="not_evaluated" finding,
never a fabricated "pass" (which would silently claim "checked, no drift"
when nothing was actually checked) and never a fabricated "fail"
(claiming drift with no evidence). Baseline snapshot *persistence* --
capturing and storing a schema snapshot after each run so a later run has
something to compare against -- is real Phase 6 scheduling/storage work;
until then, every run of this check reports not_evaluated unless a
caller (main.py, or eventually a test harness backed by real persisted
snapshots) supplies one explicitly.

--- Phase 4 correction: detected vs. open ---
See shared/incidents.py's module docstring for the authoritative
definition: "detected" is reserved for a raw, unconfirmed monitoring
signal that has not yet been confirmed as an actionable incident; "open"
is the first status for a confirmed, actionable, tracked incident. Every
finding this module promotes into an Incident (build_incident_from_finding,
below) came from a deterministic check that already ran to completion and
already breached its documented rule -- there is no further confirmation
step pending, unlike (for example) an anomaly-detection heuristic that
flags something for a human to triage before it's known to be real. That
is exactly why build_incident_from_finding() always starts new incidents
at "open", never "detected": this app's checks never produce the kind of
raw, unconfirmed signal "detected" exists to represent. If a future check
category is added that only produces a preliminary, unconfirmed signal
(e.g. a statistical outlier flag pending human review), THAT would be the
correct place to use "detected" instead.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from apps.data_quality_triage.models import (
    CheckDefinition,
    CheckSeverity,
    CheckStatus,
    SchemaSnapshot,
    TableFinding,
)
from apps.data_quality_triage.profiling import TableProfile
from shared.audit import build_event
from shared.metric_catalog import definitions_referencing_table
from shared.models import AuditEvent, Incident, Severity

def _hash_sql_template(sql_template: Optional[str]) -> Optional[str]:
    """Deterministically hash an identifier-only SQL template for
    Incident.query_hash, so recurring incidents against the same check
    can be correlated without re-storing or re-comparing full SQL text.
    Returns None when there is no template to hash (metadata-only findings).
    """

    if sql_template is None:
        return None
    return hashlib.sha256(sql_template.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Severity / status classification (pure)
# ---------------------------------------------------------------------------


def classify_ratio_severity(
    ratio: float, *, medium: float, high: float, critical: float
) -> CheckSeverity:
    """Classify a ratio-based observation (duplicate ratio, null ratio,
    etc.) into the 4-level CheckSeverity, given ascending thresholds.
    Ported from the original anomaly_engine.py's `_severity_from_ratio`.
    """

    if ratio >= critical:
        return "critical"
    if ratio >= high:
        return "high"
    if ratio >= medium:
        return "medium"
    return "low"


def classify_volume_drift_severity(drift_ratio: float) -> Optional[CheckSeverity]:
    """Classify a latest-day-vs-trailing-average drift ratio. Returns None
    when the ratio is within the normal [0.5, 1.5] band (i.e. not flagged
    at all) -- ported verbatim from the original anomaly_engine.py logic:
    flagged at <=0.5x or >=1.5x prior average, critical at <=0.2x or >=3x.
    """

    if drift_ratio <= 0.2 or drift_ratio >= 3.0:
        return "critical"
    if drift_ratio <= 0.5 or drift_ratio >= 1.5:
        return "high"
    return None


def status_for_severity(severity: CheckSeverity) -> CheckStatus:
    """Map a CheckSeverity to a pass/warn/fail CheckStatus. "low" always
    reads as a pass -- it's the floor of the ratio classifiers above, not
    an anomaly in its own right."""

    if severity in ("high", "critical"):
        return "fail"
    if severity == "medium":
        return "warn"
    return "pass"


# ---------------------------------------------------------------------------
# Metadata-only checks (operate on an already-fetched TableProfile; no
# BigQuery access happens here)
# ---------------------------------------------------------------------------

# First migrated default for "how stale is too stale." Not derived from any
# documented SLA in the original app (none was found there) -- tunable
# later via a rule_version bump once real freshness SLAs are agreed per
# table, per docs/data-quality-triage.md's "rule version that generated the
# incident" field.
STALE_AFTER_MINUTES = 60 * 24 * 2  # 2 days


def check_empty_table(profile: TableProfile) -> Optional[TableFinding]:
    if profile.row_count > 0:
        return None
    return TableFinding(
        table_id=profile.table_id,
        check_name="row_count_empty",
        status="fail",
        severity="critical",
        observed_value=0.0,
        threshold=0.0,
        summary=f"{profile.table_id} currently has zero rows.",
        likely_root_cause="Upstream load job may have failed, or the table was truncated.",
    )


def check_stale_freshness(
    profile: TableProfile, *, stale_after_minutes: float = STALE_AFTER_MINUTES
) -> Optional[TableFinding]:
    if profile.freshness_minutes is None or profile.freshness_minutes < stale_after_minutes:
        return None
    return TableFinding(
        table_id=profile.table_id,
        check_name="freshness_delay",
        status="fail",
        severity="high",
        observed_value=profile.freshness_minutes,
        threshold=stale_after_minutes,
        summary=(
            f"{profile.table_id} has not been modified in "
            f"{profile.freshness_minutes:.0f} minutes (expected within {stale_after_minutes:.0f})."
        ),
        likely_root_cause="Scheduled load or upstream pipeline may be delayed or failing silently.",
    )


def check_missing_primary_candidate(profile: TableProfile) -> Optional[TableFinding]:
    if profile.primary_candidate is not None:
        return None
    return TableFinding(
        table_id=profile.table_id,
        check_name="no_primary_key_candidate",
        status="warn",
        severity="low",
        observed_value=None,
        threshold=None,
        summary=f"{profile.table_id} has no column matching known primary-key naming patterns.",
        likely_root_cause=(
            "Table schema may be non-standard, or the table is intentionally "
            "keyless (e.g. an append-only event/log table)."
        ),
    )


def check_schema_drift(profile: TableProfile, baseline: Optional[SchemaSnapshot]) -> TableFinding:
    """Deterministic schema-drift check: compares profile.column_types
    (the just-fetched, current schema) against `baseline` (a prior
    snapshot of the same table's schema).

    Unlike the other metadata checks above, this ALWAYS returns a
    TableFinding -- never None -- so "no baseline was available" and "the
    schema is unchanged" are both auditable, distinguishable outcomes,
    never silently collapsed into the same "nothing to report" case:

      - baseline is None -> status="not_evaluated". This function never
        claims drift occurred (or that nothing changed) when there is
        nothing to compare against.
      - baseline present, no differences -> status="pass".
      - baseline present, only additions -> status="warn" (the least
        disruptive category of drift; existing queries keep working).
      - baseline present, any removal, rename, or type change ->
        status="fail" (these can silently break downstream queries).

    Renamed-column detection is a heuristic, not a certainty: a column
    that disappears from `current` while a different, previously-absent
    column of the exact same type appears is reported as a "renamed
    (candidate)", never asserted as a confirmed rename -- name+type
    matching alone cannot distinguish "X was renamed to Y" from "X was
    dropped and an unrelated column Y happened to be added with the same
    type." Each column name participates in at most one rename guess.
    """

    if baseline is None:
        return TableFinding(
            table_id=profile.table_id,
            check_name="schema_drift",
            status="not_evaluated",
            severity="low",
            observed_value=None,
            threshold=None,
            summary=f"No schema baseline is available for {profile.table_id}; schema drift was not evaluated.",
            likely_root_cause="No prior schema snapshot has been captured for this table yet.",
        )

    current = profile.column_types
    prior = baseline.columns

    added = sorted(set(current) - set(prior))
    removed = sorted(set(prior) - set(current))
    type_changed = sorted(name for name in (set(current) & set(prior)) if current[name] != prior[name])

    remaining_removed = list(removed)
    remaining_added = list(added)
    renamed: list[tuple[str, str]] = []
    for removed_name in list(remaining_removed):
        removed_type = prior[removed_name]
        match = next((candidate for candidate in remaining_added if current[candidate] == removed_type), None)
        if match is not None:
            renamed.append((removed_name, match))
            remaining_removed.remove(removed_name)
            remaining_added.remove(match)

    pure_added, pure_removed = remaining_added, remaining_removed

    if not (pure_added or pure_removed or type_changed or renamed):
        return TableFinding(
            table_id=profile.table_id,
            check_name="schema_drift",
            status="pass",
            severity="low",
            observed_value=0.0,
            threshold=0.0,
            summary=f"{profile.table_id}'s schema matches the baseline captured at {baseline.captured_at}.",
            likely_root_cause="No schema drift detected.",
        )

    change_notes: list[str] = []
    if pure_added:
        change_notes.append(f"added: {', '.join(pure_added)}")
    if pure_removed:
        change_notes.append(f"removed: {', '.join(pure_removed)}")
    if renamed:
        change_notes.append("renamed (candidate): " + ", ".join(f"{old}->{new}" for old, new in renamed))
    if type_changed:
        change_notes.append(
            "type changed: " + ", ".join(f"{name} ({prior[name]}->{current[name]})" for name in type_changed)
        )

    breaking = bool(pure_removed or renamed or type_changed)
    status: CheckStatus = "fail" if breaking else "warn"
    severity: CheckSeverity = "high" if breaking else "medium"
    drift_count = len(pure_added) + len(pure_removed) + len(renamed) + len(type_changed)

    return TableFinding(
        table_id=profile.table_id,
        check_name="schema_drift",
        status=status,
        severity=severity,
        observed_value=float(drift_count),
        threshold=0.0,
        summary=(
            f"{profile.table_id}'s schema has drifted from the baseline captured at "
            f"{baseline.captured_at}: " + "; ".join(change_notes) + "."
        ),
        likely_root_cause=(
            "An upstream schema migration or ETL change altered this table's columns "
            "since the baseline was captured."
        ),
    )


def run_metadata_checks(
    profile: TableProfile, *, schema_baseline: Optional[SchemaSnapshot] = None
) -> list[TableFinding]:
    """Run every metadata-only check against one profile, returning only
    the findings that actually fired for the "return None means nothing to
    report" checks -- but check_schema_drift()'s result is always
    included, since even its "not_evaluated"/"pass" outcomes are
    meaningful, auditable facts (see that function's docstring), not
    absence-of-a-finding.

    `schema_baseline` is None by default: baseline snapshot persistence is
    Phase 6 work (see check_schema_drift()'s docstring), so callers with
    no live baseline store yet should simply omit it, which produces an
    honest not_evaluated schema_drift finding rather than skipping the
    check family entirely.
    """

    optional_checks = (
        check_empty_table(profile),
        check_stale_freshness(profile),
        check_missing_primary_candidate(profile),
    )
    findings = [finding for finding in optional_checks if finding is not None]
    findings.append(check_schema_drift(profile, schema_baseline))
    return findings


# ---------------------------------------------------------------------------
# Guardrails catalog (static, presentation-facing -- what Triage checks for)
# ---------------------------------------------------------------------------
#
# Eight entries, covering all six docs/data-quality-triage.md categories:
# metadata-only (row-count/empty, freshness delay, schema drift,
# primary-key-candidate advisory), live ratio queries (duplicate keys,
# null spike, volume drift), and query exceptions (anomaly_engine.py). As
# of the Phase 4 correction pass, every documented category has a
# deterministic implementation; production scheduling of schema-baseline
# snapshots (so schema_drift can move beyond not_evaluated) remains
# Phase 6 work -- see check_schema_drift()'s docstring.

GUARDRAILS_CATALOG: list[CheckDefinition] = [
    CheckDefinition(
        name="Row Count / Empty Table",
        description="Flags a table that currently has zero rows.",
        threshold="fail when row_count == 0",
        severity="critical",
    ),
    CheckDefinition(
        name="Freshness Delay",
        description="Flags a table that hasn't been modified within the expected freshness window.",
        threshold=f"fail when freshness exceeds {STALE_AFTER_MINUTES:.0f} minutes",
        severity="high",
    ),
    CheckDefinition(
        name="Schema Drift",
        description=(
            "Flags added, removed, renamed, or type-changed columns versus a supplied baseline "
            "snapshot. Reports not_evaluated (never a guess) when no baseline exists yet."
        ),
        threshold="fail on removal/rename/type-change, warn on additions only, not_evaluated with no baseline",
        severity="high",
    ),
    CheckDefinition(
        name="Volume Drift",
        description="Flags a table's latest-day row count drifting sharply from its trailing 7-day average.",
        threshold="high at <=0.5x or >=1.5x prior average; critical at <=0.2x or >=3x",
        severity="high",
    ),
    CheckDefinition(
        name="Null Spike",
        description="Flags a rising null ratio on a table's nullable candidate column.",
        threshold="medium >=2%, high >=10%, critical >=25%",
        severity="medium",
    ),
    CheckDefinition(
        name="Duplicate Key Growth",
        description="Flags a rising duplicate ratio on a table's primary-key candidate column.",
        threshold="medium >=0.1%, high >=1%, critical >=5%",
        severity="medium",
    ),
    CheckDefinition(
        name="Query Exception",
        description=(
            "Flags a check query that failed to execute (timeout, permission denial, malformed query, "
            "or another warehouse-side failure), as distinct from a query that ran fine and found bad data."
        ),
        threshold="error whenever a check query raises instead of returning a result",
        severity="high",
    ),
    CheckDefinition(
        name="Primary Key Candidate Missing",
        description="Advisory: flags a table with no column matching known primary-key naming patterns.",
        threshold="warn when no primary-key candidate is found",
        severity="low",
    ),
]


# ---------------------------------------------------------------------------
# TableFinding -> shared.models.Incident (the one authoritative promotion path)
# ---------------------------------------------------------------------------

# Documented, single collapse point from the app-local 4-level CheckSeverity
# to shared.models.Severity's 3-level vocabulary (docs/data-quality-triage.md's
# severity baseline has no "critical" tier). This mapping happens nowhere
# else in the codebase.
_SEVERITY_COLLAPSE: dict[CheckSeverity, Severity] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "critical": "high",
}


def collapse_severity(severity: CheckSeverity) -> Severity:
    """Translate a TableFinding's 4-level severity into
    shared.models.Incident's 3-level severity, per docs/data-quality-triage.md.
    """

    return _SEVERITY_COLLAPSE[severity]


# Statuses that never become an Incident: "pass" (the check ran and found
# nothing wrong) and "not_evaluated" (the check could not honestly reach a
# verdict -- e.g. schema_drift with no baseline yet). Both are meaningful,
# auditable outcomes worth keeping in a findings list for visibility, but
# neither represents a confirmed problem, so neither is promoted.
_NON_INCIDENT_STATUSES: frozenset[CheckStatus] = frozenset({"pass", "not_evaluated"})


def build_incident_from_finding(
    finding: TableFinding,
    *,
    dataset: str,
    created_at: str,
    rule_version: str = "v1-migrated",
) -> Incident:
    """Promote one confirmed (non-pass, non-not_evaluated) TableFinding
    into a shared.models.Incident.

    Behavioral changes vs. the original app:
      - status always starts "open", never "investigating" and never
        "detected". The original app set status="investigating" directly
        for high/critical findings, which skips
        shared.incidents.ALLOWED_TRANSITIONS's required "acknowledged"
        step. "detected" is reserved for a raw, unconfirmed monitoring
        signal (see shared/incidents.py's module docstring); every
        finding reaching this function already came from a deterministic
        check that ran to completion and already breached its documented
        rule, so it is already a confirmed, actionable incident -- "open",
        the first status in the shared lifecycle for confirmed incidents,
        is the only correct starting point. Moving it further requires an
        explicit acknowledge/investigate transition via
        incident_lifecycle.py. See this module's docstring for the full
        "detected vs. open" discussion.
      - affected_metrics is now populated from
        shared.metric_catalog.definitions_referencing_table(), rather than
        left empty -- the original local Incident model had no equivalent
        of this cross-app lookup available to it. That lookup normalizes
        BigQuery identifier forms (bare table name, dataset.table,
        project.dataset.table) so a differently-qualified finding.table_id
        still matches the catalog's stored table names -- see
        shared/metric_catalog.py's _normalize_table_identifier().
      - sql_template is populated from finding.sql for live ratio-query
        findings (None for metadata-only findings, including
        query_exception findings, which never carry SQL text -- see
        anomaly_engine.py's _query_exception_finding()) -- the original
        local Incident model had no sql field at all. finding.sql is
        always an identifier-only template (table/column names resolved
        from BigQuery metadata) with no bound literal values, so it is
        safe to persist verbatim as sql_template (shared/models.py,
        Phase 6 amendment 9) rather than needing separate redaction.
        query_hash is derived from that same template so repeated
        occurrences of the same check can be correlated without
        re-comparing full SQL text.
      - "error" status findings (query_exception -- the check's query
        itself failed to execute) DO become incidents, same as "warn"/
        "fail": an inability to even run a check is itself an
        operability problem worth surfacing, per
        docs/data-quality-triage.md's "Query exceptions" category.
    """

    if finding.status in _NON_INCIDENT_STATUSES:
        raise ValueError(
            f"A {finding.status!r} TableFinding ({finding.check_name!r}) is not an incident."
        )

    incident_id = f"{dataset}.{finding.table_id}.{finding.check_name}.{created_at}"
    return Incident(
        incident_id=incident_id,
        created_at=created_at,
        dataset=dataset,
        table_id=finding.table_id,
        check_type=finding.check_name,
        severity=collapse_severity(finding.severity),
        status="open",
        observed_value=finding.observed_value,
        expected_value=finding.threshold,
        sql_template=finding.sql,
        query_hash=_hash_sql_template(finding.sql),
        affected_metrics=[d.name for d in definitions_referencing_table(finding.table_id)],
        affected_dashboards=[],
        playbook=None,
        rule_version=rule_version,
    )


def findings_to_incidents(
    findings: list[TableFinding],
    *,
    dataset: str,
    created_at: str,
    rule_version: str = "v1-migrated",
) -> list[Incident]:
    """Promote every confirmed finding in `findings` into an Incident, in
    the same order. "pass" and "not_evaluated" findings are silently
    skipped -- see _NON_INCIDENT_STATUSES."""

    return [
        build_incident_from_finding(finding, dataset=dataset, created_at=created_at, rule_version=rule_version)
        for finding in findings
        if finding.status not in _NON_INCIDENT_STATUSES
    ]


def build_audit_event_for_incident(
    incident: Incident,
    finding: TableFinding,
    *,
    event_id: str,
    timestamp: str,
    actor: str = "data_quality_triage.checks",
) -> AuditEvent:
    """Build (not persist -- shared/audit.py's write path is contract-only
    until Phase 6, same as everywhere else) an audit event that retains
    the TableFinding's original, full-resolution local severity alongside
    the collapsed Incident severity it became.

    This is the concrete answer to "the original local severity is
    retained in structured metadata or audit context when useful for
    diagnosis": collapse_severity() is a lossy, one-way translation on the
    Incident record itself (docs/data-quality-triage.md's 3-level
    vocabulary has no "critical" tier), but nothing about the original
    4-level classification is discarded -- it is always still present on
    the TableFinding that produced the incident (correlate by table_id +
    check_type/check_name, as apps/data_quality_triage/ui.py's
    _finding_for() already does), and this function additionally captures
    it in a structured audit-event context, so "was this originally a
    'critical' or merely a 'high' finding before collapse" is answerable
    without needing to guess or re-run anything.
    """

    return build_event(
        event_id=event_id,
        timestamp=timestamp,
        actor=actor,
        event_type="incident_created",
        subject=incident.incident_id,
        outcome="incident_created",
        context={
            "table_id": finding.table_id,
            "check_name": finding.check_name,
            "local_severity": finding.severity,
            "collapsed_severity": incident.severity,
            "check_status": finding.status,
        },
    )
