"""Tests for Komga-friendly pipeline naming."""

from pathlib import Path

from manga_pipeline.config import PipelineConfig
from manga_pipeline.models import MangaRecord
from manga_pipeline.pipeline import (
    _build_book_title,
    _build_clean_name,
    _build_series_name,
    _download_bookwalker_artwork,
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
