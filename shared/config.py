"""Typed, framework-independent persistence configuration.

Per Phase 6's approved amendment 8: this module (and every other module
under shared/) must remain usable with no Streamlit (or any other UI
framework) installed or imported. It exposes pure functions only --
`validate_persistence_config()` returns a plain, hashable/cacheable
result object; if an app wants to cache that result across Streamlit
reruns, the app's own apps/*/main.py (or a small apps/*/config_cache.py)
wraps this function with `st.cache_resource`, never the other way around.
Nothing here imports streamlit, and nothing here should ever need to.

Per amendment 6, LOUPE_BQ_LOCATION defaults to "US" and is validated as a
real dataset-location compatibility requirement (bigquery-public-data.
thelook_ecommerce is hosted in the US multi-region; a loupe_platform
dataset in any other location could not be joined against it in a single
query without an explicit, costly cross-region copy) -- not a
cost-optimization preference.

Per amendment 14, nothing in this module creates, migrates, seeds,
truncates, or deletes anything. `validate_persistence_config()` and
`validate_schema_version()` are read-only checks; bootstrap/migrate/seed
live only in shared/schema_management.py's explicit CLI entry point.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol


DEFAULT_DATASET = "loupe_platform"
DEFAULT_LOCATION = "US"

# Phase 6D: which backing store an app's build_state() reads from.
# "constants" (the default) means the app reads shared/metric_catalog.py's
# in-memory registry and never touches loupe_platform -- this is the
# explicit pre-cutover/demo configuration, per amendment 14, never an
# automatic fallback an app silently chooses for itself when persistence
# happens to be unreachable (that case is reported as an honest
# "unavailable" state instead -- see shared/persistence_bootstrap.py).
# "persisted" means the app reads/writes through the shared/*_persistence.py
# modules against real loupe_platform tables.
PersistenceMode = Literal["persisted", "constants"]
_PERSISTENCE_MODE_VALUES = {"persisted", "constants"}
DEFAULT_PERSISTENCE_MODE: PersistenceMode = "constants"

# Table identifiers, all namespaced under the single loupe_platform
# dataset (or its configured override). Centralized here so that no
# module anywhere else in the codebase spells out a bare table-name
# string literal -- every persistence module imports these constants.
_TABLE_SUFFIXES = {
    "metric_catalog": "metric_catalog",
    "metric_versions": "metric_versions",
    "incidents": "incidents",
    "incident_transitions": "incident_transitions",
    "audit_events": "audit_events",
    "schema_snapshots": "schema_snapshots",
    "schema_baselines": "schema_baselines",
    "schema_migrations": "schema_migrations",
    "write_locks": "write_locks",
}


@dataclass(frozen=True)
class PlatformConfig:
    """All environment/deployment-derived identifiers persistence code
    needs. Frozen and hashable so it can be used as a cache key by a
    caller that wants to cache validation results (see module docstring).
    """

    project: str
    dataset: str = DEFAULT_DATASET
    location: str = DEFAULT_LOCATION
    strict_separation_of_duties: bool = False
    """Phase 6D governance policy setting: whether
    shared.metric_catalog_persistence.certify_metric_definition() must
    refuse a certification where reviewer == created_by. Defaults to
    False for this portfolio deployment (see that module's docstring,
    "Phase 6D policy correction") -- created_by and reviewer are always
    recorded as distinct fields regardless of this setting; this flag
    only controls whether the SAME identity is allowed to occupy both
    roles on one certification."""

    @property
    def metric_catalog_table(self) -> str:
        return f"{self.dataset}.{_TABLE_SUFFIXES['metric_catalog']}"

    @property
    def metric_versions_table(self) -> str:
        return f"{self.dataset}.{_TABLE_SUFFIXES['metric_versions']}"

    @property
    def incidents_table(self) -> str:
        return f"{self.dataset}.{_TABLE_SUFFIXES['incidents']}"

    @property
    def incident_transitions_table(self) -> str:
        return f"{self.dataset}.{_TABLE_SUFFIXES['incident_transitions']}"

    @property
    def audit_events_table(self) -> str:
        return f"{self.dataset}.{_TABLE_SUFFIXES['audit_events']}"

    @property
    def schema_snapshots_table(self) -> str:
        return f"{self.dataset}.{_TABLE_SUFFIXES['schema_snapshots']}"

    @property
    def schema_baselines_table(self) -> str:
        return f"{self.dataset}.{_TABLE_SUFFIXES['schema_baselines']}"

    @property
    def schema_migrations_table(self) -> str:
        return f"{self.dataset}.{_TABLE_SUFFIXES['schema_migrations']}"

    @property
    def write_locks_table(self) -> str:
        return f"{self.dataset}.{_TABLE_SUFFIXES['write_locks']}"

    def all_tables(self) -> dict[str, str]:
        """Return {logical_name: fully_qualified_table_id} for every
        persistence table this config knows about -- used by
        schema_management.py's bootstrap and by schema-contract tests so
        there is exactly one place enumerating "the nine tables"."""

        return {name: f"{self.dataset}.{suffix}" for name, suffix in _TABLE_SUFFIXES.items()}


class ConfigError(ValueError):
    """Raised when required configuration is missing or malformed. Never
    includes credential material -- only identifiers (project/dataset
    names) and the name of the missing/invalid environment variable."""


def load_platform_config(env: Optional[dict[str, str]] = None) -> PlatformConfig:
    """Build a PlatformConfig from environment variables.

    `env` defaults to os.environ but accepts an explicit mapping so this
    function is trivially unit-testable without mutating process-global
    state (tests pass a plain dict).

    Required: GOOGLE_CLOUD_PROJECT (or LOUPE_BQ_PROJECT as an explicit
    override, checked first so a deployment can target a persistence
    project distinct from wherever query jobs happen to run).
    Optional: LOUPE_DATASET (default "loupe_platform"), LOUPE_BQ_LOCATION
    (default "US").
    """

    source = env if env is not None else os.environ
    project = source.get("LOUPE_BQ_PROJECT") or source.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise ConfigError(
            "No BigQuery project configured. Set LOUPE_BQ_PROJECT or "
            "GOOGLE_CLOUD_PROJECT."
        )

    dataset = source.get("LOUPE_DATASET", DEFAULT_DATASET)
    if not dataset:
        raise ConfigError("LOUPE_DATASET must not be empty if set.")

    location = source.get("LOUPE_BQ_LOCATION", DEFAULT_LOCATION)
    if not location:
        raise ConfigError("LOUPE_BQ_LOCATION must not be empty if set.")

    strict_separation_raw = source.get("LOUPE_STRICT_SEPARATION_OF_DUTIES", "false").strip().lower()
    strict_separation_of_duties = strict_separation_raw in ("1", "true", "yes")

    return PlatformConfig(
        project=project,
        dataset=dataset,
        location=location,
        strict_separation_of_duties=strict_separation_of_duties,
    )


def load_persistence_mode(env: Optional[dict[str, str]] = None) -> PersistenceMode:
    """Read which backing store an app should use, from LOUPE_PERSISTENCE_MODE.

    Defaults to "constants" (DEFAULT_PERSISTENCE_MODE) -- an app must be
    EXPLICITLY configured into "persisted" mode; it is never chosen
    automatically as an error-recovery fallback. An unrecognized value
    raises ConfigError immediately rather than silently defaulting, so a
    typo'd environment variable is never treated as "constants" by
    accident.
    """

    source = env if env is not None else os.environ
    raw = source.get("LOUPE_PERSISTENCE_MODE", DEFAULT_PERSISTENCE_MODE).strip().lower()
    if raw not in _PERSISTENCE_MODE_VALUES:
        raise ConfigError(
            f"LOUPE_PERSISTENCE_MODE={raw!r} is not one of "
            f"{sorted(_PERSISTENCE_MODE_VALUES)}."
        )
    return raw  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Read-only startup validation (never creates/migrates/seeds anything)
# ---------------------------------------------------------------------------


class DatasetMetadataClientLike(Protocol):
    """Structural type for the minimal client surface
    validate_persistence_config() needs: reading a dataset's location.
    Satisfied by google.cloud.bigquery.Client and by a test fake."""

    def get_dataset(self, dataset_ref: str) -> Any: ...


@dataclass(frozen=True)
class ConfigValidationResult:
    """The outcome of validate_persistence_config() -- a plain, cacheable
    value object, never raised as control flow for the "expected to
    sometimes fail" case (an unreachable/misconfigured dataset is a normal
    startup condition this platform must render honestly, not a bug to
    propagate as an uncaught exception up through Streamlit)."""

    ok: bool
    project: str
    dataset: str
    expected_location: str
    actual_location: Optional[str] = None
    safe_error: Optional[str] = None
    """A message safe to display to an end user or write to a log --
    never a raw exception string, which could embed internal identifiers,
    stack frames, or (in principle) request metadata. See
    shared/audit.py's parallel "never leak raw exception text" rule."""


