"""Repository-level boundary test: proves apps/ never registers a
transaction template.

Per Phase 6B-prep amendment ("tighten the allowlist boundary before
Phase 6B"): registered transaction templates must live in the
persistence layer (shared/), never in an app or an LLM-facing module
(apps/*/chat.py, apps/*/explanations.py). No public path may register
arbitrary runtime SQL. This is enforced two ways:

1. Structurally, shared.persistence_transactions._TEMPLATES is a
   module-private dict, and register_template() is the only function
   that mutates it -- there is no other way to add a template.
2. This test, which scans every .py file under apps/ (source text, not
   just import-time behavior, so it also catches a call that only
   happens inside a function body that is never exercised at import
   time) for any reference to register_template or direct mutation of
   the private template registry, and fails if it finds one.

This is a static, whole-repository check, not a unit test of one
module's behavior -- it belongs at the top level of tests/ rather than
under tests/shared/ or tests/data_quality_triage/ etc., mirroring
test_import_smoke.py's placement for the same reason.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = REPO_ROOT / "apps"

_FORBIDDEN_MARKERS = (
    "register_template",
    "_TEMPLATES[",
    "_TEMPLATES.update",
    "persistence_transactions._TEMPLATES",
)


def _all_app_python_files() -> list[Path]:
    return sorted(APPS_DIR.rglob("*.py"))


def test_apps_directory_is_not_empty_so_this_test_is_actually_checking_something():
    # A guard against this test silently passing because APPS_DIR
    # doesn't resolve the way this test assumes.
    files = _all_app_python_files()
    assert len(files) > 10


def test_no_app_module_registers_a_transaction_template():
    offenders: list[str] = []
    for path in _all_app_python_files():
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in _FORBIDDEN_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: contains {marker!r}")

    assert not offenders, (
        "apps/ must never register a transaction template or mutate the "
        "persistence-layer template registry directly -- only shared/ "
        "modules may do so. Offending references:\n" + "\n".join(offenders)
    )


def test_no_app_module_imports_register_template_by_name():
    offenders: list[str] = []
    for path in _all_app_python_files():
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "import register_template" in text or "register_template," in text or ", register_template" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, f"apps/ must never import register_template: {offenders}"


def test_llm_facing_modules_never_reference_execute_transaction_directly():
    # chat.py / explanations.py are the LLM-facing narration modules --
    # even reaching execute_transaction() from there (bypassing an
    # app-level, human-reviewed business function) would blur the line
    # between "AI explains structured evidence" and "AI can write."
    llm_facing = [p for p in _all_app_python_files() if p.name in {"chat.py", "explanations.py"}]
    assert llm_facing, "expected to find chat.py/explanations.py under apps/"

    offenders: list[str] = []
    for path in llm_facing:
        text = path.read_text(encoding="utf-8")
        if "execute_transaction" in text or "persistence_transactions" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, f"LLM-facing modules must never call the transaction API directly: {offenders}"


def test_templates_registry_is_module_private_in_the_persistence_layer():
    from shared import persistence_transactions

    assert hasattr(persistence_transactions, "_TEMPLATES")
    assert not hasattr(persistence_transactions, "TEMPLATES"), (
        "the template registry must stay private (leading underscore) -- "
        "a public, non-underscored TEMPLATES name would invite direct "
        "external mutation instead of going through register_template()."
    )


# ---------------------------------------------------------------------------
# Phase 6C: metric-catalog seeding must never run from application startup
# ---------------------------------------------------------------------------
#
# seed_metric_definition()/seed_current_catalog() (shared/
# metric_catalog_persistence.py) are explicit, administrator-invoked
# bootstrap actions (per amendment 14's "bootstrap is an explicit
# administrative action only" pattern) -- never something an app's
# main.py/build_state() calls at startup or on any request path.

_FORBIDDEN_SEEDING_MARKERS = (
    "seed_metric_definition",
    "seed_current_catalog",
)


def test_no_app_module_calls_metric_catalog_seeding():
    offenders: list[str] = []
    for path in _all_app_python_files():
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in _FORBIDDEN_SEEDING_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: contains {marker!r}")

    assert not offenders, (
        "apps/ must never call metric-catalog seeding -- seeding is an "
        "explicit, one-time administrative CLI action "
        "(shared/metric_catalog_persistence.py's `seed` command), never "
        "something triggered by application startup or a request path. "
        "Offending references:\n" + "\n".join(offenders)
    )


_CERTIFICATION_ALLOWED_PATHS = frozenset(
    {
        "apps/metric_governance/persistence.py",
        "apps/metric_governance/ui.py",
    }
)


def test_certify_metric_definition_is_only_referenced_from_the_allowed_governance_paths():
    # Phase 6D wires certification into Metric Governance as an explicit,
    # human-triggered action ONLY -- apps/metric_governance/persistence.py
    # (the thin pass-through wrapper) and apps/metric_governance/ui.py
    # (the Catalog page's "Certify" button/form handler) are the only
    # places allowed to reference it. Every other app module, and
    # critically main.py's build_state() (the automatic, request-time
    # assembly path) and chat.py/explanations.py (the LLM-facing
    # narration modules, already covered by
    # test_llm_facing_modules_never_reference_execute_transaction_directly
    # above) must never reference it.
    offenders: list[str] = []
    for path in _all_app_python_files():
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "certify_metric_definition" not in text:
            continue
        relative = str(path.relative_to(REPO_ROOT))
        if relative not in _CERTIFICATION_ALLOWED_PATHS:
            offenders.append(relative)

    assert not offenders, (
        "certify_metric_definition() must only be referenced from "
        f"{sorted(_CERTIFICATION_ALLOWED_PATHS)} (an explicit, "
        f"human-triggered governance action): {offenders}"
    )


def test_no_build_state_function_calls_certify_metric_definition():
    # Even within the allowed paths, certification must never be reachable
    # from build_state() itself -- it is a Streamlit button/form handler
    # invoked only by direct human interaction, never part of the
    # automatic per-run state assembly. main.py is where build_state()
    # lives for every app; it must never mention certify_metric_definition
    # at all (that's a stronger, simpler guarantee than trying to parse
    # which function a reference sits inside).
    offenders: list[str] = []
    for path in _all_app_python_files():
        if "__pycache__" in path.parts or path.name != "main.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "certify_metric_definition" in text or "certify_definition(" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, (
        "No app's main.py (build_state()'s home) may reference metric "
        f"certification -- it must only be reachable from a human-triggered "
        f"UI action: {offenders}"
    )
