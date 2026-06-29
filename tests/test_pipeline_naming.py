"""Tests for Komga-friendly pipeline naming."""

import zipfile
from pathlib import Path

from manga_pipeline.bangumi import BangumiMetadata
from manga_pipeline.bookwalker_tw import BookwalkerMetadata
from manga_pipeline.config import MetadataConfig, PathsConfig, PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.filename_parser import parse_filename
from manga_pipeline.llm_metadata import LlmMetadata, ScrapeVerification
from manga_pipeline.models import MangaRecord, ProcessingStatus
from manga_pipeline.pipeline import (
    _apply_collection_title,
    _advance_record,
    _build_book_title,
    _build_clean_name,
    _build_series_name,
    _canonical_series_name,
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


def test_canonical_series_name_strips_provider_label_suffix() -> None:
    assert _canonical_series_name("最終兵器彼女（ビッグコミックス）") == "最終兵器彼女"


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


def test_collection_title_can_use_second_bracket_when_first_is_author_group() -> None:
    parsed = parse_filename(
        "[GAINAX×貞本義行][新世紀EVANGELION福音戰士][東販][C.C].Vol.01.zip"
    )

    _apply_collection_title(
        parsed,
        "[GAINAX×貞本義行][新世紀EVANGELION福音戰士][東販][C.C][14完]",
    )

    assert parsed.title == "新世紀EVANGELION福音戰士"
    assert parsed.series == "新世紀EVANGELION福音戰士"
    assert parsed.author == "GAINAX×貞本義行"
    assert parsed.publisher == "東販"
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


def test_collection_title_allows_evangelion_decorative_token() -> None:
    record = MangaRecord(
        collection_title="[GAINAX×貞本義行][新世紀EVANGELION福音戰士][東販][C.C][14完]"
    )
    parsed = parse_filename(
        "[GAINAX×貞本義行][新世紀EVANGELION福音戰士][東販][C.C].Vol.01.zip"
    )
    _apply_collection_title(parsed, record.collection_title)
    metadata = BookwalkerMetadata(title="新世紀福音戰士", series="新世紀福音戰士")

    assert _metadata_matches_record_context(metadata, parsed, record) is True


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


def test_source_filename_volume_wins_over_bangumi_subject_volume(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        metadata=MetadataConfig(
            bookwalker_tw_enabled=False,
            bookwalker_jp_enabled=False,
            bangumi_enabled=True,
            bangumi_min_confidence=0.65,
            llm_normalize_enabled=False,
        )
    )
    db = Database(tmp_path / "pipeline.db")
    record = MangaRecord(
        file_name="BTX16.zip",
        file_hash="btx-v16",
        collection_title="[B'TX鋼鐵神兵][車田正美][東贩][C.C][1-16完]",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)

    def fake_bangumi(*_args: object, **_kwargs: object) -> BangumiMetadata:
        return BangumiMetadata(
            subject_id="207906",
            title="B'T-X (1)",
            series="B'T-X",
            volume="1",
            authors=["車田 正美"],
            publisher="角川書店",
            confidence=0.9,
        )

    monkeypatch.setattr("manga_pipeline.pipeline.search_bangumi", fake_bangumi)

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.title == "B'T-X"
    assert updated.series == "B'T-X"
    assert updated.volume == "16"
    assert updated.author == "車田 正美"


def test_bangumi_candidate_requires_llm_confirmation_when_verify_enabled(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        metadata=MetadataConfig(
            bookwalker_tw_enabled=False,
            bookwalker_jp_enabled=False,
            bangumi_enabled=True,
            bangumi_min_confidence=0.65,
            llm_normalize_enabled=False,
            llm_verify_scrape_enabled=True,
            confidence_auto_accept=0.65,
        )
    )
    db = Database(tmp_path / "pipeline.db")
    source = tmp_path / "BTX16.zip"
    source.write_bytes(b"zip")
    record = MangaRecord(
        original_path=str(source),
        file_name="BTX16.zip",
        file_hash="btx-v16",
        collection_title="[B'TX鋼鐵神兵][車田正美][東贩][C.C][1-16完]",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)

    def fake_bangumi(*_args: object, **_kwargs: object) -> BangumiMetadata:
        return BangumiMetadata(
            subject_id="207906",
            title="B'T-X (1)",
            series="B'T-X",
            volume="1",
            authors=["車田 正美"],
            publisher="角川書店",
            confidence=0.9,
        )

    monkeypatch.setattr("manga_pipeline.pipeline.search_bangumi", fake_bangumi)
    monkeypatch.setattr("manga_pipeline.pipeline.verify_scrape_with_llm", lambda *_args, **_kwargs: None)

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is False
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.current_status == ProcessingStatus.NEEDS_REVIEW
    assert source.exists()


def test_high_confidence_llm_verification_overrides_provider_low_confidence(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        metadata=MetadataConfig(
            bookwalker_tw_enabled=True,
            bookwalker_tw_min_confidence=0.65,
            bookwalker_jp_enabled=False,
            bangumi_enabled=False,
            llm_normalize_enabled=True,
            llm_verify_scrape_enabled=True,
            llm_model="gemini-3.1-flash-lite",
            confidence_auto_accept=0.65,
        )
    )
    db = Database(tmp_path / "pipeline.db")
    record = MangaRecord(
        file_name="[高橋留美子][亂馬1／2][join].Vol.38.zip",
        file_hash="ranma-v38",
        collection_title="[高橋留美子][亂馬1／2][大然][join]38全",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)

    def fake_normalize(*_args: object, **_kwargs: object) -> LlmMetadata:
        return LlmMetadata(
            title="亂馬1／2",
            title_tw="亂馬1/2",
            title_jp="らんま1/2",
            queries_tw=["亂馬1/2"],
            author="高橋留美子",
            volume="38",
            confidence=0.8,
        )

    def fake_bookwalker_tw(*_args: object, **_kwargs: object) -> BookwalkerMetadata:
        return BookwalkerMetadata(
            product_id="268207",
            title="亂馬 1/2 (38)",
            series="亂馬 1/2",
            volume="38",
            authors=["高橋留美子"],
            publisher="尖端出版",
            confidence=0.2,
        )

    monkeypatch.setattr("manga_pipeline.pipeline.normalize_with_llm", fake_normalize)
    monkeypatch.setattr("manga_pipeline.pipeline.search_bookwalker_tw", fake_bookwalker_tw)
    monkeypatch.setattr(
        "manga_pipeline.pipeline.verify_scrape_with_llm",
        lambda *_args, **_kwargs: ScrapeVerification(
            match=True,
            confidence=0.95,
            reason="同一作品同一卷",
        ),
    )

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.current_status == ProcessingStatus.METADATA_PARSED
    assert updated.series == "亂馬 1/2"
    assert float(updated.confidence) >= 0.95


def test_collection_series_anchor_overrides_per_volume_scrape_series(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        paths=PathsConfig(komga_library=tmp_path / "komga-library"),
        metadata=MetadataConfig(
            bookwalker_tw_enabled=False,
            bookwalker_jp_enabled=False,
            bangumi_enabled=True,
            bangumi_min_confidence=0.65,
            llm_normalize_enabled=True,
            llm_verify_scrape_enabled=True,
            llm_model="gemini-3.1-flash-lite",
            confidence_auto_accept=0.65,
        ),
    )
    db = Database(tmp_path / "pipeline.db")
    collection_dir = tmp_path / "inbox" / "五星物语"
    collection_dir.mkdir(parents=True)
    source = collection_dir / "vol_02.zip"
    source.write_bytes(b"zip")
    record = MangaRecord(
        original_path=str(source),
        file_name=source.name,
        file_hash="fss-v2",
        collection_title="五星物语",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)
    collection_calls: list[str] = []

    def fake_collection_llm(
        collection_title: str,
        filenames: list[str],
        *_args: object,
        **_kwargs: object,
    ) -> LlmMetadata:
        collection_calls.append(collection_title)
        assert filenames == ["vol_02.zip"]
        return LlmMetadata(
            title="五星物語",
            title_tw="五星物語",
            title_jp="ファイブスター物語",
            author="永野護",
            queries_bangumi=["ファイブスター物語", "五星物語", "FSS"],
            search_titles=["五星物語", "ファイブスター物語", "The Five Star Stories"],
            confidence=0.9,
        )

    def fake_volume_llm(*_args: object, **_kwargs: object) -> LlmMetadata:
        return LlmMetadata(
            title="ファイブスター物語",
            title_jp="ファイブスター物語",
            author="永野護",
            volume="2",
            confidence=0.7,
        )

    def fake_bangumi(*_args: object, **_kwargs: object) -> BangumiMetadata:
        return BangumiMetadata(
            subject_id="1772",
            title="ファイブスター物語",
            series="ファイブスター物語",
            volume="2",
            authors=["永野護"],
            confidence=0.85,
        )

    monkeypatch.setattr("manga_pipeline.pipeline.normalize_collection_with_llm", fake_collection_llm)
    monkeypatch.setattr("manga_pipeline.pipeline.normalize_with_llm", fake_volume_llm)
    monkeypatch.setattr("manga_pipeline.pipeline.search_bangumi", fake_bangumi)
    monkeypatch.setattr(
        "manga_pipeline.pipeline.verify_scrape_with_llm",
        lambda *_args, **_kwargs: ScrapeVerification(
            match=True,
            confidence=0.9,
            reason="同一作品不同语言标题",
        ),
    )

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
        anchor = db.get_series_anchor("五星物语")
    finally:
        db.close()

    assert collection_calls == ["五星物语"]
    assert anchor is not None
    assert anchor.canonical_series == "五星物語"
    assert updated is not None
    assert updated.current_status == ProcessingStatus.METADATA_PARSED
    assert updated.title == "五星物語"
    assert updated.series == "五星物語"
    assert updated.volume == "2"
    assert updated.author == "永野護"


def test_numeric_collection_child_directory_volume_overrides_llm_and_scrape(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        paths=PathsConfig(komga_library=tmp_path / "komga-library"),
        metadata=MetadataConfig(
            bookwalker_tw_enabled=False,
            bookwalker_jp_enabled=False,
            bangumi_enabled=True,
            bangumi_min_confidence=0.65,
            llm_normalize_enabled=True,
            llm_verify_scrape_enabled=False,
            llm_model="gemini-3.1-flash-lite",
            confidence_auto_accept=0.65,
        ),
    )
    db = Database(tmp_path / "pipeline.db")
    source_dir = tmp_path / "inbox" / "草莓100%" / "2"
    source_dir.mkdir(parents=True)
    (source_dir / "001.jpg").write_bytes(b"page")
    record = MangaRecord(
        original_path=str(source_dir),
        file_name=source_dir.name,
        file_hash="ichigo-v2",
        collection_title="草莓100%",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)

    def fake_collection_llm(*_args: object, **_kwargs: object) -> LlmMetadata:
        return LlmMetadata(
            title="草莓100%",
            title_tw="草莓100%",
            title_jp="いちご100%",
            queries_bangumi=["いちご100%", "草莓100%"],
            confidence=0.9,
        )

    def fake_volume_llm(*_args: object, **_kwargs: object) -> LlmMetadata:
        return LlmMetadata(
            title="草莓100%",
            title_jp="いちご100%",
            volume="7",
            confidence=0.8,
        )

    def fake_bangumi(*_args: object, **_kwargs: object) -> BangumiMetadata:
        return BangumiMetadata(
            subject_id="1234",
            title="いちご100%",
            series="いちご100%",
            volume="11",
            authors=["河下水希"],
            confidence=0.9,
        )

    monkeypatch.setattr("manga_pipeline.pipeline.normalize_collection_with_llm", fake_collection_llm)
    monkeypatch.setattr("manga_pipeline.pipeline.normalize_with_llm", fake_volume_llm)
    monkeypatch.setattr("manga_pipeline.pipeline.search_bangumi", fake_bangumi)

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.current_status == ProcessingStatus.METADATA_PARSED
    assert updated.series == "草莓100%"
    assert updated.volume == "2"


def test_discovered_record_advances_to_parse_stage(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig()
    db = Database(tmp_path / "pipeline.db")
    source = tmp_path / "inbox" / "草莓100%" / "1"
    source.mkdir(parents=True)
    (source / "001.jpg").write_bytes(b"page")
    record = MangaRecord(
        original_path=str(source),
        file_name="1",
        file_hash="ichigo-v1",
        collection_title="草莓100%",
        current_status=ProcessingStatus.DISCOVERED,
    )
    record_id = db.insert_record(record)
    record.id = record_id
    seen: dict[str, object] = {}

    def fake_parse(
        parsed_record_id: int,
        parsed_record: MangaRecord,
        *_args: object,
    ) -> bool:
        seen["record_id"] = parsed_record_id
        seen["status"] = parsed_record.current_status
        return True

    monkeypatch.setattr("manga_pipeline.pipeline._step_parse_metadata", fake_parse)

    try:
        assert _advance_record(record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert seen == {
        "record_id": record_id,
        "status": ProcessingStatus.WAITING_STABLE,
    }
    assert updated is not None
    assert updated.current_status == ProcessingStatus.WAITING_STABLE


def test_larger_same_volume_replaces_existing_generated_artifacts(
    tmp_path: Path,
) -> None:
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
    cfg = PipelineConfig(paths=paths)
    db = Database(tmp_path / "pipeline.db")
    source = paths.inbox / "vol_01卷.zip"
    source.parent.mkdir(parents=True)
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("001.jpg", b"x" * 2048)

    record = MangaRecord(
        original_path=str(source),
        file_name=source.name,
        file_hash="larger-replacement-v1",
        current_status=ProcessingStatus.METADATA_PARSED,
        title="異世界歸來的舅舅",
        series="異世界歸來的舅舅",
        volume="1",
    )
    record_id = db.insert_record(record)
    clean_name = _build_clean_name(record)
    target_archive = paths.archive_cbz / f"{clean_name}.cbz"
    ready_path = paths.kepub_ready / f"{clean_name}.kepub.epub"
    imported_path = paths.komga_library / record.series / f"{clean_name}.kepub.epub"
    for path, content in [
        (target_archive, b"old"),
        (ready_path, b"old ready"),
        (ready_path.with_suffix(".jpg"), b"old ready cover"),
        (imported_path, b"old imported"),
        (imported_path.with_suffix(".jpg"), b"old imported cover"),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    try:
        assert _step_normalize_and_archive(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.current_status == ProcessingStatus.ARCHIVED
    assert Path(updated.archive_path) == target_archive
    assert target_archive.is_file()
    assert not ready_path.exists()
    assert not ready_path.with_suffix(".jpg").exists()
    assert not imported_path.exists()
    assert not imported_path.with_suffix(".jpg").exists()

    backups = list((paths.processing / "replacement-backups").glob("*"))
    assert len(backups) == 1
    assert (backups[0] / "archive_cbz" / target_archive.name).read_bytes() == b"old"
    assert (backups[0] / "kepub_ready" / ready_path.name).read_bytes() == b"old ready"
    assert (
        backups[0] / "komga-library" / record.series / imported_path.name
    ).read_bytes() == b"old imported"


def test_same_volume_replacement_rejects_source_that_is_not_larger(
    tmp_path: Path,
) -> None:
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
    cfg = PipelineConfig(paths=paths)
    db = Database(tmp_path / "pipeline.db")
    source = paths.inbox / "vol_01卷.zip"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"small")

    record = MangaRecord(
        original_path=str(source),
        file_name=source.name,
        file_hash="small-replacement-v1",
        current_status=ProcessingStatus.METADATA_PARSED,
        title="異世界歸來的舅舅",
        series="異世界歸來的舅舅",
        volume="1",
    )
    record_id = db.insert_record(record)
    target_archive = paths.archive_cbz / f"{_build_clean_name(record)}.cbz"
    target_archive.parent.mkdir(parents=True)
    target_archive.write_bytes(b"larger existing archive")

    try:
        assert _step_normalize_and_archive(record_id, record, cfg, db) is False
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.current_status == ProcessingStatus.FAILED
    assert "source is not larger" in updated.error_message
    assert target_archive.read_bytes() == b"larger existing archive"


def test_llm_enabled_parse_does_not_fallback_to_local_parser(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        metadata=MetadataConfig(
            llm_normalize_enabled=True,
            llm_model="gemini-3.1-flash-lite",
            bookwalker_tw_enabled=False,
            bookwalker_jp_enabled=False,
            bangumi_enabled=True,
        )
    )
    db = Database(tmp_path / "pipeline.db")
    record = MangaRecord(
        file_name="[高槁真][最終兵器少女][尖端].Vol.05.zip",
        file_hash="saikano-v5",
        collection_title="[高槁真][最終兵器少女][尖端][7全]",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)

    def fail_bangumi(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("provider search should not run without LLM parse")

    monkeypatch.setattr("manga_pipeline.pipeline.normalize_with_llm", lambda *_a, **_k: None)
    monkeypatch.setattr("manga_pipeline.pipeline.search_bangumi", fail_bangumi)

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is False
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.current_status == ProcessingStatus.NEEDS_REVIEW
    assert updated.error_message == "LLM filename normalization unavailable"


def test_llm_verification_decides_cross_language_provider_match(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg = PipelineConfig(
        metadata=MetadataConfig(
            bookwalker_tw_enabled=False,
            bookwalker_jp_enabled=False,
            bangumi_enabled=True,
            bangumi_min_confidence=0.65,
            llm_normalize_enabled=True,
            llm_verify_scrape_enabled=True,
            llm_model="gemini-3.1-flash-lite",
            confidence_auto_accept=0.65,
        )
    )
    db = Database(tmp_path / "pipeline.db")
    record = MangaRecord(
        file_name="[高槁真][最終兵器少女][尖端].Vol.01.zip",
        file_hash="saikano-v1",
        collection_title="[高槁真][最終兵器少女][尖端][7全]",
        current_status=ProcessingStatus.WAITING_STABLE,
    )
    record_id = db.insert_record(record)

    def fake_normalize(*_args: object, **_kwargs: object) -> LlmMetadata:
        return LlmMetadata(
            title="最終兵器少女",
            title_tw="最終兵器少女",
            title_jp="最終兵器彼女",
            queries_bangumi=["最終兵器彼女"],
            author="高橋しん",
            volume="1",
            confidence=0.8,
        )

    def fake_bangumi(*_args: object, **_kwargs: object) -> BangumiMetadata:
        return BangumiMetadata(
            subject_id="980",
            title="最終兵器彼女",
            series="最終兵器彼女",
            volume="1",
            authors=["高橋しん"],
            publisher="小学館",
            confidence=0.75,
        )

    monkeypatch.setattr("manga_pipeline.pipeline.normalize_with_llm", fake_normalize)
    monkeypatch.setattr("manga_pipeline.pipeline.search_bangumi", fake_bangumi)
    monkeypatch.setattr(
        "manga_pipeline.pipeline.verify_scrape_with_llm",
        lambda *_args, **_kwargs: ScrapeVerification(
            match=True,
            confidence=0.9,
            reason="中文译名与日文原名为同一作品",
        ),
    )

    try:
        assert _step_parse_metadata(record_id, record, cfg, db) is True
        updated = db.get_record_by_id(record_id)
    finally:
        db.close()

    assert updated is not None
    assert updated.current_status == ProcessingStatus.METADATA_PARSED
    assert updated.series == "最終兵器彼女"
    assert updated.volume == "1"


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


def test_llm_search_result_is_not_overridden_by_collection_edition_parse(
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
    assert updated.title == "灌籃高手新裝再編版"
    assert updated.series == "灌籃高手新裝再編版"
    assert updated.volume == "1"
    assert updated.source_url == "https://www.bookwalker.com.tw/product/251291"


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
