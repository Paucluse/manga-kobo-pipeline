"""BookWalker Taiwan metadata provider."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests

from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)

BASE_URL = "https://www.bookwalker.com.tw"
SEARCH_URL = f"{BASE_URL}/search?w={{query}}"
PRODUCT_URL = f"{BASE_URL}/product/{{product_id}}"
MULTIPLICATION_SIGN = "\N{MULTIPLICATION SIGN}"


@dataclass
class BookwalkerMetadata:
    """Normalized BookWalker Taiwan metadata."""

    product_id: str = ""
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
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def author_text(self) -> str:
        """Return authors as a stable display string."""
        return "/".join(self.authors)


@dataclass
class SearchCandidate:
    """BookWalker search result candidate."""

    product_id: str
    title: str
    volume: str = ""
    category: str = ""
    category2: str = ""
    index: int = 9999


def search_bookwalker_tw(
    title: str,
    volume: str = "",
    author: str = "",
    timeout: int = 15,
    max_candidates: int = 8,
) -> BookwalkerMetadata | None:
    """Search BookWalker Taiwan and return the best matching metadata."""
    query = _build_query(title, volume)
    html_text = _request_get(SEARCH_URL.format(query=quote_plus(query)), timeout=timeout)
    candidates = parse_search_candidates(html_text)
    if not candidates:
        logger.info("BookWalker TW found no search candidates for query=%s", query)
        return None

    ranked = sorted(
        candidates,
        key=lambda c: _score_candidate(c, title, volume),
        reverse=True,
    )
    dominant_series = _dominant_search_series(candidates, title)

    best: BookwalkerMetadata | None = None
    for candidate in ranked[:max_candidates]:
        try:
            metadata = fetch_product_metadata(candidate.product_id, timeout=timeout)
        except requests.RequestException as e:
            logger.warning("BookWalker TW detail fetch failed for %s: %s", candidate.product_id, e)
            continue

        score = _score_metadata(metadata, title, volume, author, dominant_series)
        metadata.confidence = min(score / 100, 1.0)
        if best is None or metadata.confidence > best.confidence:
            best = metadata

    return best


def fetch_product_metadata(product_id: str, timeout: int = 15) -> BookwalkerMetadata:
    """Fetch and parse one BookWalker Taiwan product detail page."""
    detail_url = PRODUCT_URL.format(product_id=product_id)
    html_text = _request_get(detail_url, timeout=timeout)
    return parse_product_detail(product_id, html_text, detail_url)


def parse_search_candidates(html_text: str) -> list[SearchCandidate]:
    """Parse BookWalker search result candidates from page analytics JSON."""
    candidates: dict[str, SearchCandidate] = {}

    for obj in _extract_bwlayer_objects(html_text):
        if obj.get("event") != "view_item_list":
            continue
        items = obj.get("ecommerce", {}).get("items", [])
        if not isinstance(items, list):
            continue
        for item in items:
            product_id = _clean(item.get("item_id"))
            title = _clean(item.get("item_name"))
            if not product_id or not title:
                continue
            candidates[product_id] = SearchCandidate(
                product_id=product_id,
                title=title,
                volume=_extract_volume_from_title(title),
                category=_clean(item.get("item_category")),
                category2=_clean(item.get("item_category2")),
                index=int(item.get("index") or 9999),
            )

    if candidates:
        return sorted(candidates.values(), key=lambda c: c.index)

    link_pattern = re.compile(
        r'href="https://www\.bookwalker\.com\.tw/product/(\d+)".*?title="([^"]+)"',
        re.DOTALL,
    )
    for product_id, title in link_pattern.findall(html_text):
        candidates[product_id] = SearchCandidate(
            product_id=product_id,
            title=html.unescape(title),
            volume=_extract_volume_from_title(title),
        )

    return list(candidates.values())


def parse_product_detail(
    product_id: str,
    html_text: str,
    detail_url: str | None = None,
) -> BookwalkerMetadata:
    """Parse metadata from one BookWalker Taiwan product detail page."""
    detail_url = detail_url or PRODUCT_URL.format(product_id=product_id)
    page_data = _extract_inertia_page_data(html_text)
    product_data = page_data.get("props", {}).get("productData", {})
    detail_info = product_data.get("product_detail_info", {})
    meta = page_data.get("props", {}).get("meta", {})

    product_value = _extract_product_page_data(html_text)
    custom = product_value.get("custom", {}) if isinstance(product_value, dict) else {}

    title = _clean(product_data.get("product_name")) or _clean(product_value.get("name"))
    series = (
        _clean(product_data.get("product_series_name"))
        or _clean(detail_info.get("series"))
        or _clean(custom.get("series_name"))
        or _series_from_title(title)
    )
    volume = _extract_volume_from_title(title)
    authors = _authors_from_product_data(product_data) or _as_text_list(custom.get("authors"))
    publisher = (
        _clean(product_data.get("publisher", {}).get("text"))
        if isinstance(product_data.get("publisher"), dict)
        else ""
    ) or _clean(detail_info.get("publisher")) or _clean(custom.get("vendor_name"))

    cover_url = (
        _clean(product_data.get("product_big_image"))
        or _clean(product_data.get("product_image"))
        or _clean(product_value.get("product_image_url"))
        or _meta_content(html_text, "og:image")
    )
    summary = _clean(meta.get("description")) or _meta_content(html_text, "og:description")

    taxonomy = product_value.get("taxonomy", [])
    categories = _as_text_list(taxonomy)
    tags = _as_text_list(custom.get("tags"))
    type_tags = product_data.get("type_tag", [])
    if isinstance(type_tags, list):
        tags.extend(_clean(x.get("name")) for x in type_tags if isinstance(x, dict))

    return BookwalkerMetadata(
        product_id=product_id,
        title=title,
        series=series,
        volume=volume,
        authors=[x for x in authors if x],
        publisher=publisher,
        summary=summary,
        cover_url=_normalize_cover_url(cover_url),
        detail_url=detail_url,
        isbn=_clean(detail_info.get("publisher_isbn")),
        page_count=_clean(detail_info.get("pages")),
        publish_date=_clean(detail_info.get("sell_date_start")),
        categories=[x for x in categories if x],
        tags=sorted({x for x in tags if x}),
    )


def download_cover(url: str, destination: Path, timeout: int = 20) -> bool:
    """Download a BookWalker cover image to a local sidecar path."""
    if not url:
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(
            url,
            headers=_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Could not download BookWalker cover %s: %s", url, e)
        return False

    destination.write_bytes(response.content)
    return True


def _request_get(url: str, timeout: int) -> str:
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return response.text


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
        "Referer": BASE_URL,
    }


def _extract_bwlayer_objects(html_text: str) -> list[dict[str, Any]]:
    matches = re.findall(r"window\.bwLayer\.push\((\{.*?\})\);", html_text, flags=re.DOTALL)
    objects: list[dict[str, Any]] = []
    for match in matches:
        try:
            obj = json.loads(match)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


def _extract_product_page_data(html_text: str) -> dict[str, Any]:
    for obj in _extract_bwlayer_objects(html_text):
        product_page_data = obj.get("productPageData", {})
        if isinstance(product_page_data, dict):
            value = product_page_data.get("value", {})
            if isinstance(value, dict):
                return value
    return {}


def _extract_inertia_page_data(html_text: str) -> dict[str, Any]:
    match = re.search(r'<div id="app" data-page="([^"]+)"', html_text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        value = html.unescape(match.group(1))
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _meta_content(html_text: str, property_name: str) -> str:
    pattern = re.compile(
        rf'<meta\s+(?:property|name)="{re.escape(property_name)}"\s+content="([^"]*)"',
        re.IGNORECASE,
    )
    match = pattern.search(html_text)
    return html.unescape(match.group(1)) if match else ""


def _authors_from_product_data(product_data: dict[str, Any]) -> list[str]:
    authors = product_data.get("author", [])
    if not isinstance(authors, list):
        return []
    return [_clean(x.get("name")) for x in authors if isinstance(x, dict)]


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean(x) for x in value]
    text = _clean(value)
    return [text] if text else []


def _build_query(title: str, volume: str) -> str:
    title = _series_from_title(title)
    return f"{title} {int(volume)}" if volume.isdigit() else title


def _score_candidate(candidate: SearchCandidate, title: str, volume: str) -> int:
    score = 0
    if _titles_match(_series_from_title(candidate.title), title):
        score += 35
    if volume and candidate.volume == volume:
        score += 45
    elif volume and candidate.volume:
        score -= 20
    if candidate.category == "漫畫":
        score += 10
    if candidate.category2:
        score += 5
    score -= min(candidate.index, 50)
    return score


def _score_metadata(
    metadata: BookwalkerMetadata,
    title: str,
    volume: str,
    author: str,
    dominant_series: str = "",
) -> int:
    score = 0
    if _titles_match(metadata.series or metadata.title, title):
        score += 45
    elif dominant_series and _normalized(metadata.series) == _normalized(dominant_series):
        score += 35
    if volume and metadata.volume == volume:
        score += 35
    elif volume and metadata.volume:
        score -= 30
    if author and _normalized(author) in _normalized(metadata.author_text):
        score += 10
    if metadata.publisher:
        score += 5
    if metadata.cover_url:
        score += 5
    return score


def _dominant_search_series(candidates: list[SearchCandidate], query_title: str) -> str:
    """Return a dominant BookWalker series for Chinese-only queries.

    BookWalker TW search handles simplified-to-traditional matching. We only
    trust that behavior when the query is Chinese-only and the top results agree
    on the same series; mixed alphanumeric titles need stricter literal checks.
    """
    if re.search(r"[A-Za-z0-9]", _series_from_title(query_title)):
        return ""

    counts: dict[str, int] = {}
    for candidate in candidates[:12]:
        series = _series_from_title(candidate.title)
        if series:
            counts[series] = counts.get(series, 0) + 1

    if not counts:
        return ""

    series, count = max(counts.items(), key=lambda item: item[1])
    return series if count >= 3 else ""


def _titles_match(candidate: str, expected: str) -> bool:
    c = _normalized(_series_from_title(candidate))
    e = _normalized(_series_from_title(expected))
    return bool(c and e and (c in e or e in c))


def _extract_volume_from_title(title: str) -> str:
    match = re.search(r"\(\s*(\d+)\s*\)\s*$", unicodedata.normalize("NFKC", title))
    return (match.group(1).lstrip("0") or "0") if match else ""


def _series_from_title(title: str) -> str:
    value = unicodedata.normalize("NFKC", _clean(title))
    return re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", value).strip()


def _normalize_cover_url(url: str) -> str:
    return re.sub(r"_1(?=\.[A-Za-z0-9]+$)", "", _clean(url))


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", _clean(value).lower())
    value = value.replace(MULTIPLICATION_SIGN, "x")
    return re.sub(r"[\s_\-・.()【】\\[\\]:]+", "", value)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return html.unescape(str(value)).strip()
