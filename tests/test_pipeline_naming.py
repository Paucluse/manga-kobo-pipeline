"""Tests for Komga-friendly pipeline naming."""

import zipfile
from pathlib import Path

from manga_pipeline.bangumi import BangumiMetadata
from manga_pipeline.bookwalker_tw import BookwalkerMetadata
from manga_pipeline.config import MetadataConfig, PathsConfig, PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.filename_parser import parse_filename
from manga_pipeline.llm_metadata import LlmMetadata
from manga_pipeline.models import MangaRecord, ProcessingStatus
from manga_pipeline.pipeline import (
    _apply_collection_title,
    _build_book_title,
    _build_clean_name,
    _build_series_name,
    _download_metadata_artwork,
    _metadata_matches_record_context,
    _metadata_search_titles,
    _step_normalize_and_archive,
    _step_parse_metadata,
)

THREE_BY_THREE_EYES = "3" + "\N{MULTIPLICATION SIGN}" + "3EYES"
FW_TILDE = "\N{FULLWIDTH TILDE}"
FW_DOT = "\N{FULLWIDTH FULL STOP}"
FW_LPAREN = "\N{FULLWIDTH LEFT PARENTHESIS}"
FW_RPAREN = "\N{FULLWIDTH RIGHT PARENTHESIS}"
DNA2_JP_SERIES = f"D・N・A2 {FW_TILDE}何処かで失くしたあいつのアイツ{FW_TILDE}"
DNA2_JP_V3 = f"{DNA2_JP_SERIES} 3"
GUNDAM_TW_TITLE = f"機動戰士鋼彈 光輝的阿{FW_DOT}巴瓦{FW_DOT}空"
GUNDAM_JP_TITLE = "機動戦士ガンダム 光芒のア・バオア・クー"


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


def test_collection_title_can_use_second_bracket_when_first_is_author_alias() -> None:
    parsed = parse_filename("[銃夢(第一部)[木城ゆきと][東立]Vol_01.rar")

    _apply_collection_title(
        parsed,
        "[木城ゆきと_木城幸人][銃夢][東立][aaa874160][9完]",
    )

    assert parsed.title == "銃夢"
    assert parsed.series == "銃夢"
    assert parsed.author == "木城ゆきと_木城幸人"
    assert parsed.publisher == "東立"
    assert parsed.volume == "1"


def test_short_collection_title_rejects_substring_false_positive() -> None:
    record = MangaRecord(collection_title="[木城ゆきと_木城幸人][銃夢][東立][9完]")
    parsed = parse_filename("[銃夢(第一部)[木城ゆきと][東立]Vol_03.rar")
    _apply_collection_title(parsed, record.collection_title)
    false_positive = "木城ゆきと画集 ARS MAGNA デビューから銃夢火星戦記まで"
    metadata = BookwalkerMetadata(
        title=false_positive,
        series=false_positive,
        authors=["木城ゆきと"],
        confidence=0.95,
    )

    assert _metadata_matches_record_context(metadata, parsed, record) is False


def test_plain_collection_title_rejects_unrequested_edition_or_sequel() -> None:
    record = MangaRecord(collection_title="[木城ゆきと_木城幸人][銃夢][東立][9完]")
    parsed = parse_filename("[銃夢(第一部)[木城ゆきと][東立]Vol_09.rar")
    _apply_collection_title(parsed, record.collection_title)

    complete = BookwalkerMetadata(
        title="銃夢 完全版 v1",
        series="銃夢 完全版",
        volume="1",
        authors=["木城ゆきと"],
        confidence=0.9,
    )
    last_order = BookwalkerMetadata(
        title="銃夢 Last Order v9",
        series="銃夢 Last Order",
        volume="9",
        authors=["木城ゆきと"],
        confidence=1.0,
    )

    assert _metadata_matches_record_context(complete, parsed, record) is False
    assert _metadata_matches_record_context(last_order, parsed, record) is False


def test_prompt_console_schema_adds_bookwalker_jp_search_title() -> None:
    record = MangaRecord(
        file_name=(
            f"[Comic][{GUNDAM_TW_TITLE}]"
            "[Ark Performance][角川][ZZGUNDAM][1完].zip"
        )
    )
    parsed = parse_filename(record.file_name)
    llm = LlmMetadata(
        title=GUNDAM_TW_TITLE,
        title_tw=GUNDAM_TW_TITLE,
        title_jp=GUNDAM_JP_TITLE,
        search_titles=[GUNDAM_JP_TITLE],
        author="Ark Performance",
        volume="1",
        confidence=0.95,
    )

    titles = _metadata_search_titles(parsed, record, llm, "jp")

    assert titles[0] == GUNDAM_JP_TITLE


