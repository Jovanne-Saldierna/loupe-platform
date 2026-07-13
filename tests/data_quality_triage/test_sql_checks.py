from apps.data_quality_triage.sql_checks import suggested_debugging_steps, suggested_sql_checks


def test_duplicate_check_type_returns_duplicate_key_sql():
    checks = suggested_sql_checks("duplicate_key_ratio", "order_items")
    assert len(checks) == 1
    assert "duplicate" in checks[0].title.lower()
    assert "HAVING COUNT(*) > 1" in checks[0].sql
    assert "`bigquery-public-data.thelook_ecommerce.order_items`" in checks[0].sql
    assert checks[0].sql.startswith("-- Suggested debugging SQL (not executed automatically)")


def test_null_check_type_returns_null_rate_sql():
    checks = suggested_sql_checks("null_ratio", "order_items")
    assert "null_rate" in checks[0].sql
    assert "COUNTIF" in checks[0].sql


def test_freshness_check_type_returns_a_multi_step_investigation_workflow():
    checks = suggested_sql_checks("freshness_delay", "order_items")
    # Requirement: at minimum (1) latest timestamp vs threshold, (2) row
    # counts by day/hour over 7-14 days, (3) inspect latest records.
    assert len(checks) >= 3
    assert "minutes_since_latest_row" in checks[0].sql
    joined_sql = "\n".join(c.sql for c in checks)
    joined_titles = " ".join(c.title.lower() for c in checks)
    assert "row_count" in joined_sql
    assert "day" in joined_titles or "hour" in joined_titles
    assert "latest records" in joined_titles or "inspect" in joined_titles
    assert all(c.purpose for c in checks)


def test_row_count_check_type_returns_row_count_sql():
    checks = suggested_sql_checks("row_count_empty", "order_items")
    assert "row_count" in checks[0].sql


def test_volume_drift_matches_row_count_family():
    checks = suggested_sql_checks("volume_drift", "order_items")
    assert "row_count" in checks[0].sql


def test_schema_drift_check_type_returns_information_schema_sql():
    checks = suggested_sql_checks("schema_drift", "order_items")
    assert "INFORMATION_SCHEMA.COLUMNS" in checks[0].sql
    assert "order_items" in checks[0].sql


def test_unknown_check_type_falls_back_to_generic_spot_check():
    checks = suggested_sql_checks("some_future_check_type", "order_items")
    assert "Spot-check" in checks[0].title
    assert "LIMIT 100" in checks[0].sql


def test_qualified_table_id_is_not_double_qualified():
    checks = suggested_sql_checks("row_count_empty", "thelook_ecommerce.order_items")
    assert "`thelook_ecommerce.order_items`" in checks[0].sql
    assert "bigquery-public-data.thelook_ecommerce.thelook_ecommerce" not in checks[0].sql


def test_debugging_steps_agree_with_sql_check_family():
    steps = suggested_debugging_steps("duplicate_key_ratio", "order_items")
    assert any("duplicate" in s.lower() for s in steps)
    steps = suggested_debugging_steps("null_ratio", "order_items")
    assert any("null" in s.lower() for s in steps)
    steps = suggested_debugging_steps("freshness_delay", "order_items")
    assert any("load" in s.lower() or "pipeline" in s.lower() for s in steps)


def test_debugging_steps_never_empty_even_for_unknown_check_type():
    steps = suggested_debugging_steps("totally_unrecognized", "order_items")
    assert len(steps) == 3
    assert all(isinstance(s, str) and s for s in steps)


def test_every_suggested_check_across_every_family_has_a_purpose():
    for check_type in ["duplicate_key_ratio", "null_ratio", "freshness_delay", "row_count_empty", "schema_drift", "unrecognized"]:
        for check in suggested_sql_checks(check_type, "order_items"):
            assert isinstance(check.purpose, str) and check.purpose.strip()
