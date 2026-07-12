"""Deterministic comparison between two definitions or versions of the
SAME business metric.

Owns exactly two responsibilities, kept in this one file because they are
two sides of the same "definition diff" concern:

1. compare_definitions() -- a generic, field-by-field comparison utility.
   Given any two shared.models.MetricDefinition objects, it produces a
   structured DefinitionDiff by comparing their actual fields. This part
   is deliberately metric-agnostic: it does not decide whether the two
   inputs SHOULD be compared, only how to compare them once asked.

2. find_definition_diff_pairs() and its two helpers -- deciding WHICH
   pairs are automatically surfaced on the Definition Diff page. An
   earlier version of this module paired any two metrics sharing >=2
   approved source tables (e.g. revenue, margin, and margin_leakage all
   reference order_items+products). That was wrong: those are three
   distinct business metrics with different meanings, not different
   definitions of one metric, and presenting them as a "Definition Diff"
   implied a mismatch that does not exist. Per the corrected Phase 3
   scope, a pair is only ever surfaced automatically if one of these
   holds:

   - the same stable metric identity (currently: MetricDefinition.name,
     since shared/metric_catalog.py already keys its registry by name --
     see the module note on find_alternate_version_pairs() below) at two
     different `version` values, or
   - an explicit, curated comparison relationship someone deliberately
     registered in _EXPLICIT_COMPARISONS below.

   A third case -- a user manually picking any two arbitrary metrics to
   compare -- is intentionally NOT part of this module's automatic pairing
   at all. That is apps/metric_governance/ui.py's "Compare Any Two
   Metrics" section, which calls compare_definitions() directly and labels
   its output as a cross-metric, informational comparison rather than a
   definition mismatch. Keeping that path out of
   find_definition_diff_pairs() is what prevents it from silently
   reappearing as an implied "these are the same thing" pairing.
"""

from __future__ import annotations

from itertools import combinations

from apps.metric_governance.models import DefinitionDiff
from shared.models import MetricDefinition

# Explicit, curated pairs of metric names that a steward has deliberately
# declared comparable as alternate definitions of the same underlying
# business question -- e.g. two teams' competing formulas for "the same"
# metric. Empty for now: shared/metric_catalog.py's five real metrics
# (revenue, margin, return_rate, margin_leakage, channel_mix) are five
# distinct business metrics, not competing definitions of one metric, so
# none of them belong here. This registry exists so that relationship can
# be declared explicitly and reviewably, in code, rather than inferred
# from incidental table overlap.
_EXPLICIT_COMPARISONS: tuple[tuple[str, str], ...] = ()


def compare_definitions(left: MetricDefinition, right: MetricDefinition) -> DefinitionDiff:
    """Deterministically compare two metric definitions field by field.

    Every line in `matches` and `differences` is derived directly from a
    field comparison -- nothing here is subjective judgment. Field order
    is fixed so the output is reproducible for the same two inputs.
    """

    matches: list[str] = []
    differences: list[str] = []

    left_tables = set(left.approved_source_tables)
    right_tables = set(right.approved_source_tables)
    shared_tables = sorted(left_tables & right_tables)

    if shared_tables:
        matches.append(f"Both draw from {', '.join(shared_tables)}.")
    else:
        differences.append("They share no approved source tables.")

    if left.measurement_grain == right.measurement_grain:
        matches.append(f"Both are computed at the same measurement grain: {left.measurement_grain}.")
    else:
        differences.append(
            f'Measurement grain differs: {left.name} is "{left.measurement_grain}"; '
            f'{right.name} is "{right.measurement_grain}".'
        )

    if left.certification_status == right.certification_status:
        matches.append(f"Both currently carry the same certification status: {left.certification_status}.")
    else:
        differences.append(
            f"Certification status differs: {left.name} is {left.certification_status}; "
            f"{right.name} is {right.certification_status}."
        )

    if left.owner == right.owner:
        matches.append(f"Both are owned by {left.owner}.")
    else:
        differences.append(f"Ownership differs: {left.name} is owned by {left.owner}; {right.name} is owned by {right.owner}.")

    if left.freshness_expectation == right.freshness_expectation:
        matches.append("Both declare the same freshness expectation.")
    else:
        differences.append(
            f'Freshness expectation differs: {left.name} declares "{left.freshness_expectation}"; '
            f'{right.name} declares "{right.freshness_expectation}".'
        )

    only_left = sorted(left_tables - right_tables)
    only_right = sorted(right_tables - left_tables)
    if only_left:
        differences.append(f"{left.name} also references {', '.join(only_left)}, which {right.name} does not.")
    if only_right:
        differences.append(f"{right.name} also references {', '.join(only_right)}, which {left.name} does not.")

    if left.formula != right.formula:
        differences.append(f'Formulas differ: {left.name} = "{left.formula}"; {right.name} = "{right.formula}".')
    else:
        matches.append("Formulas are identical.")

    if not differences:
        recommended_use = (
            f"{left.name} and {right.name} match on every compared field; treat them as the same "
            "metric rather than maintaining two separate definitions."
        )
    elif not shared_tables:
        recommended_use = (
            f"{left.name} and {right.name} do not share a source table, so they are unrelated "
            "metrics that happen to be grouped or named similarly -- they are not interchangeable."
        )
    else:
        recommended_use = (
            f"{left.name} and {right.name} both touch {', '.join(shared_tables)} but diverge on "
            f"{len(differences)} compared field(s); confirm which definition actually answers the "
            "question being asked before reusing either number."
        )

    return DefinitionDiff(
        left_name=left.name,
        right_name=right.name,
        matches=matches,
        differences=differences,
        recommended_use=recommended_use,
    )


