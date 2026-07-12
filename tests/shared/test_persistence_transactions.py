"""Tests for shared/persistence_transactions.py.

Exercises the transaction-execution mechanism (template allowlisting,
retry/backoff, error classification, lock-domain validation) against the
fake BigQuery client's transaction-simulation support -- no live
BigQuery access. Phase 6A registers only test-scoped templates here; the
real business templates (metric certification, incident transition,
baseline promotion) are registered in Phase 6B/6C alongside the code
that uses them.
"""

from __future__ import annotations

import pytest

from shared.persistence_transactions import (
    LOCK_DOMAINS,
    BoundStatement,
    ConcurrentModificationError,
    PayloadConflictError,
    RetryPolicy,
    StatementTemplate,
    TransactionAbortedError,
    UnknownLockDomainError,
    UnregisteredTemplateError,
    _build_job_config,
    _is_retryable,
    _render_script,
    bigquery_error_diagnostics,
    execute_transaction,
    register_template,
)


# ---------------------------------------------------------------------------
# Retryable-error stand-ins, named to match _is_retryable()'s classification
# (which keys off the real google.api_core.exceptions class names this
# module expects to see against live BigQuery -- confirmed during the
# Phase 6B live transaction spike, not assumed here).
# ---------------------------------------------------------------------------


class Aborted(Exception):
    """Stand-in for google.api_core.exceptions.Aborted."""


class DeadlineExceeded(Exception):
    """Stand-in for google.api_core.exceptions.DeadlineExceeded."""


# ---------------------------------------------------------------------------
# Fixtures: a couple of harmless test-only templates
# ---------------------------------------------------------------------------


_NOOP_TEMPLATE = StatementTemplate(
    name="_TEST_NOOP_TXN",
    sql="UPDATE `loupe_platform_test.write_locks` SET last_touched_at = CURRENT_TIMESTAMP() WHERE lock_domain = @domain",
    lock_domain="incidents",
)

_SECOND_NOOP_TEMPLATE = StatementTemplate(
    name="_TEST_SECOND_NOOP_TXN",
    sql="UPDATE `loupe_platform_test.incidents` SET status = @status WHERE incident_id = @incident_id",
)

register_template(_NOOP_TEMPLATE)
register_template(_SECOND_NOOP_TEMPLATE)


def _no_sleep(calls: list[float]):
    def _fn(seconds: float) -> None:
        calls.append(seconds)

    return _fn


# ---------------------------------------------------------------------------
# Template allowlisting
# ---------------------------------------------------------------------------


def test_execute_transaction_rejects_unregistered_template_before_touching_client(fake_client):
    with pytest.raises(UnregisteredTemplateError):
        execute_transaction(fake_client, [BoundStatement(template_name="NOT_REGISTERED")])
    assert fake_client.queries == []


def test_registering_the_same_name_twice_with_different_templates_is_rejected():
    register_template(StatementTemplate(name="_TEST_DUP_TXN", sql="SELECT 1"))
    with pytest.raises(ValueError):
        register_template(StatementTemplate(name="_TEST_DUP_TXN", sql="SELECT 2"))


def test_registering_the_same_template_object_twice_is_a_no_op():
    template = StatementTemplate(name="_TEST_REREGISTER_TXN", sql="SELECT 1")
    register_template(template)
    register_template(template)  # must not raise


def test_statement_template_rejects_unknown_lock_domain():
    with pytest.raises(UnknownLockDomainError):
        StatementTemplate(name="_TEST_BAD_DOMAIN_TXN", sql="SELECT 1", lock_domain="not_a_real_domain")


def test_lock_domains_is_a_small_fixed_set():
    assert LOCK_DOMAINS == frozenset({"incidents", "audit_events", "metric_catalog", "schema_baselines"})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_execute_transaction_success_returns_final_statement_result_rows(fake_client):
    # Per the pre-6B-spike correction: execute_transaction() no longer
    # depends on job.child_statement_results() -- per-statement row
    # counts are enforced by ASSERT @@row_count inside the template
    # itself (not exercised by this fake-client test), and the only
    # thing execute_transaction() reads off the job afterward is
    # whatever rows the script's FINAL statement (job.result()) produced.
    fake_client.next_rows = [{"incident_id": "inc_1", "status": "open"}]

    result = execute_transaction(
        fake_client,
        [
            BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"}),
            BoundStatement(
                template_name="_TEST_SECOND_NOOP_TXN",
                params={"status": "open", "incident_id": "inc_1"},
            ),
        ],
    )

    assert result.result_rows == [{"incident_id": "inc_1", "status": "open"}]
    assert result.attempts == 1
    assert len(fake_client.queries) == 1  # one script, submitted as one query() call


