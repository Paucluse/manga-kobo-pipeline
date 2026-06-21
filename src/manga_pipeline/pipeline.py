"""Pipeline orchestrator.

Coordinates the full manga processing pipeline:
discovered -> stable -> parse -> normalize -> archive -> convert -> import -> done
"""

from __future__ import annotations

import contextlib
import fcntl
import re
import shutil
from pathlib import Path

from manga_pipeline.bookwalker_tw import (
    download_cover,
    search_bookwalker_tw,
    to_bookwalker_traditional,
)
from manga_pipeline.comicinfo import write_comicinfo_to_cbz
from manga_pipeline.config import PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.epub_metadata import write_epub_metadata
from manga_pipeline.filename_parser import ParseResult, parse_filename
from manga_pipeline.kcc import run_kcc
from manga_pipeline.komga import get_library_id, trigger_library_scan
from manga_pipeline.llm_metadata import normalize_with_llm
from manga_pipeline.logging_config import get_logger
from manga_pipeline.models import MangaRecord, ProcessingStatus
from manga_pipeline.normalizer import normalize_to_cbz
from manga_pipeline.review import move_to_review

logger = get_logger(__name__)


def process_all_pending(cfg: PipelineConfig, db: Database) -> int:
    """Process all pending manga records through the pipeline.

    Iterates through records in order of their pipeline stage,
    advancing each one step at a time.

    Args:
        cfg: Pipeline configuration.
        db: Database instance.

    Returns:
        Number of records successfully processed to completion.
    """
    lock_path = cfg.paths.state / "process.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("Another manga-pipeline process is already running; skipping.")
            return 0

        return _process_all_pending_locked(cfg, db)


def _process_all_pending_locked(cfg: PipelineConfig, db: Database) -> int:
    completed = 0
    _recover_already_imported_failed_records(cfg, db)
    _repair_done_collection_title_records(cfg, db)
    # Recover zombie tasks (stuck in intermediate states due to crash/restart)
    for status, fallback in [
        (ProcessingStatus.PROCESSING, ProcessingStatus.WAITING_STABLE),
        (ProcessingStatus.IMPORTING, ProcessingStatus.CONVERTED),
    ]:
        zombies = db.get_records_by_status(status)
        for z in zombies:
            logger.info(
                "[ID:%s] Recovering zombie %s task to %s",
                z.id,
                status.value,
                fallback.value,
            )
            db.update_status(z.id, fallback)  # type: ignore

    # Process each stage in order
    for status_to_process in [
        ProcessingStatus.WAITING_STABLE,
        ProcessingStatus.NORMALIZED,
        ProcessingStatus.METADATA_PARSED,
        ProcessingStatus.ARCHIVED,
        ProcessingStatus.CONVERTED,
    ]:
        records = db.get_records_by_status(status_to_process)
        for record in records:
            try:
                result = _advance_record(record, cfg, db)
                if result and record.current_status == ProcessingStatus.DONE:
                    completed += 1
            except Exception:
                logger.exception(
                    "[ID:%s] Unexpected error processing %s",
                    record.id,
                    record.file_name,
                )
                db.update_status(
                    record.id,  # type: ignore[arg-type]
                    ProcessingStatus.FAILED,
                    error_message="Unexpected error during processing",
                )

    return completed


def _recover_already_imported_failed_records(
    cfg: PipelineConfig,
    db: Database,
) -> None:
    """Mark failed records as done when the KEPUB is already in Komga."""
    for record in db.get_records_by_status(ProcessingStatus.FAILED):
        if not record.converted_path:
            continue
        converted_path = Path(record.converted_path)
        series_name, _series_dir, dest_path = _expected_import_destination(
            record,
            cfg,
            converted_path,
        )
        if not dest_path.is_file():
            continue
        record_id = record.id
        assert record_id is not None
        logger.info(
            "[ID:%s] Recovering failed record already imported at %s",
            record_id,
            dest_path,
        )
        _mark_import_done(record_id, record, db, cfg, series_name, dest_path)


