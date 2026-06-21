"""Tests for Komga-friendly pipeline naming."""

from pathlib import Path

from manga_pipeline.config import MetadataConfig, PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.filename_parser import parse_filename
from manga_pipeline.llm_metadata import LlmMetadata
from manga_pipeline.models import MangaRecord, ProcessingStatus
from manga_pipeline.pipeline import (
    _apply_collection_title,
    _build_book_title,
    _build_clean_name,
    _build_series_name,
    _download_bookwalker_artwork,
    _step_parse_metadata,
)

THREE_BY_THREE_EYES = "3" + "\N{MULTIPLICATION SIGN}" + "3EYES"


def test_build_clean_name_uses_series_and_sortable_volume() -> None:
    record = MangaRecord(
        file_name=f"[Comic][{THREE_BY_THREE_EYES}][高田裕三][天下][C.C]Vol_01.zip",
        title=THREE_BY_THREE_EYES,
        series=THREE_BY_THREE_EYES,
        author="高田裕三",
        volume="1",
    )

    assert _build_series_name(record) == THREE_BY_THREE_EYES
    assert _build_clean_name(record) == f"{THREE_BY_THREE_EYES} v001"
    assert _build_book_title(record) == f"{THREE_BY_THREE_EYES} 卷1"


def test_build_clean_name_omits_author_decoration() -> None:
    record = MangaRecord(
        file_name="[Ark Performance] 苍蓝钢铁战舰 卷1.cbz",
        title="苍蓝钢铁战舰",
        series="苍蓝钢铁战舰",
        author="Ark Performance",
        volume="12",
    )

    assert _build_clean_name(record) == "苍蓝钢铁战舰 v012"


def test_collection_title_overrides_child_title() -> None:
    parsed = parse_filename("02.zip")

    _apply_collection_title(parsed, "苍蓝钢铁战舰")

    assert parsed.title == "蒼藍鋼鐵戰艦"
    assert parsed.series == "蒼藍鋼鐵戰艦"
    assert parsed.volume == "2"
    assert parsed.confidence >= 0.6


def test_collection_title_uses_loose_title_author_publisher() -> None:
    parsed = parse_filename("[3x3EYES完全版][高田裕三][玉皇朝]Vol.03.pdf")

    _apply_collection_title(parsed, "三只眼 典藏版 [高田裕三][玉皇朝]")

    assert parsed.title == "三隻眼 典藏版"
    assert parsed.series == "三隻眼 典藏版"
    assert parsed.author == "高田裕三"
    assert parsed.publisher == "玉皇朝"
    assert parsed.volume == "3"


def test_collection_title_uses_first_three_brackets_for_series_folder() -> None:
    parsed = parse_filename("[Dr.SLUMP怪博士與機器娃娃][鳥山明][東立]Vol.01.zip")

    _apply_collection_title(
        parsed,
        "[Dr.SLUMP怪博士與機器娃娃][鳥山明][東立][nearfly][15完]",
    )

    assert parsed.title == "Dr.SLUMP怪博士與機器娃娃"
    assert parsed.series == "Dr.SLUMP怪博士與機器娃娃"
    assert parsed.author == "鳥山明"
    assert parsed.publisher == "東立"
    assert parsed.volume == "1"


def test_llm_result_is_not_overridden_by_collection_title(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        metadata=MetadataConfig(
            bookwalker_tw_enabled=False,
            llm_normalize_enabled=True,
            llm_model="gemini-3.1-flash-lite",
        )
    )
    db = Database(tmp_path / "pipeline.db")
    record = MangaRecord(
        file_name="[Dr.SLUMP怪博士與機器娃娃][鳥山明][東立]Vol.01.zip",
        collection_title="[Dr.SLUMP怪博士與機器娃娃][鳥山明][東立][nearfly][15完]",
        file_hash="dr-slump-v1",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)

    def fake_normalize(*_args: object, **_kwargs: object) -> LlmMetadata:
        return LlmMetadata(
            title="Dr.SLUMP怪博士與機器娃娃",
            author="鳥山明",
            publisher="東立",
            volume="1",
            confidence=0.95,
        )

    monkeypatch.setattr("manga_pipeline.pipeline.normalize_with_llm", fake_normalize)

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.title == "Dr.SLUMP怪博士與機器娃娃"
    assert updated.series == "Dr.SLUMP怪博士與機器娃娃"
    assert updated.author == "鳥山明"
    assert updated.publisher == "東立"
    assert updated.volume == "1"


def test_bookwalker_series_cover_prefers_first_volume(
    tmp_path: Path, monkeypatch: object
) -> None:
    downloads: list[tuple[str, str]] = []

    def fake_download(url: str, destination: Path) -> bool:
        downloads.append((url, destination.name))
        destination.write_text(url, encoding="utf-8")
        return True

    monkeypatch.setattr("manga_pipeline.pipeline.download_cover", fake_download)

    cfg = PipelineConfig()
    series_dir = tmp_path / "蒼藍鋼鐵戰艦"
    series_dir.mkdir()

    volume_2 = MangaRecord(
        file_name="蒼藍鋼鐵戰艦 v002.cbz",
        title="蒼藍鋼鐵戰艦",
        series="蒼藍鋼鐵戰艦",
        volume="2",
        cover_url="https://img.example/vol2.jpg",
    )
    _download_bookwalker_artwork(
        volume_2,
        series_dir / "蒼藍鋼鐵戰艦 v002.kepub.epub",
        series_dir,
        cfg,
    )

    volume_3 = MangaRecord(
        file_name="蒼藍鋼鐵戰艦 v003.cbz",
        title="蒼藍鋼鐵戰艦",
        series="蒼藍鋼鐵戰艦",
        volume="3",
        cover_url="https://img.example/vol3.jpg",
    )
    _download_bookwalker_artwork(
        volume_3,
        series_dir / "蒼藍鋼鐵戰艦 v003.kepub.epub",
        series_dir,
        cfg,
    )

    assert (series_dir / "cover.jpg").read_text(encoding="utf-8").endswith("vol2.jpg")

    volume_1 = MangaRecord(
        file_name="蒼藍鋼鐵戰艦 v001.cbz",
        title="蒼藍鋼鐵戰艦",
        series="蒼藍鋼鐵戰艦",
        volume="001",
        cover_url="https://img.example/vol1.jpg",
    )
    _download_bookwalker_artwork(
        volume_1,
        series_dir / "蒼藍鋼鐵戰艦 v001.kepub.epub",
        series_dir,
        cfg,
    )

    assert (series_dir / "cover.jpg").read_text(encoding="utf-8").endswith("vol1.jpg")
    assert ("https://img.example/vol3.jpg", "cover.jpg") not in downloads
