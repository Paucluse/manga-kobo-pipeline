"""Tests for LLM metadata normalization helpers."""

from manga_pipeline.llm_metadata import _parse_llm_json


def test_parse_llm_json() -> None:
    metadata = _parse_llm_json(
        '{"title":"蒼藍鋼鐵戰艦","author":"ArkPerformance","publisher":"長鴻出版社","volume":"1","confidence":0.9}'
    )

    assert metadata is not None
    assert metadata.title == "蒼藍鋼鐵戰艦"
    assert metadata.author == "ArkPerformance"
    assert metadata.publisher == "長鴻出版社"
    assert metadata.volume == "1"
    assert metadata.confidence == 0.9


def test_parse_invalid_llm_json_returns_none() -> None:
    assert _parse_llm_json("not json") is None
