"""Tests for shared/schema_management.py.

Exercises bootstrap()/migrate()/validate_schema_version() against the
fake BigQuery client only -- no live BigQuery access, per
docs/development.md's fixture-based test strategy. Also proves the
corrected nine-table count (Phase 6 amendment 3) and idempotent
re-application (running bootstrap twice issues no redundant DDL).
"""

from __future__ import annotations

from shared.config import PlatformConfig
from shared.schema_management import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
    bootstrap,
    migrate,
    validate_schema_version,
)


def _config() -> PlatformConfig:
    return PlatformConfig(project="test-project", dataset="loupe_platform_test", location="US")


def test_exactly_one_migration_is_defined_at_current_version():
    assert len(MIGRATIONS) == 1
    assert MIGRATIONS[0].version == CURRENT_SCHEMA_VERSION


def test_migration_v1_creates_exactly_nine_tables(fake_client):
    config = _config()
    result = bootstrap(fake_client, config)

    assert result.already_current is False
    assert len(result.applied) == 1
    assert result.applied[0].statements_executed == 9  # 8 persistence tables + schema_migrations itself


def test_bootstrap_ddl_uses_create_table_if_not_exists_only(fake_client):
    config = _config()
    bootstrap(fake_client, config)

    ddl_statements = [sql for sql, _ in fake_client.queries if "CREATE TABLE" in sql]
    assert len(ddl_statements) == 9
    for statement in ddl_statements:
        assert "CREATE TABLE IF NOT EXISTS" in statement
        assert "DROP" not in statement.upper()
        assert "TRUNCATE" not in statement.upper()


def test_bootstrap_never_issues_drop_or_truncate(fake_client):
    config = _config()
    bootstrap(fake_client, config)
    for sql, _ in fake_client.queries:
        assert "DROP TABLE" not in sql.upper()
        assert "TRUNCATE" not in sql.upper()
        assert "DROP COLUMN" not in sql.upper()


def test_bootstrap_creates_every_expected_table_name(fake_client):
    config = _config()
    bootstrap(fake_client, config)

    ddl_statements = "\n".join(sql for sql, _ in fake_client.queries)
    for logical_name, table_id in config.all_tables().items():
        assert table_id in ddl_statements, f"missing DDL for {logical_name}"


def test_bootstrap_is_idempotent_and_skips_already_applied_migrations(fake_client):
    config = _config()
    first = bootstrap(fake_client, config)
    assert len(first.applied) == 1

    query_count_after_first = len(fake_client.queries)

    # Simulate that migration 1 is now recorded as applied, the way a
    # real schema_migrations table would report it on the next run.
    fake_client.next_rows = [{"version": 1}]

    second = bootstrap(fake_client, config)
    assert second.applied == []
    assert second.already_current is True

    # The read-side version check and idempotent lock-row repair run on every
    # bootstrap. No DDL or migration-record insert should repeat.
    new_queries = fake_client.queries[query_count_after_first:]
    assert len(new_queries) == 2
    assert "SELECT version" in new_queries[0][0]
    assert "MERGE" in new_queries[1][0]
    assert config.write_locks_table in new_queries[1][0]


def test_bootstrap_seeds_every_fixed_write_lock_domain(fake_client):
    config = _config()

    bootstrap(fake_client, config)

    seed_sql = next(sql for sql, _ in fake_client.queries if f"MERGE `{config.write_locks_table}`" in sql)
    for domain in ("incidents", "audit_events", "metric_catalog", "schema_baselines"):
        assert f"'{domain}'" in seed_sql
    assert "WHEN NOT MATCHED" in seed_sql


def test_migrate_is_bootstrap(fake_client):
    # migrate() is currently an alias for bootstrap() -- this test pins
    # that equivalence so a future split (see schema_management.py's
    # comment on why they're separate names) is a deliberate, visible
    # change rather than an accidental behavior drift.
    assert migrate is bootstrap


def test_validate_schema_version_reports_not_ok_when_nothing_applied(fake_client):
    config = _config()
    fake_client.next_rows = []

    result = validate_schema_version(fake_client, config)

    assert result.ok is False
    assert result.applied_version is None
    assert result.safe_error is not None


def test_validate_schema_version_reports_ok_when_current(fake_client):
    config = _config()
    fake_client.next_rows = [{"version": 1}]

    result = validate_schema_version(fake_client, config)

    assert result.ok is True
    assert result.applied_version == CURRENT_SCHEMA_VERSION


def test_validate_schema_version_never_creates_anything(fake_client):
    config = _config()
    fake_client.next_rows = []

    validate_schema_version(fake_client, config)

    for sql, _ in fake_client.queries:
        assert "CREATE" not in sql.upper()
        assert "INSERT" not in sql.upper()
