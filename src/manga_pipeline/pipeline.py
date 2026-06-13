"""Pipeline orchestrator.

Coordinates the full manga processing pipeline:
discovered -> stable -> parse -> normalize -> archive -> convert -> import -> done
"""

from __future__ import annotations

from pathlib import Path

from manga_pipeline.calibre import CalibreMetadata, run_calibredb_add
from manga_pipeline.config import PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.filename_parser import parse_filename
from manga_pipeline.kcc import run_kcc
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
    completed = 0
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
        ProcessingStatus.DISCOVERED,
        ProcessingStatus.WAITING_STABLE,
        ProcessingStatus.METADATA_PARSED,
        ProcessingStatus.ARCHIVED,
        ProcessingStatus.CONVERTED,
    ]:
        records = db.get_records_by_status(status_to_process)
        for record in records:
            try:
                result = _advance_record(record, cfg, db)
                latest = db.get_record_by_id(record.id) if record.id else None
                if result and latest and latest.current_status == ProcessingStatus.DONE:
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

    if status == ProcessingStatus.DISCOVERED:
        db.update_status(record_id, ProcessingStatus.WAITING_STABLE)
        return True

    if status == ProcessingStatus.WAITING_STABLE:
        return _step_parse_metadata(record_id, record, cfg, db)

    if status == ProcessingStatus.METADATA_PARSED:
        return _step_normalize_and_archive(record_id, record, cfg, db)

    if status == ProcessingStatus.ARCHIVED:
        return _step_convert_kcc(record_id, record, cfg, db)

    if status == ProcessingStatus.CONVERTED:
        return _step_import_calibre(record_id, record, cfg, db)

    return False


def _step_parse_metadata(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Parse filename metadata."""
    parsed = parse_filename(record.file_name)
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
            file_path, cfg.paths.archive_cbz, clean_name
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


def _step_import_calibre(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Import converted file into Calibre library."""
    converted_path = Path(record.converted_path)
    if not converted_path.is_file():
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Converted file missing: {converted_path}",
        )
        return False

    # Rename .kepub.epub to .kepub so Calibre registers it as KEPUB format natively
    if converted_path.name.endswith(".kepub.epub"):
        new_path = converted_path.with_name(converted_path.name.replace(".kepub.epub", ".kepub"))
        converted_path = converted_path.rename(new_path)
        # Update DB with new path so cleanup finds it later
        db.update_status(record_id, ProcessingStatus.CONVERTED, converted_path=str(converted_path))

    # Build metadata for Calibre
    meta = CalibreMetadata(
        title=_build_calibre_title(record),
        authors=record.author,
        series=record.series,
        series_index=record.volume,
        publisher=record.publisher,
        languages=cfg.metadata.default_language,
        tags=",".join(cfg.metadata.default_tags),
    )

    db.update_status(record_id, ProcessingStatus.IMPORTING)

    result = run_calibredb_add(
        file_path=converted_path,
        library_path=cfg.paths.calibre_library,
        metadata=meta,
        calibredb_cmd=cfg.commands.calibredb,
    )

    if result.success:
        db.update_status(
            record_id,
            ProcessingStatus.DONE,
            calibre_book_id=result.book_id,
        )
        logger.info(
            "[ID:%s] Imported to Calibre: %s (book_id=%s)",
            record_id,
            record.file_name,
            result.book_id,
        )

        # Clean up: delete original from inbox
        if cfg.processing.delete_inbox_after_archive:
            inbox_path = Path(record.original_path)
            try:
                if inbox_path.is_file():
                    inbox_path.unlink()
                    logger.info("[ID:%s] Deleted inbox original: %s", record_id, inbox_path.name)
                    _delete_empty_inbox_parent(inbox_path.parent, cfg.paths.inbox, record_id)
            except OSError as e:
                logger.warning(
                    "[ID:%s] Could not delete inbox file %s: %s", record_id, inbox_path.name, e
                )

        # Clean up: delete converted file from kepub_ready
        if cfg.processing.cleanup_after_import:
            try:
                converted_path.unlink()
                logger.info("[ID:%s] Deleted converted file: %s", record_id, converted_path.name)
            except OSError as e:
                logger.warning(
                    "[ID:%s] Could not delete converted file %s: %s",
                    record_id,
                    converted_path.name,
                    e,
                )

        return True
    else:
        # Check retry
        if record.retry_count < cfg.processing.max_retries:
            db.increment_retry(record_id)
            logger.warning(
                "[ID:%s] Calibre import failed for %s (retry %d/%d): %s",
                record_id,
                record.file_name,
                record.retry_count + 1,
                cfg.processing.max_retries,
                result.stderr[:200],
            )
            # Revert status back to CONVERTED so it can be retried
            db.update_status(record_id, ProcessingStatus.CONVERTED)
            return False
        else:
            db.update_status(
                record_id,
                ProcessingStatus.FAILED,
                error_message=(
                    "Calibre import failed after "
                    f"{cfg.processing.max_retries} retries: {result.stderr[:200]}"
                ),
            )
            return False


def _build_clean_name(record: MangaRecord) -> str:
    """Build a clean filename from parsed metadata."""
    parts = []
    if record.author:
        parts.append(f"[{record.author}]")
    if record.title:
        parts.append(record.title)
    if record.volume:
        parts.append(f"v{record.volume.zfill(2)}")

    if parts:
        return " ".join(parts)
    return record.file_name.rsplit(".", 1)[0]


def _build_calibre_title(record: MangaRecord) -> str:
    """Build a Calibre title that stays unique for each volume."""
    title = record.title or record.file_name
    if record.volume:
        return f"{title} 卷{record.volume}"
    return title


def _delete_empty_inbox_parent(parent_path: Path, inbox_dir: Path, record_id: int) -> None:
    """Delete an empty first-level inbox directory after its last volume is imported."""
    if parent_path == inbox_dir or not parent_path.is_relative_to(inbox_dir):
        return
    if parent_path.parent != inbox_dir:
        return

    try:
        parent_path.rmdir()
        logger.info("[ID:%s] Deleted empty inbox directory: %s", record_id, parent_path.name)
    except OSError:
        return
