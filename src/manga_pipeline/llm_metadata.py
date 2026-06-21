"""Optional LLM filename normalization."""

from __future__ import annotations

import json
import os
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

    api_key = _read_api_key(cfg)
    if not api_key:
        logger.warning(
            "LLM normalization enabled but no API key is available from %s or %s",
            cfg.llm_api_key_file,
            cfg.llm_api_key_env,
        )
        return None

    payload = {
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
    }

    response = _post_chat_completion(cfg, api_key, payload)
    payload_json = response.json()
    content = payload_json["choices"][0]["message"]["content"]
    return _parse_llm_json(content)


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

    return LlmMetadata(
        title=str(data.get("title") or "").strip(),
        author=str(data.get("author") or "").strip(),
        publisher=str(data.get("publisher") or "").strip(),
        volume=str(data.get("volume") or "").strip(),
        confidence=float(data.get("confidence") or 0.0),
    )
