"""Canonical content-hash computation for MetricVersion rows.

Per Phase 6's approved amendment 6: the content hash must cover every
field that affects a metric's MEANING, not just formula/grain/tables/
filters, and must canonicalize the ordering of repeated (list) fields
before hashing so that two semantically-identical definitions expressed
with lists in a different order still hash identically. Owner, reviewer,
and certification/review metadata must NOT affect this hash -- two
versions with the same semantic content but different reviewers (or no
reviewer yet) must produce the same hash, which is exactly what lets
shared/metric_catalog.py's certify_definition() (Phase 6C) tell "content
unchanged, only approval state changed" apart from "content changed"
without re-deriving that distinction ad hoc at every call site.

This module is deliberately tiny and dependency-free (no BigQuery, no
Streamlit, no shared.data_service import) so it can be unit-tested in
isolation and reused by both the eventual certification code (Phase 6C)
and any future reconciliation/migration tooling that needs to check
whether a definition actually changed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

# The exact, ordered set of fields that constitute a metric's MEANING.
# Deliberately excludes: name-as-identity is included (per amendment 6,
# "metric identity/name" is explicitly listed as in-scope -- a definition
# for "revenue" and a byte-identical definition mistakenly registered
# under "revenue_v2" must NOT hash the same, since the metric's identity
# is part of what it means). Explicitly excludes: owner, reviewer,
# certification_status, validation_evidence, review_notes, reviewed_at,
# created_by, created_at, version, prior_version, change_reason -- all of
# those describe *how this version came to exist and who is accountable
# for it*, never *what the metric means*. downstream_dashboards is
# included per amendment 6's "downstream assets when treated as governed
# lineage" -- this platform treats declared downstream dashboards as part
# of a metric's governed lineage (docs/contracts.md), so a change in
# which dashboards a definition is declared to back is a meaning change,
# not incidental metadata.
_CANONICAL_FIELDS = (
    "name",
    "description",
    "formula",
    "measurement_grain",
    "freshness_expectation",
    "approved_source_tables",
    "required_filters",
    "downstream_dashboards",
)

_LIST_FIELDS = frozenset({"approved_source_tables", "required_filters", "downstream_dashboards"})


def compute_content_hash(
    *,
    name: str,
    description: str,
    formula: str,
    measurement_grain: str,
    freshness_expectation: str,
    approved_source_tables: list[str],
    required_filters: Optional[list[str]] = None,
    downstream_dashboards: Optional[list[str]] = None,
) -> str:
    """Compute a deterministic SHA-256 content hash over exactly the
    semantic-meaning fields listed in _CANONICAL_FIELDS.

    List-valued fields are sorted before hashing so that
    approved_source_tables=["a", "b"] and ["b", "a"] -- which mean the
    same thing -- always produce the same hash. Scalar fields are used
    verbatim (not case-folded or whitespace-trimmed): a genuine wording
    change in `description` or `formula` SHOULD change the hash, since
    those are exactly the fields this hash exists to protect.
    """

    payload = {
        "name": name,
        "description": description,
        "formula": formula,
        "measurement_grain": measurement_grain,
        "freshness_expectation": freshness_expectation,
        "approved_source_tables": sorted(approved_source_tables),
        "required_filters": sorted(required_filters or []),
        "downstream_dashboards": sorted(downstream_dashboards or []),
    }
    # json.dumps with sort_keys=True gives a stable, canonical
    # serialization regardless of the dict's construction order above --
    # belt-and-suspenders alongside the explicit field list, so a future
    # accidental reordering of the dict literal cannot change the hash.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
