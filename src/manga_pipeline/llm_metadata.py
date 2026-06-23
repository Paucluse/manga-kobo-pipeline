"""LLM filename normalization for manga metadata extraction.

Uses an OpenAI-compatible chat endpoint (Gemini, etc.) to parse manga
filenames into structured metadata usable for downstream scraping.

Design decisions
----------------
- The prompt is fixed in this module (SYSTEM_PROMPT constant) so that the
  JSON schema and the parser stay in sync without any config drift.
- The Web-UI "active prompt" override is still honoured when set, giving an
  emergency escape hatch without touching code.
- The user message only contains the raw input; the schema is declared once
  inside the system prompt so the LLM never sees two conflicting schemas.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from manga_pipeline.config import MetadataConfig
from manga_pipeline.filename_parser import ParseResult
from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Fixed system prompt — edit here to change LLM behaviour.
# The downstream parser (_parse_llm_json) must stay in sync with this schema.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
你是漫画文件名归一化助手。根据输入的漫画压缩包或目录名，提取用于 \
BookWalker 台湾、BookWalker 日本、Bangumi 刮削的关键词。

规则：
1. 只输出严格 JSON，不要 Markdown 代码块，不要任何解释文字。
2. 不要编造信息。无法确认的字段填 null 或空数组 []。
3. 不要把扫描组、资源站、压缩组、画质标记、格式标记、卷号、完结标记当作书名。
4. 括号内常见的噪音词：DL版、掃圖、掃描、汉化、漢化、熱血、東立、尖端、玉皇朝、\
长鸿、青文、天下、台灣東販、三采、遠流、時報、新人類、nearfly、凤凰、无名汉化、\
64k、90k、高清、無修正、ZIP、RAR、7Z、JPEG、PNG、彩色、黑白、完全版、完結。
5. 台湾/香港出版社只能作为 publisher_hints，不能作为书名一部分。
6. confidence 要保守：仅凭文件名推断时不超过 0.6；文件名中有明确的日文原名或\
台版正式名时可到 0.7~0.8。

输出 JSON 格式（字段含义见注释，实际输出不要包含注释）：
{
  "raw_filename": "<原始文件名>",
  "parse_status": "<ok | ambiguous | insufficient>",
  "clean_title": "<从文件名中剥离噪音后的系列名，null 表示无法判断>",
  "volume_number": "<阿拉伯数字卷号字符串，null 表示未知或单行本>",
  "volume_text": "<原始卷号文字，如 '第01巻'，null 表示无>",
  "edition_hints": ["<版本信息，如 完全版、文库版>"],
  "publisher_hints": ["<出版社提示>"],
  "noise_removed": ["<已识别并剔除的噪音片段>"],
  "titles": {
    "traditional_chinese": "<繁体中文正式书名或常用译名，null 表示不确定>",
    "simplified_chinese": "<简体中文书名，null 表示不确定>",
    "japanese": "<日文原名，null 表示不确定>",
    "romaji": "<罗马字，null 表示不确定>",
    "aliases": ["<其他别名或俗称>"]
  },
  "authors": ["<作者名>"],
  "scraping_queries": {
    "bookwalker_tw": ["<适合 BookWalker 台湾搜索的繁体中文词条，按优先级排列>"],
    "bookwalker_jp": ["<适合 BookWalker 日本搜索的日文词条，按优先级排列>"],
    "bangumi": ["<适合 Bangumi 搜索的词条，日文优先>"]
  },
  "verified": false,
  "verification_level": "<filename_only | search_single_source | search_multi_source>",
  "confidence": 0.0,
  "warnings": ["<值得注意的解析风险或歧义说明>"]
}
"""


