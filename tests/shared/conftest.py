"""Shared pytest fixtures for shared/ module tests.

FakeBigQueryClient is an in-memory stand-in for google.cloud.bigquery.Client
that satisfies both BigQueryClientLike (query().result()) and
InsertableClient (insert_rows_json()), so shared/data_service.py and
shared/audit.py can be unit tested without real cloud credentials or
network access, per docs/development.md: "Use fixtures for BigQuery
responses so unit tests do not require live cloud access."
"""

from __future__ import annotations

import pytest


class _FakeQueryJob:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.received_timeout: float | None = None

    def result(self, timeout: float | None = None):
        self.received_timeout = timeout
        return list(self._rows)


class _FakeTableRef:
    def __init__(self, table_id: str):
        self.table_id = table_id


class _FakeField:
    def __init__(self, name: str, field_type: str = "STRING"):
        self.name = name
        self.field_type = field_type


class FakeTable:
    """Stand-in for a google.cloud.bigquery.Table -- just enough surface
    for get_table_metadata() to read (num_rows, modified, schema).

    column_types is an optional {column_name: BigQuery field_type} map;
    any column not listed there defaults to "STRING", so existing callers
    that only pass `columns` (no types) are unaffected.
    """

    def __init__(
        self,
        table_id: str,
        num_rows: int = 0,
        modified=None,
        columns: "list[str] | None" = None,
        column_types: "dict[str, str] | None" = None,
    ):
        self.table_id = table_id
        self.num_rows = num_rows
        self.modified = modified
        types = column_types or {}
        self.schema = [_FakeField(name, types.get(name, "STRING")) for name in (columns or [])]


class FakeBigQueryClient:
    def __init__(self) -> None:
        self.queries: list[tuple[str, object]] = []
        self.inserted_rows: list[tuple[str, list[dict]]] = []
        self.next_rows: list[dict] = []
        self.insert_errors: list = []
        self.last_job: "_FakeQueryJob | None" = None
        self.query_exception: Exception | None = None
        self.table_ids_by_dataset: dict[str, list[str]] = {}
        self.tables: dict[str, FakeTable] = {}  # keyed by "dataset.table_id"
        self.datasets: dict[str, "FakeDataset"] = {}  # keyed by "project.dataset"
        self.get_dataset_exception: Exception | None = None

        # --- Transaction-simulation support (Phase 6A, amendment 1/3) ---
        # query_exception_queue is consulted BEFORE the single
        # `query_exception` attribute above, and is popped (front-first)
        # once per .query() call -- this lets a test simulate "the first
        # N attempts raise a retryable error, then the (N+1)th succeeds,"
        # which is exactly the shape execute_transaction()'s retry loop
        # needs to be exercised against. Existing tests that only ever
        # set the single `query_exception` attribute are unaffected: this
        # queue defaults to empty, in which case behavior falls back to
        # the original single-exception check unchanged.
        self.query_exception_queue: list[Exception | None] = []

    def query(self, sql: str, job_config=None):
        if self.query_exception_queue:
            next_exc = self.query_exception_queue.pop(0)
            if next_exc is not None:
                raise next_exc
        elif self.query_exception is not None:
            raise self.query_exception
        self.queries.append((sql, job_config))
        self.last_job = _FakeQueryJob(self.next_rows)
        return self.last_job

    def insert_rows_json(self, table: str, json_rows: list[dict]):
        self.inserted_rows.append((table, json_rows))
        return self.insert_errors

    def list_tables(self, dataset: str):
        return [_FakeTableRef(table_id) for table_id in self.table_ids_by_dataset.get(dataset, [])]

    def get_table(self, table_ref: str):
        return self.tables[table_ref]

    def get_dataset(self, dataset_ref: str):
        if self.get_dataset_exception is not None:
            raise self.get_dataset_exception
        return self.datasets[dataset_ref]


class FakeDataset:
    """Stand-in for a google.cloud.bigquery.Dataset -- just enough
    surface for shared.config.validate_persistence_config() to read
    (.location)."""

    def __init__(self, location: str = "US"):
        self.location = location


@pytest.fixture
def fake_client() -> FakeBigQueryClient:
    return FakeBigQueryClient()


class SequencedFakeBigQueryClient(FakeBigQueryClient):
    """A FakeBigQueryClient whose `.next_rows` is set from a caller-supplied
    queue, one entry per `.query()` call, in order -- for tests (Phase 6D)
    that need to exercise a sequence of DIFFERENT persistence calls in one
    scenario (e.g. create_incident() followed by write_event_idempotent(),
    or derive_source_health() called once per approved source table),
    where the base FakeBigQueryClient's single shared `next_rows` list
    would return the same (wrongly-shaped) rows to every call.

    Once the queue is exhausted, `.query()` falls back to whatever
    `.next_rows` was last set to (base class behavior), so a test can still
    pre-seed a final steady-state value if it doesn't want to queue every
    single call explicitly.
    """

    def __init__(self, rows_per_call: "list[list[dict]]") -> None:
        super().__init__()
        self._rows_queue: list[list[dict]] = list(rows_per_call)

    def query(self, sql: str, job_config=None):
        if self._rows_queue:
            self.next_rows = self._rows_queue.pop(0)
        return super().query(sql, job_config=job_config)
