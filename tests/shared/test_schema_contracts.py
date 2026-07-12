"""Schema-contract tests: catch drift between shared/schema_management.py's
DDL and shared/models.py's dataclasses at unit-test speed, with no live
BigQuery access required.

Per Phase 6 amendment 4, not every table is required to match a domain
dataclass field-for-field (metric_catalog is a thin current-state
pointer, schema_baselines/schema_snapshots/write_locks/schema_migrations
have no corresponding shared.models dataclass at all, by design). These
tests instead check the tables that DO have a corresponding dataclass
(Incident, MetricVersion, AuditEvent) so a future field added to one side
without the other is caught here rather than at first live use.
"""

from __future__ import annotations

import dataclasses
import re

from shared.config import PlatformConfig
from shared.models import AuditEvent, Incident, MetricVersion
from shared.schema_management import (
    MIGRATIONS,
    _audit_events_ddl,
    _incidents_ddl,
    _metric_versions_ddl,
)


def _config() -> PlatformConfig:
    return PlatformConfig(project="p", dataset="loupe_platform_test")


def _strip_line_comments(sql: str) -> str:
    """Strip `-- ...` line comments before any structural parsing.

    Without this, a comment containing its own comma (e.g. "-- primary
    logical identifier, informational only") would be mistaken for a
    column-list separator by the naive comma-splitter below, silently
    merging the next real column name into the tail of the comment
    instead of treating it as its own column.
    """

    return re.sub(r"--[^\n]*", "", sql)


def _ddl_column_names(ddl: str) -> set[str]:
    """Extract column names from a CREATE TABLE ... ( ... ) DDL string --
    a deliberately simple parser (first identifier on each comma-
    separated top-level segment inside the parens, after stripping line
    comments), good enough for a schema-contract test without pulling in
    a real SQL DDL parser."""

    ddl = _strip_line_comments(ddl)
    match = re.search(r"\((.*)\)\s*(?:PARTITION|CLUSTER|$)", ddl, re.DOTALL)
    assert match, "could not locate column list in DDL"
    body = match.group(1)
    columns = set()
    depth = 0
    current = []
    parts = []
    for char in body:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))

    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        name = stripped.split()[0]
        columns.add(name)
    return columns


def _dataclass_field_names(cls) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_exactly_one_migration_covers_all_nine_tables():
    config = _config()
    assert len(MIGRATIONS) == 1
    ddl_statements = MIGRATIONS[0].ddl(config)
    assert len(ddl_statements) == 9


def test_incidents_ddl_covers_every_incident_dataclass_field():
    ddl_columns = _ddl_column_names(_incidents_ddl(_config()))
    dataclass_fields = _dataclass_field_names(Incident)
    missing = dataclass_fields - ddl_columns
    assert not missing, f"Incident fields missing from incidents DDL: {missing}"


def test_incidents_ddl_has_no_leftover_sql_column():
    # The Incident.sql field was replaced by sql_template/query_hash
    # (Phase 6 amendment 9) -- this pins that the DDL was updated in
    # lockstep, not just the Python dataclass.
    ddl_columns = _ddl_column_names(_incidents_ddl(_config()))
    assert "sql" not in ddl_columns
    assert "sql_template" in ddl_columns
    assert "query_hash" in ddl_columns


def test_metric_versions_ddl_covers_every_metric_version_dataclass_field():
    ddl_columns = _ddl_column_names(_metric_versions_ddl(_config()))
    dataclass_fields = _dataclass_field_names(MetricVersion)
    missing = dataclass_fields - ddl_columns
    assert not missing, f"MetricVersion fields missing from metric_versions DDL: {missing}"


def test_metric_versions_ddl_keeps_created_by_and_reviewer_distinct():
    ddl_columns = _ddl_column_names(_metric_versions_ddl(_config()))
    assert "created_by" in ddl_columns
    assert "reviewer" in ddl_columns
    assert "content_hash" in ddl_columns
    assert "prior_version" in ddl_columns
    assert "change_reason" in ddl_columns


def test_repeated_fields_never_use_unsupported_not_null_constraint():
    from shared.schema_management import _schema_baselines_ddl, _schema_snapshots_ddl

    ddls = (
        _metric_versions_ddl(_config()),
        _schema_snapshots_ddl(_config()),
        _schema_baselines_ddl(_config()),
    )
    for ddl in ddls:
        normalized = " ".join(_strip_line_comments(ddl).split()).upper()
        assert "ARRAY<STRING> NOT NULL" not in normalized
        assert "ARRAY<STRUCT<NAME STRING, FIELD_TYPE STRING>> NOT NULL" not in normalized


def test_audit_events_ddl_covers_every_audit_event_dataclass_field():
    ddl_columns = _ddl_column_names(_audit_events_ddl(_config()))
    dataclass_fields = _dataclass_field_names(AuditEvent)
    missing = dataclass_fields - ddl_columns
    assert not missing, f"AuditEvent fields missing from audit_events DDL: {missing}"


def test_audit_events_table_is_named_audit_events_not_audit_log():
    config = _config()
    assert config.audit_events_table.endswith(".audit_events")