@dataclass
class LlmMetadata:
    """Structured metadata extracted by the LLM from a manga filename."""

    # Core title fields
    title: str = ""          # Best display title (tw > jp > clean_title)
    title_tw: str = ""       # Traditional-Chinese official title
    title_jp: str = ""       # Japanese original title
    author: str = ""
    publisher: str = ""
    volume: str = ""

    # Per-provider search terms (already filtered by the LLM)
    queries_tw: list[str] = field(default_factory=list)
    queries_jp: list[str] = field(default_factory=list)
    queries_bangumi: list[str] = field(default_factory=list)

    # Legacy flat list kept for backward-compat with _metadata_search_titles
    search_titles: list[str] | None = None

    # Parse quality signals
    parse_status: str = "ok"          # ok | ambiguous | insufficient
    verified: bool = False
    verification_level: str = "filename_only"
    warnings: list[str] = field(default_factory=list)
    noise_removed: list[str] = field(default_factory=list)

    # Telemetry
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
    """Normalize filename metadata through an OpenAI-compatible chat endpoint.

    Args:
        filename: Raw filename (or collection + filename) to analyse.
        parsed: Current regex-parsed result, passed to LLM as a hint.
        cfg: MetadataConfig containing LLM connection settings.
        prompt_template: Optional override prompt (from Web UI emergency control).
                         When empty the fixed SYSTEM_PROMPT constant is used.

    Returns:
        LlmMetadata on success, None if LLM is disabled or unavailable.
    """
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

    # Use override prompt when explicitly set (emergency escape hatch),
    # otherwise fall back to the canonical fixed prompt.
    system_prompt = prompt_template.strip() or SYSTEM_PROMPT

    # User message: raw filename + current regex parse as a hint.
    # We do NOT repeat the schema here — it lives in the system prompt only.
    user_content = json.dumps(
        {
            "filename": filename,
            "regex_parse_hint": {
                "title": parsed.title,
                "author": parsed.author,
                "publisher": parsed.publisher,
                "volume": parsed.volume,
            },
        },
        ensure_ascii=False,
    )

    payload: dict[str, object] = {
        "model": cfg.llm_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }

    start = time.monotonic()
    response = _post_chat_completion(cfg, api_key, payload)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    resp_json = response.json()
    content = resp_json["choices"][0]["message"]["content"]
    metadata = _parse_llm_json(content)
    if metadata is not None:
        metadata.raw_content = content
        metadata.prompt = system_prompt
        metadata.elapsed_ms = elapsed_ms
    return metadata


# ---------------------------------------------------------------------------
# Post-scraping LLM verification
# ---------------------------------------------------------------------------

VERIFY_PROMPT = """\
你是漫画元数据验证助手。
你将收到：原始文件名、第一步 LLM 解析结果、以及某个刮削平台返回的元数据。
你的任务：判断刮削结果描述的是否与原始文件同一部作品，并给出理由。

规则：
1. 只输出严格 JSON，不要 Markdown，不要解释文字。
2. 以下情况判定 match=false：
   - 刮削结果的系列名与文件名描述的作品明显不同（换了作品）。
   - 卷号差异超过 1（如文件明确是第 3 卷，但刮削结果是第 7 卷）。
3. 以下情况仍可判定 match=true：
   - 繁体/简体/日文名称不同但指同一作品。
   - 刮削结果缺少卷号（单行本或系列级匹配）。
   - 作者名字因翻译差异略有不同。
4. confidence 表示你对判断的把握程度（0.0~1.0）。

输出格式：
{
  "match": true,
  "confidence": 0.9,
  "reason": "简短说明（中文，一句话）"
}
"""


@dataclass
class ScrapeVerification:
    """Result of LLM verification of a scraped metadata result."""

    match: bool = False
    confidence: float = 0.0
    reason: str = ""
    elapsed_ms: int = 0


