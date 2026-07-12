"""Phase 6E: guarded, idempotent bootstrap + seed of the isolated
`loupe_platform_test` BigQuery dataset.

STATUS: NOT YET RUN. This script requires a real, authenticated Google
Cloud identity -- there is no Application Default Credentials file, no
gcloud CLI, and no service-account key material available in the
sandboxed environment this repository was prepared in, and per the
approved instructions this script must never request, generate, upload,
or read one. Run this yourself, from either:

  (a) a local terminal with `gcloud auth application-default login`
      already completed, or
  (b) Google Cloud Shell (which already has an authenticated identity).

WHAT THIS DOES
---------------
One command, two idempotent steps, against ONE target only
(project=ai-weekend-agent-501502, dataset=loupe_platform_test,
location=US):

  1. shared.schema_management.bootstrap() -- applies every pending
     migration (currently just version 1: the nine persistence tables).
     Safe to run repeatedly: every CREATE TABLE uses IF NOT EXISTS, every
     migration records itself in schema_migrations so a second run
     applies nothing new.
  2. shared.metric_catalog_persistence.seed_current_catalog() -- seeds
     the five `pending_validation` metric definitions already in
     shared.metric_catalog's in-memory registry (revenue, margin,
     return_rate, margin_leakage, channel_mix). Safe to run repeatedly:
     seeding identical content twice is a successful no-op; seeding
     genuinely different content under the same name/version raises
     PayloadConflictError rather than silently overwriting anything.

WHAT THIS NEVER DOES
----------------------
  - Never certifies a metric. This script does not import, reference, or
    call shared.metric_catalog_persistence.certify_metric_definition()
    anywhere -- certification is a per-metric, human-reviewed governance
    action taken through Metric Governance's UI (apps/metric_governance/
    ui.py's "Certify" form), never a bulk bootstrap step. Every seeded
    definition is certification_status="pending_validation", exactly as
    seed_metric_definition() itself refuses to seed anything else.
  - Never runs against `loupe_platform` (production). --dataset is
    validated by tools.phase6e_ops.safety.require_safe_test_dataset()
    BEFORE any environment variable is set or any shared/*_persistence.py
    module is imported (those modules resolve their table-name constants
    from LOUPE_DATASET at import time -- see shared/data_service.py's
    INCIDENTS_TABLE docstring for why import order matters here).
  - Never runs automatically. This is a standalone `python -m
    tools.phase6e_ops.bootstrap_test_dataset` command only -- nothing in
    shared/ or apps/ imports this module, and no application startup
    path calls bootstrap()/seed_current_catalog() (enforced by
    tests/test_persistence_boundary.py for apps/, and by this module
    simply never being imported anywhere else).

USAGE
------
Dry run (prints the plan, touches nothing):

    python -m tools.phase6e_ops.bootstrap_test_dataset \\
        --project ai-weekend-agent-501502 --dataset loupe_platform_test \\
        --location US --actor <your-identity>

Actually execute:

    python -m tools.phase6e_ops.bootstrap_test_dataset \\
        --project ai-weekend-agent-501502 --dataset loupe_platform_test \\
        --location US --actor <your-identity> --yes

REQUIRED PERMISSIONS
----------------------
BigQuery Job User + BigQuery Data Editor scoped to loupe_platform_test,
plus either the dataset already existing in US or the running identity
holding bigquery.datasets.create. See docs/PHASE_6B_HANDOFF.md's
"Authentication and credential rules" section -- unchanged for this
script.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from tools.phase6e_ops.safety import DatasetTargetGuard, UnsafeTargetError, require_safe_test_dataset


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.phase6e_ops.bootstrap_test_dataset",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True, help="Must contain 'test' and must never be 'loupe_platform'.")
    parser.add_argument("--location", default="US")
    parser.add_argument("--actor", required=True, help="Identity recorded as created_by for every seeded definition.")
    parser.add_argument("--yes", action="store_true", help="Required to actually execute. Without it, dry-run only.")
    args = parser.parse_args(argv)

    # Safety gate FIRST -- before any environment variable is touched and
    # before any shared/*_persistence.py module is imported (their
    # table-name constants are resolved from LOUPE_DATASET at import
    # time, so import order is itself part of this script's safety
    # contract).
    try:
        require_safe_test_dataset(args.project, args.dataset, args.location)
    except UnsafeTargetError as exc:
        print(f"ERROR: {exc}")
        return 2

    print(
        f"Target: project={args.project!r} dataset={args.dataset!r} "
        f"location={args.location!r} actor={args.actor!r}"
    )

    if not args.yes:
        print(
            "Dry run only (pass --yes to actually execute). This command "
            "would:\n"
            "  1) apply pending schema migrations (idempotent) against "
            f"{args.project}.{args.dataset}\n"
            "  2) seed the five pending_validation metric definitions "
            "(idempotent) -- never certifying any of them"
        )
        return 0

    # Set LOUPE_DATASET (and the project/location the rest of this
    # process's shared.config reads) BEFORE importing anything that
    # resolves table names from it.
    os.environ["LOUPE_BQ_PROJECT"] = args.project
    os.environ["LOUPE_DATASET"] = args.dataset
    os.environ["LOUPE_BQ_LOCATION"] = args.location

    from datetime import datetime, timezone

    from google.cloud import bigquery

    from shared.config import load_platform_config
    from shared.metric_catalog_persistence import seed_current_catalog
    from shared.schema_management import bootstrap

    config = load_platform_config()
    # Re-assert the safety gate against whatever load_platform_config()
    # actually resolved -- defense in depth against an operator's own
    # stray environment variable overriding what --dataset said.
    require_safe_test_dataset(config.project, config.dataset, config.location)

    # Phase 6E correction 2: guard the client so that ANY generated SQL
    # this run's calls issue -- including from any module whose table-name
    # constants happen to have been resolved before this point -- is
    # refused outright if it references a dataset other than the one just
    # validated above. Never a silent fallback to loupe_platform.
    client = DatasetTargetGuard(
        bigquery.Client(project=config.project, location=config.location),
        allowed_dataset=config.dataset,
    )

    print(f"\nStep 1/2: applying pending schema migrations against {config.dataset}...")
    bootstrap_result = bootstrap(client, config)
    if bootstrap_result.already_current:
        print("  Already current -- no migrations applied (idempotent no-op).")
    else:
        for application in bootstrap_result.applied:
            print(f"  Applied migration v{application.version}: {application.description}")

    print(f"\nStep 2/2: seeding the five pending_validation metric definitions into {config.dataset}...")
    created_at = datetime.now(timezone.utc).isoformat()
    seed_results = seed_current_catalog(client, created_by=args.actor, created_at=created_at)
    for result in seed_results:
        print(f"  {result.name}: version={result.version!r} certification_status={result.certification_status!r}")

    print(
        "\nDone. Every seeded definition's certification_status is "
        "'pending_validation' -- this script never certified anything. "
        "Certification is a separate, human-triggered action in Metric "
        "Governance's UI."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
