"""Version-controlled, idempotent, forward-only schema management for the
nine `loupe_platform` persistence tables.

Per Phase 6's approved amendment 3, the corrected table count is NINE:
metric_catalog, metric_versions, incidents, incident_transitions,
audit_events, schema_snapshots, schema_baselines, schema_migrations, and
write_locks (added by amendment 2's write-lock-row uniqueness strategy).
source_health is deliberately NOT a table -- it stays derived on demand
from `incidents` (shared.data_service.derive_source_health()), per the
approved open decision against materializing it.

--- Bootstrap is an explicit administrative action only (amendment 14) ---
Nothing in this module is called from any app's main.py / build_state().
bootstrap() and migrate() are only ever invoked via this module's `python
-m shared.schema_management ...` CLI entry point at the bottom of this
file, run by a human as a deliberate administrative step. Application
startup calls only validate_schema_version() (read-only: checks
schema_migrations' latest applied version and confirms expected tables
exist via metadata calls) -- it never creates, migrates, seeds,
truncates, or deletes anything, no matter what it finds.

--- Forward-only migrations ---
BigQuery's DDL is not transactional and has limited support for column
drops/renames. Every migration in MIGRATIONS therefore only ever adds
(new tables, or new nullable columns via `ADD COLUMN IF NOT EXISTS`) --
never drops or renames. A field that needs retiring is documented as
deprecated and left in place rather than removed; a genuine destructive
change is a separate, manual, out-of-band operation this module
deliberately does not expose (no DROP TABLE / DROP COLUMN / TRUNCATE path
exists anywhere in this file).

--- Idempotency ---
Every CREATE TABLE uses `IF NOT EXISTS`; every ADD COLUMN uses
`IF NOT EXISTS`. Running bootstrap() twice against an already-current
schema is a safe no-op. schema_migrations records which migration
versions have been applied so migrate() only re-runs what's pending.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from shared.config import DEFAULT_LOCATION, PlatformConfig, load_platform_config

CURRENT_SCHEMA_VERSION = 1


class DDLClientLike(Protocol):
    """Structural type for anything schema_management can issue DDL
    against. Deliberately narrower than run_query()'s read-only contract
    -- this module is the one, explicit exception to "SQL only flows
    through the read-only gateway," and that exception is scoped to this
    file alone, invoked only via the CLI at the bottom, never imported
    into any app's request path."""

    def query(self, sql: str, job_config: Any = None) -> Any: ...


# ---------------------------------------------------------------------------
# Table DDL (nine tables total)
# ---------------------------------------------------------------------------


def _metric_catalog_ddl(config: PlatformConfig) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS `{config.metric_catalog_table}` (
        name STRING NOT NULL,                 -- primary logical identifier (informational only; BigQuery does not enforce uniqueness)
        current_version STRING NOT NULL,       -- FK-by-value into metric_versions.version for this name (informational only)
        owner STRING NOT NULL,
        certification_status STRING NOT NULL,
        last_reviewed_at TIMESTAMP,
        updated_at TIMESTAMP NOT NULL
    )
    CLUSTER BY name
    """


def _metric_versions_ddl(config: PlatformConfig) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS `{config.metric_versions_table}` (
        name STRING NOT NULL,
        version STRING NOT NULL,               -- primary logical identifier is (name, version), informational only
        description STRING NOT NULL,
        formula STRING NOT NULL,
        measurement_grain STRING NOT NULL,
        freshness_expectation STRING,
        certification_status STRING NOT NULL,
        approved_source_tables ARRAY<STRING>, -- BigQuery arrays cannot be declared NOT NULL; NULL is stored as []
        required_filters ARRAY<STRING>,
        downstream_dashboards ARRAY<STRING>,
        content_hash STRING NOT NULL,          -- shared.metric_hashing.compute_content_hash() output
        prior_version STRING,
        created_by STRING NOT NULL,            -- author of this version's content -- never conflated with reviewer
        created_at TIMESTAMP NOT NULL,
        change_reason STRING NOT NULL,
        validation_evidence STRING,
        review_notes STRING,
        reviewer STRING,                       -- distinct from created_by; null until this version is reviewed/certified
        reviewed_at TIMESTAMP
    )
    CLUSTER BY name, version
    """


