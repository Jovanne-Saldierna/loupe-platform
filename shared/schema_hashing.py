"""Deterministic schema-hash computation for schema_snapshots rows.

Per Phase 6's approved amendment 9, a new schema snapshot must only be
persisted when the schema actually changed (or no prior snapshot exists,
or an explicit cadence threshold requires a fresh observation) -- not on
every Streamlit rerun or profiling request. This module provides the
deterministic hash that decision depends on; the decision itself (read
the most recent snapshot, compare hashes, decide whether to insert) is
Phase 6B/6D work, once real persisted reads exist to compare against.
Kept separate and dependency-free (no BigQuery, no Streamlit) for the
same reason as shared/metric_hashing.py: it needs to be trivially
unit-testable and reusable by whichever module ends up writing snapshots.
"""

from __future__ import annotations

import hashlib
import json


def compute_schema_hash(columns: dict[str, str]) -> str:
    """Deterministically hash a {column_name: bigquery_field_type} map.

    Sorted by column name before hashing so that a schema observed in a
    different column order (which BigQuery's metadata API does not
    guarantee is stable) still hashes identically when the actual set of
    columns and types is unchanged.
    """

    canonical = json.dumps(dict(sorted(columns.items())), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
