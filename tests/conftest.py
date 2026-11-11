"""Existing analysis tests describe the model-reads-everything pipeline, so they run in
"llm" mode.  Hybrid / local behaviour is tested explicitly in tests/test_hybrid.py."""
import pytest


@pytest.fixture(autouse=True)
def _llm_mode_by_default(monkeypatch):
    from app import analysis
    monkeypatch.setattr(analysis, "ANALYSIS_MODE", "llm")