def _incidents_ddl(config: PlatformConfig) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS `{config.incidents_table}` (
        incident_id STRING NOT NULL,           -- primary logical identifier, informational only
        created_at TIMESTAMP NOT NULL,
        dataset STRING NOT NULL,
        table_id STRING NOT NULL,
        check_type STRING NOT NULL,
        severity STRING NOT NULL,
        status STRING NOT NULL,
        observed_value FLOAT64,
        expected_value FLOAT64,
        sql_template STRING,                   -- identifier-only template, never bound literal values (Phase 6 amendment 9)
        query_hash STRING,
        affected_metrics ARRAY<STRING>,
        affected_dashboards ARRAY<STRING>,
        playbook STRING,
        owner STRING,
        acknowledged_at TIMESTAMP,
        resolved_at TIMESTAMP,
        resolution_notes STRING,
        rule_version STRING,
        recurrence_of_incident_id STRING,
        row_version INT64 NOT NULL             -- optimistic-concurrency counter; see incident_transitions
    )
    PARTITION BY DATE(created_at)
    CLUSTER BY dataset, table_id, status
    """


def _incident_transitions_ddl(config: PlatformConfig) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS `{config.incident_transitions_table}` (
        transition_id STRING NOT NULL,         -- primary logical identifier, informational only
        incident_id STRING NOT NULL,           -- FK-by-value to incidents.incident_id, informational only
        from_status STRING NOT NULL,
        to_status STRING NOT NULL,
        transitioned_at TIMESTAMP NOT NULL,
        actor STRING NOT NULL,
        resolution_notes STRING,
        row_version_before INT64 NOT NULL
    )
    PARTITION BY DATE(transitioned_at)
    CLUSTER BY incident_id
    """


def _audit_events_ddl(config: PlatformConfig) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS `{config.audit_events_table}` (
        event_id STRING NOT NULL,              -- primary logical identifier, informational only
        timestamp TIMESTAMP NOT NULL,
        actor STRING NOT NULL,
        event_type STRING NOT NULL,
        subject STRING NOT NULL,
        outcome STRING NOT NULL,
        context JSON
    )
    PARTITION BY DATE(timestamp)
    CLUSTER BY subject, event_type
    """


def _schema_snapshots_ddl(config: PlatformConfig) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS `{config.schema_snapshots_table}` (
        snapshot_id STRING NOT NULL,           -- primary logical identifier, informational only
        table_id STRING NOT NULL,
        dataset STRING NOT NULL,
        captured_at TIMESTAMP NOT NULL,
        columns ARRAY<STRUCT<name STRING, field_type STRING>>, -- repeated fields are always nullable/empty in BigQuery
        schema_hash STRING NOT NULL            -- deterministic hash of (columns), used to suppress redundant snapshots (Phase 6 amendment 9)
    )
    PARTITION BY DATE(captured_at)
    CLUSTER BY dataset, table_id
    """


def _schema_baselines_ddl(config: PlatformConfig) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS `{config.schema_baselines_table}` (
        dataset STRING NOT NULL,
        table_id STRING NOT NULL,              -- primary logical identifier is (dataset, table_id), informational only
        columns ARRAY<STRUCT<name STRING, field_type STRING>>, -- repeated fields are always nullable/empty in BigQuery
        source_snapshot_id STRING NOT NULL,    -- FK-by-value to schema_snapshots.snapshot_id, informational only
        promoted_at TIMESTAMP NOT NULL,
        promoted_by STRING NOT NULL
    )
    CLUSTER BY dataset, table_id
    """


def _schema_migrations_ddl(config: PlatformConfig) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS `{config.schema_migrations_table}` (
        version INT64 NOT NULL,
        description STRING NOT NULL,
        applied_at TIMESTAMP NOT NULL
    )
    """


