"""Transactional schema-baseline promotion.

Promotes an observed schema (a {column_name: bigquery_field_type} map,
the same shape shared.schema_hashing.compute_schema_hash() already takes)
to `schema_baselines` -- the "this is the expected/approved shape of this
table" row Governance/Triage compare future observations against -- and
records that promotion as an audit event, both in ONE atomic script.
Follows shared/persistence_transactions.py's own worked example exactly
("a schema-baseline promotion's upsert + audit-event-insert must succeed
or fail as one atomic unit").

`schema_baselines`'s natural key is (dataset, table_id) -- there is no
separate identity column (see shared/schema_management.py's DDL comment:
"primary logical identifier is (dataset, table_id), informational
only"), so promotion is an upsert (MERGE), not an insert-if-absent: a
second promotion for the same (dataset, table_id) is expected to
overwrite the prior baseline with the newly-promoted one, not conflict
with it -- promoting a new baseline over an old one is the whole point of
this operation, unlike incident/audit-event creation where the SAME id
recurring is expected to mean "the same logical write, retried."

The script touches BOTH the 'schema_baselines' write-lock domain (before
the MERGE) and the 'audit_events' write-lock domain (before the audit
insert) -- each table this script writes to is guarded by its own
domain's lock row first, per shared/persistence_transactions.py's
write-lock discipline, rather than only locking one of the two domains a
two-table write touches.

`columns` (ARRAY<STRUCT<name STRING, field_type STRING>> in the DDL) is
passed through the same PARSE_JSON(@columns_json)-on-a-bound-STRING
pattern shared/audit_persistence.py uses for `context` -- see that
module's docstring for why (no native named-parameter binding for
STRUCT/JSON in the BigQuery Python client) and the same caveat: this
specific SQL shape (PARSE_JSON + JSON_QUERY_ARRAY reconstructing a STRUCT
array) has not itself been exercised by the Phase 6B live spike, only
execute_transaction()'s core mechanism has. Recorded as a follow-up live
check.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from shared.audit import validate_no_secrets
from shared.config import DEFAULT_DATASET, PlatformConfig, assert_sql_targets_dataset
from shared.data_service import BigQueryClientLike, run_query
from shared.persistence_transactions import (
    BoundStatement,
    StatementTemplate,
    TransactionalClientLike,
    execute_transaction,
    register_template,
)
from shared.schema_hashing import compute_schema_hash

# Phase 6E correction: dataset-parameterized from LOUPE_DATASET (see
# shared/data_service.py's INCIDENTS_TABLE for the identical rationale) --
# these were previously hardcoded "loupe_platform.*" literals baked into
# PROMOTE_SCHEMA_BASELINE_TXN's SQL at import time.
_DATASET = os.environ.get("LOUPE_DATASET", DEFAULT_DATASET)
SCHEMA_BASELINES_TABLE = f"{_DATASET}.schema_baselines"
AUDIT_EVENTS_TABLE = f"{_DATASET}.audit_events"
WRITE_LOCKS_TABLE = f"{_DATASET}.write_locks"

def _promote_baseline_sql(
    schema_baselines_table: str, audit_events_table: str, write_locks_table: str
) -> tuple[str, str]:
    sql = f"""
    UPDATE `{write_locks_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = @promoted_by
    WHERE lock_domain = 'schema_baselines';
    ASSERT @@row_count = 1 AS 'expected exactly one write_locks row for domain schema_baselines';

    MERGE `{schema_baselines_table}` T
    USING (
      SELECT
        @dataset AS dataset,
        @table_id AS table_id,
        ARRAY(
          SELECT AS STRUCT
            JSON_VALUE(col, '$.name') AS name,
            JSON_VALUE(col, '$.field_type') AS field_type
          FROM UNNEST(JSON_QUERY_ARRAY(PARSE_JSON(@columns_json))) AS col
        ) AS columns,
        @source_snapshot_id AS source_snapshot_id,
        CURRENT_TIMESTAMP() AS promoted_at,
        @promoted_by AS promoted_by
    ) S
    ON T.dataset = S.dataset AND T.table_id = S.table_id
    WHEN MATCHED THEN UPDATE SET
      columns = S.columns,
      source_snapshot_id = S.source_snapshot_id,
      promoted_at = S.promoted_at,
      promoted_by = S.promoted_by
    WHEN NOT MATCHED THEN INSERT (dataset, table_id, columns, source_snapshot_id, promoted_at, promoted_by)
      VALUES (S.dataset, S.table_id, S.columns, S.source_snapshot_id, S.promoted_at, S.promoted_by);
    ASSERT @@row_count = 1 AS 'expected exactly one schema_baselines row upserted';

    UPDATE `{write_locks_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = @promoted_by
    WHERE lock_domain = 'audit_events';
    ASSERT @@row_count = 1 AS 'expected exactly one write_locks row for domain audit_events';

    INSERT INTO `{audit_events_table}` (event_id, timestamp, actor, event_type, subject, outcome, context)
    SELECT @event_id, @event_timestamp, @promoted_by, 'schema_baseline_promoted', @subject, 'completed', PARSE_JSON(@context_json)
    FROM UNNEST([1]) AS _seed
    WHERE NOT EXISTS (
      SELECT 1 FROM `{audit_events_table}` WHERE event_id = @event_id
    );
    ASSERT @@row_count IN (0, 1) AS 'insert-if-absent must affect at most one audit_events row';
    """
    result_sql = f"""
    SELECT dataset, table_id, source_snapshot_id, promoted_at, promoted_by
    FROM `{schema_baselines_table}` WHERE dataset = @dataset AND table_id = @table_id;
    """
    return sql, result_sql


_promote_sql_default, _promote_result_sql_default = _promote_baseline_sql(
    SCHEMA_BASELINES_TABLE, AUDIT_EVENTS_TABLE, WRITE_LOCKS_TABLE
)
PROMOTE_SCHEMA_BASELINE_TXN = StatementTemplate(
    name="PROMOTE_SCHEMA_BASELINE_TXN",
    lock_domain="schema_baselines",
    sql=_promote_sql_default,
    result_sql=_promote_result_sql_default,
)
register_template(PROMOTE_SCHEMA_BASELINE_TXN)

_CONFIG_PROMOTE_TEMPLATES: dict[str, StatementTemplate] = {}


def _promote_template_for(config: PlatformConfig) -> StatementTemplate:
    cached = _CONFIG_PROMOTE_TEMPLATES.get(config.dataset)
    if cached is not None:
        return cached
    sql, result_sql = _promote_baseline_sql(
        config.schema_baselines_table, config.audit_events_table, config.write_locks_table
    )
    assert_sql_targets_dataset(sql, config.dataset)
    assert_sql_targets_dataset(result_sql, config.dataset)
    template = StatementTemplate(
        name=f"PROMOTE_SCHEMA_BASELINE_TXN::{config.dataset}",
        lock_domain="schema_baselines",
        sql=sql,
        result_sql=result_sql,
    )
    register_template(template)
    _CONFIG_PROMOTE_TEMPLATES[config.dataset] = template
    return template


@dataclass(frozen=True)
class SchemaBaselinePromotionResult:
    dataset: str
    table_id: str
    source_snapshot_id: str
    promoted_by: str


def promote_schema_baseline(
    client: "TransactionalClientLike",
    *,
    dataset: str,
    table_id: str,
    columns: dict[str, str],
    source_snapshot_id: str,
    promoted_by: str,
    event_id: str,
    event_timestamp: str,
    config: Optional[PlatformConfig] = None,
) -> SchemaBaselinePromotionResult:
    """Atomically upsert `columns` as the new baseline for
    (dataset, table_id) and record the promotion as an audit event, both
    in one script.

    `columns` maps column_name -> BigQuery field_type, the same shape
    shared.schema_hashing.compute_schema_hash() takes -- and this
    function computes that hash to include in the recorded audit event's
    context (subject to the same secret-scan every other audit write
    applies), giving each promotion a traceable, deterministic fingerprint
    of exactly what was promoted without duplicating the column list
    itself into the audit trail.

    Unlike create_incident()/write_event_idempotent(), this is an upsert,
    not an insert-if-absent -- see module docstring for why re-promoting
    the same (dataset, table_id) is expected to overwrite, not conflict.
    `event_id` must still be unique per promotion call (caller-supplied,
    same discipline as every other event_id in this codebase) so that a
    RETRY of the exact same promotion call (ambiguous prior outcome)
    does not double-record the audit event even though the MERGE itself
    is naturally idempotent.
    """

    context = {
        "table_id": table_id,
        "dataset": dataset,
        "source_snapshot_id": source_snapshot_id,
        "schema_hash": compute_schema_hash(columns),
        "column_count": len(columns),
    }
    validate_no_secrets(context)

    columns_json = json.dumps(
        [{"name": name, "field_type": field_type} for name, field_type in sorted(columns.items())]
    )

    template = _promote_template_for(config) if config is not None else PROMOTE_SCHEMA_BASELINE_TXN
    result = execute_transaction(
        client,
        [
            BoundStatement(
                template_name=template.name,
                params={
                    "dataset": dataset,
                    "table_id": table_id,
                    "columns_json": columns_json,
                    "source_snapshot_id": source_snapshot_id,
                    "promoted_by": promoted_by,
                    "event_id": event_id,
                    "event_timestamp": event_timestamp,
                    "subject": f"schema_baseline:{dataset}.{table_id}",
                    "context_json": json.dumps(context, sort_keys=True),
                },
            )
        ],
    )

    if not result.result_rows:
        raise RuntimeError(
            f"PROMOTE_SCHEMA_BASELINE_TXN committed but no row was found "
            f"for ({dataset!r}, {table_id!r}) afterward -- this should be "
            "unreachable."
        )

    persisted = result.result_rows[0]
    return SchemaBaselinePromotionResult(
        dataset=persisted["dataset"],
        table_id=persisted["table_id"],
        source_snapshot_id=persisted["source_snapshot_id"],
        promoted_by=persisted["promoted_by"],
    )


# ---------------------------------------------------------------------------
# Read (Phase 6D): a plain shared.data_service.run_query() read, no
# transaction needed -- reading the current baseline for one table never
# needs the write-lock/ASSERT machinery above, exactly like
# shared/metric_catalog_persistence.py's get_current_definition().
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistedSchemaBaseline:
    """The persisted `schema_baselines` row for one (dataset, table_id),
    in the same {column_name: field_type} shape
    shared.schema_hashing.compute_schema_hash() and
    promote_schema_baseline()'s own `columns` argument use -- callers
    (e.g. apps/data_quality_triage/persistence.py) adapt this into
    whatever app-local baseline shape their own check functions expect.
    """

    dataset: str
    table_id: str
    columns: dict[str, str]
    source_snapshot_id: str
    promoted_at: str
    promoted_by: str


def get_schema_baseline(
    client: "BigQueryClientLike",
    *,
    dataset: str,
    table_id: str,
    config: Optional[PlatformConfig] = None,
) -> Optional[PersistedSchemaBaseline]:
    """Read the currently-promoted baseline for (dataset, table_id), or
    None if nothing has ever been promoted for this table -- a normal,
    non-error outcome (the same "not catalogued yet" distinction
    shared.metric_catalog_persistence.get_current_definition() makes),
    never an exception.

    Raises whatever the underlying run_query() raises if storage itself
    is unreachable -- callers that need the persisted-mode-safe
    "unavailable" wrapper should catch broadly around this call
    themselves, exactly as
    shared.metric_catalog_persistence.resolve_current_definition() does
    around its own get_current_definition() read.
    """

    table = config.schema_baselines_table if config is not None else SCHEMA_BASELINES_TABLE
    sql = f"""
        SELECT dataset, table_id, columns, source_snapshot_id, promoted_at, promoted_by
        FROM `{table}`
        WHERE dataset = @dataset AND table_id = @table_id
        LIMIT 1
    """
    if config is not None:
        assert_sql_targets_dataset(sql, config.dataset)
    rows = run_query(client, sql, {"dataset": dataset, "table_id": table_id})
    if not rows:
        return None
    row = rows[0]
    raw_columns = row.get("columns") or []
    columns = {entry["name"]: entry["field_type"] for entry in raw_columns}
    return PersistedSchemaBaseline(
        dataset=row["dataset"],
        table_id=row["table_id"],
        columns=columns,
        source_snapshot_id=row["source_snapshot_id"],
        promoted_at=row["promoted_at"],
        promoted_by=row["promoted_by"],
    )
