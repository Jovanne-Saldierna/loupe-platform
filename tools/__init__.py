"""Operator-run diagnostic/administrative tooling for the loupe-platform
monorepo, distinct from apps/ (Streamlit delivery) and shared/ (the
persistence and query layer they're built on).

Added alongside the Phase 6B packaging fix: `tools/phase6b_spike/`
already had its own `__init__.py`, but `tools/` itself did not -- which
meant it worked at runtime via `python -m tools.phase6b_spike...` (any
directory on sys.path can be an implicit namespace package) but was not
reliably discoverable as a real package by `pip install -e .`'s explicit
setuptools `packages.find` configuration (see pyproject.toml). This file
is never imported by shared/, apps/, or tests/.
"""
