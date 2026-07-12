"""Shared safety-gate validators for every Phase 6E operator script.

Mirrors tools/phase6b_spike/live_transaction_spike.py's
`_validate_project_id`/`_validate_dataset_id`/`_require_safe_target`
pattern exactly (same regexes, same refusal rules) rather than
reimplementing a looser variant -- both this package and the spike must
agree on what counts as a safe target, since both are guarded gateways
to real BigQuery writes.
"""

from __future__ import annotations

import re
from typing import Any

from shared.config import assert_sql_targets_dataset

REAL_DATASET_NAME = "loupe_platform"
REQUIRED_LOCATION = "US"

_PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{10}$")


class UnsafeTargetError(ValueError):
    """Raised and left uncaught -- every Phase 6E operator script must
    exit non-zero rather than proceed if any safety check fails."""


def validate_project_id(project: str) -> None:
    if not _PROJECT_ID_PATTERN.fullmatch(project):
        raise UnsafeTargetError(
            f"--project {project!r} does not look like a valid Google Cloud "
            "project ID (lowercase letters, digits, hyphens, 6-30 chars, "
            "starting with a letter). Refusing to interpolate it into any "
            "SQL identifier or environment variable."
        )


def validate_dataset_id(dataset: str) -> None:
    if not _DATASET_ID_PATTERN.fullmatch(dataset):
        raise UnsafeTargetError(
            f"--dataset {dataset!r} does not look like a valid BigQuery "
            "dataset ID (letters, digits, underscores only). Refusing to "
            "interpolate it into any SQL identifier or environment variable."
        )


def validate_run_id(run_id: str) -> None:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise UnsafeTargetError(
            f"run_id {run_id!r} does not match {_RUN_ID_PATTERN.pattern!r} "
            "(exactly 10 lowercase hex characters, the same format "
            "uuid.uuid4().hex[:10] always produces). Refusing to query, "
            "tag, or clean up anything with a malformed run_id."
        )


def require_safe_test_dataset(project: str, dataset: str, location: str) -> None:
    """The one safety gate every Phase 6E script that touches real
    BigQuery must call FIRST, before setting any environment variable or
    importing any shared/*_persistence.py module (whose table-name
    constants are resolved from LOUPE_DATASET at import time -- see
    shared/data_service.py's INCIDENTS_TABLE docstring).

    Refuses outright (never a warning, never a --force override) unless
    ALL of:
      - --project matches the standard GCP project-ID identifier pattern
      - --dataset matches the standard BigQuery dataset-ID identifier
        pattern
      - --location is exactly "US" (bigquery-public-data.thelook_ecommerce
        is hosted in the US multi-region; a mismatched dataset location
        cannot be joined against it in one query)
      - --dataset is NOT "loupe_platform" (the real production dataset)
      - --dataset contains the substring "test" (case-insensitive)
    """

    validate_project_id(project)
    validate_dataset_id(dataset)
    if location != REQUIRED_LOCATION:
        raise UnsafeTargetError(
            f"--location must be {REQUIRED_LOCATION!r} (got {location!r}). "
            "bigquery-public-data.thelook_ecommerce is hosted in US; "
            "these operator scripts are not scoped to verify any other "
            "location."
        )
    if dataset == REAL_DATASET_NAME:
        raise UnsafeTargetError(
            f"Refusing to run: --dataset must never be {REAL_DATASET_NAME!r} "
            "(the real production dataset name). Phase 6E's guarded "
            "operator scripts only ever target an isolated test dataset."
        )
    if "test" not in dataset.lower():
        raise UnsafeTargetError(
            f"Refusing to run: --dataset {dataset!r} does not contain "
            "'test'. Phase 6E's guarded operator scripts only ever "
            "operate against a dataset name that is unambiguously a test "
            "target."
        )


def generate_run_id() -> str:
    import uuid

    run_id = uuid.uuid4().hex[:10]
    validate_run_id(run_id)  # self-check
    return run_id


# ---------------------------------------------------------------------------
# DatasetTargetGuard: Phase 6E correction 2's generic, module-agnostic
# backstop against "no write target may silently fall back to
# loupe_platform."
# ---------------------------------------------------------------------------
#
# shared/incident_persistence.py's create_incident()/record_incident_transition()
# accept an explicit `config` argument that makes THEM authoritative
# regardless of import order (see that module's docstring). Every other
# shared/*_persistence.py module these two operator scripts touch
# (shared.audit_persistence, shared.metric_catalog_persistence,
# shared.schema_baseline_persistence, shared.schema_management,
# shared.data_service) still resolves its table-name constants once, at
# that module's first import, from LOUPE_DATASET -- correct for a normal
# deployed process, but not provably authoritative for an operator CLI if
# some future change accidentally imported one of them before --dataset
# was parsed.
#
# DatasetTargetGuard closes that gap generically, for every module, without
# requiring each one to grow its own config-plumbing: it wraps the real
# BigQuery client these scripts construct, and checks every SQL string
# actually about to be sent to BigQuery (via .query()) against the
# operator-validated target dataset before forwarding it. If ANY generated
# DML/SELECT references a different dataset -- most dangerously, the real
# `loupe_platform` -- this raises rather than silently executing it. Both
# tools/phase6e_ops/bootstrap_test_dataset.py and
# tools/phase6e_ops/live_integration_validation.py wrap their client with
# this immediately after construction, before it is ever passed to any
# persistence function.


class DatasetTargetGuard:
    """Wraps any BigQueryClientLike/TransactionalClientLike so every SQL
    string passed to `.query()` is checked against `allowed_dataset`
    (via shared.config.assert_sql_targets_dataset()) before being
    forwarded to the wrapped client. Every other attribute/method
    (get_dataset, get_table, list_tables, insert_rows_json, ...) is
    delegated to the wrapped client unguarded -- those are metadata reads
    or the streaming audit path this platform's governed writes never
    use, not a route by which a script-based DML write could reach the
    wrong dataset silently.
    """

    def __init__(self, client: Any, *, allowed_dataset: str) -> None:
        self._client = client
        self._allowed_dataset = allowed_dataset

    def query(self, sql: str, job_config: Any = None) -> Any:
        assert_sql_targets_dataset(sql, self._allowed_dataset)
        return self._client.query(sql, job_config=job_config)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