def find_alternate_version_pairs(
    definitions: list[MetricDefinition],
) -> list[tuple[MetricDefinition, MetricDefinition]]:
    """Return pairs of definitions that share a stable metric identity but
    carry different `version` values -- i.e. genuine alternate versions of
    the SAME metric, never two different metrics.

    Identity note: shared.models.MetricDefinition has no separate
    metric_id field distinct from `name`, and shared/metric_catalog.py's
    registry is itself keyed by name (`_CATALOG[definition.name] =
    definition`), which structurally means that catalog can only ever
    hold one definition per name at a time today -- registering a second
    version of "margin" would silently overwrite the first rather than
    create a second, comparable entry. That is a real limitation of the
    current catalog storage, not something this function can paper over;
    until shared/metric_catalog.py (or a future BigQuery-backed
    replacement) can hold multiple versions per name, this function will
    correctly return an empty list against the real catalog, per the
    honest "no alternate versions available yet" UI state. It is still
    fully exercised here against directly-constructed fixtures so its
    pairing logic is proven correct independent of that storage gap.

    Two definitions with the same name AND the same version are treated
    as duplicate registrations, not a version pair, and are not paired.
    """

    by_name: dict[str, list[MetricDefinition]] = {}
    for definition in definitions:
        by_name.setdefault(definition.name, []).append(definition)

    pairs: list[tuple[MetricDefinition, MetricDefinition]] = []
    for name in sorted(by_name):
        entries = sorted(by_name[name], key=lambda definition: definition.version)
        for left, right in combinations(entries, 2):
            if left.version != right.version:
                pairs.append((left, right))
    return pairs


def find_explicit_comparison_pairs(
    definitions: list[MetricDefinition],
    explicit_pairs: tuple[tuple[str, str], ...] = _EXPLICIT_COMPARISONS,
) -> list[tuple[MetricDefinition, MetricDefinition]]:
    """Resolve _EXPLICIT_COMPARISONS's curated (name, name) pairs against
    the definitions actually present. A declared pair whose metric no
    longer exists in `definitions` is silently skipped rather than
    raising -- the registry is allowed to reference metrics that were
    later removed or renamed without breaking the page.
    """

    by_name = {definition.name: definition for definition in definitions}
    pairs: list[tuple[MetricDefinition, MetricDefinition]] = []
    for left_name, right_name in explicit_pairs:
        left = by_name.get(left_name)
        right = by_name.get(right_name)
        if left is not None and right is not None:
            pairs.append((left, right))
    return pairs


def find_definition_diff_pairs(
    definitions: list[MetricDefinition],
) -> list[tuple[MetricDefinition, MetricDefinition]]:
    """Return every pair of definitions the Definition Diff page should
    automatically surface: alternate versions of the same metric, plus
    any explicitly curated comparison relationships. Nothing else --
    in particular, never a pair inferred merely from shared source
    tables, and never a user's ad hoc cross-metric selection (see
    apps/metric_governance/ui.py's separate "Compare Any Two Metrics"
    section for that).

    Deterministic and deduplicated: alternate-version pairs are listed
    first (already sorted by name, then version), followed by any
    explicit comparisons not already covered.
    """

    seen: set[tuple[str, str, str, str]] = set()
    result: list[tuple[MetricDefinition, MetricDefinition]] = []
    for left, right in find_alternate_version_pairs(definitions) + find_explicit_comparison_pairs(definitions):
        key = (left.name, left.version, right.name, right.version)
        if key not in seen:
            seen.add(key)
            result.append((left, right))
    return result
