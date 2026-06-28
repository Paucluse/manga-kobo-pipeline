"""Tests for LLM metadata normalization helpers."""

import json
from pathlib import Path

import requests

from manga_pipeline.config import MetadataConfig
from manga_pipeline.filename_parser import ParseResult
from manga_pipeline.llm_metadata import (
    _parse_llm_json,
    _post_chat_completion,
    _read_api_key,
    normalize_with_llm,
)

FW_DOT = "\N{FULLWIDTH FULL STOP}"
GUNDAM_TW_TITLE = f"機動戰士鋼彈 光輝的阿{FW_DOT}巴瓦{FW_DOT}空"
GUNDAM_JP_TITLE = "機動戦士ガンダム 光芒のア・バオア・クー"


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


def test_parse_llm_json_accepts_prompt_console_schema() -> None:
    metadata = _parse_llm_json(
        json.dumps(
            {
                "clean_title": GUNDAM_TW_TITLE,
                "volume_number": 1,
                "titles": {
                    "traditional_chinese": GUNDAM_TW_TITLE,
                    "japanese": GUNDAM_JP_TITLE,
                    "romaji": "Kidou Senshi Gundam: Koubou no A Baoa Qu",
                    "aliases": [],
                },
                "authors": ["Ark Performance"],
                "publisher_hints": ["角川"],
                "scraping_queries": {
                    "bookwalker_tw": [GUNDAM_TW_TITLE],
                    "bookwalker_jp": [GUNDAM_JP_TITLE],
                    "bangumi": [GUNDAM_JP_TITLE],
                },
                "confidence": 0.95,
            },
            ensure_ascii=False,
        )
    )

    assert metadata is not None
    assert metadata.title == GUNDAM_TW_TITLE
    assert metadata.title_tw == GUNDAM_TW_TITLE
    assert metadata.title_jp == GUNDAM_JP_TITLE
    assert metadata.author == "Ark Performance"
    assert metadata.publisher == "角川"
    assert metadata.volume == "1"
    assert GUNDAM_JP_TITLE in (metadata.search_titles or [])


def test_parse_llm_json_prefers_volume_number_over_volume_text() -> None:
    metadata = _parse_llm_json(
        json.dumps(
            {
                "clean_title": "新世紀福音戰士",
                "volume_number": "1",
                "volume_text": "Vol.01",
                "confidence": 0.8,
            },
            ensure_ascii=False,
        )
    )

    assert metadata is not None
    assert metadata.volume == "1"


def test_normalize_with_llm_sends_raw_filename_without_regex_hint(
    tmp_path: Path, monkeypatch: object
) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("test-key", encoding="utf-8")
    cfg = MetadataConfig(
        llm_normalize_enabled=True,
        llm_model="gemini-3.1-flash-lite",
        llm_api_key_file=key_file,
    )
    captured: dict[str, object] = {}

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "clean_title": "新世紀福音戰士",
                                    "volume_number": "1",
                                    "confidence": 0.8,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    def fake_post(_cfg: object, _api_key: str, payload: dict[str, object]) -> FakeResponse:
        captured["payload"] = payload
        return FakeResponse()

    monkeypatch.setattr("manga_pipeline.llm_metadata._post_chat_completion", fake_post)

    metadata = normalize_with_llm(
        "[bad][raw][name].zip",
        ParseResult(title="wrong", author="wrong", volume="99"),
        cfg,
    )

    assert metadata is not None
    payload = captured["payload"]
    assert isinstance(payload, dict)
    messages = payload["messages"]
    assert isinstance(messages, list)
    user_content = json.loads(messages[1]["content"])
    assert user_content == {"filename": "[bad][raw][name].zip"}


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


def test_post_chat_completion_retries_transient_http_errors(
    monkeypatch: object,
) -> None:
    cfg = MetadataConfig(
        llm_max_retries=3,
        llm_retry_backoff_seconds=0,
    )
    attempts = {"count": 0}

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests.HTTPError(
                    f"HTTP {self.status_code}",
                    response=self,
                )

    def fake_post(*_args: object, **_kwargs: object) -> FakeResponse:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return FakeResponse(503)
        return FakeResponse(200)

    monkeypatch.setattr("manga_pipeline.llm_metadata.requests.post", fake_post)

    response = _post_chat_completion(
        cfg,
        "key",
        {
            "model": "gemini",
            "messages": [],
            "response_format": {"type": "json_object"},
        },
    )

    assert response.status_code == 200
    assert attempts["count"] == 2
