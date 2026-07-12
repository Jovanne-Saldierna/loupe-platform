"""Tests for shared/config.py's Phase 6D additions: load_persistence_mode()
and PlatformConfig.strict_separation_of_duties."""

from __future__ import annotations

import pytest

from shared.config import ConfigError, load_persistence_mode, load_platform_config


def test_load_persistence_mode_defaults_to_constants():
    assert load_persistence_mode({}) == "constants"


def test_load_persistence_mode_reads_persisted_explicitly():
    assert load_persistence_mode({"LOUPE_PERSISTENCE_MODE": "persisted"}) == "persisted"


def test_load_persistence_mode_is_case_insensitive():
    assert load_persistence_mode({"LOUPE_PERSISTENCE_MODE": "PERSISTED"}) == "persisted"


def test_load_persistence_mode_rejects_unrecognized_value():
    with pytest.raises(ConfigError):
        load_persistence_mode({"LOUPE_PERSISTENCE_MODE": "sample_data"})


def test_load_platform_config_defaults_strict_separation_of_duties_to_false():
    config = load_platform_config({"GOOGLE_CLOUD_PROJECT": "proj"})
    assert config.strict_separation_of_duties is False


def test_load_platform_config_reads_strict_separation_of_duties_true():
    config = load_platform_config({"GOOGLE_CLOUD_PROJECT": "proj", "LOUPE_STRICT_SEPARATION_OF_DUTIES": "true"})
    assert config.strict_separation_of_duties is True


def test_load_platform_config_treats_unrecognized_strict_value_as_false():
    config = load_platform_config({"GOOGLE_CLOUD_PROJECT": "proj", "LOUPE_STRICT_SEPARATION_OF_DUTIES": "nope"})
    assert config.strict_separation_of_duties is False
