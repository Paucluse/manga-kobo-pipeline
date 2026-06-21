"""Tests for LLM metadata normalization helpers."""

from pathlib import Path

from manga_pipeline.config import MetadataConfig
from manga_pipeline.llm_metadata import _parse_llm_json, _read_api_key


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


def test_read_api_key_prefers_file(tmp_path: Path, monkeypatch: object) -> None:
    key_file = tmp_path / "gemini_api_key"
    key_file.write_text("file-key\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")  # type: ignore[attr-defined]

    cfg = MetadataConfig(
        llm_api_key_file=key_file,
        llm_api_key_env="GEMINI_API_KEY",
    )

    assert _read_api_key(cfg) == "file-key"


def test_read_api_key_falls_back_to_env(monkeypatch: object) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")  # type: ignore[attr-defined]

    cfg = MetadataConfig(
        llm_api_key_file=Path("/missing/gemini_api_key"),
        llm_api_key_env="GEMINI_API_KEY",
    )

    assert _read_api_key(cfg) == "env-key"