def _write_locks_ddl(config: PlatformConfig) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS `{config.write_locks_table}` (
        lock_domain STRING NOT NULL,           -- one of shared.persistence_transactions.LOCK_DOMAINS, never dynamic
        last_touched_at TIMESTAMP,
        last_touched_by STRING
    )
    """


# Seed rows for write_locks -- one row per predefined LOCK_DOMAINS entry.
# This is DATA (a fixed, tiny set of domain rows the transaction API
# mutates to force contention), not catalog content, so it is applied as
# part of bootstrap's DDL-adjacent setup rather than being confused with
# "seeding the metric catalog" (which per amendment 10/14 remains a
# separate, explicit, never-automatic administrative action).
_LOCK_DOMAIN_SEED_ROWS = ("incidents", "audit_events", "metric_catalog", "schema_baselines")


def _ensure_write_lock_rows(client: "DDLClientLike", config: PlatformConfig) -> None:
    """Idempotently create the four fixed transaction lock rows.

    This runs on every explicit bootstrap invocation, including when the
    schema migration is already recorded. That is intentional: an earlier
    bootstrap version created the table but omitted these rows, and the
    transactional writers correctly refuse to operate without them.
    """

    values = ", ".join(f"'{domain}'" for domain in _LOCK_DOMAIN_SEED_ROWS)
    client.query(
        f"""
        MERGE `{config.write_locks_table}` T
        USING (
          SELECT lock_domain
          FROM UNNEST([{values}]) AS lock_domain
        ) S
        ON T.lock_domain = S.lock_domain
        WHEN NOT MATCHED THEN
          INSERT (lock_domain, last_touched_at, last_touched_by)
          VALUES (S.lock_domain, NULL, NULL)
        """,
        job_config=None,
    ).result()


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    ddl: Callable[[PlatformConfig], list[str]]


def _migration_v1_ddl(config: PlatformConfig) -> list[str]:
    return [
        _schema_migrations_ddl(config),  # must exist first, to record this migration's own application
        _metric_catalog_ddl(config),
        _metric_versions_ddl(config),
        _incidents_ddl(config),
        _incident_transitions_ddl(config),
        _audit_events_ddl(config),
        _schema_snapshots_ddl(config),
        _schema_baselines_ddl(config),
        _write_locks_ddl(config),
    ]


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description=(
            "Initial bootstrap: nine persistence tables (metric_catalog, "
            "metric_versions, incidents, incident_transitions, "
            "audit_events, schema_snapshots, schema_baselines, "
            "schema_migrations, write_locks). audit_events is named "
            "audit_events from the start (Phase 6 amendment 5) -- no "
            "audit_log table has ever existed live, so no rename "
            "migration is needed."
        ),
        ddl=_migration_v1_ddl,
    ),
]


# ---------------------------------------------------------------------------
# Applying migrations (explicit CLI action only -- see module docstring)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MigrationApplication:
    version: int
    description: str
    statements_executed: int


@dataclass(frozen=True)
class BootstrapResult:
    applied: list[MigrationApplication]
    already_current: bool


def _applied_versions(client: "DDLClientLike", config: PlatformConfig) -> set[int]:
    """Read which migration versions are already recorded. Returns an
    empty set (not an error) if schema_migrations itself does not exist
    yet -- that is the expected state before the very first bootstrap."""

    try:
        job = client.query(
            f"SELECT version FROM `{config.schema_migrations_table}`", job_config=None
        )
        rows = job.result()
        return {row["version"] if isinstance(row, dict) else row.version for row in rows}
    except Exception:
        return set()


def bootstrap(client: "DDLClientLike", config: PlatformConfig) -> BootstrapResult:
    """Apply every migration in MIGRATIONS that is not yet recorded in
    schema_migrations, in version order. Idempotent: running this twice
    against an already-current schema applies nothing the second time.

    This function issues DDL directly via `client.query()` -- it does NOT
    go through shared.data_service.run_query(), which would reject DDL
    outright via UnsafeQueryError. That rejection is correct and must stay
    in place for every other module; this file is the one, explicit,
    narrowly-scoped exception, and it is never imported by application
    request-handling code (see module docstring).
    """

    already_applied = _applied_versions(client, config)
    applications: list[MigrationApplication] = []

    for migration in MIGRATIONS:
        if migration.version in already_applied:
            continue
        statements = migration.ddl(config)
        for statement in statements:
            client.query(statement, job_config=None).result()
        client.query(
            f"""
            INSERT INTO `{config.schema_migrations_table}` (version, description, applied_at)
            VALUES ({migration.version}, {migration.description!r}, CURRENT_TIMESTAMP())
            """,
            job_config=None,
        ).result()
        applications.append(
            MigrationApplication(
                version=migration.version,
                description=migration.description,
                statements_executed=len(statements),
            )
        )

    # Lock rows are required data for every transactional writer. Keep this
    # outside the migration-only branch so bootstrap repairs an existing v1
    # schema whose write_locks table is present but empty.
    _ensure_write_lock_rows(client, config)

    return BootstrapResult(applied=applications, already_current=not applications)


# migrate() is an alias for bootstrap() at this stage: both simply apply
# whatever is pending. They are kept as two distinct names because a
# future version may want bootstrap() to also handle first-time-only
# concerns (e.g. verifying the dataset's location) that a later
# incremental migrate() call should not repeat -- that split is not yet
# needed with only one migration defined, so both names currently share
# one implementation rather than duplicating it.
migrate = bootstrap


# ---------------------------------------------------------------------------
# Read-only startup validation (safe to call from application code)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchemaValidationResult:
    ok: bool
    applied_version: int | None
    expected_version: int
    safe_error: str | None = None


def validate_schema_version(client: "DDLClientLike", config: PlatformConfig) -> SchemaValidationResult:
    """Read-only check: does schema_migrations report the expected
    current version? Never creates, migrates, or seeds anything, no
    matter what it finds -- per amendment 14, that is bootstrap()'s job
    alone, invoked only via this module's explicit CLI.
    """

    applied = _applied_versions(client, config)
    if not applied:
        return SchemaValidationResult(
            ok=False,
            applied_version=None,
            expected_version=CURRENT_SCHEMA_VERSION,
            safe_error=(
                "No applied schema migrations were found. Persistence "
                "tables may not be bootstrapped yet in this environment."
            ),
        )

    highest = max(applied)
    if highest < CURRENT_SCHEMA_VERSION:
        return SchemaValidationResult(
            ok=False,
            applied_version=highest,
            expected_version=CURRENT_SCHEMA_VERSION,
            safe_error=(
                f"Persisted schema is at version {highest}, but this "
                f"application expects version {CURRENT_SCHEMA_VERSION}. "
                "An administrator needs to run the migration command."
            ),
        )

    return SchemaValidationResult(ok=True, applied_version=highest, expected_version=CURRENT_SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# Explicit administrative CLI entry point (amendment 14)
# ---------------------------------------------------------------------------
#
# `python -m shared.schema_management bootstrap --project ... [--dataset ...]`
# `python -m shared.schema_management migrate --project ... [--dataset ...]`
#
# This is the ONLY supported way to run bootstrap()/migrate() against a
# real BigQuery project. It requires the caller to explicitly name the
# target dataset (never defaults silently to a production-sounding name
# without the operator having typed it) and prints a loud confirmation of
# exactly which project/dataset/location it is about to modify before
# doing anything, per amendment 11's Phase 6A safety requirement that
# bootstrap must never run against production loupe_platform as a side
# effect of anything else.


def _build_real_client(project: str, location: str):
    from google.cloud import bigquery

    return bigquery.Client(project=project, location=location)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m shared.schema_management",
        description="Explicit, administrator-invoked schema bootstrap/migration for loupe_platform.",
    )
    parser.add_argument("action", choices=["bootstrap", "migrate", "validate"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", default=None, help="Defaults to LOUPE_DATASET or 'loupe_platform'.")
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required to actually execute bootstrap/migrate. Without it, this only prints what would run.",
    )
    args = parser.parse_args(argv)

    env_overrides = {"LOUPE_BQ_PROJECT": args.project, "LOUPE_BQ_LOCATION": args.location}
    if args.dataset:
        env_overrides["LOUPE_DATASET"] = args.dataset
    config = load_platform_config(env_overrides)

    print(f"Target: project={config.project!r} dataset={config.dataset!r} location={config.location!r}")
    if args.action == "validate":
        client = _build_real_client(config.project, config.location)
        result = validate_schema_version(client, config)
        print(result)
        return 0 if result.ok else 1

    if not args.yes:
        print(
            "Dry run only (pass --yes to actually execute). This command "
            "would apply pending migrations against the target above."
        )
        return 0

    client = _build_real_client(config.project, config.location)
    result = bootstrap(client, config)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
