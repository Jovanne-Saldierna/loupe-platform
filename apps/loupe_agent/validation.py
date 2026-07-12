"""Deterministic, LLM-free validation of every value an LLM extraction
step hands back before it is allowed to reach a query function.

Per the Phase 5 correction review: "The model may classify a user's
question or extract bounded parameters, but it must only invoke a
deterministic allowlisted query function... Validate all extracted
categories, states, dates, channels, and scenario levers before query
execution."

Every function here is a closed-set membership check against a fixed,
code-defined list (apps.loupe_agent.metrics.ALL_CATEGORIES,
apps.loupe_agent.metrics.STATE_ABBREV, apps.loupe_agent.scenarios.LEVER_RULES)
-- never a regex sanitizer, never an SQL-escaping routine. Rejecting
anything outside the allowlist, rather than trying to sanitize it, is
what makes this immune to prompt injection: there is no transformation
step an attacker's extracted text could pass through and still reach a
query. A rejected value never reaches shared.data_service.run_query() at
all, regardless of what it contains -- not even as a bound parameter.

This is defense in depth on top of (not a replacement for)
shared.data_service.run_query()'s own protections: even a validated value
only ever reaches BigQuery as a named ScalarQueryParameter/ArrayQueryParameter
(see metrics.py), never interpolated into SQL text, so a validated but
adversarial-looking category name (there are none, by construction) still
could not achieve SQL injection. Validation exists to reject nonsense/
injected input outright, not merely to neutralize it.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from apps.loupe_agent.metrics import ALL_CATEGORIES, STATE_ABBREV
from apps.loupe_agent.scenarios import LEVER_RULES

_CATEGORY_LOOKUP = {name.strip().lower(): name for name in ALL_CATEGORIES}
_STATE_LOOKUP = {name.strip().lower(): name for name in STATE_ABBREV}


def validate_category(raw: str) -> Optional[str]:
    """Return the canonical category name if `raw` (case-insensitively,
    whitespace-trimmed) matches one of apps.loupe_agent.metrics.ALL_CATEGORIES,
    else None. `raw` is never returned unmodified/unchecked -- only the
    canonical, code-defined string is ever handed to a query function."""

    if not isinstance(raw, str):
        return None
    return _CATEGORY_LOOKUP.get(raw.strip().lower())


def validate_state(raw: str) -> Optional[str]:
    """Return the canonical state name if `raw` matches one of
    apps.loupe_agent.metrics.STATE_ABBREV's keys, else None."""

    if not isinstance(raw, str):
        return None
    return _STATE_LOOKUP.get(raw.strip().lower())


def validate_lever(raw: str) -> Optional[str]:
    """Return `raw` unchanged if it is one of
    apps.loupe_agent.scenarios.LEVER_RULES's keys, else None. Lever names
    are internal identifiers (not free text), so this is an exact,
    case-sensitive match -- no normalization is applied."""

    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped if stripped in LEVER_RULES else None


def validate_category_list(raw_list: list[str]) -> tuple[list[str], list[str]]:
    """Validate every entry in `raw_list` independently. Returns
    (valid_canonical_names, rejected_raw_values) -- a single invalid or
    injected entry in a multi-category question never invalidates the
    whole request; it is simply dropped and reported as rejected."""

    valid: list[str] = []
    rejected: list[str] = []
    for entry in raw_list:
        canonical = validate_category(entry)
        if canonical is not None:
            if canonical not in valid:  # de-duplicate
                valid.append(canonical)
        else:
            rejected.append(entry)
    return valid, rejected


def validate_state_list(raw_list: list[str]) -> tuple[list[str], list[str]]:
    """State-list equivalent of validate_category_list()."""

    valid: list[str] = []
    rejected: list[str] = []
    for entry in raw_list:
        canonical = validate_state(entry)
        if canonical is not None:
            if canonical not in valid:
                valid.append(canonical)
        else:
            rejected.append(entry)
    return valid, rejected


def validate_date_range(start: date, end: date) -> Optional[str]:
    """Return an error message if the date range is malformed (not real
    `date` objects, or start after end), else None. Dashboard dates come
    from Streamlit's typed `st.date_input` widget, never from LLM-extracted
    text, but this guard exists so any future caller (or a malformed
    session-state value) cannot silently issue a query with an inverted or
    non-date range."""

    if not isinstance(start, date) or not isinstance(end, date):
        return "Date range must be real date values."
    if start > end:
        return f"Date range is invalid: start date {start.isoformat()} is after end date {end.isoformat()}."
    return None
