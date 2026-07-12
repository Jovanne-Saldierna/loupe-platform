"""Import smoke test for all three app entry points.

Proves the declared dependencies in pyproject.toml are sufficient to
import every app's main.py (and therefore its full module graph --
chat.py's lazy langchain-anthropic import included, since chat.py itself
imports cleanly even though ChatAnthropic is only constructed inside a
function call) without a live BigQuery project, Streamlit runtime, or
Anthropic API key configured. This is the regression test for "the
application is reproducible from pyproject.toml" -- if a required package
is missing from [project.dependencies], this test fails with
ModuleNotFoundError before any other test would.
"""

from __future__ import annotations

import importlib


def test_metric_governance_main_imports_cleanly():
    importlib.import_module("apps.metric_governance.main")


def test_data_quality_triage_main_imports_cleanly():
    importlib.import_module("apps.data_quality_triage.main")


def test_loupe_agent_main_imports_cleanly():
    importlib.import_module("apps.loupe_agent.main")


def test_loupe_agent_chat_module_imports_without_requiring_a_live_api_key(monkeypatch):
    # chat.py must be importable even with no ANTHROPIC_API_KEY configured
    # -- the ChatAnthropic/langchain-core imports are deferred to inside
    # _model(), never executed at module import time.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    module = importlib.import_module("apps.loupe_agent.chat")
    assert hasattr(module, "run_agent")


def test_langchain_anthropic_is_importable_where_chat_py_expects_it():
    # Confirms the langchain-anthropic/langchain-core dependency versions
    # declared in pyproject.toml actually satisfy the deferred imports
    # inside chat.py::_model()/_prompt(), without requiring a live model
    # call or API key.
    from langchain_anthropic import ChatAnthropic  # noqa: F401
    from langchain_core.prompts import ChatPromptTemplate  # noqa: F401
