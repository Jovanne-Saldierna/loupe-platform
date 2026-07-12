"""Startup-safe (read-only) resolution of persistence availability.

Phase 6D's explicit cutover rule: "No app startup may bootstrap, migrate,
seed, or certify. Application startup may only validate availability and
schema version." This module is the one place that rule is implemented,
so all three apps' main.py call the same function rather than each
re-deriving their own startup sequence.

resolve_persistence() performs, in order, only read-only operations:

  1. shared.config.load_persistence_mode() -- explicit env-driven mode
     selection. "constants" short-circuits immediately: no client is ever
     constructed, no BigQuery call of any kind happens, per amendment
     14's "constants mode is an explicit pre-cutover/demo configuration."
  2. shared.config.load_platform_config() -- read env-derived identifiers
     only (no I/O).
  3. Construct a client (or accept a test-supplied one).
  4. shared.config.validate_persistence_config() -- one read-only
     get_dataset() metadata call.
  5. shared.schema_management.validate_schema_version() -- one read-only
     query against schema_migrations.

If ANY of steps 2-5 fails or reports not-ok, the result is
`available=False` with a safe, non-raw-exception error message -- never
an uncaught exception propagating out of an app's build_state(), and
never a silent fallback to constants mode (a caller that asked for
"persisted" and didn't get it must render that honestly; falling back to
constants mode is a separate, distinct app-level decision this module
never makes on a caller's behalf -- see each app's main.py for how it
chooses to render "persisted mode configured but unavailable").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from shared.config import ConfigError, PersistenceMode, PlatformConfig, load_persistence_mode, load_platform_config
from shared.config import validate_persistence_config
from shared.data_service import BigQueryClientLike, get_bigquery_client
from shared.schema_management import validate_schema_version


@dataclass(frozen=True)
class PersistenceResolution:
    """The outcome of resolve_persistence() -- a plain, non-raising value
    object, matching shared.config.ConfigValidationResult's pattern."""

    mode: PersistenceMode
    available: bool
    client: Optional["BigQueryClientLike"]
    config: Optional[PlatformConfig]
    safe_error: Optional[str]


def resolve_persistence(
    env: Optional[dict[str, str]] = None,
    *,
    client_factory: Callable[[str, Optional[str]], "BigQueryClientLike"] = get_bigquery_client,
    client: Optional["BigQueryClientLike"] = None,
) -> PersistenceResolution:
    """Resolve whether persisted mode is configured, reachable, and at the
    expected schema version -- entirely read-only, safe to call from any
    app's build_state() on every request/run.

    `client` lets tests inject a fake client directly (bypassing
    `client_factory`, which would otherwise construct a real
    google.cloud.bigquery.Client and require real credentials) -- exactly
    the same pattern shared/data_service.py's own tests use.
    """

    try:
        mode = load_persistence_mode(env)
    except ConfigError as exc:
        # An invalid LOUPE_PERSISTENCE_MODE value: report honestly rather
        # than guessing which mode was intended.
        return PersistenceResolution(mode="constants", available=False, client=None, config=None, safe_error=str(exc))

    if mode == "constants":
        return PersistenceResolution(mode="constants", available=False, client=None, config=None, safe_error=None)

    try:
        config = load_platform_config(env)
    except ConfigError as exc:
        return PersistenceResolution(mode="persisted", available=False, client=None, config=None, safe_error=str(exc))

    resolved_client = client
    if resolved_client is None:
        try:
            resolved_client = client_factory(config.project, config.location)
        except Exception as exc:  # noqa: BLE001 -- client construction failure must degrade honestly
            return PersistenceResolution(
                mode="persisted",
                available=False,
                client=None,
                config=config,
                safe_error=f"Could not construct a BigQuery client for project {config.project!r}: {exc!r}",
            )

    config_check = validate_persistence_config(resolved_client, config)
    if not config_check.ok:
        return PersistenceResolution(
            mode="persisted", available=False, client=None, config=config, safe_error=config_check.safe_error
        )

    schema_check = validate_schema_version(resolved_client, config)
    if not schema_check.ok:
        return PersistenceResolution(
            mode="persisted", available=False, client=None, config=config, safe_error=schema_check.safe_error
        )

    return PersistenceResolution(
        mode="persisted", available=True, client=resolved_client, config=config, safe_error=None
    )
