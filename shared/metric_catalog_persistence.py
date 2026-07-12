"""Persisted metric-catalog reads, explicit one-time seeding, and governed
metric certification.

Phase 6C business persistence, built on the same spike-verified
`shared.persistence_transactions.execute_transaction()` mechanism Phase 6B's
`shared/incident_persistence.py`, `shared/audit_persistence.py`, and
`shared/schema_baseline_persistence.py` already use (commit/rollback,
`ASSERT @@row_count`, `result_sql`-after-`COMMIT`, and the `write_locks`
lock-row contention pattern).

--- Three responsibilities, kept separate on purpose ---

1. Persisted reads (`get_current_definition`, `get_version_history`,
   `resolve_current_definition`): resolve `metric_catalog`'s pointer row
   for a metric to its currently-active, immutable `metric_versions` row,
   and return the flat `MetricDefinition` shape every existing caller
   (Loupe, Triage, Governance's UI) already consumes. Pure reads --
   `shared.data_service.run_query()`'s read-only surface, no transaction
   needed. `resolve_current_definition()` is the persisted-mode-safe
   wrapper: if the underlying read fails, it reports `ok=False` with a
   safe error rather than silently falling back to anything else (e.g.
   the in-memory `shared.metric_catalog` registry) -- a caller that has
   opted into persisted mode must render an honest "unavailable" state,
   not a value it never actually reads unless it explicitly still wants
   the in-memory registry as a distinct fallback strategy of its own.

2. Explicit one-time seeding (`seed_metric_definition`,
   `seed_current_catalog`): populates `metric_catalog` +
   `metric_versions` with the five `pending_validation` definitions
   already in `shared/metric_catalog.py`'s in-memory registry. This is an
   administrative bootstrap step ONLY -- never called from any app's
   `main.py` / `build_state()` (see `tests/test_persistence_boundary.py`'s
   companion assertions in this phase), and only ever invoked for real via
   this module's own `python -m shared.metric_catalog_persistence seed
   ...` CLI, which requires `--yes` and is never run against production
   BigQuery in this phase (see docs/PHASE_6B_HANDOFF.md's Phase 6C
   section). Idempotent: seeding the exact same content twice is a
   successful no-op; seeding genuinely different content under a name/
   version that already exists raises `PayloadConflictError`.

3. Governed certification (`certify_metric_definition`): the one path
   that creates a brand-new, immutable `MetricVersion`, advances
   `metric_catalog`'s pointer to it, and records the certification as an
   audit event, all as ONE atomic script. Requires reviewer, validation
   evidence, a review timestamp, and a change reason up front -- refused
   before any BigQuery call if any is missing. No real metric is ever
   certified by this module's own tests.

   --- Phase 6D policy correction: created_by/reviewer separation of duties ---
   `created_by` (who authored a version's content) and `reviewer` (who
   certified it) are always kept as distinct, separately-recorded fields
   in `MetricVersion` and in every persisted version/audit row -- that
   part is structural and unconditional, never optional. Whether the SAME
   identity is allowed to occupy both roles on one certification is a
   separate, configurable governance policy: `require_separation_of_duties`
   (default `False`, matching this portfolio deployment's actual review
   staffing) controls whether `certify_metric_definition()` refuses a
   certification where `reviewer == created_by`. When `False` (the
   default), a single reviewer may certify their own authored content --
   both identities are still recorded honestly as whatever they actually
   were, never silently merged or overwritten. When a deployment sets
   `require_separation_of_duties=True` (e.g. a larger team with distinct
   author/reviewer staffing), the same-identity case is refused before any
   BigQuery call, exactly as the unconditional check used to behave.
   Callers thread this flag through explicitly (see
   `shared.config.PlatformConfig.strict_separation_of_duties`) -- this
   module never reads global/environment state on its own.

--- Audit ownership (per Phase 6C's explicit requirement) ---

`certify_metric_definition()`'s audit event is written by composing
`shared.audit_persistence.WRITE_AUDIT_EVENT_TXN` -- the SAME registered
template `shared/audit_persistence.py` already owns -- as a second
`BoundStatement` in the same `execute_transaction()` call that also runs
this module's own `CERTIFY_METRIC_VERSION_TXN`. This is deliberately
different from `shared/schema_baseline_persistence.py`'s approach (which
re-embeds an equivalent audit-insert SQL fragment inline in its own
template): reusing `audit_persistence.py`'s template here means the audit-
event SQL, its `audit_events` lock-domain discipline, and its
insert-if-absent-by-`event_id` guard are all defined in exactly one
place, not re-derived a third time. (Refactoring
`schema_baseline_persistence.py` to do the same is a reasonable future
cleanup, but is out of this phase's scope -- it works correctly as-is and
touching it isn't necessary to demonstrate the triage -> governance ->
Loupe workflow this phase targets.)

`shared.audit.write_event()` (the streaming `insert_rows_json()` path) is
never used for a governed state transition anywhere in this codebase --
not here, not in `shared/incident_persistence.py`'s status transitions,
not in `shared/schema_baseline_persistence.py`'s promotions. It remains
the right tool ONLY for ordinary, single-shot audit writes that are not
required to commit atomically with another write (e.g. a simple
informational log entry an app records on its own, with no accompanying
state change that must succeed-or-fail together with it). Concretely,
"prevent application code from accidentally choosing the weaker path"
means: this module's `certify_metric_definition()` never imports or calls
`shared.audit.write_event()` at all (see
`tests/shared/test_metric_catalog_persistence.py`'s
`test_module_never_imports_the_streaming_write_event_path` for the static
check), so there is no code path through this module that could
accidentally use it instead of the atomic template composition above.

--- Not done in this phase (explicitly out of scope) ---

Application (`apps/*/main.py`) wiring to call any of this module's
functions, any real (non-test) `loupe_platform` seeding or certification,
Phase 6D, UI implementation, and any further concurrency research beyond
what Phase 6B's live spike already confirmed for
`execute_transaction()`'s core mechanism (this module registers new
templates but introduces no new correctness mechanism of its own).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from shared.audit import validate_no_secrets
from shared.audit_persistence import WRITE_AUDIT_EVENT_TXN
from shared.config import DEFAULT_DATASET
from shared.data_service import BigQueryClientLike, run_query
from shared.metric_hashing import compute_content_hash
from shared.models import MetricDefinition, MetricVersion
from shared.persistence_transactions import (
    BoundStatement,
    PayloadConflictError,
    StatementTemplate,
    TransactionalClientLike,
    execute_transaction,
    register_template,
)

# Phase 6E correction: dataset-parameterized from LOUPE_DATASET (see
# shared/data_service.py's INCIDENTS_TABLE for the identical rationale) --
# these were previously hardcoded "loupe_platform.*" literals baked into
# SEED_METRIC_DEFINITION_TXN/CERTIFY_METRIC_VERSION_TXN's SQL at import
# time, which meant the seeding CLI at the bottom of this module could
# never actually target an isolated test dataset regardless of what
# --project was passed -- a real gap now closed the same way every other
# persistence module in this phase was.
_DATASET = os.environ.get("LOUPE_DATASET", DEFAULT_DATASET)
METRIC_CATALOG_TABLE = f"{_DATASET}.metric_catalog"
METRIC_VERSIONS_TABLE = f"{_DATASET}.metric_versions"
WRITE_LOCKS_TABLE = f"{_DATASET}.write_locks"


# ---------------------------------------------------------------------------
# 1. Persisted reads
# ---------------------------------------------------------------------------


def _row_to_definition(row: dict) -> MetricDefinition:
    return MetricDefinition(
        name=row["name"],
        owner=row["owner"],
        description=row["description"],
        formula=row["formula"],
        measurement_grain=row["measurement_grain"],
        freshness_expectation=row["freshness_expectation"],
        # Per amendment 6's separation of concerns: metric_catalog's own
        # certification_status is the authoritative "current state of the
        # metric as a whole" (MetricCatalogPointer's documented
        # responsibility), so it -- not the resolved version row's own
        # certification_status field -- is what MetricDefinition reports.
        # In practice the two are kept in sync by certify_metric_definition()
        # (both set to 'certified' together), but the pointer is the one
        # source of truth this function reads from, so a pending_validation
        # metric is reported as pending_validation honestly, never silently
        # upgraded or downgraded by which column happened to be selected.
        certification_status=row["certification_status"],
        approved_source_tables=list(row.get("approved_source_tables") or []),
        required_filters=list(row.get("required_filters") or []),
        downstream_dashboards=list(row.get("downstream_dashboards") or []),
        version=row["version"],
        last_reviewed_at=row.get("last_reviewed_at"),
    )


def get_current_definition(client: "BigQueryClientLike", name: str) -> Optional[MetricDefinition]:
    """Resolve `metric_catalog`'s pointer row for `name` to its currently-
    active `metric_versions` row, and return it as a `MetricDefinition`.

    Returns None if no `metric_catalog` row exists for `name` yet (never
    caught elsewhere as an error -- "not catalogued yet" is a normal
    outcome, distinct from "storage is unavailable," which is what
    `resolve_current_definition()` below exists to distinguish).
    """

    sql = f"""
        SELECT
          c.name AS name,
          c.owner AS owner,
          c.certification_status AS certification_status,
          c.last_reviewed_at AS last_reviewed_at,
          v.version AS version,
          v.description AS description,
          v.formula AS formula,
          v.measurement_grain AS measurement_grain,
          v.freshness_expectation AS freshness_expectation,
          v.approved_source_tables AS approved_source_tables,
          v.required_filters AS required_filters,
          v.downstream_dashboards AS downstream_dashboards
        FROM `{METRIC_CATALOG_TABLE}` c
        JOIN `{METRIC_VERSIONS_TABLE}` v
          ON v.name = c.name AND v.version = c.current_version
        WHERE c.name = @name
        LIMIT 1
    """
    rows = run_query(client, sql, {"name": name})
    if not rows:
        return None
    return _row_to_definition(rows[0])


def _row_to_version(row: dict) -> MetricVersion:
    return MetricVersion(
        name=row["name"],
        version=row["version"],
        description=row["description"],
        formula=row["formula"],
        measurement_grain=row["measurement_grain"],
        freshness_expectation=row["freshness_expectation"],
        certification_status=row["certification_status"],
        approved_source_tables=list(row.get("approved_source_tables") or []),
        content_hash=row["content_hash"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        change_reason=row["change_reason"],
        required_filters=list(row.get("required_filters") or []),
        downstream_dashboards=list(row.get("downstream_dashboards") or []),
        prior_version=row.get("prior_version"),
        validation_evidence=row.get("validation_evidence"),
        review_notes=row.get("review_notes"),
        reviewer=row.get("reviewer"),
        reviewed_at=row.get("reviewed_at"),
    )


def get_version_history(client: "BigQueryClientLike", name: str) -> list[MetricVersion]:
    """Return every persisted, immutable `MetricVersion` row for `name`,
    newest first -- the append-only history a governance UI would show.
    Returns an empty list (not an error) if nothing is persisted yet.
    """

    sql = f"""
        SELECT * FROM `{METRIC_VERSIONS_TABLE}`
        WHERE name = @name
        ORDER BY created_at DESC
    """
    rows = run_query(client, sql, {"name": name})
    return [_row_to_version(row) for row in rows]


@dataclass(frozen=True)
class MetricDefinitionResolution:
    """The outcome of `resolve_current_definition()` -- a plain,
    non-raising value object, matching `shared.config.
    ConfigValidationResult`'s pattern for "this is a normal, renderable
    outcome, not an exception to propagate."

    `ok=False` means the persisted read itself failed (storage
    unavailable, permissions, network, etc.) -- render this honestly as
    "certification status unavailable," never silently substitute a
    default or fall back to a different source without that being a
    caller's own, separate, explicit decision. `ok=True` with
    `definition=None` means the read succeeded and simply found no
    catalogued row for this name yet -- a different, non-error outcome
    from `ok=False`.
    """

    ok: bool
    definition: Optional[MetricDefinition] = None
    safe_error: Optional[str] = None


def resolve_current_definition(client: "BigQueryClientLike", name: str) -> MetricDefinitionResolution:
    """The persisted-mode-safe wrapper around `get_current_definition()`.

    Never lets a raw exception (which could embed internal identifiers)
    escape to a caller -- catches broadly and reports `ok=False` with a
    generic, safe message, matching `shared/audit.py` and `shared/
    config.py`'s "never leak raw exception text" discipline.
    """

    try:
        definition = get_current_definition(client, name)
    except Exception:
        return MetricDefinitionResolution(
            ok=False,
            safe_error=(
                f"Could not read the persisted metric catalog for "
                f"{name!r}. Treat this metric's certification state as "
                "unavailable rather than assuming a default."
            ),
        )
    return MetricDefinitionResolution(ok=True, definition=definition)


# ---------------------------------------------------------------------------
# 2. Explicit one-time seeding
# ---------------------------------------------------------------------------
#
# Insert-if-absent for BOTH metric_versions (keyed on name+version) and
# metric_catalog (keyed on name), in one script guarded by the
# 'metric_catalog' write-lock domain -- the same insert-if-absent shape
# CREATE_INCIDENT_TXN and WRITE_AUDIT_EVENT_TXN already use. A second call
# with the identical name+version is a no-op (WHERE NOT EXISTS matches
# nothing to insert); a second call under the same name+version but with
# DIFFERENT content is caught by the content_hash comparison after the
# transaction commits and raised as PayloadConflictError.

SEED_METRIC_DEFINITION_TXN = StatementTemplate(
    name="SEED_METRIC_DEFINITION_TXN",
    lock_domain="metric_catalog",
    sql=f"""
    UPDATE `{WRITE_LOCKS_TABLE}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = @created_by
    WHERE lock_domain = 'metric_catalog';
    ASSERT @@row_count = 1 AS 'expected exactly one write_locks row for domain metric_catalog';

    INSERT INTO `{METRIC_VERSIONS_TABLE}` (
      name, version, description, formula, measurement_grain, freshness_expectation,
      certification_status, approved_source_tables, required_filters, downstream_dashboards,
      content_hash, prior_version, created_by, created_at, change_reason,
      validation_evidence, review_notes, reviewer, reviewed_at
    )
    SELECT @name, @version, @description, @formula, @measurement_grain, @freshness_expectation,
           @certification_status, @approved_source_tables, @required_filters, @downstream_dashboards,
           @content_hash, NULL, @created_by, @created_at, @change_reason,
           NULL, NULL, NULL, NULL
    FROM UNNEST([1]) AS _seed
    WHERE NOT EXISTS (
      SELECT 1 FROM `{METRIC_VERSIONS_TABLE}` WHERE name = @name AND version = @version
    );
    ASSERT @@row_count IN (0, 1) AS 'insert-if-absent must affect at most one metric_versions row';

    INSERT INTO `{METRIC_CATALOG_TABLE}` (name, current_version, owner, certification_status, last_reviewed_at, updated_at)
    SELECT @name, @version, @owner, @certification_status, NULL, @created_at
    FROM UNNEST([1]) AS _seed
    WHERE NOT EXISTS (
      SELECT 1 FROM `{METRIC_CATALOG_TABLE}` WHERE name = @name
    );
    ASSERT @@row_count IN (0, 1) AS 'insert-if-absent must affect at most one metric_catalog row';
    """,
    result_sql=f"""
    SELECT c.name AS name, c.current_version AS current_version, c.owner AS owner,
           c.certification_status AS certification_status,
           v.version AS version_version, v.content_hash AS content_hash
    FROM `{METRIC_CATALOG_TABLE}` c
    JOIN `{METRIC_VERSIONS_TABLE}` v ON v.name = c.name AND v.version = c.current_version
    WHERE c.name = @name;
    """,
)
register_template(SEED_METRIC_DEFINITION_TXN)


@dataclass(frozen=True)
class SeedResult:
    name: str
    version: str
    certification_status: str


def seed_metric_definition(
    client: "TransactionalClientLike",
    definition: MetricDefinition,
    *,
    created_by: str,
    created_at: str,
    change_reason: str = "initial seed from shared.metric_catalog",
) -> SeedResult:
    """Seed one `pending_validation` `MetricDefinition` into persisted
    storage, insert-if-absent, guarded by the 'metric_catalog' write-lock
    domain.

    Refuses outright (before touching BigQuery) if `definition.
    certification_status` is anything other than "pending_validation" --
    this administrative bootstrap step exists to seed exactly the
    not-yet-reviewed state the in-memory catalog already declares those
    five definitions to be in; it must never be the path that marks a
    metric certified.

    Idempotent: a second call with identical name/version/content is a
    successful no-op. A second call under the same name+version but with
    genuinely different content (detected via `shared.metric_hashing.
    compute_content_hash()`), or where `metric_catalog` already points at
    a DIFFERENT version than the one being seeded, raises
    `PayloadConflictError` -- never silently overwrites existing content.
    """

    if definition.certification_status != "pending_validation":
        raise ValueError(
            "seed_metric_definition() only seeds pending_validation "
            "definitions -- it must never be used to mark a metric "
            "certified as a side effect of bootstrap seeding."
        )

    content_hash = compute_content_hash(
        name=definition.name,
        description=definition.description,
        formula=definition.formula,
        measurement_grain=definition.measurement_grain,
        freshness_expectation=definition.freshness_expectation,
        approved_source_tables=definition.approved_source_tables,
        required_filters=definition.required_filters,
        downstream_dashboards=definition.downstream_dashboards,
    )

    result = execute_transaction(
        client,
        [
            BoundStatement(
                template_name=SEED_METRIC_DEFINITION_TXN.name,
                params={
                    "created_by": created_by,
                    "name": definition.name,
                    "version": definition.version,
                    "description": definition.description,
                    "formula": definition.formula,
                    "measurement_grain": definition.measurement_grain,
                    "freshness_expectation": definition.freshness_expectation,
                    "certification_status": definition.certification_status,
                    "approved_source_tables": definition.approved_source_tables,
                    "required_filters": definition.required_filters,
                    "downstream_dashboards": definition.downstream_dashboards,
                    "content_hash": content_hash,
                    "created_at": created_at,
                    "change_reason": change_reason,
                    "owner": definition.owner,
                },
            )
        ],
    )

    if not result.result_rows:
        raise RuntimeError(
            f"SEED_METRIC_DEFINITION_TXN committed but no row was found "
            f"for name={definition.name!r} afterward -- this should be "
            "unreachable."
        )

    persisted = result.result_rows[0]

    if persisted["version_version"] != definition.version:
        raise PayloadConflictError(
            f"name={definition.name!r} conflicts: metric_catalog already "
            "points at a different version than the one being seeded "
            "(values withheld)"
        )
    if persisted["content_hash"] != content_hash:
        raise PayloadConflictError(
            f"name={definition.name!r} version={definition.version!r} "
            "conflicts: differently-worded content is already persisted "
            "under this same version identifier (values withheld)"
        )

    return SeedResult(
        name=persisted["name"],
        version=persisted["current_version"],
        certification_status=persisted["certification_status"],
    )


def seed_current_catalog(
    client: "TransactionalClientLike", *, created_by: str, created_at: str
) -> list[SeedResult]:
    """Seed every definition currently in `shared.metric_catalog`'s
    in-memory registry (the five extracted, pending_validation Loupe
    metrics) into persisted storage.

    This is the one function the administrative CLI at the bottom of
    this module calls -- never anything an application imports or calls
    from a request-handling path or startup routine (see
    `tests/test_persistence_boundary.py`'s "no startup seeding" checks).
    """

    from shared.metric_catalog import list_definitions

    return [
        seed_metric_definition(client, definition, created_by=created_by, created_at=created_at)
        for definition in list_definitions()
    ]


# ---------------------------------------------------------------------------
# 3. Governed certification
# ---------------------------------------------------------------------------
#
# CERTIFY_METRIC_VERSION_TXN itself only inserts the new metric_versions
# row and advances metric_catalog's pointer -- it declares no result_sql
# of its own. The audit event is written by combining this template with
# shared.audit_persistence.WRITE_AUDIT_EVENT_TXN (which DOES declare
# result_sql) as a second BoundStatement in the SAME execute_transaction()
# call -- see this module's docstring, "Audit ownership."

CERTIFY_METRIC_VERSION_TXN = StatementTemplate(
    name="CERTIFY_METRIC_VERSION_TXN",
    lock_domain="metric_catalog",
    sql=f"""
    UPDATE `{WRITE_LOCKS_TABLE}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = @reviewer
    WHERE lock_domain = 'metric_catalog';
    ASSERT @@row_count = 1 AS 'expected exactly one write_locks row for domain metric_catalog';

    INSERT INTO `{METRIC_VERSIONS_TABLE}` (
      name, version, description, formula, measurement_grain, freshness_expectation,
      certification_status, approved_source_tables, required_filters, downstream_dashboards,
      content_hash, prior_version, created_by, created_at, change_reason,
      validation_evidence, review_notes, reviewer, reviewed_at
    )
    SELECT @name, @new_version, @description, @formula, @measurement_grain, @freshness_expectation,
           'certified', @approved_source_tables, @required_filters, @downstream_dashboards,
           @content_hash, @expected_current_version, @created_by, @created_at, @change_reason,
           @validation_evidence, @review_notes, @reviewer, @reviewed_at
    FROM UNNEST([1]) AS _seed
    WHERE NOT EXISTS (
      SELECT 1 FROM `{METRIC_VERSIONS_TABLE}` WHERE name = @name AND version = @new_version
    );
    ASSERT @@row_count = 1 AS 'expected exactly one new metric_versions row inserted for this certification -- a version identifier collision or a rolled-back retry must not silently no-op';

    UPDATE `{METRIC_CATALOG_TABLE}`
    SET current_version = @new_version,
        certification_status = 'certified',
        last_reviewed_at = @reviewed_at,
        updated_at = @reviewed_at
    WHERE name = @name AND current_version = @expected_current_version;
    ASSERT @@row_count = 1 AS 'expected exactly one metric_catalog pointer updated from the expected current version -- a stale expected_current_version means someone else certified first';
    """,
)
register_template(CERTIFY_METRIC_VERSION_TXN)


@dataclass(frozen=True)
class MetricCertificationResult:
    name: str
    version: str
    prior_version: str
    content_hash: str
    reviewer: str
    created_by: str


def certify_metric_definition(
    client: "TransactionalClientLike",
    *,
    name: str,
    new_version: str,
    expected_current_version: str,
    description: str,
    formula: str,
    measurement_grain: str,
    freshness_expectation: str,
    approved_source_tables: list[str],
    created_by: str,
    reviewer: str,
    validation_evidence: str,
    reviewed_at: str,
    change_reason: str,
    event_id: str,
    required_filters: Optional[list[str]] = None,
    downstream_dashboards: Optional[list[str]] = None,
    review_notes: Optional[str] = None,
    created_at: Optional[str] = None,
    require_separation_of_duties: bool = False,
) -> MetricCertificationResult:
    """Atomically certify a new, immutable `MetricVersion` for `name`,
    advance `metric_catalog`'s pointer to it, and record the
    certification as an audit event -- one script, one atomic commit,
    via `execute_transaction()`.

    Governance requirements enforced BEFORE any BigQuery call:
      - `validation_evidence`, `reviewed_at`, and `change_reason` are all
        required, non-empty strings -- certification without evidence, a
        review timestamp, or a stated reason is refused outright, never
        silently defaulted.
      - `reviewer` and `created_by` must be distinct identities ONLY when
        `require_separation_of_duties=True` -- see this module's docstring,
        "Phase 6D policy correction." Defaults to `False`: a single
        reviewer may certify their own authored content in this
        portfolio deployment. Either way, both identities are always
        recorded exactly as given, in both the new `MetricVersion` row
        and the certification's audit event -- never conflated.

    `new_version` and `event_id` are caller-supplied deterministic
    identifiers, matching this codebase's existing discipline
    (`incident_id`/`transition_id` in `shared/incident_persistence.py`) --
    this function does not invent a version- or event-id scheme.

    `expected_current_version` is the version the caller last read as
    `metric_catalog`'s `current_version`. It serves two purposes at once:
    it becomes the new version's `prior_version` (preserving an unbroken
    history chain), and it doubles as the pointer UPDATE's optimistic-
    concurrency guard -- if `metric_catalog`'s persisted `current_version`
    no longer matches (someone else certified first, or the caller's read
    was stale), the UPDATE's `ASSERT @@row_count = 1` fails and the WHOLE
    transaction -- including the just-inserted `metric_versions` row and
    the audit event -- rolls back together. Nothing partially applies.

    The certified content's `content_hash` (via `shared.metric_hashing.
    compute_content_hash()`) is unaffected by whether the content actually
    changed since the prior version -- certifying byte-identical content
    (a pure approval-state change) and certifying genuinely different
    content are both ordinary, supported calls; only the resulting
    `content_hash` differs, which is exactly the distinction that hash
    exists to preserve (see `shared/metric_hashing.py`'s module
    docstring).
    """

    if require_separation_of_duties and reviewer == created_by:
        raise ValueError(
            "reviewer and created_by must be distinct when "
            "require_separation_of_duties=True -- the identity "
            "certifying a metric version can never also be recorded as "
            "that same version's author under this deployment's stricter "
            "policy."
        )
    if not validation_evidence:
        raise ValueError("validation_evidence is required to certify a metric version.")
    if not reviewed_at:
        raise ValueError("reviewed_at is required to certify a metric version.")
    if not change_reason:
        raise ValueError("change_reason is required to certify a metric version.")

    required_filters = list(required_filters or [])
    downstream_dashboards = list(downstream_dashboards or [])
    effective_created_at = created_at if created_at is not None else reviewed_at

    content_hash = compute_content_hash(
        name=name,
        description=description,
        formula=formula,
        measurement_grain=measurement_grain,
        freshness_expectation=freshness_expectation,
        approved_source_tables=approved_source_tables,
        required_filters=required_filters,
        downstream_dashboards=downstream_dashboards,
    )

    audit_context = {
        "metric": name,
        "new_version": new_version,
        "prior_version": expected_current_version,
        "content_hash": content_hash,
        "change_reason": change_reason,
    }
    validate_no_secrets(audit_context)
    context_json = json.dumps(audit_context, sort_keys=True)

    result = execute_transaction(
        client,
        [
            BoundStatement(
                template_name=CERTIFY_METRIC_VERSION_TXN.name,
                params={
                    "name": name,
                    "new_version": new_version,
                    "expected_current_version": expected_current_version,
                    "description": description,
                    "formula": formula,
                    "measurement_grain": measurement_grain,
                    "freshness_expectation": freshness_expectation,
                    "approved_source_tables": approved_source_tables,
                    "required_filters": required_filters,
                    "downstream_dashboards": downstream_dashboards,
                    "content_hash": content_hash,
                    "created_by": created_by,
                    "created_at": effective_created_at,
                    "change_reason": change_reason,
                    "validation_evidence": validation_evidence,
                    "review_notes": review_notes,
                    "reviewer": reviewer,
                    "reviewed_at": reviewed_at,
                },
            ),
            BoundStatement(
                template_name=WRITE_AUDIT_EVENT_TXN.name,
                params={
                    "actor": reviewer,
                    "event_id": event_id,
                    "timestamp": reviewed_at,
                    "event_type": "metric_certified",
                    "subject": f"metric:{name}",
                    "outcome": "completed",
                    "context_json": context_json,
                },
            ),
        ],
    )

    if not result.result_rows:
        raise RuntimeError(
            f"CERTIFY_METRIC_VERSION_TXN committed but the combined "
            f"audit-event lookup returned no row for event_id={event_id!r} "
            "afterward -- this should be unreachable."
        )

    persisted_event = result.result_rows[0]
    if persisted_event["event_id"] != event_id:
        raise RuntimeError(
            f"CERTIFY_METRIC_VERSION_TXN committed but the persisted audit "
            f"event_id {persisted_event['event_id']!r} did not match the "
            f"requested {event_id!r} -- this should be unreachable."
        )

    # Every field below was either supplied by this call (and guaranteed
    # to match exactly what was persisted, per the template's own
    # ASSERTs) or is the deterministic content_hash this function itself
    # computed -- no second read-back of metric_catalog/metric_versions
    # is needed to report the outcome.
    return MetricCertificationResult(
        name=name,
        version=new_version,
        prior_version=expected_current_version,
        content_hash=content_hash,
        reviewer=reviewer,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# Explicit administrative CLI entry point (seeding only -- amendment 14's
# "bootstrap is an explicit administrative action only" pattern, mirroring
# shared/schema_management.py's CLI exactly). Certification is NOT exposed
# here: it is a per-metric, human-reviewed governance action taken through
# an eventual Governance UI (Phase 6D+), not a bulk CLI command.
# ---------------------------------------------------------------------------


def _build_real_client(project: str, location: str):
    from google.cloud import bigquery

    return bigquery.Client(project=project, location=location)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(
        prog="python -m shared.metric_catalog_persistence",
        description=(
            "Explicit, administrator-invoked one-time seeding of the "
            "persisted metric catalog from shared.metric_catalog's "
            "in-memory pending_validation registry. Must NOT be run "
            "against production loupe_platform in this phase."
        ),
    )
    parser.add_argument("action", choices=["seed"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="US")
    parser.add_argument("--actor", required=True, help="Identity recorded as created_by for every seeded version.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required to actually execute seeding. Without it, this only prints what would run.",
    )
    args = parser.parse_args(argv)

    print(f"Target: project={args.project!r} location={args.location!r} actor={args.actor!r}")
    if not args.yes:
        print(
            "Dry run only (pass --yes to actually execute). This command "
            "would seed the five pending_validation definitions from "
            "shared.metric_catalog into metric_catalog/metric_versions."
        )
        return 0

    client = _build_real_client(args.project, args.location)
    created_at = datetime.now(timezone.utc).isoformat()
    results = seed_current_catalog(client, created_by=args.actor, created_at=created_at)
    for result in results:
        print(result)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
