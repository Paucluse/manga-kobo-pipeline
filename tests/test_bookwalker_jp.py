"""Tests for BookWalker Japan metadata parsing."""

from __future__ import annotations

import json

from manga_pipeline.bookwalker_jp import (
    _titles_match,
    parse_product_detail,
    parse_search_candidates,
    parse_series_candidates,
)

FW_TILDE = "\N{FULLWIDTH TILDE}"
FW_LPAREN = "\N{FULLWIDTH LEFT PARENTHESIS}"
FW_RPAREN = "\N{FULLWIDTH RIGHT PARENTHESIS}"
DNA2_SERIES = f"D・N・A2 {FW_TILDE}何処かで失くしたあいつのアイツ{FW_TILDE}"
DNA2_SERIES_WITH_LABEL = f"{DNA2_SERIES}{FW_LPAREN}ジャンプコミックスDIGITAL{FW_RPAREN}"
DNA2_V3 = f"{DNA2_SERIES} 3"


def test_titles_match_rejects_short_title_substring_false_positive() -> None:
    assert _titles_match("銃夢", "銃夢")
    assert not _titles_match("木城ゆきと画集 ARS MAGNA デビューから銃夢火星戦記まで", "銃夢")
    assert not _titles_match("銃夢火星戦記", "銃夢")


def test_parse_search_candidates_reads_series_card() -> None:
    page = f"""
<div class="m-book-item ">
  <a href="https://bookwalker.jp/series/12987/list/" class="m-thumb__image">
    <img data-original="https://rimg.bookwalker.jp/459726/cover.jpg"
      title="{DNA2_SERIES_WITH_LABEL}" />
  </a>
  <span class="a-tag-comic">マンガ</span>
  <a href="https://bookwalker.jp/def1b57569-3f91-418d-b7d7-d20973659a13/"
    data-uuid="f1b57569-3f91-418d-b7d7-d20973659a13">1巻を見る</a>
</div>
"""

    candidates = parse_search_candidates(page)

    assert len(candidates) == 1
    assert candidates[0].series_url == "https://bookwalker.jp/series/12987/list/"
    assert candidates[0].detail_url == (
        "https://bookwalker.jp/def1b57569-3f91-418d-b7d7-d20973659a13/"
    )
    assert candidates[0].series == DNA2_SERIES
    assert candidates[0].category == "マンガ"


def test_parse_series_candidates_extracts_per_volume_card() -> None:
    page = f"""
<div class="m-book-item ">
  <a href="https://bookwalker.jp/de77397f43-76ca-44f5-aa61-20b09f6600ce/"
    class="m-thumb__image">
    <img data-original="https://rimg.bookwalker.jp/869726/cover.jpg"
      title="{DNA2_V3}" />
  </a>
  <span>マンガ</span>
  <a href="https://bookwalker.jp/de77397f43-76ca-44f5-aa61-20b09f6600ce/"
    class="m-book-item__title"
    title="{DNA2_V3}">
    {DNA2_V3}
  </a>
  著: 桂正和
</div>
"""

    candidates = parse_series_candidates(page)

    assert len(candidates) == 1
    assert candidates[0].product_id == "de77397f43-76ca-44f5-aa61-20b09f6600ce"
    assert candidates[0].volume == "3"
    assert candidates[0].author == "桂正和"
    assert candidates[0].cover_url == "https://rimg.bookwalker.jp/869726/cover.jpg"


def test_parse_product_detail_reads_json_ld_and_information() -> None:
    product = {
        "@context": "http://schema.org",
        "@type": "Product",
        "name": DNA2_V3,
        "image": "https://c.bookwalker.jp/869726/t_700x780.jpg",
        "description": "第三卷简介",
        "url": "https://bookwalker.jp/de77397f43-76ca-44f5-aa61-20b09f6600ce/",
        "category": "マンガ",
        "brand": {"@type": "Brand", "name": "集英社"},
    }
    breadcrumbs = {
        "@context": "http://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"item": {"@id": "/top/", "name": "電子書籍ストア"}},
            {
                "item": {
                    "@id": "https://bookwalker.jp/series/12987/list/",
                    "name": DNA2_SERIES_WITH_LABEL,
                }
            },
        ],
    }
    page = f"""
<script type="application/ld+json">{json.dumps(product, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps(breadcrumbs, ensure_ascii=False)}</script>
<dl class="t-c-detail-about-information__data">
  <dt>著者</dt>
  <dd><a data-action-label="author">桂正和(著)</a></dd>
  <dt>出版社</dt>
  <dd><a data-action-label="publisher">集英社</a></dd>
  <dt>レーベル</dt>
  <dd><a data-action-label="label">ジャンプコミックスDIGITAL</a></dd>
  <dt>配信開始日</dt><dd>2014/3/4</dd>
  <dt>ページ概数</dt><dd><button>204</button></dd>
</dl>
"""

    metadata = parse_product_detail(
        "de77397f43-76ca-44f5-aa61-20b09f6600ce",
        page,
        "https://bookwalker.jp/de77397f43-76ca-44f5-aa61-20b09f6600ce/",
    )

    assert metadata.title == DNA2_V3
    assert metadata.series == DNA2_SERIES
    assert metadata.volume == "3"
    assert metadata.author_text == "桂正和"
    assert metadata.publisher == "集英社"
    assert metadata.cover_url == "https://c.bookwalker.jp/869726/t_700x780.jpg"
    assert metadata.page_count == "204"
    assert metadata.publish_date == "2014/3/4"
