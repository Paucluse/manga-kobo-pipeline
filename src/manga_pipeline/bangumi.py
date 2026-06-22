"""Bangumi metadata fallback provider."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import requests

from manga_pipeline.bookwalker_tw import to_bookwalker_traditional
from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.bgm.tv"
WEB_SUBJECT_URL = "https://bgm.tv/subject/{subject_id}"
SEARCH_URL = f"{BASE_URL}/v0/search/subjects"
SUBJECT_URL = f"{BASE_URL}/v0/subjects/{{subject_id}}"
BOOK_SUBJECT_TYPE = 1
MULTIPLICATION_SIGN = "\N{MULTIPLICATION SIGN}"


@dataclass
class BangumiMetadata:
    """Normalized Bangumi subject metadata."""

    subject_id: str = ""
    title: str = ""
    series: str = ""
    volume: str = ""
    authors: list[str] = field(default_factory=list)
    publisher: str = ""
    summary: str = ""
    cover_url: str = ""
    detail_url: str = ""
    isbn: str = ""
    page_count: str = ""
    publish_date: str = ""
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def author_text(self) -> str:
        """Return authors as a stable display string."""
        return "/".join(self.authors)


def search_bangumi(
    title: str,
    volume: str = "",
    author: str = "",
    timeout: int = 15,
    max_candidates: int = 8,
) -> BangumiMetadata | None:
    """Search Bangumi book subjects and return the best matching metadata."""
    query_title = to_bookwalker_traditional(title)
    queries = _build_queries(query_title or title, volume)
    if query_title != title:
        queries.extend(q for q in _build_queries(title, volume) if q not in queries)

    candidates_by_id: dict[str, dict[str, Any]] = {}
    for query in queries:
        try:
            query_candidates = _search_subjects(query, timeout, max_candidates)
        except requests.RequestException as e:
            logger.warning("Bangumi search failed for %s: %s", query, e)
            continue
        for candidate in query_candidates:
            subject_id = str(candidate.get("id") or "")
            if subject_id and subject_id not in candidates_by_id:
                candidates_by_id[subject_id] = candidate

    candidates = list(candidates_by_id.values())

    if not candidates:
        logger.info("Bangumi found no search candidates for queries=%s", queries)
        return None

    best: BangumiMetadata | None = None
    for candidate in candidates[:max_candidates]:
        subject = _fetch_subject(candidate, timeout)
        metadata = parse_subject(subject)
        score = _score_metadata(
            metadata,
            query_title or title,
            volume,
            to_bookwalker_traditional(author),
        )
        metadata.confidence = min(score / 100, 1.0)
        if best is None or metadata.confidence > best.confidence:
            best = metadata

    return best


def fetch_subject_metadata(subject_id: str, timeout: int = 15) -> BangumiMetadata:
    """Fetch and parse one Bangumi subject detail."""
    response = requests.get(
        SUBJECT_URL.format(subject_id=subject_id),
        headers=_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return BangumiMetadata()
    return parse_subject(data)


def parse_subject(subject: dict[str, Any]) -> BangumiMetadata:
    """Parse one Bangumi subject object from the v0 API."""
    subject_id = str(subject.get("id") or "")
    name = _clean(subject.get("name"))
    name_cn = _clean(subject.get("name_cn"))
    title = name_cn or name
    series = _series_from_title(title)
    volume = _extract_volume_from_title(name) or _extract_volume_from_title(name_cn)
    infobox = subject.get("infobox", [])
    info = _parse_infobox(infobox if isinstance(infobox, list) else [])

    images = subject.get("images", {})
    cover_url = ""
    if isinstance(images, dict):
        cover_url = (
            _clean(images.get("large"))
            or _clean(images.get("common"))
            or _clean(images.get("medium"))
            or _clean(images.get("small"))
        )

    tags = []
    for tag in subject.get("tags", []) if isinstance(subject.get("tags"), list) else []:
        if isinstance(tag, dict):
            tags.append(_clean(tag.get("name")))
        else:
            tags.append(_clean(tag))

    return BangumiMetadata(
        subject_id=subject_id,
        title=title,
        series=series,
        volume=volume,
        authors=_authors_from_info(info),
        publisher=_first_info_value(info, "出版社", "發行", "发行"),
        summary=_clean(subject.get("summary")),
        cover_url=cover_url,
        detail_url=WEB_SUBJECT_URL.format(subject_id=subject_id) if subject_id else "",
        isbn=_first_info_value(info, "ISBN"),
        page_count=_first_info_value(info, "页数", "頁數"),
        publish_date=_clean(subject.get("date")) or _first_info_value(info, "发售日", "発売日"),
        tags=sorted({tag for tag in tags if tag}),
    )


def _search_subjects(title: str, timeout: int, max_candidates: int) -> list[dict[str, Any]]:
    if not title:
        return []

    response = requests.post(
        SEARCH_URL,
        headers=_headers(),
        json={
            "keyword": title,
            "sort": "match",
            "filter": {"type": [BOOK_SUBJECT_TYPE]},
        },
        params={"limit": max_candidates, "offset": 0},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    items = data.get("data", []) if isinstance(data, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _fetch_subject(candidate: dict[str, Any], timeout: int) -> dict[str, Any]:
    subject_id = str(candidate.get("id") or "")
    if not subject_id:
        return candidate
    try:
        response = requests.get(
            SUBJECT_URL.format(subject_id=subject_id),
            headers=_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.warning("Bangumi subject detail fetch failed for %s: %s", subject_id, e)
        return candidate
    return data if isinstance(data, dict) else candidate


def _build_queries(title: str, volume: str) -> list[str]:
    title = _series_from_title(title)
    titles = _title_tokens(title)
    for alias in _title_aliases(title):
        titles.extend(token for token in _title_tokens(alias) if token not in titles)
    queries: list[str] = []

    if volume.isdigit():
        vol = str(int(volume))
        for current_title in titles:
            queries.extend(
                [
                    f"{current_title} ({vol})",
                    f"{current_title} {vol}",
                ]
            )

    queries.extend(titles)
    return [query for query in dict.fromkeys(queries) if query]


def _title_aliases(title: str) -> list[str]:
    aliases: list[str] = []
    normalized = _normalized(title)
    if normalized == "dna2":
        aliases.append("D・N・A²")
    return aliases


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "manga-kobo-pipeline/0.1 (https://github.com/)",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _parse_infobox(infobox: list[Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in infobox:
        if not isinstance(item, dict):
            continue
        key = _clean(item.get("key"))
        values = _flatten_info_value(item.get("value"))
        if key and values:
            result.setdefault(key, []).extend(values)
    return result


def _flatten_info_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = _clean(value)
        return [text] if text else []
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_flatten_info_value(item))
        return values
    if isinstance(value, dict):
        text = _clean(value.get("v") or value.get("name") or value.get("value"))
        return [text] if text else []
    text = _clean(value)
    return [text] if text else []


def _authors_from_info(info: dict[str, list[str]]) -> list[str]:
    authors: list[str] = []
    for key in ("作者", "作画", "作畫", "原作"):
        authors.extend(info.get(key, []))
    return list(dict.fromkeys(authors))


def _first_info_value(info: dict[str, list[str]], *keys: str) -> str:
    for key in keys:
        values = info.get(key, [])
        if values:
            return values[0]
    return ""


def _score_metadata(
    metadata: BangumiMetadata,
    title: str,
    volume: str,
    author: str,
) -> int:
    score = 0
    expected_volume = str(int(volume)) if volume.isdigit() else volume
    if _titles_match(metadata.series or metadata.title, title):
        score += 65
    if expected_volume and metadata.volume == expected_volume:
        score += 25
    elif expected_volume and metadata.volume:
        score -= 30
    elif expected_volume:
        score -= 20
    if author and any(_normalized(author) in _normalized(x) for x in metadata.authors):
        score += 15
    if metadata.publisher:
        score += 5
    if metadata.cover_url:
        score += 5
    if metadata.summary:
        score += 5
    return score


def _titles_match(candidate: str, expected: str) -> bool:
    candidate_names = [_series_from_title(candidate)]
    expected_names = _title_tokens(expected)
    for candidate_name in candidate_names:
        c = _normalized(candidate_name)
        if not c:
            continue
        for expected_name in expected_names:
            e = _normalized(expected_name)
            if c and e and (c in e or e in c):
                return True
    return False


def _title_tokens(value: str) -> list[str]:
    value = _series_from_title(value)
    tokens = re.split(r"[-_/\uFF0F\uFF5C|]+", value)
    tokens.append(value)
    return [token.strip() for token in dict.fromkeys(tokens) if token.strip()]


def _series_from_title(title: str) -> str:
    value = unicodedata.normalize("NFKC", _clean(title))
    value = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", value).strip()
    return re.sub(r"\s+v(?:ol(?:ume)?)?\.?\s*\d+\s*$", "", value, flags=re.IGNORECASE)


def _extract_volume_from_title(title: str) -> str:
    value = unicodedata.normalize("NFKC", _clean(title))
    match = re.search(r"\(\s*(\d+)\s*\)\s*$", value)
    if not match:
        match = re.search(r"\bFILE\s*(\d+)\s*$", value, re.IGNORECASE)
    if not match:
        match = re.search(r"\b(?:v(?:ol(?:ume)?)?\.?\s*)?(\d+)\s*$", value, re.IGNORECASE)
    return (match.group(1).lstrip("0") or "0") if match else ""


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", _clean(value).lower())
    value = value.replace(MULTIPLICATION_SIGN, "x")
    value = value.replace("²", "2")
    return re.sub(r"[\s_\-・.()【】\[\]:]+", "", value)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in text)
    return " ".join(text.split())
