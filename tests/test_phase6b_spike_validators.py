"""Credential-free tests for tools/phase6b_spike/live_transaction_spike.py's
safety validators (amendment 9 of the pre-6B-spike revision).

These exercise only pure string-validation functions -- no BigQuery
client, no network access, no google-cloud-bigquery import at all. They
prove the run_id/project/dataset/location guards reject unsafe input
BEFORE any cloud call would ever be attempted, independent of whether
the live spike itself is ever run.
"""

from __future__ import annotations

import re

import pytest

from tools.phase6b_spike.live_transaction_spike import (
    REAL_DATASET_NAME,
    REQUIRED_LOCATION,
    UnsafeSpikeConfigurationError,
    _classify_concurrency_round,
    _generate_run_id,
    _insert_if_absent_script,
    _require_safe_target,
    _RUN_ID_PATTERN,
    _touch_lock_script,
    _validate_dataset_id,
    _validate_project_id,
    _validate_run_id,
)


# ---------------------------------------------------------------------------
# --run-id validation (amendment 2)
# ---------------------------------------------------------------------------


def test_valid_run_id_is_accepted():
    _validate_run_id("a1b2c3d4e5")  # must not raise


@pytest.mark.parametrize(
    "bad_run_id",
    [
        "",  # empty
        "a1b2c3d4e",  # 9 chars -- shortened
        "a1b2c3d4e5f",  # 11 chars -- extended
        "**********",  # wildcard-like
        "a1b2c3d4e*",  # wildcard-like, mixed
        "a1b2c3d4-5",  # punctuation
        "a1b2c3d4e5;DROP",  # punctuation / injection-shaped
        "A1B2C3D4E5",  # uppercase hex -- not accepted (pattern is lowercase-only)
        "g1b2c3d4e5",  # non-hex letter ('g' is not a hex digit)
        "zzzzzzzzzz",  # non-hex letters
        "0123456789abcdef",  # valid hex chars but wrong length
        "          ",  # whitespace only
        "a1b2c3d4e5\n",  # trailing newline / injection-shaped
    ],
)
def test_invalid_run_ids_are_rejected(bad_run_id):
    with pytest.raises(UnsafeSpikeConfigurationError):
        _validate_run_id(bad_run_id)


def test_run_id_pattern_is_exactly_ten_lowercase_hex_chars():
    assert _RUN_ID_PATTERN.pattern == r"^[0-9a-f]{10}$"


def test_generate_run_id_always_satisfies_its_own_validator():
    for _ in range(25):
        run_id = _generate_run_id()
        _validate_run_id(run_id)  # must not raise
        assert len(run_id) == 10


# ---------------------------------------------------------------------------
# --project / --dataset identifier validation (amendment 3)
# ---------------------------------------------------------------------------


def test_valid_project_id_is_accepted():
    _validate_project_id("ai-weekend-agent-501502")  # must not raise


@pytest.mark.parametrize(
    "bad_project",
    [
        "",
        "AI-Weekend-Agent",  # uppercase not allowed
        "1ai-weekend-agent",  # must start with a letter
        "ai-",  # too short after truncation
        "ai_weekend_agent",  # underscores not allowed in project IDs
        "ai-weekend-agent-`; DROP TABLE x; --",  # injection-shaped
        "ai weekend agent",  # spaces
        "a" * 40,  # too long
    ],
)
def test_invalid_project_ids_are_rejected(bad_project):
    with pytest.raises(UnsafeSpikeConfigurationError):
        _validate_project_id(bad_project)


def test_valid_dataset_id_is_accepted():
    _validate_dataset_id("loupe_platform_test")  # must not raise


@pytest.mark.parametrize(
    "bad_dataset",
    [
        "",
        "loupe-platform-test",  # hyphens not allowed in dataset IDs
        "loupe.platform.test",  # dots not allowed
        "loupe platform test",  # spaces
        "loupe_platform_test`; DROP TABLE x; --",  # injection-shaped
        "loupe_platform_test;",  # trailing punctuation
    ],
)
def test_invalid_dataset_ids_are_rejected(bad_dataset):
    with pytest.raises(UnsafeSpikeConfigurationError):
        _validate_dataset_id(bad_dataset)


