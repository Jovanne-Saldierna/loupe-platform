"""Tests for shared/config.py.

Per Phase 6 amendment 8, shared/config.py must remain framework-
independent -- these tests import nothing from streamlit and never
construct a Streamlit context, proving the module works standalone.
"""

from __future__ import annotations

import pytest

from shared.config import (
    ConfigError,
    PlatformConfig,
    UnexpectedDatasetTargetError,
    assert_sql_targets_dataset,
    load_platform_config,
    validate_persistence_config,
)
from tests.shared.conftest import FakeDataset


def test_load_platform_config_requires_a_project():
    with pytest.raises(ConfigError):
        load_platform_config({})


def test_load_platform_config_prefers_explicit_loupe_bq_project():
    config = load_platform_config(
        {"LOUPE_BQ_PROJECT": "explicit-project", "GOOGLE_CLOUD_PROJECT": "fallback-project"}
    )
    assert config.project == "explicit-project"


def test_load_platform_config_falls_back_to_google_cloud_project():
    config = load_platform_config({"GOOGLE_CLOUD_PROJECT": "my-project"})
    assert config.project == "my-project"


def test_load_platform_config_defaults_dataset_and_location():
    config = load_platform_config({"GOOGLE_CLOUD_PROJECT": "my-project"})
    assert config.dataset == "loupe_platform"
    assert config.location == "US"


def test_load_platform_config_honors_overrides():
    config = load_platform_config(
        {
            "GOOGLE_CLOUD_PROJECT": "my-project",
            "LOUPE_DATASET": "loupe_platform_test",
            "LOUPE_BQ_LOCATION": "US",
        }
    )
    assert config.dataset == "loupe_platform_test"


def test_platform_config_table_properties_are_fully_qualified():
    config = PlatformConfig(project="p", dataset="loupe_platform")
    assert config.metric_catalog_table == "loupe_platform.metric_catalog"
    assert config.audit_events_table == "loupe_platform.audit_events"
    assert config.write_locks_table == "loupe_platform.write_locks"


def test_all_tables_enumerates_exactly_nine_tables():
    config = PlatformConfig(project="p")
    tables = config.all_tables()
    assert len(tables) == 9
    assert set(tables) == {
        "metric_catalog",
        "metric_versions",
        "incidents",
        "incident_transitions",
        "audit_events",
        "schema_snapshots",
        "schema_baselines",
        "schema_migrations",
        "write_locks",
    }


# ---------------------------------------------------------------------------
# validate_persistence_config()
# ---------------------------------------------------------------------------


def test_validate_persistence_config_ok_when_location_matches(fake_client):
    config = PlatformConfig(project="p", dataset="loupe_platform_test", location="US")
    fake_client.datasets["p.loupe_platform_test"] = FakeDataset(location="US")

    result = validate_persistence_config(fake_client, config)

    assert result.ok is True
    assert result.actual_location == "US"
    assert result.safe_error is None


def test_validate_persistence_config_fails_on_location_mismatch(fake_client):
    config = PlatformConfig(project="p", dataset="loupe_platform_test", location="US")
    fake_client.datasets["p.loupe_platform_test"] = FakeDataset(location="EU")

    result = validate_persistence_config(fake_client, config)

    assert result.ok is False
    assert result.actual_location == "EU"
    assert "US" in result.safe_error
    assert "EU" in result.safe_error


def test_validate_persistence_config_fails_honestly_when_dataset_missing(fake_client):
    config = PlatformConfig(project="p", dataset="loupe_platform_test", location="US")
    fake_client.get_dataset_exception = Exception("boom: internal detail nobody should see")

    result = validate_persistence_config(fake_client, config)

    assert result.ok is False
    assert "boom" not in result.safe_error
    assert "internal detail" not in result.safe_error


def test_validate_persistence_config_never_raises(fake_client):
    # Even on total failure, this returns a value object rather than
    # propagating an exception -- callers (apps' honest-unavailable
    # rendering paths) must never need a try/except around this call.
    config = PlatformConfig(project="p", dataset="does_not_exist")
    result = validate_persistence_config(fake_client, config)
    assert result.ok is False


# ---------------------------------------------------------------------------
# assert_sql_targets_dataset() -- Phase 6E correction 2's query-target
# assertion: the structural backstop against "no write target may
# silently fall back to loupe_platform."
# ---------------------------------------------------------------------------


def test_assert_sql_targets_dataset_accepts_sql_that_only_references_the_expected_dataset():
    sql = "UPDATE `loupe_platform_test.write_locks` SET x = 1 WHERE lock_domain = 'incidents';"
    assert_sql_targets_dataset(sql, "loupe_platform_test")  # no exception


def test_assert_sql_targets_dataset_accepts_sql_with_no_qualified_identifiers():
    assert_sql_targets_dataset("SELECT 1", "loupe_platform_test")  # nothing to check, trivially ok


def test_assert_sql_targets_dataset_rejects_a_reference_to_a_different_dataset():
    sql = "UPDATE `loupe_platform.write_locks` SET x = 1 WHERE lock_domain = 'incidents';"
    with pytest.raises(UnexpectedDatasetTargetError) as excinfo:
        assert_sql_targets_dataset(sql, "loupe_platform_test")
    assert "loupe_platform" in str(excinfo.value)


def test_assert_sql_targets_dataset_rejects_mixed_references():
    sql = (
        "UPDATE `loupe_platform_test.write_locks` SET x = 1 WHERE lock_domain = 'incidents';\n"
        "INSERT INTO `loupe_platform.incidents` (incident_id) VALUES (@incident_id);"
    )
    with pytest.raises(UnexpectedDatasetTargetError):
        assert_sql_targets_dataset(sql, "loupe_platform_test")


def test_assert_sql_targets_dataset_never_requires_a_specific_dataset_to_be_mentioned():
    # A statement that references only the expected dataset (or none at
    # all) is fine even if it doesn't reference every table this caller
    # might care about -- this is a "never wrong," not a "must mention
    # everything," check.
    assert_sql_targets_dataset("SELECT * FROM `loupe_platform_test.incidents`", "loupe_platform_test")