def validate_persistence_config(
    client: "DatasetMetadataClientLike", config: PlatformConfig
) -> ConfigValidationResult:
    """Read-only check that `config.dataset` exists and is in
    `config.location` ("US" by default, per amendment 6's dataset-location
    compatibility requirement against bigquery-public-data.
    thelook_ecommerce, which is hosted in the US multi-region).

    This performs exactly one metadata call (get_dataset) -- no query, no
    DDL, no write of any kind. It never creates the dataset if missing:
    a missing dataset is reported as a validation failure with a safe
    message, per amendment 14 ("application startup may validate schema
    version and availability but must never create ... persistence
    tables").
    """

    try:
        dataset_ref = client.get_dataset(f"{config.project}.{config.dataset}")
    except Exception:
        # Deliberately generic: the real exception (permissions error,
        # not-found, network failure) is never included in the returned
        # message, only its occurrence -- matching shared/audit.py's
        # "never leak raw exception text" discipline (Phase 6 amendment 9).
        return ConfigValidationResult(
            ok=False,
            project=config.project,
            dataset=config.dataset,
            expected_location=config.location,
            safe_error=(
                f"Could not read metadata for dataset {config.dataset!r} in "
                f"project {config.project!r}. It may not exist yet, or the "
                "current identity may lack permission to read it."
            ),
        )

    actual_location = getattr(dataset_ref, "location", None)
    if actual_location != config.location:
        return ConfigValidationResult(
            ok=False,
            project=config.project,
            dataset=config.dataset,
            expected_location=config.location,
            actual_location=actual_location,
            safe_error=(
                f"Dataset {config.dataset!r} is in location "
                f"{actual_location!r}, but {config.location!r} is required "
                "for compatibility with bigquery-public-data."
                "thelook_ecommerce (hosted in US)."
            ),
        )

    return ConfigValidationResult(
        ok=True,
        project=config.project,
        dataset=config.dataset,
        expected_location=config.location,
        actual_location=actual_location,
    )