def _repair_done_collection_title_records(
    cfg: PipelineConfig,
    db: Database,
) -> None:
    """Normalize already imported collection records after title rules change."""
    for record in db.get_records_by_status(ProcessingStatus.DONE):
        if not record.collection_title:
            continue

        title = to_bookwalker_traditional(record.title or record.series)
        series = to_bookwalker_traditional(record.series or record.title)
        if title == record.title and series == record.series:
            continue

        record_id = record.id
        assert record_id is not None
        logger.info(
            "[ID:%s] Repairing collection title: %s -> %s",
            record_id,
            record.series or record.title,
            series or title,
        )

        record.title = title or record.title
        record.series = series or record.series or record.title

        archive_path = _repair_archive_path(record, cfg)
        imported_path = _repair_imported_epub_path(record, cfg)
        _rewrite_imported_metadata(record, cfg, archive_path, imported_path)

        db.update_status(
            record_id,
            ProcessingStatus.DONE,
            title=record.title,
            series=record.series,
            archive_path=str(archive_path) if archive_path else record.archive_path,
            converted_path=str(imported_path) if imported_path else record.converted_path,
            library_book_id=_build_series_name(record),
        )


def _repair_archive_path(record: MangaRecord, cfg: PipelineConfig) -> Path | None:
    archive_path = Path(record.archive_path)
    if not archive_path.is_file():
        return None

    target = cfg.paths.archive_cbz / f"{_build_clean_name(record)}.cbz"
    if archive_path != target and not target.exists():
        archive_path.rename(target)
        archive_path = target
    return archive_path


def _repair_imported_epub_path(record: MangaRecord, cfg: PipelineConfig) -> Path | None:
    current = _find_imported_epub(record, cfg)
    if current is None:
        return None

    target_dir = cfg.paths.komga_library / _build_series_name(record)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_build_clean_name(record)}.kepub.epub"
    if current == target:
        return current
    if target.exists():
        return target

    old_cover = current.with_suffix(".jpg")
    new_cover = target.with_suffix(".jpg")
    old_parent = current.parent
    shutil.move(str(current), str(target))
    if old_cover.is_file() and not new_cover.exists():
        shutil.move(str(old_cover), str(new_cover))
    with contextlib.suppress(OSError):
        old_parent.rmdir()
    return target


