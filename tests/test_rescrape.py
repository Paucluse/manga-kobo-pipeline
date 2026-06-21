"""Tests for BookWalker metadata re-scraping."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from manga_pipeline.bookwalker_tw import BookwalkerMetadata
from manga_pipeline.config import MetadataConfig, PathsConfig, PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.models import MangaRecord, ProcessingStatus
from manga_pipeline.rescrape import rescrape_records, select_records


def test_select_records_defaults_to_done_only(tmp_path: Path) -> None:
    db = Database(tmp_path / "state" / "pipeline.db")
    try:
        done_id = db.insert_record(
            MangaRecord(
                original_path="/data/inbox/done.cbz",
                file_name="done.cbz",
                file_hash="done",
                current_status=ProcessingStatus.DONE,
                title="三只眼",
            )
        )
        db.insert_record(
            MangaRecord(
                original_path="/data/inbox/pending.cbz",
                file_name="pending.cbz",
                file_hash="pending",
                current_status=ProcessingStatus.METADATA_PARSED,
                title="三只眼",
            )
        )

        records = select_records(db, all_records=True)

        assert [record.id for record in records] == [done_id]
    finally:
        db.close()


def test_rescrape_updates_database_and_imported_files(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    cfg = _test_config(tmp_path)
    archive_path = cfg.paths.archive_cbz / "old.cbz"
    epub_path = cfg.paths.komga_library / "Old Series" / "old.kepub.epub"
    _write_cbz(archive_path)
    _write_minimal_epub(epub_path)

    db = Database(cfg.paths.state / "pipeline.db")
    try:
        record_id = db.insert_record(
            MangaRecord(
                original_path="/data/inbox/三只眼 典藏版][高田裕三][玉皇朝]Vol.02.pdf",
                file_name="三只眼 典藏版][高田裕三][玉皇朝]Vol.02.pdf",
                file_hash="hash-2",
                current_status=ProcessingStatus.DONE,
                title="三只眼 典藏版][高田裕三][玉皇朝]",
                series="三只眼 典藏版][高田裕三][玉皇朝]",
                volume="2",
                archive_path=str(archive_path),
                converted_path=str(epub_path),
                library_book_id="Old Series",
            )
        )
        record = db.get_record_by_id(record_id)
        assert record is not None

        def fake_search(
            title: str,
            volume: str = "",
            author: str = "",
            timeout: int = 15,
            max_candidates: int = 8,
        ) -> BookwalkerMetadata:
            assert title == "三只眼 典藏版"
            assert volume == "2"
            assert author == "高田裕三"
            return BookwalkerMetadata(
                product_id="278741",
                title="三隻眼 典藏版(02)",
                series="三隻眼 典藏版",
                volume="2",
                authors=["高田裕三"],
                publisher="尖端出版",
                summary="第二卷簡介",
                detail_url="https://www.bookwalker.com.tw/product/278741",
                isbn="9786263162501",
                page_count="400",
                confidence=1.0,
            )

        monkeypatch.setattr("manga_pipeline.rescrape.search_bookwalker_tw", fake_search)
        monkeypatch.setattr("manga_pipeline.rescrape._trigger_komga_scan", lambda cfg: None)

        results = rescrape_records([record], cfg, db)

        assert results[0].status == "updated"
        updated = db.get_record_by_id(record_id)
        assert updated is not None
        assert updated.title == "三隻眼 典藏版"
        assert updated.series == "三隻眼 典藏版"
        assert updated.author == "高田裕三"
        assert updated.publisher == "尖端出版"
        assert updated.summary == "第二卷簡介"
        assert updated.source_url == "https://www.bookwalker.com.tw/product/278741"

        with zipfile.ZipFile(epub_path) as zf:
            opf = ET.fromstring(zf.read("OEBPS/content.opf"))
        ns = {
            "opf": "http://www.idpf.org/2007/opf",
            "dc": "http://purl.org/dc/elements/1.1/",
        }
        assert opf.findtext(".//dc:title", namespaces=ns) == "三隻眼 典藏版 卷2"
        assert opf.findtext(".//dc:creator", namespaces=ns) == "高田裕三"
        assert opf.findtext(".//dc:description", namespaces=ns) == "第二卷簡介"
        collection = opf.find(".//opf:meta[@property='belongs-to-collection']", namespaces=ns)
        assert collection is not None
        assert collection.text == "三隻眼 典藏版"

        with zipfile.ZipFile(archive_path) as zf:
            comicinfo = zf.read("ComicInfo.xml").decode("utf-8")
        assert "<Series>三隻眼 典藏版</Series>" in comicinfo
        assert "<Summary>第二卷簡介</Summary>" in comicinfo
    finally:
        db.close()


def _test_config(tmp_path: Path) -> PipelineConfig:
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
    return PipelineConfig(
        paths=paths,
        metadata=MetadataConfig(download_bookwalker_covers=False),
    )


def _write_cbz(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("0001.jpg", b"page")


def _write_minimal_epub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        zf.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Old Title</dc:title>
  </metadata>
  <manifest/>
  <spine/>
</package>
""",
        )