def test_execute_transaction_with_no_trailing_select_returns_empty_result_rows(fake_client):
    fake_client.next_rows = []
    result = execute_transaction(fake_client, [BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"})])
    assert result.result_rows == []


def test_execute_transaction_script_contains_begin_and_commit(fake_client):
    execute_transaction(fake_client, [BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"})])
    sql, _ = fake_client.queries[0]
    assert "BEGIN TRANSACTION" in sql
    assert "COMMIT TRANSACTION" in sql


def test_execute_transaction_namespaces_parameters_per_statement(fake_client):
    execute_transaction(
        fake_client,
        [
            BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"}),
            BoundStatement(
                template_name="_TEST_SECOND_NOOP_TXN",
                params={"status": "open", "incident_id": "inc_1"},
            ),
        ],
    )
    _, job_config = fake_client.queries[0]
    param_names = {p.name for p in job_config.query_parameters}
    assert param_names == {"s0_domain", "s1_status", "s1_incident_id"}


# ---------------------------------------------------------------------------
# Parameter binding (Phase 6B correction: empty-array parameters)
# ---------------------------------------------------------------------------


def test_build_job_config_binds_empty_array_as_typed_empty_string_array():
    # Prior behavior raised ValueError("Array parameter ... must not be
    # empty") -- but an incident with no affected_metrics/
    # affected_dashboards yet is a legitimate, common case (Phase 6B's
    # shared/incident_persistence.py CREATE_INCIDENT_TXN hits this on
    # every incident that doesn't yet have known affected metrics), so an
    # empty list must bind cleanly rather than being rejected outright.
    job_config = _build_job_config({"affected_metrics": []})
    (param,) = job_config.query_parameters
    assert param.name == "affected_metrics"
    assert param.array_type == "STRING"
    assert param.values == []


def test_build_job_config_still_infers_element_type_for_non_empty_arrays():
    job_config = _build_job_config({"ids": [1, 2, 3]})
    (param,) = job_config.query_parameters
    assert param.array_type == "INT64"
    assert param.values == [1, 2, 3]


@pytest.mark.parametrize(
    "name",
    [
        "created_at",
        "s0_created_at",
        "s1_timestamp",
        "s2_event_timestamp",
        "s3_acknowledged_at",
        "s4_resolved_at",
        "s5_reviewed_at",
    ],
)
def test_build_job_config_binds_known_temporal_parameters_as_timestamps(name):
    job_config = _build_job_config({name: "2026-07-12T03:30:00+00:00"})
    (param,) = job_config.query_parameters
    assert param.type_ == "TIMESTAMP"


def test_build_job_config_binds_null_optional_timestamp_as_timestamp():
    job_config = _build_job_config({"s0_resolved_at": None})
    (param,) = job_config.query_parameters
    assert param.type_ == "TIMESTAMP"


def test_build_job_config_keeps_ordinary_iso_looking_strings_as_strings():
    job_config = _build_job_config({"change_reason": "2026-07-12T03:30:00+00:00"})
    (param,) = job_config.query_parameters
    assert param.type_ == "STRING"


@pytest.mark.parametrize("name", ["observed_value", "s0_expected_value"])
def test_build_job_config_binds_nullable_measurements_as_float64(name):
    job_config = _build_job_config({name: None})
    (param,) = job_config.query_parameters
    assert param.type_ == "FLOAT64"


@pytest.mark.parametrize("name", ["column_count", "s0_row_version_before"])
def test_build_job_config_binds_nullable_integer_contract_fields_as_int64(name):
    job_config = _build_job_config({name: None})
    (param,) = job_config.query_parameters
    assert param.type_ == "INT64"


# ---------------------------------------------------------------------------
# Retry / concurrency behavior (Phase 6 amendment 1)
# ---------------------------------------------------------------------------


def test_execute_transaction_retries_on_classified_retryable_error_then_succeeds(fake_client):
    fake_client.query_exception_queue = [Aborted("conflicting transaction"), None]
    sleep_calls: list[float] = []

    result = execute_transaction(
        fake_client,
        [BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"})],
        sleep_fn=_no_sleep(sleep_calls),
    )

    assert result.attempts == 2
    assert len(sleep_calls) == 1  # exactly one backoff sleep before the successful retry


def test_execute_transaction_raises_concurrent_modification_after_retry_budget_exhausted(fake_client):
    policy = RetryPolicy(max_retries=2, base_delay_seconds=0.001, max_delay_seconds=0.01)
    fake_client.query_exception_queue = [Aborted("c1"), Aborted("c2"), Aborted("c3")]
    sleep_calls: list[float] = []

    with pytest.raises(ConcurrentModificationError):
        execute_transaction(
            fake_client,
            [BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"})],
            retry_policy=policy,
            sleep_fn=_no_sleep(sleep_calls),
        )

    assert len(sleep_calls) == 2  # retried twice (attempts 0 and 1), then gave up on attempt 2


def test_execute_transaction_treats_deadline_exceeded_as_retryable(fake_client):
    fake_client.query_exception_queue = [DeadlineExceeded("timeout"), None]

    result = execute_transaction(
        fake_client,
        [BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"})],
        sleep_fn=_no_sleep([]),
    )
    assert result.attempts == 2


def test_execute_transaction_does_not_retry_non_retryable_errors(fake_client):
    fake_client.query_exception_queue = [ValueError("this is a real bug, not contention")]
    sleep_calls: list[float] = []

    with pytest.raises(ValueError):
        execute_transaction(
            fake_client,
            [BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"})],
            sleep_fn=_no_sleep(sleep_calls),
        )

    assert sleep_calls == []  # never slept/retried for a non-retryable failure


def test_retry_policy_backoff_never_exceeds_max_delay():
    policy = RetryPolicy(max_retries=10, base_delay_seconds=0.1, max_delay_seconds=1.0)
    for attempt in range(10):
        for _ in range(20):  # jitter is random; sample repeatedly
            delay = policy.delay_for_attempt(attempt)
            assert 0 <= delay <= 1.0


# ---------------------------------------------------------------------------
# PayloadConflictError exists and carries a safe, non-value-leaking message
# ---------------------------------------------------------------------------


def test_payload_conflict_error_message_never_forced_to_include_values():
    # This test documents the contract callers (Phase 6B/6C) must follow
    # when raising PayloadConflictError: the message names the id and
    # which fields differed, never the actual conflicting values.
    err = PayloadConflictError("id='inc_1' conflicts on fields: severity, status (values withheld)")
    assert "inc_1" in str(err)
    assert "severity" in str(err)


# ---------------------------------------------------------------------------
# result_sql: rendered AFTER COMMIT TRANSACTION, not before it (Phase 6B
# spike correction -- step 9 originally returned an empty result_rows
# despite the transaction committing successfully, because its trailing
# SELECT was embedded in `sql` and therefore rendered BEFORE COMMIT).
# ---------------------------------------------------------------------------

_RESULT_SQL_TEMPLATE = StatementTemplate(
    name="_TEST_RESULT_SQL_TXN",
    sql="UPDATE `loupe_platform_test.write_locks` SET last_touched_at = CURRENT_TIMESTAMP() WHERE lock_domain = @domain;"
    " ASSERT @@row_count = 1 AS 'expected exactly one lock row updated';",
    lock_domain="incidents",
    result_sql="SELECT @who AS touched_by;",
)
register_template(_RESULT_SQL_TEMPLATE)

_SECOND_RESULT_SQL_TEMPLATE = StatementTemplate(
    name="_TEST_SECOND_RESULT_SQL_TXN",
    sql="UPDATE `loupe_platform_test.incidents` SET status = @status WHERE incident_id = @incident_id;",
    result_sql="SELECT @status AS new_status;",
)
register_template(_SECOND_RESULT_SQL_TEMPLATE)


def test_render_script_places_result_sql_after_commit_transaction():
    script, params = _render_script(
        [BoundStatement(template_name="_TEST_RESULT_SQL_TXN", params={"domain": "incidents", "who": "tester"})]
    )
    commit_index = script.index("COMMIT TRANSACTION;")
    select_index = script.index("SELECT @s0_who AS touched_by;")
    assert commit_index < select_index, (
        "result_sql must be rendered AFTER COMMIT TRANSACTION -- a SELECT "
        "before COMMIT is not the script's final statement, so "
        "job.result() would not return its rows (confirmed against real "
        "BigQuery by the Phase 6B spike's step 9)."
    )
    assert params == {"s0_domain": "incidents", "s0_who": "tester"}


def test_render_script_result_sql_appears_before_end():
    script, _ = _render_script(
        [BoundStatement(template_name="_TEST_RESULT_SQL_TXN", params={"domain": "incidents", "who": "tester"})]
    )
    select_index = script.index("SELECT @s0_who AS touched_by;")
    end_index = script.rindex("END;")
    assert select_index < end_index


def test_render_script_with_no_result_sql_has_no_statement_after_commit_besides_end():
    script, _ = _render_script([BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"})])
    after_commit = script.split("COMMIT TRANSACTION;", 1)[1].strip()
    assert after_commit == "END;"


def test_render_script_rejects_two_statements_both_declaring_result_sql():
    with pytest.raises(ValueError, match="At most one statement"):
        _render_script(
            [
                BoundStatement(
                    template_name="_TEST_RESULT_SQL_TXN", params={"domain": "incidents", "who": "tester"}
                ),
                BoundStatement(
                    template_name="_TEST_SECOND_RESULT_SQL_TXN",
                    params={"status": "open", "incident_id": "inc_1"},
                ),
            ]
        )


def test_execute_transaction_with_result_sql_returns_its_rows(fake_client):
    fake_client.next_rows = [{"touched_by": "tester"}]
    result = execute_transaction(
        fake_client,
        [BoundStatement(template_name="_TEST_RESULT_SQL_TXN", params={"domain": "incidents", "who": "tester"})],
    )
    assert result.result_rows == [{"touched_by": "tester"}]


# ---------------------------------------------------------------------------
# bigquery_error_diagnostics(): sanitized, structured error summary --
# never the raw exception message.
# ---------------------------------------------------------------------------


class _FakeBadRequest(Exception):
    """Stand-in for google.api_core.exceptions.BadRequest, whose real
    shape carries a `.errors` list (from the REST error response) and a
    `.code`. Named distinctly (not literally `BadRequest`) for these
    diagnostics-extraction tests so they don't depend on the
    _is_retryable()-specific type-name check exercised separately below.
    """

    def __init__(self, message: str, errors: list[dict] | None = None, code: int | None = None):
        super().__init__(message)
        self.errors = errors or []
        self.code = code


def test_bigquery_error_diagnostics_extracts_reason_codes_and_status():
    exc = _FakeBadRequest(
        "this raw message must never appear in the returned diagnostics",
        errors=[{"reason": "invalidQuery", "message": "some internal detail"}],
        code=400,
    )
    diagnostics = bigquery_error_diagnostics(exc)
    assert diagnostics["exception_type"] == "_FakeBadRequest"
    assert diagnostics["reason_codes"] == ["invalidQuery"]
    assert diagnostics["http_status"] == 400


def test_bigquery_error_diagnostics_never_includes_the_raw_message():
    secret_marker = "TABLE_NAME_OR_OTHER_IDENTIFIER_THAT_MUST_NOT_LEAK"
    exc = _FakeBadRequest(secret_marker, errors=[{"reason": "invalidQuery", "message": secret_marker}])
    diagnostics = bigquery_error_diagnostics(exc)
    assert secret_marker not in str(diagnostics)


def test_bigquery_error_diagnostics_handles_missing_errors_attribute_gracefully():
    diagnostics = bigquery_error_diagnostics(ValueError("plain exception, no .errors at all"))
    assert diagnostics["reason_codes"] == []
    assert diagnostics["http_status"] is None
    assert diagnostics["concurrent_update_signature_matched"] is False


def test_bigquery_error_diagnostics_deduplicates_and_sorts_reason_codes():
    exc = _FakeBadRequest(
        "msg", errors=[{"reason": "duplicate"}, {"reason": "invalidQuery"}, {"reason": "duplicate"}]
    )
    diagnostics = bigquery_error_diagnostics(exc)
    assert diagnostics["reason_codes"] == ["duplicate", "invalidQuery"]


def test_bigquery_error_diagnostics_reports_concurrent_update_signature_as_a_boolean_only():
    exc = _FakeBadRequest(
        "Transaction is aborted due to concurrent update against table `proj.ds.spike_x_lock_rows`",
        errors=[{"reason": "invalidQuery"}],
        code=400,
    )
    diagnostics = bigquery_error_diagnostics(exc)
    assert diagnostics["concurrent_update_signature_matched"] is True
    # The table identifier embedded in the raw message must never leak
    # into the sanitized diagnostics dict, even though it triggered a
    # True classification.
    assert "spike_x_lock_rows" not in str(diagnostics)


# ---------------------------------------------------------------------------
# _is_retryable(): BadRequest is retryable ONLY when HTTP status is 400,
# reason includes "invalidQuery", AND the sanitized concurrent-update
# message signature matched -- never on type name or reason code alone.
# Confirmed necessary by the second Phase 6B live spike run
# (2026-07-12, run_id=ad466ad893): the deliberately-failing ASSERT and
# the genuine concurrent lock-row conflicts BOTH raised BadRequest with
# reason "invalidQuery" -- reason code alone cannot tell them apart.
# ---------------------------------------------------------------------------


class BadRequest(Exception):
    """Named exactly `BadRequest` so type(exc).__name__ matches what
    _is_retryable() keys off of for real google.api_core.exceptions.BadRequest."""

    def __init__(self, message: str = "", errors: list[dict] | None = None, code: int | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or []
        self.code = code


_CONCURRENT_UPDATE_MESSAGE = "Transaction is aborted due to concurrent update against table `proj.ds.spike_x_lock_rows`"


def test_bad_request_matching_concurrent_update_signature_is_retryable():
    exc = BadRequest(_CONCURRENT_UPDATE_MESSAGE, errors=[{"reason": "invalidQuery"}], code=400)
    assert _is_retryable(exc) is True


def test_bad_request_syntax_error_is_not_retryable():
    # e.g. a genuine typo in a template's SQL -- same type and reason
    # code as a real conflict, but no concurrent-update signature.
    exc = BadRequest("Syntax error: Unexpected keyword WHERE at [10:7]", errors=[{"reason": "invalidQuery"}], code=400)
    assert _is_retryable(exc) is False


def test_bad_request_failed_assert_is_not_retryable():
    # e.g. a template's own invariant failing -- same type and reason
    # code as a real conflict, but no concurrent-update signature.
    exc = BadRequest(
        "Assertion failed: expected exactly one lock row updated", errors=[{"reason": "invalidQuery"}], code=400
    )
    assert _is_retryable(exc) is False


def test_bad_request_with_no_reason_codes_is_not_retryable():
    assert _is_retryable(BadRequest("generic 400, no structured errors", code=400)) is False


def test_bad_request_with_matching_message_but_wrong_reason_code_is_not_retryable():
    # All three conditions must hold; the reason-code check is not
    # bypassable just because the message happens to contain the phrase.
    exc = BadRequest(_CONCURRENT_UPDATE_MESSAGE, errors=[{"reason": "somethingElse"}], code=400)
    assert _is_retryable(exc) is False


def test_bad_request_with_matching_message_and_reason_but_wrong_http_status_is_not_retryable():
    exc = BadRequest(_CONCURRENT_UPDATE_MESSAGE, errors=[{"reason": "invalidQuery"}], code=409)
    assert _is_retryable(exc) is False


def test_is_retryable_still_honors_the_unconditional_type_name_allowlist():
    assert _is_retryable(Aborted("conflicting transaction")) is True
    assert _is_retryable(DeadlineExceeded("timeout")) is True


def test_is_retryable_rejects_unrelated_exception_types():
    assert _is_retryable(ValueError("a real bug")) is False
    assert _is_retryable(RuntimeError("also a real bug")) is False


# ---------------------------------------------------------------------------
# Retry-budget exhaustion, exercised through the real classification path
# (a BadRequest carrying the confirmed concurrent-update signature), not
# just the pre-existing Aborted/DeadlineExceeded stand-ins above.
# ---------------------------------------------------------------------------


def test_execute_transaction_retries_a_concurrent_update_bad_request_then_succeeds(fake_client):
    fake_client.query_exception_queue = [BadRequest(_CONCURRENT_UPDATE_MESSAGE, errors=[{"reason": "invalidQuery"}], code=400), None]
    sleep_calls: list[float] = []

    result = execute_transaction(
        fake_client,
        [BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"})],
        sleep_fn=_no_sleep(sleep_calls),
    )

    assert result.attempts == 2
    assert len(sleep_calls) == 1


def test_execute_transaction_exhausts_retry_budget_on_sustained_concurrent_update_conflict(fake_client):
    policy = RetryPolicy(max_retries=2, base_delay_seconds=0.001, max_delay_seconds=0.01)
    conflict = lambda: BadRequest(_CONCURRENT_UPDATE_MESSAGE, errors=[{"reason": "invalidQuery"}], code=400)
    fake_client.query_exception_queue = [conflict(), conflict(), conflict()]

    with pytest.raises(ConcurrentModificationError):
        execute_transaction(
            fake_client,
            [BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"})],
            retry_policy=policy,
            sleep_fn=_no_sleep([]),
        )


def test_execute_transaction_does_not_retry_a_syntax_error_shaped_bad_request(fake_client):
    fake_client.query_exception_queue = [
        BadRequest("Syntax error: Unexpected keyword WHERE at [10:7]", errors=[{"reason": "invalidQuery"}], code=400)
    ]
    sleep_calls: list[float] = []

    with pytest.raises(BadRequest):
        execute_transaction(
            fake_client,
            [BoundStatement(template_name="_TEST_NOOP_TXN", params={"domain": "incidents"})],
            sleep_fn=_no_sleep(sleep_calls),
        )

    assert sleep_calls == []  # never retried a genuine SQL bug