def _find_imported_epub(record: MangaRecord, cfg: PipelineConfig) -> Path | None:
    candidates = []
    if record.converted_path:
        candidates.append(Path(record.converted_path))
    if record.archive_path:
        candidates.append(
            cfg.paths.komga_library
            / _sanitize_dirname(record.series or record.title)
            / Path(record.archive_path).with_suffix(".kepub.epub").name
        )

    names = [candidate.name for candidate in candidates if candidate.name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for name in dict.fromkeys(names):
        matches = list(cfg.paths.komga_library.rglob(name))
        if matches:
            return matches[0]
    return None


def _rewrite_imported_metadata(
    record: MangaRecord,
    cfg: PipelineConfig,
    archive_path: Path | None,
    imported_path: Path | None,
) -> None:
    if archive_path and archive_path.is_file():
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

    if imported_path and imported_path.is_file():
        write_epub_metadata(
            imported_path,
            title=_build_book_title(record),
            series=_build_series_name(record),
            number=record.volume,
            writer=record.author,
            language_iso=cfg.metadata.default_language,
            summary=record.summary,
            manga=cfg.kobo.manga_style,
        )


def _advance_record(
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Advance a single record to its next pipeline stage.

    Returns True if the record was advanced, False if it couldn't be.
    """
    record_id = record.id
    assert record_id is not None

    status = record.current_status

    if status == ProcessingStatus.WAITING_STABLE:
        return _step_parse_metadata(record_id, record, cfg, db)

    if status == ProcessingStatus.METADATA_PARSED:
        return _step_normalize_and_archive(record_id, record, cfg, db)

    if status == ProcessingStatus.ARCHIVED:
        return _step_convert_kcc(record_id, record, cfg, db)

    if status == ProcessingStatus.CONVERTED:
        return _step_import_komga(record_id, record, cfg, db)

    return False


def _step_parse_metadata(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Parse filename metadata."""
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
        logger.warning("[ID:%s] LLM filename normalization failed: %s", record_id, e)
        llm_metadata = None

    if llm_metadata and llm_metadata.confidence >= 0.65:
        logger.info(
            "[ID:%s] LLM normalized filename: title=%s, author=%s, vol=%s",
            record_id,
            llm_metadata.title,
            llm_metadata.author,
            llm_metadata.volume,
        )
        parsed.title = llm_metadata.title or parsed.title
        parsed.series = llm_metadata.title or parsed.series
        parsed.author = llm_metadata.author or parsed.author
        parsed.publisher = llm_metadata.publisher or parsed.publisher
        parsed.volume = llm_metadata.volume or parsed.volume
        parsed.confidence = max(parsed.confidence, llm_metadata.confidence)
        if record.collection_title:
            _apply_collection_title(parsed, record.collection_title)

    bookwalker = None
    accepted_bookwalker = None
    if cfg.metadata.bookwalker_tw_enabled and parsed.title:
        try:
            bookwalker = search_bookwalker_tw(
                parsed.title,
                volume=parsed.volume,
                author=parsed.author,
                max_candidates=cfg.metadata.bookwalker_tw_max_candidates,
            )
        except Exception as e:
            logger.warning("[ID:%s] BookWalker TW lookup failed: %s", record_id, e)

    if (
        bookwalker is not None
        and bookwalker.confidence >= cfg.metadata.bookwalker_tw_min_confidence
    ):
        accepted_bookwalker = bookwalker
        logger.info(
            "[ID:%s] BookWalker TW matched %s (confidence=%.2f, product=%s)",
            record_id,
            bookwalker.title,
            bookwalker.confidence,
            bookwalker.product_id,
        )
        parsed.title = bookwalker.series or parsed.title
        parsed.series = bookwalker.series or parsed.series or parsed.title
        parsed.volume = bookwalker.volume or parsed.volume
        parsed.author = bookwalker.author_text or parsed.author
        parsed.publisher = bookwalker.publisher or parsed.publisher
        parsed.confidence = max(parsed.confidence, bookwalker.confidence)

    logger.info(
        "[ID:%s] Parsed %s: title=%s, author=%s, vol=%s (confidence=%.2f)",
        record_id,
        record.file_name,
        parsed.title,
        parsed.author,
        parsed.volume,
        parsed.confidence,
    )

    # Check confidence threshold
    threshold = cfg.metadata.confidence_auto_accept
    if parsed.confidence < threshold:
        logger.warning(
            "[ID:%s] Low confidence (%.2f < %.2f), sending to review: %s",
            record_id,
            parsed.confidence,
            threshold,
            record.file_name,
        )
        file_path = Path(record.original_path)
        if file_path.is_file():
            move_to_review(
                file_path,
                cfg.paths.manual_review,
                reason=f"Low confidence: {parsed.confidence:.2f}",
                parsed_metadata={
                    "title": parsed.title,
                    "author": parsed.author,
                    "volume": parsed.volume,
                },
            )
        db.update_status(
            record_id,
            ProcessingStatus.NEEDS_REVIEW,
            error_message=f"Low confidence: {parsed.confidence:.2f}",
        )
        return False

    # Update record with parsed metadata
    db.update_status(
        record_id,
        ProcessingStatus.METADATA_PARSED,
        title=parsed.title,
        author=parsed.author,
        series=parsed.series,
        volume=parsed.volume,
        publisher=parsed.publisher,
        summary=accepted_bookwalker.summary if accepted_bookwalker else "",
        cover_url=accepted_bookwalker.cover_url if accepted_bookwalker else "",
        source_url=accepted_bookwalker.detail_url if accepted_bookwalker else "",
        isbn=accepted_bookwalker.isbn if accepted_bookwalker else "",
        page_count=accepted_bookwalker.page_count if accepted_bookwalker else "",
        confidence=str(parsed.confidence),
    )
    return True


def _step_normalize_and_archive(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Normalize archive format and archive the CBZ."""
    file_path = Path(record.original_path)
    if not file_path.is_file():
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Source file missing: {file_path}",
        )
        return False

    try:
        # Build a clean filename
        clean_name = _build_clean_name(record)

        # Normalize to CBZ in archive directory
        archive_path = normalize_to_cbz(
            file_path,
            cfg.paths.archive_cbz,
            clean_name,
            pdf_config=cfg.pdf,
            pdfimages_cmd=cfg.commands.pdfimages,
            pdftoppm_cmd=cfg.commands.pdftoppm,
        )
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

        db.update_status(
            record_id,
            ProcessingStatus.ARCHIVED,
            archive_path=str(archive_path),
        )
        logger.info("[ID:%s] Archived: %s -> %s", record_id, file_path.name, archive_path.name)

        return True

    except (ValueError, OSError, ImportError) as e:
        logger.error(
            "[ID:%s] Normalization failed for %s: %s", record_id, record.file_name, e
        )
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Normalization failed: {e}",
        )
        return False


def _step_convert_kcc(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Convert archived CBZ to KEPUB via KCC."""
    archive_path = Path(record.archive_path)
    if not archive_path.is_file():
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Archive missing: {archive_path}",
        )
        return False

    cfg.paths.kepub_ready.mkdir(parents=True, exist_ok=True)

    result = run_kcc(
        input_path=archive_path,
        output_dir=cfg.paths.kepub_ready,
        kcc_cmd=cfg.commands.kcc,
        kobo_config=cfg.kobo,
    )

    if result.success and result.output_path:
        converted_path = Path(result.output_path)
        try:
            write_epub_metadata(
                converted_path,
                title=_build_book_title(record),
                series=_build_series_name(record),
                number=record.volume,
                writer=record.author,
                language_iso=cfg.metadata.default_language,
                summary=record.summary,
                manga=cfg.kobo.manga_style,
            )
        except (KeyError, ValueError, OSError) as e:
            logger.warning("[ID:%s] Could not update EPUB metadata: %s", record_id, e)

        db.update_status(
            record_id,
            ProcessingStatus.CONVERTED,
            converted_path=result.output_path,
        )
        logger.info(
            "[ID:%s] Converted: %s -> %s",
            record_id,
            archive_path.name,
            result.output_path,
        )
        return True
    else:
        # Check retry
        if record.retry_count < cfg.processing.max_retries:
            db.increment_retry(record_id)
            logger.warning(
                "[ID:%s] KCC failed for %s (retry %d/%d): %s",
                record_id,
                record.file_name,
                record.retry_count + 1,
                cfg.processing.max_retries,
                result.stderr[:200],
            )
            return False
        else:
            db.update_status(
                record_id,
                ProcessingStatus.FAILED,
                error_message=f"KCC failed after {cfg.processing.max_retries} retries",
            )
            return False


def _step_import_komga(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Import converted file into Komga library by moving it to the library directory."""
    converted_path = Path(record.converted_path)
    series_name, series_dir, dest_path = _expected_import_destination(
        record,
        cfg,
        converted_path,
    )
    series_dir.mkdir(parents=True, exist_ok=True)

    if not converted_path.is_file():
        if dest_path.is_file():
            logger.info(
                "[ID:%s] Converted file already imported: %s",
                record_id,
                dest_path,
            )
            _mark_import_done(record_id, record, db, cfg, series_name, dest_path)
            return True
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Converted file missing: {converted_path}",
        )
        return False

    db.update_status(record_id, ProcessingStatus.IMPORTING)

    try:
        # Move (or copy + delete) the converted file into the Komga library
        shutil.move(str(converted_path), str(dest_path))
        _download_bookwalker_artwork(record, dest_path, series_dir, cfg)
        logger.info(
            "[ID:%s] Moved to Komga library: %s -> %s",
            record_id,
            converted_path.name,
            dest_path,
        )
    except OSError as e:
        if dest_path.is_file():
            logger.info(
                "[ID:%s] Import destination already exists after move failure: %s",
                record_id,
                dest_path,
            )
            _mark_import_done(record_id, record, db, cfg, series_name, dest_path)
            return True
        logger.error("[ID:%s] Failed to move file to Komga library: %s", record_id, e)
        if record.retry_count < cfg.processing.max_retries:
            db.increment_retry(record_id)
            db.update_status(record_id, ProcessingStatus.CONVERTED)
            return False
        else:
            db.update_status(
                record_id,
                ProcessingStatus.FAILED,
                error_message=f"Failed to move to Komga library: {e}",
            )
            return False

    _mark_import_done(record_id, record, db, cfg, series_name, dest_path)
    return True


def _expected_import_destination(
    record: MangaRecord,
    cfg: PipelineConfig,
    converted_path: Path,
) -> tuple[str, Path, Path]:
    series_name = _sanitize_dirname(record.series or record.title or "Unknown")
    series_dir = cfg.paths.komga_library / series_name
    filename = converted_path.name
    if not filename and record.archive_path:
        filename = Path(record.archive_path).with_suffix(".kepub.epub").name
    return series_name, series_dir, series_dir / filename


def _mark_import_done(
    record_id: int,
    record: MangaRecord,
    db: Database,
    cfg: PipelineConfig,
    series_name: str,
    dest_path: Path,
) -> None:
    """Mark a record as imported and perform scan/cleanup side effects."""
    library_id = cfg.komga.library_id
    if not library_id:
        library_id = get_library_id(
            cfg.komga.base_uri, cfg.komga.user, cfg.komga.password
        ) or ""

    if library_id:
        scan_result = trigger_library_scan(
            base_uri=cfg.komga.base_uri,
            library_id=library_id,
            user=cfg.komga.user,
            password=cfg.komga.password,
        )
        if not scan_result.success:
            logger.warning(
                "[ID:%s] Komga scan trigger failed (non-fatal): %s",
                record_id,
                scan_result.error,
            )
    else:
        logger.warning("[ID:%s] No Komga library ID found, skipping scan trigger.", record_id)

    # Mark as done
    db.update_status(
        record_id,
        ProcessingStatus.DONE,
        converted_path=str(dest_path),
        library_book_id=series_name,
    )
    logger.info(
        "[ID:%s] Successfully imported to Komga: %s (series=%s)",
        record_id,
        record.file_name,
        series_name,
    )

    _cleanup_inbox_original(record_id, record, cfg)


def _cleanup_inbox_original(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
) -> None:
    if cfg.processing.delete_inbox_after_archive:
        inbox_path = Path(record.original_path)
        try:
            if inbox_path.is_file():
                inbox_path.unlink()
                logger.info("[ID:%s] Deleted inbox original: %s", record_id, inbox_path.name)
                _remove_empty_inbox_parent(inbox_path, cfg)
            elif inbox_path.is_dir():
                shutil.rmtree(inbox_path)
                logger.info(
                    "[ID:%s] Deleted inbox source directory: %s",
                    record_id,
                    inbox_path.name,
                )
                _remove_empty_inbox_parent(inbox_path, cfg)
        except OSError as e:
            logger.warning(
                "[ID:%s] Could not delete inbox file %s: %s", record_id, inbox_path.name, e
            )


def _apply_collection_title(parsed: ParseResult, collection_title: str) -> None:
    """Apply an inbox collection directory name as the primary series title."""
    collection = _parse_collection_title(collection_title)
    title = to_bookwalker_traditional(collection.title or collection_title.strip())
    if not title:
        return
    parsed.title = title
    parsed.series = title
    if not parsed.author and collection.author:
        parsed.author = collection.author
    if not parsed.publisher and collection.publisher:
        parsed.publisher = collection.publisher
    parsed.confidence = max(parsed.confidence, 0.6)


def _parse_collection_title(collection_title: str) -> ParseResult:
    """Parse a top-level inbox collection directory name.

    Collection folders are user-facing series folders, so their first title-like
    component is more trustworthy than generic release-group bracket heuristics.
    """
    value = collection_title.strip()

    loose = re.match(
        r"^(?P<title>.+?)\s*\[(?P<author>[^\]]+)\]\[(?P<publisher>[^\]]+)\]",
        value,
    )
    if loose:
        title = loose.group("title").strip(" []")
        if title:
            return ParseResult(
                title=title,
                series=title,
                author=loose.group("author").strip(),
                publisher=loose.group("publisher").strip(),
                confidence=0.8,
            )

    bracketed = re.findall(r"\[([^\]]+)\]", value)
    if value.startswith("[") and len(bracketed) >= 3:
        title = bracketed[0].strip()
        return ParseResult(
            title=title,
            series=title,
            author=bracketed[1].strip(),
            publisher=bracketed[2].strip(),
            confidence=0.8,
        )

    return parse_filename(collection_title)


def _remove_empty_inbox_parent(path: Path, cfg: PipelineConfig) -> None:
    parent = path.parent
    if parent == cfg.paths.inbox:
        return
    try:
        parent.relative_to(cfg.paths.inbox)
    except ValueError:
        return
    with contextlib.suppress(OSError):
        parent.rmdir()


def _sanitize_dirname(name: str) -> str:
    """Sanitize a string for use as a directory name."""
    # Remove characters that are problematic in file paths
    forbidden = '<>:"/\\|?*'
    for ch in forbidden:
        name = name.replace(ch, "")
    # Collapse whitespace
    name = " ".join(name.split())
    return name.strip() or "Unknown"


def _download_bookwalker_artwork(
    record: MangaRecord,
    dest_path: Path,
    series_dir: Path,
    cfg: PipelineConfig,
) -> None:
    """Download BookWalker cover art as Komga local artwork sidecars."""
    if not cfg.metadata.download_bookwalker_covers or not record.cover_url:
        return

    book_cover_path = dest_path.with_suffix(".jpg")
    if download_cover(record.cover_url, book_cover_path):
        logger.info("Downloaded BookWalker book cover: %s", book_cover_path.name)

    series_cover_path = series_dir / "cover.jpg"
    should_update_series_cover = (
        not series_cover_path.exists() or _is_first_volume(record.volume)
    )
    if should_update_series_cover and download_cover(record.cover_url, series_cover_path):
        logger.info("Downloaded BookWalker series cover: %s", series_cover_path.name)


def _is_first_volume(volume: str) -> bool:
    """Return whether a volume token represents the first book in a series."""
    normalized = volume.strip().lower().lstrip("v")
    return normalized in {"1", "01", "001"}


def _build_clean_name(record: MangaRecord) -> str:
    """Build a Komga-friendly filename from parsed metadata.

    Komga groups books by parent directory, while book names come from the
    filename. Keep the file stem predictable for metadata matchers and sorting;
    richer display metadata is written into ComicInfo/EPUB metadata.
    """
    series = _build_series_name(record)
    if series:
        return _sanitize_filename(f"{series} {_format_volume_token(record.volume)}").strip()

    return _sanitize_filename(record.file_name.rsplit(".", 1)[0])


def _build_series_name(record: MangaRecord) -> str:
    """Return the canonical series name used for Komga folders and metadata."""
    return _sanitize_dirname(record.series or record.title or "")


def _build_book_title(record: MangaRecord) -> str:
    """Return a human-readable book title for Komga metadata."""
    title = record.title or record.series or record.file_name.rsplit(".", 1)[0]
    if record.volume:
        return f"{title} 卷{record.volume}"
    return title


def _format_volume_token(volume: str) -> str:
    """Return a scraper-friendly sortable volume token."""
    if not volume:
        return ""
    if volume.isdigit():
        return f"v{int(volume):03d}"
    return f"v{volume}"


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a file stem."""
    name = _sanitize_dirname(name)
    return " ".join(name.split())
