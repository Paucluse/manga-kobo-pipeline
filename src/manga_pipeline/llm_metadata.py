"""Optional LLM filename normalization."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import requests

from manga_pipeline.config import MetadataConfig
from manga_pipeline.filename_parser import ParseResult
from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class LlmMetadata:
    """LLM-normalized metadata fields."""

    title: str = ""
    author: str = ""
    publisher: str = ""
    volume: str = ""
    confidence: float = 0.0


def normalize_with_llm(
    filename: str,
    parsed: ParseResult,
    cfg: MetadataConfig,
) -> LlmMetadata | None:
    """Normalize filename metadata through an OpenAI-compatible chat endpoint."""
    if not cfg.llm_normalize_enabled or not cfg.llm_model:
        return None

    api_key = os.environ.get(cfg.llm_api_key_env)
    if not api_key:
        logger.warning("LLM normalization enabled but %s is not set", cfg.llm_api_key_env)
        return None

    response = requests.post(
        f"{cfg.llm_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": cfg.llm_model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是台湾漫画电子书文件名标准化器。只输出 JSON, 不要解释。"
                        "全部以台湾繁体中文和台湾出版信息为准, 不使用香港版信息。"
                    ),
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
                                "title": "台湾常用系列名",
                                "author": "作者名",
                                "publisher": "台湾出版社, 未知则空字符串",
                                "volume": "阿拉伯数字卷号, 未知则空字符串",
                                "confidence": "0 到 1 的数字",
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=cfg.llm_timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    return _parse_llm_json(content)


def _parse_llm_json(content: str) -> LlmMetadata | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None

    return LlmMetadata(
        title=str(data.get("title") or "").strip(),
        author=str(data.get("author") or "").strip(),
        publisher=str(data.get("publisher") or "").strip(),
        volume=str(data.get("volume") or "").strip(),
        confidence=float(data.get("confidence") or 0.0),
    )
