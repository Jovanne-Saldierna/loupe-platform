"""Credential-free, install-free packaging validation.

Per the Phase 6B correction: `pyproject.toml` previously had no
`[build-system]` table and no `[tool.setuptools.packages.find]` table, so
`pip install -e .`'s package discovery in this flat-layout, multi-package
(apps/, shared/, tools/) repo was undocumented/unreliable, and `tools/`
itself was missing the top-level `__init__.py` a classic (non-namespace)
setuptools `find:` requires.

This test does NOT perform a real `pip install -e .` (that would touch
the environment's installed packages as a side effect of running the test
suite, which is exactly the kind of hidden side effect this repo's tests
avoid elsewhere -- see docs/development.md). Instead it drives
`setuptools.find_packages()` directly, with the exact `include`/`exclude`
arguments `pyproject.toml` declares, against the real repository tree --
proving discovery actually resolves to the right package set without
needing a real install, a virtualenv, or network access.
"""

from __future__ import annotations

from pathlib import Path

import pytest

setuptools = pytest.importorskip("setuptools", reason="setuptools is a declared build-system requirement")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_pyproject_text() -> str:
    return (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# pyproject.toml declares the expected sections (text-level check -- avoids
# a hard dependency on tomllib, which is 3.11+ only, in a project that
# declares requires-python = ">=3.10")
# ---------------------------------------------------------------------------


def test_pyproject_declares_a_setuptools_build_backend():
    text = _read_pyproject_text()
    assert "[build-system]" in text
    assert "setuptools" in text
    assert 'build-backend = "setuptools.build_meta"' in text


def test_pyproject_declares_explicit_package_discovery():
    text = _read_pyproject_text()
    assert "[tool.setuptools.packages.find]" in text
    assert 'include = ["api*", "apps*", "shared*", "tools*"]' in text
    assert 'exclude = ["tests*"]' in text


# ---------------------------------------------------------------------------
# The discovery configuration actually resolves correctly against the real
# repository tree -- this is what would go wrong silently if the include/
# exclude patterns were subtly off (e.g. "app*" instead of "apps*", or a
# missing "tests*" exclude letting test code ship as an installable
# package).
# ---------------------------------------------------------------------------


def _discovered_packages() -> set[str]:
    return set(
        setuptools.find_packages(
            where=str(REPO_ROOT),
            include=["api*", "apps*", "shared*", "tools*"],
            exclude=["tests*"],
        )
    )


def test_discovery_finds_all_three_top_level_packages():
    discovered = _discovered_packages()
    assert "apps" in discovered
    assert "shared" in discovered
    assert "tools" in discovered


def test_discovery_finds_expected_subpackages():
    discovered = _discovered_packages()
    assert "apps.loupe_agent" in discovered
    assert "apps.metric_governance" in discovered
    assert "apps.data_quality_triage" in discovered
    assert "tools.phase6b_spike" in discovered


def test_discovery_never_includes_tests():
    discovered = _discovered_packages()
    assert "tests" not in discovered
    assert not any(name == "tests" or name.startswith("tests.") for name in discovered)


def test_tools_package_has_its_own_init_file():
    # A classic (non-namespace) setuptools `find:` requires __init__.py
    # at every package level being discovered, not just at leaf packages
    # -- tools/phase6b_spike/__init__.py alone is not sufficient for
    # `tools` itself to be discovered as a real package.
    assert (REPO_ROOT / "tools" / "__init__.py").is_file()


def test_every_discovered_package_directory_has_an_init_file():
    # Belt-and-suspenders: confirm setuptools' own discovery result is
    # internally consistent with the classic package contract it's
    # supposed to be enforcing.
    for package in _discovered_packages():
        package_dir = REPO_ROOT / Path(*package.split("."))
        assert (package_dir / "__init__.py").is_file(), (
            f"{package!r} was discovered as a package but "
            f"{package_dir / '__init__.py'} does not exist"
        )
