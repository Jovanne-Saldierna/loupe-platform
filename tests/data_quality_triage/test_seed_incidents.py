from datetime import datetime, timezone

from apps.data_quality_triage import seed_incidents


def test_seed_row_if_needed_returns_rows_unchanged_when_not_empty():
    rows = [{"incident_id": "inc-1", "table_id": "order_items"}]
    assert seed_incidents.seed_row_if_needed(rows) is rows


def test_seed_row_if_needed_returns_one_realistic_row_when_empty():
    fixed_now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    result = seed_incidents.seed_row_if_needed([], now=fixed_now)
    assert len(result) == 1
    row = result[0]
    assert row["table_id"] == "order_items"
    # Matches the real check taxonomy in apps/data_quality_triage/checks.py
    # (check_stale_freshness), not an invented one.
    assert row["check_type"] == "freshness_delay"
    assert row["severity"] == "high"
    assert row["status"] == "open"
    assert row["observed_value"] > row["expected_value"]
    assert row["expected_value"] == 2880.0  # checks.py STALE_AFTER_MINUTES
    assert row["affected_metrics"] == ["revenue", "margin"]
    assert row["owner"] is None
    assert row["created_at"] == fixed_now.isoformat()
    assert row["_seeded"] is True


def test_seed_row_if_needed_defaults_created_at_to_now_when_not_supplied():
    before = datetime.now(timezone.utc)
    row = seed_incidents.seed_row_if_needed([])[0]
    after = datetime.now(timezone.utc)
    created_at = datetime.fromisoformat(row["created_at"])
    assert before <= created_at <= after