# ---------------------------------------------------------------------------
# Query-target assertion (Phase 6E correction 2: "no write target may
# silently fall back to loupe_platform")
# ---------------------------------------------------------------------------
#
# Every shared/*_persistence.py module resolves its table-name constants
# from LOUPE_DATASET once, at that module's first import in a process (see
# shared/incident_persistence.py's module docstring for the full
# rationale). That is correct and sufficient for a normal deployed
# process, where the environment is fully configured before anything is
# imported. It is NOT sufficient for an operator CLI that selects its
# target dataset from a --dataset argument: if any shared/*_persistence.py
# module were ever imported before that argument is parsed (a real risk
# for any future script that imports one of these modules indirectly,
# e.g. via a shared test helper, before constructing its own
# PlatformConfig), the frozen constant would silently keep pointing at
# whatever LOUPE_DATASET happened to be at that earlier import -- which
# could be the production default. assert_sql_targets_dataset() is the
# structural backstop for exactly that scenario: it inspects the SQL text
# actually about to be sent to BigQuery and refuses to let it proceed if
# it references any dataset other than the one the operator explicitly
# validated, regardless of how any module's constants were resolved.


class UnexpectedDatasetTargetError(RuntimeError):
    """Raised by assert_sql_targets_dataset() (and by anything built on
    top of it, e.g. tools/phase6e_ops/safety.py's DatasetTargetGuard) when
    generated SQL references a dataset other than the one exactly
    validated for this call. Never a warning, never silently corrected --
    the caller must fix the actual configuration/import-order bug that
    produced the mismatch."""


_QUALIFIED_TABLE_PATTERN = re.compile(r"`([A-Za-z0-9_-]+)\.[A-Za-z0-9_]+`")


def assert_sql_targets_dataset(sql: str, expected_dataset: str) -> None:
    """Scan `sql` for every backtick-quoted `dataset.table` identifier and
    raise UnexpectedDatasetTargetError if any of them names a dataset
    other than `expected_dataset`.

    This is a text-level check, not a SQL parse -- deliberately so: it
    must work uniformly across both shared.data_service.run_query()'s
    plain SELECT statements and shared.persistence_transactions.
    execute_transaction()'s multi-statement scripts, and it must never
    need to understand a statement's semantics to do its job (it is a
    target-identity check, not a query-safety check -- that is
    shared.data_service.UnsafeQueryError's job, and this function is not a
    substitute for it).

    A SQL string with no backtick-quoted identifiers at all (e.g. a
    trivial `SELECT 1`) passes trivially -- there is nothing to check.
    This function only ever rejects a PRESENT, WRONG dataset reference; it
    never requires a specific dataset to be mentioned.
    """

    referenced = {match.group(1) for match in _QUALIFIED_TABLE_PATTERN.finditer(sql)}
    unexpected = sorted(referenced - {expected_dataset})
    if unexpected:
        raise UnexpectedDatasetTargetError(
            f"Generated SQL references dataset(s) {unexpected!r}, but the "
            f"only validated target for this call is {expected_dataset!r}. "
            "Refusing to execute -- this SQL text is never sent to "
            "BigQuery. This usually means a persistence module's "
            "table-name constant was resolved before the operator's "
            "--dataset argument was parsed; construct an explicit "
            "PlatformConfig and pass it into the persistence call rather "
            "than relying on module-import-time resolution."
        )