def verify_scrape_with_llm(
    filename: str,
    llm_parse: LlmMetadata | None,
    provider: str,
    scraped_title: str,
    scraped_series: str,
    scraped_volume: str,
    scraped_author: str,
    cfg: MetadataConfig,
) -> ScrapeVerification | None:
    """Ask the LLM whether a scraped result matches the original file.

    Args:
        filename: The raw source filename (as fed to the pipeline).
        llm_parse: The LlmMetadata from the filename-normalisation step,
                   or None if normalisation was skipped/failed.
        provider: Human-readable provider name for logging (e.g. 'BookWalker TW').
        scraped_title: Title returned by the scraping provider.
        scraped_series: Series name returned by the scraping provider.
        scraped_volume: Volume string returned by the scraping provider.
        scraped_author: Author string returned by the scraping provider.
        cfg: MetadataConfig with LLM connection settings.

    Returns:
        ScrapeVerification on success, None if verification is disabled,
        the LLM is unavailable, or an error occurs.
    """
    if not cfg.llm_verify_scrape_enabled or not cfg.llm_model:
        return None

    api_key = _read_api_key(cfg)
    if not api_key:
        return None

    user_content = json.dumps(
        {
            "original_filename": filename,
            "llm_parse_result": {
                "title": llm_parse.title if llm_parse else "",
                "title_tw": llm_parse.title_tw if llm_parse else "",
                "title_jp": llm_parse.title_jp if llm_parse else "",
                "author": llm_parse.author if llm_parse else "",
                "volume": llm_parse.volume if llm_parse else "",
            },
            "scraped_result": {
                "provider": provider,
                "title": scraped_title,
                "series": scraped_series,
                "volume": scraped_volume,
                "author": scraped_author,
            },
        },
        ensure_ascii=False,
    )

    payload: dict[str, object] = {
        "model": cfg.llm_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": VERIFY_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }

    start = time.monotonic()
    try:
        response = _post_chat_completion(cfg, api_key, payload)
        elapsed_ms = int((time.monotonic() - start) * 1000)
    except Exception as exc:
        logger.warning("LLM scrape verification request failed (%s): %s", provider, exc)
        return None

    try:
        data = json.loads(response.json()["choices"][0]["message"]["content"])
    except (KeyError, json.JSONDecodeError) as exc:
        logger.warning("LLM scrape verification unparseable response (%s): %s", provider, exc)
        return None

    return ScrapeVerification(
        match=bool(data.get("match", True)),   # default True: don't block on bad response
        confidence=float(data.get("confidence") or 0.0),
        reason=str(data.get("reason") or ""),
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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
    """POST to an OpenAI-compatible endpoint with a JSON-mode fallback.

    Some providers (e.g. older Gemini endpoints) return HTTP 400 when
    response_format is present; in that case we retry without it.
    """
    url = f"{cfg.llm_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json=payload, timeout=cfg.llm_timeout_seconds)
    if response.status_code != 400 or "response_format" not in payload:
        response.raise_for_status()
        return response

    # Retry without response_format
    fallback = {k: v for k, v in payload.items() if k != "response_format"}
    retry = requests.post(url, headers=headers, json=fallback, timeout=cfg.llm_timeout_seconds)
    retry.raise_for_status()
    return retry


def _parse_llm_json(content: str) -> LlmMetadata | None:
    """Parse the LLM JSON response into an LlmMetadata instance.

    Handles both the new canonical schema and legacy field names so that any
    old cached responses or override prompts still work.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.debug("LLM returned non-JSON content: %.200s", content)
        return None

    if not isinstance(data, dict):
        return None

    titles: dict = data.get("titles") if isinstance(data.get("titles"), dict) else {}
    scraping: dict = (
        data.get("scraping_queries")
        if isinstance(data.get("scraping_queries"), dict)
        else {}
    )

    # --- title fields ---
    title_tw = _first_text(data.get("title_tw"), titles.get("traditional_chinese"))
    title_jp = _first_text(data.get("title_jp"), titles.get("japanese"))
    title = _first_text(data.get("title"), data.get("clean_title"), title_tw, title_jp)
    author = _first_text(
        data.get("author"),
        *_list_text(data.get("authors")),
    )
    publisher = _first_text(
        data.get("publisher"),
        *_list_text(data.get("publisher_hints")),
    )
    volume = _first_text(
        data.get("volume"),
        data.get("volume_text"),
        data.get("volume_number"),
    )

    # --- per-provider search terms ---
    queries_tw = _list_text(scraping.get("bookwalker_tw"))
    queries_jp = _list_text(scraping.get("bookwalker_jp"))
    queries_bangumi = _list_text(scraping.get("bangumi"))

    # Legacy flat list: tw first, then jp, then bangumi, then aliases
    search_titles = _dedupe_text(
        queries_tw
        + queries_jp
        + queries_bangumi
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

    # --- quality signals ---
    parse_status = str(data.get("parse_status") or "ok")
    verified = bool(data.get("verified") or False)
    verification_level = str(data.get("verification_level") or "filename_only")
    warnings = _list_text(data.get("warnings"))
    noise_removed = _list_text(data.get("noise_removed"))
    confidence = float(data.get("confidence") or 0.0)

    # If LLM itself says parsing was insufficient, clamp its confidence.
    if parse_status == "insufficient":
        confidence = min(confidence, 0.3)
    elif parse_status == "ambiguous":
        confidence = min(confidence, 0.55)

    return LlmMetadata(
        title=title,
        title_tw=title_tw,
        title_jp=title_jp,
        author=author,
        publisher=publisher,
        volume=volume,
        queries_tw=queries_tw,
        queries_jp=queries_jp,
        queries_bangumi=queries_bangumi,
        search_titles=search_titles or None,
        parse_status=parse_status,
        verified=verified,
        verification_level=verification_level,
        warnings=warnings,
        noise_removed=noise_removed,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Small text utilities
# ---------------------------------------------------------------------------

def _first_text(*values: object) -> str:
    """Return the first non-empty string value from the arguments."""
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in ("null", "none", ""):
            return text
    return ""


def _list_text(value: object) -> list[str]:
    """Coerce a JSON value to a list of non-empty strings."""
    if isinstance(value, list):
        return [t for item in value if (t := _first_text(item))]
    text = _first_text(value)
    return [text] if text else []


def _dedupe_text(values: list[str]) -> list[str]:
    """Deduplicate a list while preserving order."""
    result: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in result:
            result.append(text)
    return result