# ---------------------------------------------------------------------------
# _require_safe_target(): combined project/dataset/location/name checks
# ---------------------------------------------------------------------------


def test_require_safe_target_accepts_the_approved_configuration():
    _require_safe_target("ai-weekend-agent-501502", "loupe_platform_test", "US")  # must not raise


def test_require_safe_target_rejects_real_production_dataset_name():
    with pytest.raises(UnsafeSpikeConfigurationError):
        _require_safe_target("ai-weekend-agent-501502", REAL_DATASET_NAME, "US")


def test_require_safe_target_rejects_dataset_without_test_in_name():
    with pytest.raises(UnsafeSpikeConfigurationError):
        _require_safe_target("ai-weekend-agent-501502", "loupe_platform_prod", "US")


@pytest.mark.parametrize("bad_location", ["EU", "us", "us-central1", "", "US "])
def test_require_safe_target_rejects_any_location_other_than_us(bad_location):
    with pytest.raises(UnsafeSpikeConfigurationError):
        _require_safe_target("ai-weekend-agent-501502", "loupe_platform_test", bad_location)


def test_required_location_constant_is_us():
    assert REQUIRED_LOCATION == "US"


def test_require_safe_target_rejects_unsafe_project_before_dataset_name_checks():
    # Identifier-shape validation happens first, regardless of whether
    # the dataset name itself would otherwise be considered safe.
    with pytest.raises(UnsafeSpikeConfigurationError):
        _require_safe_target("Not A Valid Project!", "loupe_platform_test", "US")


# ---------------------------------------------------------------------------
# _insert_if_absent_script(): regression coverage for the live-spike
# finding that BigQuery rejects "SELECT <literals> WHERE NOT EXISTS (...)"
# with no FROM clause at all ("Query without FROM clause cannot have a
# WHERE clause") -- confirmed against real BigQuery (2026-07-12 run,
# run_id=c0536479d3), which broke both the sequential and concurrent
# duplicate-insert paths (they share this one script builder). These
# tests are pure string/regex checks -- no BigQuery client, no sqlglot
# dependency required, no network access -- so they run in the same
# credential-free suite as every other test here.
# ---------------------------------------------------------------------------


def _insert_select_clause(script: str) -> str:
    """Extract just the `SELECT ... WHERE NOT EXISTS (...)` fragment
    belonging to the INSERT statement, for a narrowly-scoped check --
    isolated from the UPDATE/ASSERT statements earlier in the script,
    which legitimately do have their own WHERE clauses."""

    match = re.search(r"INSERT INTO.*?SELECT(?P<clause>.*?)ASSERT", script, re.IGNORECASE | re.DOTALL)
    assert match, "expected an INSERT ... SELECT ... ASSERT shape in the generated script"
    return match.group("clause")


def test_insert_if_absent_script_select_has_a_from_clause_before_where():
    script = _insert_if_absent_script(
        "proj.ds.spike_abc0123456_lock_rows",
        "proj.ds.spike_abc0123456_incidents_like",
        "abc0123456_some_id",
        "worker_a",
    )
    select_clause = _insert_select_clause(script)

    from_index = select_clause.upper().find("FROM")
    where_index = select_clause.upper().find("WHERE")
    assert from_index != -1, "the INSERT's SELECT must have a FROM clause"
    assert where_index != -1
    assert from_index < where_index, "FROM must appear before WHERE, not after"


def test_insert_if_absent_script_never_reintroduces_the_from_less_select():
    # The exact shape BigQuery rejected: a WHERE immediately following
    # the literal column list, with no FROM anywhere in between.
    script = _insert_if_absent_script(
        "proj.ds.spike_abc0123456_lock_rows",
        "proj.ds.spike_abc0123456_incidents_like",
        "abc0123456_some_id",
        "worker_a",
    )
    select_clause = _insert_select_clause(script)
    from_less_where = re.search(r"^(?:(?!FROM).)*WHERE", select_clause, re.IGNORECASE | re.DOTALL)
    assert from_less_where is None, (
        "the INSERT's SELECT has a WHERE with no preceding FROM -- this is "
        "exactly the shape real BigQuery rejects with 'Query without FROM "
        "clause cannot have a WHERE clause'"
    )


