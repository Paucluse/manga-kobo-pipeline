"""Optional LLM filename normalization."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from manga_pipeline.config import MetadataConfig
from manga_pipeline.filename_parser import ParseResult
from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class LlmMetadata:
    """LLM-normalized metadata fields."""

    title: str = ""
    title_tw: str = ""
    title_jp: str = ""
    author: str = ""
    publisher: str = ""
    volume: str = ""
    search_titles: list[str] | None = None
    confidence: float = 0.0
    raw_content: str = ""
    prompt: str = ""
    elapsed_ms: int = 0


def normalize_with_llm(
    filename: str,
    parsed: ParseResult,
    cfg: MetadataConfig,
    prompt_template: str = "",
) -> LlmMetadata | None:
    """Normalize filename metadata through an OpenAI-compatible chat endpoint."""
    if not cfg.llm_normalize_enabled or not cfg.llm_model:
        return None

    api_key = _read_api_key(cfg)
    if not api_key:
        logger.warning(
            "LLM normalization enabled but no API key is available from %s or %s",
            cfg.llm_api_key_file,
            cfg.llm_api_key_env,
        )
        return None

    system_prompt = (prompt_template or (
        "你是漫画电子书文件名标准化器。只输出 JSON, 不要解释。"
        "你的任务是从可能不准确的文件名推断正式书名和检索关键词。"
        "优先给出台版正式书名; 如台版可能不存在, 同时给出日版正式书名。"
        "不要编造出版社; 不确定则留空。"
    )).replace("{{RAW_FILENAME}}", filename)
    payload = {
        "model": cfg.llm_model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "filename": filename,
                        "current_parse": {
                            "title": parsed.title,
                            "author": parsed.author,
                            "publisher": parsed.publisher,
                            "volume": parsed.volume,
                        },
                        "output_schema": {
                            "clean_title": "从文件名清理出的系列名",
                            "volume_number": "阿拉伯数字卷号, 未知则 null",
                            "titles": {
                                "traditional_chinese": "繁体中文正式名或常用译名",
                                "simplified_chinese": "简体中文名",
                                "japanese": "日文正式名",
                                "romaji": "罗马字",
                                "aliases": ["其他可用于检索的别名"],
                            },
                            "authors": ["作者名"],
                            "publisher_hints": ["出版社提示"],
                            "scraping_queries": {
                                "bookwalker_tw": ["BookWalker 台湾检索词"],
                                "bookwalker_jp": ["BookWalker 日本检索词"],
                                "bangumi": ["Bangumi 检索词"],
                            },
                            "verified": "是否经过外部来源验证",
                            "verification_level": (
                                "filename_only | search_single_source | search_multi_source"
                            ),
                            "confidence": "0 到 1 的数字",
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
    }

    start = time.monotonic()
    response = _post_chat_completion(cfg, api_key, payload)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    payload_json = response.json()
    content = payload_json["choices"][0]["message"]["content"]
    metadata = _parse_llm_json(content)
    if metadata is not None:
        metadata.raw_content = content
        metadata.prompt = system_prompt
        metadata.elapsed_ms = elapsed_ms
    return metadata


def _read_api_key(cfg: MetadataConfig) -> str:
    """Read API key from a mounted file first, then from the environment."""
    if cfg.llm_api_key_file:
        try:
            value = Path(cfg.llm_api_key_file).read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            return value
    return os.environ.get(cfg.llm_api_key_env, "").strip()


def _post_chat_completion(
    cfg: MetadataConfig,
    api_key: str,
    payload: dict[str, object],
) -> requests.Response:
    """POST to an OpenAI-compatible endpoint with a JSON-mode fallback."""
    response = requests.post(
        f"{cfg.llm_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=cfg.llm_timeout_seconds,
    )
    if response.status_code != 400 or "response_format" not in payload:
        response.raise_for_status()
        return response

    fallback_payload = dict(payload)
    fallback_payload.pop("response_format", None)
    fallback_response = requests.post(
        f"{cfg.llm_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=fallback_payload,
        timeout=cfg.llm_timeout_seconds,
    )
    fallback_response.raise_for_status()
    return fallback_response


def _parse_llm_json(content: str) -> LlmMetadata | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None

    titles = data.get("titles") if isinstance(data.get("titles"), dict) else {}
    scraping_queries = (
        data.get("scraping_queries")
        if isinstance(data.get("scraping_queries"), dict)
        else {}
    )
    title_tw = _first_text(
        data.get("title_tw"),
        titles.get("traditional_chinese"),
    )
    title_jp = _first_text(data.get("title_jp"), titles.get("japanese"))
    title = _first_text(
        data.get("title"),
        data.get("clean_title"),
        title_tw,
        title_jp,
    )
    authors = _list_text(data.get("authors"))
    publishers = _list_text(data.get("publisher_hints"))
    search_titles = _dedupe_text(
        _list_text(data.get("search_titles"))
        + _list_text(scraping_queries.get("bookwalker_tw"))
        + _list_text(scraping_queries.get("bookwalker_jp"))
        + _list_text(scraping_queries.get("bangumi"))
        + _list_text(titles.get("aliases"))
        + _list_text(
            [
                titles.get("traditional_chinese"),
                titles.get("simplified_chinese"),
                titles.get("japanese"),
                titles.get("romaji"),
            ]
        )
    )

    return LlmMetadata(
        title=title,
        title_tw=title_tw,
        title_jp=title_jp,
        author=_first_text(data.get("author"), authors[0] if authors else ""),
        publisher=_first_text(
            data.get("publisher"),
            publishers[0] if publishers else "",
        ),
        volume=_first_text(
            data.get("volume"),
            data.get("volume_text"),
            data.get("volume_number"),
        ),
        search_titles=search_titles or None,
        confidence=float(data.get("confidence") or 0.0),
    )


def _first_text(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _list_text(value: object) -> list[str]:
    if isinstance(value, list):
        return [_first_text(item) for item in value if _first_text(item)]
    text = _first_text(value)
    return [text] if text else []


def _dedupe_text(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in result:
            result.append(text)
    return result
