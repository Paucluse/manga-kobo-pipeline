"""Tests for BookWalker Taiwan metadata parsing."""

from __future__ import annotations

import html
import json

from manga_pipeline.bookwalker_tw import (
    _build_queries,
    _clean,
    parse_product_detail,
    parse_search_candidates,
)


def test_parse_search_candidates_from_bwlayer() -> None:
    items = [
        {
            "item_id": 6320,
            "item_name": "蒼藍鋼鐵戰艦 (1)",
            "item_category": "漫畫",
            "item_category2": "少年漫畫",
            "index": 4,
        },
        {
            "item_id": 6406,
            "item_name": "蒼藍鋼鐵戰艦 (2)",
            "item_category": "漫畫",
            "item_category2": "少年漫畫",
            "index": 16,
        },
    ]
    payload = json.dumps(
        {"event": "view_item_list", "ecommerce": {"items": items}},
        ensure_ascii=False,
    )
    page = f"""
<script>
window.bwLayer = window.bwLayer || [];
window.bwLayer.push({payload});
</script>
"""

    candidates = parse_search_candidates(page)

    assert [x.product_id for x in candidates] == ["6320", "6406"]
    assert candidates[0].title == "蒼藍鋼鐵戰艦 (1)"
    assert candidates[0].volume == "1"
    assert candidates[0].category == "漫畫"


def test_parse_product_detail_from_public_page_data() -> None:
    page_data = {
        "props": {
            "meta": {"description": "第一卷簡介"},
            "productData": {
                "product_name": "蒼藍鋼鐵戰艦 (1)",
                "product_big_image": "https://img/zoom_big_6320.jpg",
                "author": [{"name": "ArkPerformance"}],
                "publisher": {"text": "長鴻出版社"},
                "product_series_name": "蒼藍鋼鐵戰艦",
                "type_tag": [{"name": "冒險"}],
                "product_detail_info": {
                    "publisher_isbn": "9789865390327",
                    "pages": "192",
                    "sell_date_start": "2016年11月12日",
                },
            },
        }
    }
    page_data_attr = html.escape(json.dumps(page_data, ensure_ascii=False), quote=True)
    page = f"""
<script>
window.bwLayer.push({{"productPageData":{{"type":"product","value":{{
  "id":"6320",
  "name":"蒼藍鋼鐵戰艦 (1)",
  "taxonomy":["漫畫","少年漫畫"],
  "product_image_url":"https://img/6320_1.jpg",
  "custom":{{"series_name":"蒼藍鋼鐵戰艦","authors":["ArkPerformance"],"vendor_name":"長鴻出版社","tags":["奇幻"]}}
}}}}}});
</script>
<div id="app" data-page="{page_data_attr}"></div>
"""

    metadata = parse_product_detail("6320", page, "https://www.bookwalker.com.tw/product/6320")

    assert metadata.product_id == "6320"
    assert metadata.title == "蒼藍鋼鐵戰艦 (1)"
    assert metadata.series == "蒼藍鋼鐵戰艦"
    assert metadata.volume == "1"
    assert metadata.author_text == "ArkPerformance"
    assert metadata.publisher == "長鴻出版社"
    assert metadata.summary == "第一卷簡介"
    assert metadata.cover_url == "https://img/zoom_big_6320.jpg"
    assert metadata.isbn == "9789865390327"
    assert metadata.page_count == "192"
    assert "奇幻" in metadata.tags
    assert "冒險" in metadata.tags


def test_build_queries_prefers_taiwan_traditional_volume_variants() -> None:
    queries = _build_queries("三只眼 典藏版", "2")

    assert "三隻眼 典藏版 2" in queries
    assert "三隻眼 典藏版 02" in queries
    assert "三隻眼 典藏版" in queries


def test_clean_removes_bookwalker_control_escape() -> None:
    assert _clean("x000B x000B ☆作品簡介") == "☆作品簡介"
