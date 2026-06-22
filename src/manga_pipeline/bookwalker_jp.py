"""BookWalker Japan metadata provider."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

import requests

from manga_pipeline.bookwalker_tw import BookwalkerMetadata
from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)

BASE_URL = "https://bookwalker.jp"
SEARCH_URL = f"{BASE_URL}/search/?word={{query}}"
PRODUCT_URL_RE = re.compile(r"https://bookwalker\.jp/(de[0-9a-f-]+)/")
MULTIPLICATION_SIGN = "\N{MULTIPLICATION SIGN}"


@dataclass
class JpSearchCandidate:
    """BookWalker Japan search or series result candidate."""

    product_id: str = ""
    title: str = ""
    detail_url: str = ""
    series_url: str = ""
    series: str = ""
    volume: str = ""
    cover_url: str = ""
    author: str = ""
    category: str = ""
    index: int = 9999


def search_bookwalker_jp(
    title: str,
    volume: str = "",
    author: str = "",
    timeout: int = 15,
    max_candidates: int = 8,
) -> BookwalkerMetadata | None:
    """Search BookWalker Japan and return the best matching metadata."""
    candidates_by_key: dict[str, JpSearchCandidate] = {}
    queries = _build_queries(title, volume)

    for query in queries:
        html_text = _request_get(SEARCH_URL.format(query=quote_plus(query)), timeout)
        search_candidates = sorted(
            parse_search_candidates(html_text),
            key=lambda c: _score_candidate(c, title, volume, author),
            reverse=True,
        )[:max_candidates]
        for candidate in search_candidates:
            expanded = _expand_series_candidate(candidate, volume, timeout)
            for current in expanded:
                key = current.detail_url or current.series_url or current.title
                existing = candidates_by_key.get(key)
                if existing is None or current.index < existing.index:
                    candidates_by_key[key] = current

    candidates = list(candidates_by_key.values())
    if not candidates:
        logger.info("BookWalker JP found no search candidates for queries=%s", queries)
        return None

    ranked = sorted(
        candidates,
        key=lambda c: _score_candidate(c, title, volume, author),
        reverse=True,
    )

    best: BookwalkerMetadata | None = None
    for candidate in ranked[:max_candidates]:
        if not candidate.detail_url:
            continue
        try:
            metadata = fetch_product_metadata(candidate.detail_url, timeout=timeout)
        except requests.RequestException as e:
            logger.warning("BookWalker JP detail fetch failed for %s: %s", candidate.detail_url, e)
            continue

        score = _score_metadata(metadata, title, volume, author)
        metadata.confidence = min(score / 100, 1.0)
        if best is None or metadata.confidence > best.confidence:
            best = metadata

    return best


def fetch_product_metadata(detail_url: str, timeout: int = 15) -> BookwalkerMetadata:
    """Fetch and parse one BookWalker Japan product detail page."""
    html_text = _request_get(detail_url, timeout)
    product_id = _product_id_from_url(detail_url)
    return parse_product_detail(product_id, html_text, detail_url)


def parse_search_candidates(html_text: str) -> list[JpSearchCandidate]:
    """Parse BookWalker Japan search page cards."""
    candidates: list[JpSearchCandidate] = []

    for index, block in enumerate(_book_item_blocks(html_text), start=1):
        title = _clean(_attr(block, "title"))
        series_url = _clean(
            _first_match(r'href="(https://bookwalker\.jp/series/\d+/list/)"', block)
        )
        cover_url = _clean(_attr(block, "data-original"))
        category = "マンガ" if "a-tag-comic" in block or "マンガ" in block else ""
        product_urls = list(dict.fromkeys(PRODUCT_URL_RE.findall(block)))
        detail_url = f"{BASE_URL}/{product_urls[0]}/" if product_urls else ""
        product_id = product_urls[0] if product_urls else ""

        if title or detail_url or series_url:
            candidates.append(
                JpSearchCandidate(
                    product_id=product_id,
                    title=title,
                    detail_url=detail_url,
                    series_url=series_url,
                    series=_series_from_title(title),
                    volume=_extract_volume_from_title(title),
                    cover_url=cover_url,
                    category=category,
                    index=index,
                )
            )

    return candidates


def parse_series_candidates(html_text: str) -> list[JpSearchCandidate]:
    """Parse per-volume cards from a BookWalker Japan series page."""
    candidates: list[JpSearchCandidate] = []
    for index, block in enumerate(_book_item_blocks(html_text), start=1):
        title = _clean(_attr(block, "title"))
        detail_url = _clean(_first_match(r'href="(https://bookwalker\.jp/de[0-9a-f-]+/)"', block))
        product_id = _product_id_from_url(detail_url)
        if not title or not detail_url:
            continue
        candidates.append(
            JpSearchCandidate(
                product_id=product_id,
                title=title,
                detail_url=detail_url,
                series=_series_from_title(title),
                volume=_extract_volume_from_title(title),
                cover_url=_clean(_attr(block, "data-original")),
                author=_clean(_first_match(r"著:\s*([^<]+)", block)),
                category="マンガ" if "マンガ" in block else "",
                index=index,
            )
        )
    return candidates


def parse_product_detail(
    product_id: str,
    html_text: str,
    detail_url: str,
) -> BookwalkerMetadata:
    """Parse metadata from one BookWalker Japan product detail page."""
    product = _first_ld_json(html_text, "Product")
    breadcrumbs = _first_ld_json(html_text, "BreadcrumbList")

    title = _clean(product.get("name")) or _meta_content(html_text, "og:title")
    series = _series_from_breadcrumbs(breadcrumbs) or _detail_value(html_text, "シリーズ")
    series = _series_from_title(series or title)
    authors = _detail_values(html_text, "author")
    publisher = _detail_value(html_text, "出版社") or _clean(
        (product.get("brand") or {}).get("name") if isinstance(product.get("brand"), dict) else ""
    )
    summary = _clean(product.get("description")) or _meta_content(html_text, "og:description")
    cover_url = _clean(product.get("image")) or _meta_content(html_text, "og:image")
    category = _clean(product.get("category"))

    return BookwalkerMetadata(
        product_id=product_id,
        title=title,
        series=series,
        volume=_extract_volume_from_title(title),
        authors=authors,
        publisher=publisher,
        summary=summary,
        cover_url=cover_url,
        detail_url=detail_url,
        page_count=_detail_value(html_text, "ページ概数"),
        publish_date=_detail_value(html_text, "配信開始日"),
        categories=[category] if category else [],
        tags=[_detail_value(html_text, "レーベル")] if _detail_value(html_text, "レーベル") else [],
    )


def _expand_series_candidate(
    candidate: JpSearchCandidate,
    volume: str,
    timeout: int,
) -> list[JpSearchCandidate]:
    if not candidate.series_url:
        return [candidate]

    try:
        html_text = _request_get(candidate.series_url, timeout)
    except requests.RequestException as e:
        logger.warning("BookWalker JP series fetch failed for %s: %s", candidate.series_url, e)
        return [candidate]

    series_candidates = parse_series_candidates(html_text)
    if not series_candidates:
        return [candidate]
    if volume:
        expected = _normalize_volume(volume)
        exact = [item for item in series_candidates if item.volume == expected]
        if exact:
            return exact
    return series_candidates


def _request_get(url: str, timeout: int) -> str:
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return response.text


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ja,en-US;q=0.8",
        "Referer": BASE_URL,
    }


def _build_queries(title: str, volume: str) -> list[str]:
    titles = _title_tokens(title)
    volume_values = _volume_query_values(volume)
    queries: list[str] = []
    for current_title in titles:
        for current_volume in volume_values:
            query = f"{current_title} {current_volume}" if current_volume else current_title
            if query not in queries:
                queries.append(query)
    return queries


def _title_tokens(title: str) -> list[str]:
    title = _series_from_title(title)
    tokens = [title]
    if _normalized(title) == "dna2":
        tokens.extend(["D・N・A2", "D・N・A²"])
    return [token for token in dict.fromkeys(tokens) if token]


def _volume_query_values(volume: str) -> list[str]:
    normalized = _normalize_volume(volume)
    if not normalized:
        return [""]
    return [normalized, f"{int(normalized):02d}", ""]


def _score_candidate(
    candidate: JpSearchCandidate,
    title: str,
    volume: str,
    author: str,
) -> int:
    score = 0
    if _titles_match(candidate.series or candidate.title, title):
        score += 45
    if volume and candidate.volume == _normalize_volume(volume):
        score += 35
    elif volume and candidate.volume:
        score -= 25
    if author and _normalized(author) in _normalized(candidate.author):
        score += 10
    if candidate.category == "マンガ":
        score += 5
    if candidate.cover_url:
        score += 5
    score -= min(candidate.index, 30)
    return score


def _score_metadata(
    metadata: BookwalkerMetadata,
    title: str,
    volume: str,
    author: str,
) -> int:
    score = 0
    if _titles_match(metadata.series or metadata.title, title):
        score += 50
    if volume and metadata.volume == _normalize_volume(volume):
        score += 35
    elif volume and metadata.volume:
        score -= 25
    if author and _normalized(author) in _normalized(metadata.author_text):
        score += 10
    if metadata.publisher:
        score += 5
    if metadata.cover_url:
        score += 5
    if metadata.summary:
        score += 5
    return score


def _book_item_blocks(html_text: str) -> list[str]:
    parts = re.split(r'<div class="m-book-item[^"]*"', html_text)
    return [part for part in parts[1:] if "</div>" in part]


def _first_ld_json(html_text: str, ld_type: str) -> dict[str, Any]:
    for match in re.finditer(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
        html_text,
        re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == ld_type:
            return data
    return {}


def _series_from_breadcrumbs(data: dict[str, Any]) -> str:
    items = data.get("itemListElement", [])
    if not isinstance(items, list):
        return ""
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("item", {})
        if not isinstance(value, dict):
            continue
        item_id = str(value.get("@id") or "")
        if "/series/" in item_id:
            return _clean(value.get("name"))
    return ""


def _detail_values(html_text: str, action_label: str) -> list[str]:
    pattern = re.compile(
        rf'data-action-label="{re.escape(action_label)}"[^>]*>\s*(.*?)\s*</a>',
        re.DOTALL,
    )
    values = [_clean(re.sub(r"<.*?>", "", match)) for match in pattern.findall(html_text)]
    return [_clean(re.sub(r"\([^)]*\)$", "", value)) for value in values if value]


def _detail_value(html_text: str, label: str) -> str:
    pattern = re.compile(
        rf"<dt>\s*{re.escape(label)}\s*</dt>\s*<dd[^>]*>\s*(.*?)\s*</dd>",
        re.DOTALL,
    )
    match = pattern.search(html_text)
    if not match:
        return ""
    text = _clean(re.sub(r"<.*?>", "", match.group(1)))
    if label == "ページ概数":
        page_match = re.search(r"\d+", text)
        return page_match.group(0) if page_match else ""
    return text


def _meta_content(html_text: str, property_name: str) -> str:
    pattern = re.compile(
        rf'<meta\s+(?:property|name)="{re.escape(property_name)}"\s+content="([^"]*)"',
        re.IGNORECASE,
    )
    match = pattern.search(html_text)
    return html.unescape(match.group(1)) if match else ""


def _attr(html_text: str, name: str) -> str:
    return html.unescape(_first_match(rf'{re.escape(name)}="([^"]*)"', html_text))


def _first_match(pattern: str, value: str) -> str:
    match = re.search(pattern, value, flags=re.DOTALL)
    return match.group(1) if match else ""


def _product_id_from_url(url: str) -> str:
    match = PRODUCT_URL_RE.search(url)
    return match.group(1) if match else ""


def _titles_match(candidate: str, expected: str) -> bool:
    c = _normalized(_series_from_title(candidate))
    e = _normalized(_series_from_title(expected))
    if not c or not e:
        return False
    if c == e:
        return True
    return len(e) >= 4 and (c.startswith(e) or e.startswith(c))


def _extract_volume_from_title(title: str) -> str:
    value = unicodedata.normalize("NFKC", _clean(title))
    match = re.search(r"\(\s*(\d+)\s*\)\s*$", value)
    if not match:
        match = re.search(r"(?:\s|第)(\d+)\s*(?:巻)?\s*$", value)
    return (match.group(1).lstrip("0") or "0") if match else ""


def _normalize_volume(volume: str) -> str:
    value = unicodedata.normalize("NFKC", _clean(volume))
    match = re.search(r"\d+", value)
    return (match.group(0).lstrip("0") or "0") if match else ""


def _series_from_title(title: str) -> str:
    value = _clean(title)
    value = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", value).strip()
    value = re.sub(r"(?:\s|第)\d+\s*(?:巻)?\s*$", "", value).strip()
    value = re.sub(
        r"(?:\([^)]*DIGITAL[^)]*\)|\uFF08[^\uFF09]*DIGITAL[^\uFF09]*\uFF09)$",
        "",
        value,
    ).strip()
    return value


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", _clean(value).lower())
    value = value.replace(MULTIPLICATION_SIGN, "x")
    value = value.replace("²", "2")
    return re.sub(r"[\s_\-・.()【】\[\]\uFF08\uFF09\u301C\uFF5E:\uFF1A]+", "", value)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in text)
    return " ".join(text.split())
