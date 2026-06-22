"""Tests for Bangumi fallback metadata parsing."""

from __future__ import annotations

from manga_pipeline.bangumi import parse_subject, search_bangumi


def test_parse_subject_extracts_book_metadata() -> None:
    subject = {
        "id": 1814,
        "type": 1,
        "name": "MONSTER (1)",
        "name_cn": "怪物",
        "summary": "天才外科医生追查怪物的故事。",
        "date": "1995-06",
        "images": {
            "large": "https://lain.bgm.tv/pic/cover/l/example.jpg",
            "common": "https://lain.bgm.tv/pic/cover/c/example.jpg",
        },
        "infobox": [
            {"key": "作者", "value": "浦泽直树"},
            {"key": "出版社", "value": "小学館"},
            {"key": "ISBN", "value": "9784091836517"},
            {"key": "页数", "value": "216"},
        ],
        "tags": [{"name": "漫画"}, {"name": "悬疑"}],
    }

    metadata = parse_subject(subject)

    assert metadata.subject_id == "1814"
    assert metadata.title == "怪物"
    assert metadata.series == "怪物"
    assert metadata.volume == "1"
    assert metadata.author_text == "浦泽直树"
    assert metadata.publisher == "小学館"
    assert metadata.summary == "天才外科医生追查怪物的故事。"
    assert metadata.cover_url == "https://lain.bgm.tv/pic/cover/l/example.jpg"
    assert metadata.detail_url == "https://bgm.tv/subject/1814"
    assert metadata.isbn == "9784091836517"
    assert metadata.page_count == "216"
    assert "漫画" in metadata.tags


def test_search_bangumi_matches_monster_compound_title(monkeypatch: object) -> None:
    posts: list[dict[str, object]] = []
    gets: list[str] = []

    class SearchResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            query = posts[-1]["json"]["keyword"]  # type: ignore[index]
            if query == "MONSTER (1)":
                return {
                    "data": [
                        {
                            "id": 1814,
                            "type": 1,
                            "name": "MONSTER (1)",
                            "name_cn": "",
                            "summary": "卷一简介",
                            "images": {"large": "https://img/monster-v1.jpg"},
                            "infobox": [{"key": "作者", "value": "浦泽直树"}],
                        }
                    ]
                }
            return {
                "data": [
                    {
                        "id": 2139,
                        "type": 1,
                        "name": "MONSTER",
                        "name_cn": "怪物",
                        "summary": "故事简介",
                        "images": {"large": "https://img/monster.jpg"},
                        "infobox": [{"key": "作者", "value": "浦泽直树"}],
                    }
                ]
            }

    class DetailResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "id": 1814,
                "type": 1,
                "name": "MONSTER (1)",
                "name_cn": "",
                "summary": "详情简介",
                "images": {"large": "https://img/monster-v1-detail.jpg"},
                "infobox": [
                    {"key": "作者", "value": "浦泽直树"},
                    {"key": "出版社", "value": "小学館"},
                ],
            }

    def fake_post(*_args: object, **kwargs: object) -> SearchResponse:
        posts.append(kwargs)
        return SearchResponse()

    def fake_get(url: str, **_kwargs: object) -> DetailResponse:
        gets.append(url)
        return DetailResponse()

    monkeypatch.setattr("manga_pipeline.bangumi.requests.post", fake_post)
    monkeypatch.setattr("manga_pipeline.bangumi.requests.get", fake_get)

    metadata = search_bangumi("MONSTER-怪物-", volume="1", author="浦泽直树")

    assert metadata is not None
    assert metadata.subject_id == "1814"
    assert metadata.volume == "1"
    assert metadata.confidence >= 0.8
    assert metadata.summary == "详情简介"
    assert metadata.publisher == "小学館"
    assert metadata.cover_url == "https://img/monster-v1-detail.jpg"
    assert gets == ["https://api.bgm.tv/v0/subjects/1814", "https://api.bgm.tv/v0/subjects/2139"]
    assert posts[0]["json"] == {
        "keyword": "MONSTER (1)",
        "sort": "match",
        "filter": {"type": [1]},
    }


def test_search_bangumi_matches_dna2_file_volume(monkeypatch: object) -> None:
    posts: list[dict[str, object]] = []
    gets: list[str] = []

    class SearchResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            query = posts[-1]["json"]["keyword"]  # type: ignore[index]
            if query == "D・N・A2 3":
                return {
                    "data": [
                        {
                            "id": 33268,
                            "type": 1,
                            "name": "D・N・A² 〜何処かで失くしたあいつのアイツ〜 FILE3",
                            "name_cn": "",
                            "images": {"large": "https://img/dna2-file3.jpg"},
                            "infobox": [{"key": "作者", "value": "桂正和"}],
                        }
                    ]
                }
            if query == "DNA2":
                return {
                    "data": [
                        {
                            "id": 33271,
                            "type": 1,
                            "name": "D・N・A² 〜何処かで失くしたあいつのアイツ〜",
                            "name_cn": "再造基因",
                            "images": {"large": "https://img/dna2-series.jpg"},
                            "infobox": [{"key": "作者", "value": "桂正和"}],
                        }
                    ]
                }
            return {"data": []}

    class DetailResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "id": 33268,
                "type": 1,
                "name": "D・N・A² 〜何処かで失くしたあいつのアイツ〜 FILE3",
                "name_cn": "",
                "summary": "第三卷简介",
                "images": {"large": "https://img/dna2-file3-detail.jpg"},
                "infobox": [
                    {"key": "作者", "value": "桂正和"},
                    {"key": "出版社", "value": "集英社"},
                ],
            }

    def fake_post(*_args: object, **kwargs: object) -> SearchResponse:
        posts.append(kwargs)
        return SearchResponse()

    def fake_get(url: str, **_kwargs: object) -> DetailResponse:
        gets.append(url)
        return DetailResponse()

    monkeypatch.setattr("manga_pipeline.bangumi.requests.post", fake_post)
    monkeypatch.setattr("manga_pipeline.bangumi.requests.get", fake_get)

    metadata = search_bangumi("DNA²", volume="3", author="桂正和")

    assert metadata is not None
    assert metadata.subject_id == "33268"
    assert metadata.volume == "3"
    assert metadata.confidence >= 0.65
    assert metadata.cover_url == "https://img/dna2-file3-detail.jpg"
    assert metadata.detail_url == "https://bgm.tv/subject/33268"
    assert gets == ["https://api.bgm.tv/v0/subjects/33268", "https://api.bgm.tv/v0/subjects/33271"]
    assert [post["json"]["keyword"] for post in posts][:3] == [
        "DNA2 (3)",
        "DNA2 3",
        "D・N・A2 (3)",
    ]