def test_gunnm_collection_adds_bookwalker_jp_publisher_search_title() -> None:
    record = MangaRecord(
        file_name="[銃夢(第一部)[木城ゆきと][東立]Vol_01.rar",
        collection_title="[木城ゆきと_木城幸人][銃夢][東立][9完]",
    )
    parsed = parse_filename(record.file_name)
    _apply_collection_title(parsed, record.collection_title)

    titles = _metadata_search_titles(parsed, record, None, "jp")

    assert f"銃夢{FW_LPAREN}講談社{FW_RPAREN}" in titles


def test_llm_result_is_not_overridden_by_collection_title(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        metadata=MetadataConfig(
            bookwalker_tw_enabled=False,
            bookwalker_jp_enabled=False,
            bangumi_enabled=False,
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


def test_bangumi_fallback_when_bookwalker_has_no_match(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        metadata=MetadataConfig(
            bookwalker_tw_enabled=True,
            bookwalker_jp_enabled=False,
            bangumi_enabled=True,
            bangumi_min_confidence=0.65,
            llm_normalize_enabled=False,
        )
    )
    db = Database(tmp_path / "pipeline.db")
    record = MangaRecord(
        file_name="MONSTER-怪物- v001.zip",
        file_hash="monster-v1",
        collection_title="MONSTER-怪物-",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)

    monkeypatch.setattr("manga_pipeline.pipeline.search_bookwalker_tw", lambda *_a, **_k: None)

    def fake_bangumi(*_args: object, **_kwargs: object) -> BangumiMetadata:
        return BangumiMetadata(
            subject_id="2081",
            title="怪物",
            series="怪物",
            authors=["浦泽直树"],
            publisher="小学館",
            summary="故事简介",
            cover_url="https://img/monster.jpg",
            detail_url="https://bgm.tv/subject/2081",
            confidence=0.85,
        )

    monkeypatch.setattr("manga_pipeline.pipeline.search_bangumi", fake_bangumi)

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.title == "怪物"
    assert updated.series == "怪物"
    assert updated.volume == "1"
    assert updated.author == "浦泽直树"
    assert updated.publisher == "小学館"
    assert updated.summary == "故事简介"
    assert updated.cover_url == "https://img/monster.jpg"
    assert updated.source_url == "https://bgm.tv/subject/2081"


def test_bookwalker_jp_second_layer_before_bangumi(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        metadata=MetadataConfig(
            bookwalker_tw_enabled=True,
            bookwalker_jp_enabled=True,
            bookwalker_jp_min_confidence=0.65,
            bangumi_enabled=True,
            llm_normalize_enabled=True,
            llm_model="gemini-3.1-flash-lite",
        )
    )
    db = Database(tmp_path / "pipeline.db")
    record = MangaRecord(
        file_name="[Kmoe][DNA][桂正和]卷03.epub",
        file_hash="dna-v3",
        collection_title="DNA²",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)

    monkeypatch.setattr("manga_pipeline.pipeline.search_bookwalker_tw", lambda *_a, **_k: None)

    def fake_normalize(*_args: object, **_kwargs: object) -> LlmMetadata:
        return LlmMetadata(
            title="D・N・A2",
            title_jp=DNA2_JP_SERIES,
            search_titles=["D・N・A²", "DNA2"],
            author="桂正和",
            volume="3",
            confidence=0.95,
        )

    def fake_bookwalker_jp(title: str, **_kwargs: object) -> BookwalkerMetadata:
        assert title == DNA2_JP_SERIES
        return BookwalkerMetadata(
            product_id="de77397f43-76ca-44f5-aa61-20b09f6600ce",
            title=DNA2_JP_V3,
            series=DNA2_JP_SERIES,
            volume="3",
            authors=["桂正和"],
            publisher="集英社",
            summary="第三卷简介",
            cover_url="https://c.bookwalker.jp/869726/t_700x780.jpg",
            detail_url="https://bookwalker.jp/de77397f43-76ca-44f5-aa61-20b09f6600ce/",
            confidence=0.95,
        )

    def fail_bangumi(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Bangumi should not run when BookWalker JP matches")

    monkeypatch.setattr("manga_pipeline.pipeline.normalize_with_llm", fake_normalize)
    monkeypatch.setattr("manga_pipeline.pipeline.search_bookwalker_jp", fake_bookwalker_jp)
    monkeypatch.setattr("manga_pipeline.pipeline.search_bangumi", fail_bangumi)

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.title == DNA2_JP_SERIES
    assert updated.series == DNA2_JP_SERIES
    assert updated.volume == "3"
    assert updated.publisher == "集英社"
    assert updated.source_url == "https://bookwalker.jp/de77397f43-76ca-44f5-aa61-20b09f6600ce/"


def test_collection_edition_rejects_bookwalker_false_positive(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        metadata=MetadataConfig(
            bookwalker_tw_enabled=True,
            bookwalker_tw_min_confidence=0.65,
            bookwalker_jp_enabled=False,
            bangumi_enabled=True,
            bangumi_min_confidence=0.65,
            llm_normalize_enabled=True,
            llm_model="gemini-3.1-flash-lite",
        )
    )
    db = Database(tmp_path / "pipeline.db")
    record = MangaRecord(
        file_name="[Comic][SLAM.DUNK完全版][井上雄彦][天下][C.C]Vol_01.zip",
        file_hash="slam-dunk-complete-v1",
        collection_title="[SLAM.DUNK完全版][井上雄彦][天下][C.C][1-24完]",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)

    def fake_normalize(*_args: object, **_kwargs: object) -> LlmMetadata:
        return LlmMetadata(
            title="灌籃高手新裝再編版",
            title_tw="灌籃高手新裝再編版",
            title_jp="SLAM DUNK 完全版",
            search_titles=["灌籃高手新裝再編版", "SLAM DUNK 完全版"],
            author="井上雄彦",
            volume="1",
            confidence=0.95,
        )

    def fake_bookwalker_tw(*_args: object, **_kwargs: object) -> BookwalkerMetadata:
        return BookwalkerMetadata(
            product_id="251291",
            title="灌籃高手新裝再編版 (1)",
            series="灌籃高手新裝再編版",
            volume="1",
            authors=["井上雄彦"],
            publisher="尖端出版",
            cover_url="https://img/slam-new.jpg",
            detail_url="https://www.bookwalker.com.tw/product/251291",
            confidence=0.95,
        )

    def fake_bangumi(*_args: object, **_kwargs: object) -> BangumiMetadata:
        return BangumiMetadata(
            subject_id="9093",
            title="灌籃高手 完全版",
            series="灌籃高手 完全版",
            volume="1",
            authors=["井上雄彦"],
            publisher="集英社",
            cover_url="https://img/slam-complete.jpg",
            detail_url="https://bgm.tv/subject/9093",
            confidence=0.9,
        )

    monkeypatch.setattr("manga_pipeline.pipeline.normalize_with_llm", fake_normalize)
    monkeypatch.setattr("manga_pipeline.pipeline.search_bookwalker_tw", fake_bookwalker_tw)
    monkeypatch.setattr("manga_pipeline.pipeline.search_bangumi", fake_bangumi)

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.title == "灌籃高手 完全版"
    assert updated.series == "灌籃高手 完全版"
    assert updated.volume == "1"
    assert updated.source_url == "https://bgm.tv/subject/9093"


def test_dna2_collection_adds_bookwalker_jp_official_search_title() -> None:
    record = MangaRecord(
        file_name="1",
        collection_title="DNA²",
    )
    parsed = parse_filename(record.file_name)
    _apply_collection_title(parsed, record.collection_title)

    assert DNA2_JP_SERIES in _metadata_search_titles(parsed, record, None, "jp")


def test_normalize_step_accepts_image_directory_source(tmp_path: Path) -> None:
    paths = PathsConfig(
        inbox=tmp_path / "inbox",
        processing=tmp_path / "processing",
        archive_cbz=tmp_path / "archive_cbz",
        kepub_ready=tmp_path / "kepub_ready",
        komga_library=tmp_path / "komga-library",
        state=tmp_path / "state",
        manual_review=tmp_path / "manual-review",
        logs=tmp_path / "logs",
    )
    for path in paths.model_dump().values():
        Path(path).mkdir(parents=True, exist_ok=True)

    source_dir = paths.inbox / "DNA²" / "1"
    source_dir.mkdir(parents=True)
    (source_dir / "001.png").write_bytes(b"page 1")
    (source_dir / "002.jpg").write_bytes(b"page 2")

    cfg = PipelineConfig(paths=paths)
    db = Database(paths.state / "pipeline.db")
    record = MangaRecord(
        original_path=str(source_dir),
        file_name="1",
        file_hash="dna2-v1",
        current_status=ProcessingStatus.METADATA_PARSED,
        title="DNA²",
        series="DNA²",
        volume="1",
        author="桂正和",
    )
    record_id = db.insert_record(record)

    try:
        assert _step_normalize_and_archive(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.current_status == ProcessingStatus.ARCHIVED
    archive_path = Path(updated.archive_path)
    assert archive_path.name == "DNA² v001.cbz"
    with zipfile.ZipFile(archive_path) as zf:
        assert "001.png" in zf.namelist()
        assert "002.jpg" in zf.namelist()
        assert "ComicInfo.xml" in zf.namelist()


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
    _download_metadata_artwork(
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
    _download_metadata_artwork(
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
    _download_metadata_artwork(
        volume_1,
        series_dir / "蒼藍鋼鐵戰艦 v001.kepub.epub",
        series_dir,
        cfg,
    )

    assert (series_dir / "cover.jpg").read_text(encoding="utf-8").endswith("vol1.jpg")
    assert ("https://img.example/vol3.jpg", "cover.jpg") not in downloads