def test_insert_if_absent_script_uses_unnest_seed_row():
    script = _insert_if_absent_script(
        "proj.ds.spike_abc0123456_lock_rows",
        "proj.ds.spike_abc0123456_incidents_like",
        "abc0123456_some_id",
        "worker_a",
    )
    assert "FROM UNNEST([1])" in script


def test_insert_if_absent_script_still_targets_the_deterministic_incident_id():
    script = _insert_if_absent_script(
        "proj.ds.spike_abc0123456_lock_rows",
        "proj.ds.spike_abc0123456_incidents_like",
        "abc0123456_some_id",
        "worker_a",
    )
    # The FROM-clause fix must not have disturbed the deterministic-ID
    # values the sequential and concurrent duplicate-insert tests rely on.
    assert "'abc0123456_some_id'" in script
    assert script.count("'abc0123456_some_id'") >= 2  # both the SELECT literal and the WHERE NOT EXISTS check


# ---------------------------------------------------------------------------
# _classify_concurrency_round(): honest three-way classification, now
# driven by a pre-computed `is_retryable` flag (real exception instance
# classified inside the worker thread) rather than a reconstructed fake
# exception from a type-name string.
# ---------------------------------------------------------------------------


def test_classify_concurrency_round_confirmed_when_loser_is_retryable():
    outcomes = {
        "a": {"ok": True, "is_retryable": False},
        "b": {"ok": False, "is_retryable": True},
    }
    assert _classify_concurrency_round(outcomes) == "confirmed"


def test_classify_concurrency_round_failed_when_loser_is_not_retryable():
    # This is the honest, corrected behavior: a BadRequest that does NOT
    # match a confirmed concurrent-conflict reason code (e.g. a genuine
    # SQL bug, or an ASSERT invariant failure) must NOT be reported as
    # "confirmed" contention just because one worker lost.
    outcomes = {
        "a": {"ok": True, "is_retryable": False},
        "b": {"ok": False, "is_retryable": False},
    }
    assert _classify_concurrency_round(outcomes) == "failed"


def test_classify_concurrency_round_inconclusive_when_both_succeed():
    outcomes = {
        "a": {"ok": True, "is_retryable": False},
        "b": {"ok": True, "is_retryable": False},
    }
    assert _classify_concurrency_round(outcomes) == "inconclusive"


def test_classify_concurrency_round_failed_when_both_fail():
    outcomes = {
        "a": {"ok": False, "is_retryable": True},
        "b": {"ok": False, "is_retryable": False},
    }
    assert _classify_concurrency_round(outcomes) == "failed"


def test_touch_lock_script_still_has_assert_and_commit():
    # Regression guard for the classification refactor: the lock-touch
    # script itself was not part of the SQL bug, but is exercised by the
    # same _run_concurrent_pair() path that was refactored -- confirm its
    # shape is untouched.
    script = _touch_lock_script("proj.ds.spike_abc0123456_lock_rows", "worker_a")
    assert "ASSERT @@row_count = 1" in script
    assert "COMMIT TRANSACTION" in script


# ---------------------------------------------------------------------------
# _register_insert_if_absent_template() / _insert_if_absent_via_execute_transaction():
# the execute_transaction()-routed insert-if-absent path added to
# correct spike_concurrent_duplicate_insert's manual, outside-the-
# mechanism retry, and to prove the PayloadConflictError contract.
# Exercised against the fake BigQuery client (tests/shared/conftest.py)
# -- no real BigQuery client, no network access.
# ---------------------------------------------------------------------------


