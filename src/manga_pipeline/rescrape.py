"""Re-scrape external metadata for already imported records."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from manga_pipeline.bangumi import BangumiMetadata, search_bangumi
from manga_pipeline.bookwalker_jp import search_bookwalker_jp
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
    _apply_collection_title,
    _build_book_title,
    _build_clean_name,
    _build_series_name,
    _download_metadata_artwork,
    _metadata_matches_record_context,
    _metadata_search_titles,
    _sanitize_dirname,
    _title_candidate_matches_record_context,
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
    """Re-scrape external metadata and update imported files."""
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

    metadata = _lookup_metadata(record, cfg)
    if metadata is None:
        return RescrapeResult(
            record_id=record_id,
            file_name=record.file_name,
            status="no_match",
            message="No external metadata provider returned an acceptable match",
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

    archive_path = _find_archive_cbz(record, cfg)
    if archive_path and relocate:
        archive_path = _relocate_archive_cbz(archive_path, updated, cfg)
        updated = replace(updated, archive_path=str(archive_path))

    epub_path = _find_imported_epub(record, cfg)
    if epub_path and relocate:
        epub_path = _relocate_imported_epub(epub_path, updated, cfg)
        updated = replace(updated, converted_path=str(epub_path))

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
        archive_path=str(archive_path) if archive_path else record.archive_path,
        converted_path=str(epub_path) if epub_path else record.converted_path,
        library_book_id=_build_series_name(updated) or record.library_book_id,
    )
    return result


def force_rescrape_record(
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
    *,
    provider: str,
    search_title: str,
    volume: str = "",
    author: str = "",
    dry_run: bool = False,
    relocate: bool = True,
) -> RescrapeResult:
    """Force-apply metadata from the given provider+title, bypassing ALL automatic
    filters (LLM normalization, confidence threshold, context-matching).

    This is the "manual override" path invoked from the web console. The user's
    explicit choice is treated as authoritative — no LLM or scoring can veto it.
    """
    record_id = record.id
    assert record_id is not None

    # Direct provider call — one shot, no retry loops, no filters
    try:
        if provider == "bookwalker_tw":
            metadata = search_bookwalker_tw(
                search_title,
                volume=volume or record.volume,
                author=author or record.author,
                max_candidates=cfg.metadata.bookwalker_tw_max_candidates,
            )
        elif provider == "bookwalker_jp":
            metadata = search_bookwalker_jp(
                search_title,
                volume=volume or record.volume,
                author=author or record.author,
                max_candidates=cfg.metadata.bookwalker_jp_max_candidates,
            )
        elif provider == "bangumi":
            metadata = search_bangumi(
                search_title,
                volume=volume or record.volume,
                author=author or record.author,
                max_candidates=cfg.metadata.bangumi_max_candidates,
            )
        else:
            return RescrapeResult(
                record_id=record_id,
                file_name=record.file_name,
                status="error",
                message=f"Unknown provider: {provider}",
                old_title=record.title,
                old_series=record.series,
                old_volume=record.volume,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("[ID:%s] Force-rescrape provider call failed", record_id)
        return RescrapeResult(
            record_id=record_id,
            file_name=record.file_name,
            status="error",
            message=str(e),
            old_title=record.title,
            old_series=record.series,
            old_volume=record.volume,
        )

    if metadata is None:
        return RescrapeResult(
            record_id=record_id,
            file_name=record.file_name,
            status="no_match",
            message=f"Provider '{provider}' returned no results for: {search_title}",
            old_title=record.title,
            old_series=record.series,
            old_volume=record.volume,
        )

    # Build updated record — user override: keep original volume if provider returns none
    updated = _record_with_metadata(record, metadata)
    if not updated.volume and record.volume:
        updated = replace(updated, volume=record.volume)

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

    # Write ComicInfo.xml + EPUB OPF metadata (no confidence gate here)
    archive_path = _find_archive_cbz(record, cfg)
    if archive_path and relocate:
        try:
            archive_path = _relocate_archive_cbz(archive_path, updated, cfg)
        except FileExistsError as exc:
            logger.warning("[ID:%s] Archive relocation skipped: %s", record_id, exc)
        updated = replace(updated, archive_path=str(archive_path))

    epub_path = _find_imported_epub(record, cfg)
    if epub_path and relocate:
        try:
            epub_path = _relocate_imported_epub(epub_path, updated, cfg)
        except FileExistsError as exc:
            logger.warning("[ID:%s] EPUB relocation skipped: %s", record_id, exc)
        updated = replace(updated, converted_path=str(epub_path))

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
        archive_path=str(archive_path) if archive_path else record.archive_path,
        converted_path=str(epub_path) if epub_path else record.converted_path,
        library_book_id=_build_series_name(updated) or record.library_book_id,
    )

    # Always trigger Komga scan after a forced update
    try:
        _trigger_komga_scan(cfg)
    except Exception:  # noqa: BLE001
        logger.warning("[ID:%s] Komga scan trigger failed after force-rescrape", record_id)

    return result


def _lookup_metadata(
    record: MangaRecord,
    cfg: PipelineConfig,
) -> BookwalkerMetadata | BangumiMetadata | None:
    parsed = parse_filename(record.file_name)
    if record.collection_title:
        _apply_collection_title(parsed, record.collection_title)
    try:
        source_name = (
            f"{record.collection_title} {record.file_name}"
            if record.collection_title
            else record.file_name
        )
        llm_metadata = normalize_with_llm(source_name, parsed, cfg.metadata)
    except Exception as e:
        logger.warning("[ID:%s] LLM filename normalization failed: %s", record.id, e)
        llm_metadata = None

    if llm_metadata is not None:
        apply_threshold = 0.5 if llm_metadata.parse_status == "ok" else 0.4
        if llm_metadata.confidence >= apply_threshold:
            parsed.title = llm_metadata.title or parsed.title
            parsed.series = llm_metadata.title or parsed.series
            parsed.author = llm_metadata.author or parsed.author
            parsed.publisher = llm_metadata.publisher or parsed.publisher
            parsed.volume = llm_metadata.volume or parsed.volume
            if record.collection_title:
                _apply_collection_title(parsed, record.collection_title)

    volume = parsed.volume or record.volume
    author = parsed.author or record.author
    titles = _metadata_search_titles(parsed, record, llm_metadata, "tw")
    if not titles:
        return None

    if cfg.metadata.bookwalker_tw_enabled:
        metadata = _search_provider_titles(
            record,
            "BookWalker TW",
            search_bookwalker_tw,
            titles,
            volume,
            author,
            cfg.metadata.bookwalker_tw_min_confidence,
            cfg.metadata.bookwalker_tw_max_candidates,
        )
        if metadata:
            return metadata

    if cfg.metadata.bookwalker_jp_enabled:
        metadata = _search_provider_titles(
            record,
            "BookWalker JP",
            search_bookwalker_jp,
            _metadata_search_titles(parsed, record, llm_metadata, "jp"),
            volume,
            author,
            cfg.metadata.bookwalker_jp_min_confidence,
            cfg.metadata.bookwalker_jp_max_candidates,
        )
        if metadata:
            return metadata

    if cfg.metadata.bangumi_enabled:
        metadata = _search_provider_titles(
            record,
            "Bangumi",
            search_bangumi,
            _metadata_search_titles(parsed, record, llm_metadata, "bangumi"),
            volume,
            author,
            cfg.metadata.bangumi_min_confidence,
            cfg.metadata.bangumi_max_candidates,
        )
        if metadata:
            return metadata

    return None


def _search_provider_titles(
    record: MangaRecord,
    provider_name: str,
    search_func: object,
    titles: list[str],
    volume: str,
    author: str,
    min_confidence: float,
    max_candidates: int,
) -> BookwalkerMetadata | BangumiMetadata | None:
    best: BookwalkerMetadata | BangumiMetadata | None = None
    parsed = parse_filename(record.file_name)
    if record.collection_title:
        _apply_collection_title(parsed, record.collection_title)
    for title in titles:
        if not _title_candidate_matches_record_context(title, parsed, record):
            continue
        try:
            metadata = search_func(
                title,
                volume=volume,
                author=author,
                max_candidates=max_candidates,
            )
        except Exception as e:
            logger.warning(
                "[ID:%s] %s lookup failed for %s: %s",
                record.id,
                provider_name,
                title,
                e,
            )
            continue
        if metadata is None:
            continue
        if not _metadata_matches_record_context(metadata, parsed, record):
            logger.info(
                "[ID:%s] %s rejected context mismatch: %s",
                record.id,
                provider_name,
                metadata.series or metadata.title,
            )
            continue
        if best is None or metadata.confidence > best.confidence:
            best = metadata
        if metadata.confidence >= min_confidence:
            return metadata

    if best:
        if best.confidence >= min_confidence:
            return best
        logger.info(
            "[ID:%s] %s match below threshold: %.2f < %.2f",
            record.id,
            provider_name,
            best.confidence,
            min_confidence,
        )
    return None


def _record_with_metadata(
    record: MangaRecord,
    metadata: BookwalkerMetadata | BangumiMetadata,
) -> MangaRecord:
    title = metadata.series or record.title
    series = metadata.series or record.series or record.title

    return replace(
        record,
        title=title,
        series=series,
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
        _download_metadata_artwork(record, epub_path, epub_path.parent, cfg)


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


def _find_archive_cbz(record: MangaRecord, cfg: PipelineConfig) -> Path | None:
    candidates: list[Path] = []
    if record.archive_path:
        candidates.append(Path(record.archive_path))

    names = []
    if record.archive_path:
        names.append(Path(record.archive_path).name)
    if record.converted_path:
        names.append(Path(record.converted_path).with_suffix(".cbz").name)

    for name in dict.fromkeys(names):
        candidates.append(cfg.paths.archive_cbz / name)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _relocate_archive_cbz(
    archive_path: Path,
    record: MangaRecord,
    cfg: PipelineConfig,
) -> Path:
    dest_path = cfg.paths.archive_cbz / f"{_build_clean_name(record)}.cbz"
    if archive_path == dest_path:
        return archive_path
    if dest_path.exists():
        raise FileExistsError(f"Destination already exists: {dest_path}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(archive_path), str(dest_path))
    return dest_path


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
