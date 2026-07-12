"""Credential-free tests for tools/phase6e_ops/bootstrap_test_dataset.py.

Only exercises the safety-gate and dry-run paths -- these never touch
BigQuery, never import google.cloud.bigquery, and never require
credentials. The --yes execution path is deliberately NOT covered here
(it requires a real, authenticated client) -- see
docs/persistence.md's "Live integration command" section for how an
operator actually runs this.
"""

from __future__ import annotations

import pytest

from tools.phase6e_ops.bootstrap_test_dataset import main
from tools.phase6e_ops.safety import UnsafeTargetError, require_safe_test_dataset


def test_dry_run_prints_the_plan_and_returns_zero_without_yes(capsys):
    exit_code = main(
        [
            "--project", "ai-weekend-agent-501502",
            "--dataset", "loupe_platform_test",
            "--location", "US",
            "--actor", "test-operator",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Dry run only" in out
    assert "never certifying" in out


def test_refuses_the_real_production_dataset_name(capsys):
    exit_code = main(
        [
            "--project", "ai-weekend-agent-501502",
            "--dataset", "loupe_platform",
            "--location", "US",
            "--actor", "test-operator",
            "--yes",
        ]
    )
    assert exit_code == 2
    assert "must never be" in capsys.readouterr().out


def test_refuses_a_dataset_name_without_test_in_it(capsys):
    exit_code = main(
        [
            "--project", "ai-weekend-agent-501502",
            "--dataset", "loupe_platform_staging",
            "--location", "US",
            "--actor", "test-operator",
            "--yes",
        ]
    )
    assert exit_code == 2
    assert "does not contain 'test'" in capsys.readouterr().out


def test_refuses_a_non_us_location(capsys):
    exit_code = main(
        [
            "--project", "ai-weekend-agent-501502",
            "--dataset", "loupe_platform_test",
            "--location", "EU",
            "--actor", "test-operator",
            "--yes",
        ]
    )
    assert exit_code == 2
    assert "--location must be" in capsys.readouterr().out


def test_refuses_an_unsafe_project_id(capsys):
    exit_code = main(
        [
            "--project", "'; DROP TABLE incidents; --",
            "--dataset", "loupe_platform_test",
            "--location", "US",
            "--actor", "test-operator",
            "--yes",
        ]
    )
    assert exit_code == 2


def test_the_safety_gate_itself_accepts_the_documented_target():
    # No exception raised == accepted.
    require_safe_test_dataset("ai-weekend-agent-501502", "loupe_platform_test", "US")


def test_the_safety_gate_rejects_loupe_platform_directly():
    with pytest.raises(UnsafeTargetError):
        require_safe_test_dataset("ai-weekend-agent-501502", "loupe_platform", "US")