def test_register_insert_if_absent_template_select_has_from_before_where():
    from tools.phase6b_spike.live_transaction_spike import _register_insert_if_absent_template

    template = _register_insert_if_absent_template("proj", "ds", "abc0123456")
    match = re.search(r"INSERT INTO.*?SELECT(?P<clause>.*?)ASSERT", template.sql, re.IGNORECASE | re.DOTALL)
    assert match, "expected an INSERT ... SELECT ... ASSERT shape in the registered template's sql"
    clause = match.group("clause")
    from_index = clause.upper().find("FROM")
    where_index = clause.upper().find("WHERE")
    assert from_index != -1 and where_index != -1 and from_index < where_index


def test_register_insert_if_absent_template_declares_result_sql():
    from tools.phase6b_spike.live_transaction_spike import _register_insert_if_absent_template

    template = _register_insert_if_absent_template("proj", "ds", "abc0123457")
    assert template.result_sql is not None
    assert "SELECT status" in template.result_sql


def test_insert_if_absent_via_execute_transaction_identical_payload_is_idempotent():
    from shared.persistence_transactions import StatementTemplate, register_template
    from tests.shared.conftest import FakeBigQueryClient
    from tools.phase6b_spike.live_transaction_spike import _insert_if_absent_via_execute_transaction

    fake = FakeBigQueryClient()
    fake.next_rows = [{"status": "open"}]  # what's already persisted matches the intended payload
    template = StatementTemplate(
        name="_TEST_SPIKE_INSERT_IF_ABSENT_IDEMPOTENT",
        sql="UPDATE `ds.write_locks` SET last_touched_at = CURRENT_TIMESTAMP() WHERE lock_domain = @worker_label;",
        result_sql="SELECT status FROM `ds.incidents_like` WHERE incident_id = @incident_id;",
    )
    register_template(template)

    result = _insert_if_absent_via_execute_transaction(
        lambda: fake, template.name, "some_id", "worker_a", status="open"
    )
    assert result.result_rows == [{"status": "open"}]


def test_insert_if_absent_via_execute_transaction_raises_payload_conflict_on_mismatch():
    from shared.persistence_transactions import PayloadConflictError, StatementTemplate, register_template
    from tests.shared.conftest import FakeBigQueryClient
    from tools.phase6b_spike.live_transaction_spike import _insert_if_absent_via_execute_transaction

    fake = FakeBigQueryClient()
    fake.next_rows = [{"status": "open"}]  # persisted status differs from the intended "resolved"
    template = StatementTemplate(
        name="_TEST_SPIKE_INSERT_IF_ABSENT_CONFLICT",
        sql="UPDATE `ds.write_locks` SET last_touched_at = CURRENT_TIMESTAMP() WHERE lock_domain = @worker_label;",
        result_sql="SELECT status FROM `ds.incidents_like` WHERE incident_id = @incident_id;",
    )
    register_template(template)

    with pytest.raises(PayloadConflictError, match="some_id"):
        _insert_if_absent_via_execute_transaction(
            lambda: fake, template.name, "some_id", "worker_b", status="resolved"
        )


def test_insert_if_absent_via_execute_transaction_never_leaks_status_values_in_conflict_message():
    from shared.persistence_transactions import PayloadConflictError, StatementTemplate, register_template
    from tests.shared.conftest import FakeBigQueryClient
    from tools.phase6b_spike.live_transaction_spike import _insert_if_absent_via_execute_transaction

    fake = FakeBigQueryClient()
    fake.next_rows = [{"status": "open"}]
    template = StatementTemplate(
        name="_TEST_SPIKE_INSERT_IF_ABSENT_CONFLICT_NO_LEAK",
        sql="UPDATE `ds.write_locks` SET last_touched_at = CURRENT_TIMESTAMP() WHERE lock_domain = @worker_label;",
        result_sql="SELECT status FROM `ds.incidents_like` WHERE incident_id = @incident_id;",
    )
    register_template(template)

    try:
        _insert_if_absent_via_execute_transaction(lambda: fake, template.name, "some_id", "worker_b", status="resolved")
        pytest.fail("expected PayloadConflictError")
    except PayloadConflictError as err:
        assert "open" not in str(err)
        assert "resolved" not in str(err)
