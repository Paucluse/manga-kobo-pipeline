"""Re-scrape BookWalker metadata for already imported records."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from manga_pipeline.bookwalker_tw import BookwalkerMetadata, search_bookwalker_tw
from manga_pipeline.comicinfo import write_comicinfo_to_cbz
from manga_pipeline.config import PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.epub_metadata import write_epub_metadata
from manga_pipeline.filename_parser import parse_filename
from manga_pipeline.komga import get_library_id, trigger_library_scan
from manga_pipeline.llm_metadata import normalize_with_llm
from manga_pipeline.logging_config import get_logger
from manga_pipeline.models import MangaRecord, ProcessingStatus
from manga_pipeline.pipeline import (
    _build_book_title,
    _build_clean_name,
    _build_series_name,
    _download_bookwalker_artwork,
    _sanitize_dirname,
)

logger = get_logger(__name__)


@dataclass
class RescrapeResult:
    """Result of one metadata re-scrape attempt."""

    record_id: int
    file_name: str
    status: str
    message: str = ""
    old_title: str = ""
    new_title: str = ""
    old_series: str = ""
    new_series: str = ""
    old_volume: str = ""
    new_volume: str = ""
    confidence: float = 0.0


def select_records(
    db: Database,
    *,
    ids: list[int] | None = None,
    title: str = "",
    all_records: bool = False,
    done_only: bool = True,
) -> list[MangaRecord]:
    """Select records for re-scraping."""
    selected: list[MangaRecord] = []
    seen: set[int] = set()

    if ids:
        for record_id in ids:
            record = db.get_record_by_id(record_id)
            if record and record.id not in seen:
                selected.append(record)
                seen.add(record.id or 0)

    if all_records or title:
        title_key = title.casefold().strip()
        for record in db.get_all_records():
            record_id = record.id or 0
            if record_id in seen:
                continue
            if title_key and title_key not in _record_search_text(record):
                continue
            selected.append(record)
            seen.add(record_id)

    if done_only:
        selected = [
            record for record in selected
            if record.current_status == ProcessingStatus.DONE
        ]

    return selected


def rescrape_records(
    records: list[MangaRecord],
    cfg: PipelineConfig,
    db: Database,
    *,
    dry_run: bool = False,
    relocate: bool = False,
    trigger_scan: bool = True,
) -> list[RescrapeResult]:
    """Re-scrape BookWalker metadata and update imported files."""
    results: list[RescrapeResult] = []
    changed = False

    for record in records:
        result = rescrape_record(
            record,
            cfg,
            db,
            dry_run=dry_run,
            relocate=relocate,
        )
        results.append(result)
        changed = changed or result.status == "updated"

    if changed and trigger_scan and not dry_run:
        _trigger_komga_scan(cfg)

    return results


def rescrape_record(
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
    *,
    dry_run: bool = False,
    relocate: bool = False,
) -> RescrapeResult:
    """Re-scrape and update a single record."""
    record_id = record.id
    assert record_id is not None

    metadata = _lookup_bookwalker(record, cfg)
    if metadata is None:
        return RescrapeResult(
            record_id=record_id,
            file_name=record.file_name,
            status="no_match",
            message="BookWalker TW did not return an acceptable match",
            old_title=record.title,
            old_series=record.series,
            old_volume=record.volume,
        )

    updated = _record_with_metadata(record, metadata)
    result = RescrapeResult(
        record_id=record_id,
        file_name=record.file_name,
        status="updated",
        old_title=record.title,
        new_title=updated.title,
        old_series=record.series,
        new_series=updated.series,
        old_volume=record.volume,
        new_volume=updated.volume,
        confidence=metadata.confidence,
    )

    if dry_run:
        result.status = "would_update"
        return result

    epub_path = _find_imported_epub(record, cfg)
    if epub_path and relocate:
        epub_path = _relocate_imported_epub(epub_path, updated, cfg)

    _rewrite_record_files(updated, cfg, epub_path)
    db.update_status(
        record_id,
        record.current_status,
        title=updated.title,
        author=updated.author,
        series=updated.series,
        volume=updated.volume,
        publisher=updated.publisher,
        summary=updated.summary,
        cover_url=updated.cover_url,
        source_url=updated.source_url,
        isbn=updated.isbn,
        page_count=updated.page_count,
        confidence=str(updated.confidence),
        converted_path=str(epub_path) if epub_path else record.converted_path,
        library_book_id=_build_series_name(updated) or record.library_book_id,
    )
    return result


def _lookup_bookwalker(
    record: MangaRecord,
    cfg: PipelineConfig,
) -> BookwalkerMetadata | None:
    parsed = parse_filename(record.file_name)
    try:
        llm_metadata = normalize_with_llm(record.file_name, parsed, cfg.metadata)
    except Exception as e:
        logger.warning("[ID:%s] LLM filename normalization failed: %s", record.id, e)
        llm_metadata = None

    if llm_metadata and llm_metadata.confidence >= 0.65:
        parsed.title = llm_metadata.title or parsed.title
        parsed.series = llm_metadata.title or parsed.series
        parsed.author = llm_metadata.author or parsed.author
        parsed.publisher = llm_metadata.publisher or parsed.publisher
        parsed.volume = llm_metadata.volume or parsed.volume

    title = parsed.title or record.title or record.series
    volume = parsed.volume or record.volume
    author = parsed.author or record.author

    if not cfg.metadata.bookwalker_tw_enabled or not title:
        return None

    metadata = search_bookwalker_tw(
        title,
        volume=volume,
        author=author,
        max_candidates=cfg.metadata.bookwalker_tw_max_candidates,
    )
    if metadata is None:
        return None
    if metadata.confidence < cfg.metadata.bookwalker_tw_min_confidence:
        logger.info(
            "[ID:%s] BookWalker TW match below threshold: %.2f < %.2f",
            record.id,
            metadata.confidence,
            cfg.metadata.bookwalker_tw_min_confidence,
        )
        return None
    return metadata


def _record_with_metadata(
    record: MangaRecord,
    metadata: BookwalkerMetadata,
) -> MangaRecord:
    return replace(
        record,
        title=metadata.series or record.title,
        series=metadata.series or record.series or record.title,
        volume=metadata.volume or record.volume,
        author=metadata.author_text or record.author,
        publisher=metadata.publisher or record.publisher,
        summary=metadata.summary,
        cover_url=metadata.cover_url,
        source_url=metadata.detail_url,
        isbn=metadata.isbn,
        page_count=metadata.page_count,
        confidence=max(record.confidence, metadata.confidence),
    )


def _rewrite_record_files(
    record: MangaRecord,
    cfg: PipelineConfig,
    epub_path: Path | None,
) -> None:
    archive_path = Path(record.archive_path)
    if archive_path.is_file():
        write_comicinfo_to_cbz(
            archive_path,
            title=_build_book_title(record),
            series=_build_series_name(record),
            number=record.volume,
            writer=record.author,
            publisher=record.publisher,
            summary=record.summary,
            web=record.source_url,
            language_iso=cfg.metadata.default_language,
            manga=cfg.kobo.manga_style,
            tags=cfg.metadata.default_tags,
        )

    if epub_path and epub_path.is_file():
        write_epub_metadata(
            epub_path,
            title=_build_book_title(record),
            series=_build_series_name(record),
            number=record.volume,
            writer=record.author,
            language_iso=cfg.metadata.default_language,
            summary=record.summary,
            manga=cfg.kobo.manga_style,
        )
        _download_bookwalker_artwork(record, epub_path, epub_path.parent, cfg)


def _find_imported_epub(record: MangaRecord, cfg: PipelineConfig) -> Path | None:
    candidates: list[Path] = []
    if record.converted_path:
        candidates.append(Path(record.converted_path))
    if record.archive_path:
        archive_name = Path(record.archive_path).with_suffix(".kepub.epub").name
        candidates.append(cfg.paths.komga_library / _sanitize_dirname(record.series) / archive_name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    names = []
    if record.converted_path:
        names.append(Path(record.converted_path).name)
    if record.archive_path:
        names.append(Path(record.archive_path).with_suffix(".kepub.epub").name)

    for name in dict.fromkeys(names):
        matches = list(cfg.paths.komga_library.rglob(name))
        if matches:
            return matches[0]

    return None


def _relocate_imported_epub(
    epub_path: Path,
    record: MangaRecord,
    cfg: PipelineConfig,
) -> Path:
    series_name = _build_series_name(record) or "Unknown"
    dest_dir = cfg.paths.komga_library / series_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{_build_clean_name(record)}.kepub.epub"
    if epub_path == dest_path:
        return epub_path
    if dest_path.exists():
        raise FileExistsError(f"Destination already exists: {dest_path}")

    old_cover = epub_path.with_suffix(".jpg")
    new_cover = dest_path.with_suffix(".jpg")
    shutil.move(str(epub_path), str(dest_path))
    if old_cover.is_file() and not new_cover.exists():
        shutil.move(str(old_cover), str(new_cover))
    return dest_path


def _trigger_komga_scan(cfg: PipelineConfig) -> None:
    library_id = cfg.komga.library_id
    if not library_id:
        library_id = get_library_id(
            cfg.komga.base_uri,
            cfg.komga.user,
            cfg.komga.password,
        ) or ""
    if not library_id:
        logger.warning("No Komga library ID found, skipping scan trigger.")
        return
    result = trigger_library_scan(
        base_uri=cfg.komga.base_uri,
        library_id=library_id,
        user=cfg.komga.user,
        password=cfg.komga.password,
    )
    if not result.success:
        logger.warning("Komga scan trigger failed: %s", result.error)


def _record_search_text(record: MangaRecord) -> str:
    return " ".join(
        [
            record.file_name,
            record.title,
            record.series,
            record.author,
            record.publisher,
        ]
    ).casefold()
