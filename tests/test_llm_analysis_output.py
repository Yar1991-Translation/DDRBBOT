from __future__ import annotations

from ddrbbot.models import LLMAnalysisOutput


def test_llm_analysis_output_coerces_invalid_category() -> None:
    d = LLMAnalysisOutput.model_validate({"title": "T", "category": "not-a-real-cat"})
    assert d.category == "announcement"


def test_llm_analysis_output_coerces_invalid_credibility() -> None:
    d = LLMAnalysisOutput.model_validate({"source_credibility": "bogus"})
    assert d.source_credibility == "unverified"


def test_llm_analysis_output_ignores_extra_keys() -> None:
    d = LLMAnalysisOutput.model_validate({"title": "OK", "extra_field": 1})
    assert d.title == "OK"
