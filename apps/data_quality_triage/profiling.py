"""Table metadata profiling for Data Quality Triage.

Builds a TableProfile per table in the shared dataset by combining
shared.data_service's metadata calls (list_tables, get_table_metadata) with
deterministic candidate-column heuristics (which column looks like a
primary key, which look nullable, which look temporal). No BigQuery client
is constructed here, and no bigquery.Client method is ever called directly
-- shared.data_service is the only module allowed to do that, per
docs/architecture.md. This module only calls the functions
shared.data_service already exposes for exactly this purpose.

Ported from the original data-quality-incident-triage-agent's
src/bigquery_source.py (read-only reference; that repository is not
modified), with two behavioral changes:
  1. get_bigquery_client()/client.get_table()/client.list_tables() calls
     are replaced by shared.data_service.get_bigquery_client(),
     shared.data_service.list_tables(), and
     shared.data_service.get_table_metadata() -- this file never touches a
     bigquery.Client directly.
  2. Freshness is computed from TableMetadata.modified_at (an ISO-8601
     string) rather than a raw google.cloud.bigquery.Table.modified
     datetime, since that's the shape shared.data_service.get_table_metadata()
     returns.

The column-name heuristics themselves (_pick_primary_candidate,
_pick_nullable_candidates, _pick_temporal_candidates) are preserved as
close to the original as the codebase's conventions allow: they are
deterministic, order-sensitive, and never consult an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from shared.data_service import TableMetadataClientLike, get_table_metadata, list_tables

DEFAULT_PROJECT = "bigquery-public-data"
DEFAULT_DATASET = "thelook_ecommerce"
QUALIFIED_DATASET = f"{DEFAULT_PROJECT}.{DEFAULT_DATASET}"

# Priority-ordered candidate column names, ported from the original
# src/bigquery_source.py. The first match wins; if none of these exact
# names are present, any column ending in "_id" is used as a fallback.
_PRIMARY_CANDIDATE_PRIORITY = [
    "order_id",
    "user_id",
    "product_id",
    "id",
    "item_id",
    "inventory_item_id",
]

# Priority-ordered date/time-like column names, matched against a table's
# actual columns and capped at 3, per the original app's behavior.
_TEMPORAL_CANDIDATE_PRIORITY = [
    "created_at",
    "updated_at",
    "shipped_at",
    "delivered_at",
    "returned_at",
    "date",
    "timestamp",
]


@dataclass(frozen=True)
class TableProfile:
    """Deterministic, per-table facts used as the input to both the
    metadata-only checks (checks.py) and the ratio-query checks
    (anomaly_engine.py). Mirrors the original bigquery_source.py's
    TableProfile field-for-field, so downstream check logic ports over
    unchanged.
    """

    table_id: str
    row_count: int
    last_modified: Optional[str]
    freshness_minutes: Optional[float]
    primary_candidate: Optional[str]
    nullable_candidates: list[str]
    temporal_candidates: list[str]
    column_types: dict[str, str] = field(default_factory=dict)
    """column name -> BigQuery field_type, straight from
    shared.data_service.TableMetadata.column_types. Added for schema-drift
    detection (checks.check_schema_drift()), which needs to compare
    current column types against a prior baseline -- not used by any of
    the candidate-column heuristics above. Defaults to {} so existing
    callers that construct a TableProfile without it are unaffected."""


def _pick_primary_candidate(columns: list[str]) -> Optional[str]:
    for name in _PRIMARY_CANDIDATE_PRIORITY:
        if name in columns:
            return name
    for column in columns:
        if column.endswith("_id"):
            return column
    return None


def _pick_nullable_candidates(columns: list[str]) -> list[str]:
    """The first 3 columns that don't look like an identifier column,
    per the original app's heuristic."""

    return [column for column in columns if not column.lower().endswith("id")][:3]


def _pick_temporal_candidates(columns: list[str]) -> list[str]:
    return [name for name in _TEMPORAL_CANDIDATE_PRIORITY if name in columns][:3]


def _freshness_minutes(
    modified_at: Optional[str], *, now: Optional[datetime] = None
) -> Optional[float]:
    """Minutes elapsed since `modified_at`, or None if BigQuery reported no
    modified time at all. `now` is injectable for deterministic testing.
    """

    if modified_at is None:
        return None
    reference = now if now is not None else datetime.now(tz=timezone.utc)
    modified = datetime.fromisoformat(modified_at)
    if modified.tzinfo is None:
        modified = modified.replace(tzinfo=timezone.utc)
    delta = reference - modified
    return max(delta.total_seconds() / 60.0, 0.0)


def build_table_profile(
    client: TableMetadataClientLike,
    dataset: str,
    table_id: str,
    *,
    now: Optional[datetime] = None,
) -> TableProfile:
    """Fetch and derive one table's profile via shared.data_service."""

    metadata = get_table_metadata(client, dataset, table_id)
    return TableProfile(
        table_id=table_id,
        row_count=metadata.row_count,
        last_modified=metadata.modified_at,
        freshness_minutes=_freshness_minutes(metadata.modified_at, now=now),
        primary_candidate=_pick_primary_candidate(metadata.columns),
        nullable_candidates=_pick_nullable_candidates(metadata.columns),
        temporal_candidates=_pick_temporal_candidates(metadata.columns),
        column_types=metadata.column_types,
    )


def build_table_profiles(
    client: TableMetadataClientLike,
    dataset: str,
    *,
    now: Optional[datetime] = None,
) -> list[TableProfile]:
    """Fetch and derive profiles for every table in `dataset`, via
    shared.data_service.list_tables() + build_table_profile() above.
    """

    return [
        build_table_profile(client, dataset, table_id, now=now)
        for table_id in list_tables(client, dataset)
    ]
