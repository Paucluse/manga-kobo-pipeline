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
from manga_pipeline.stability import check_file_stable_quick

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

    # Process each stage in order
    for status_to_process in [
        ProcessingStatus.DISCOVERED,
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
                    "Unexpected error processing %s (id=%s)",
                    record.file_name,
                    record.id,
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
        return _step_check_stability(record_id, record, cfg, db)

    if status == ProcessingStatus.WAITING_STABLE:
        return _step_parse_metadata(record_id, record, cfg, db)

    if status == ProcessingStatus.METADATA_PARSED:
        return _step_normalize_and_archive(record_id, record, cfg, db)

    if status == ProcessingStatus.ARCHIVED:
        return _step_convert_kcc(record_id, record, cfg, db)

    if status == ProcessingStatus.CONVERTED:
        return _step_import_calibre(record_id, record, cfg, db)

    return False


def _step_check_stability(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Check if the file is stable (fully downloaded)."""
    file_path = Path(record.original_path)

    if not file_path.is_file():
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"File not found: {file_path}",
        )
        return False

    stable = check_file_stable_quick(
        file_path, check_interval=cfg.processing.stable_check_interval
    )

    if stable:
        db.update_status(record_id, ProcessingStatus.WAITING_STABLE)
        logger.info("File stable: %s", record.file_name)
        return True
    else:
        logger.debug(
            "File not yet stable: %s", record.file_name
        )
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
        "Parsed %s: title=%s, author=%s, vol=%s (confidence=%.2f)",
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
            "Low confidence (%.2f < %.2f), sending to review: %s",
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
        logger.info("Archived: %s -> %s", file_path.name, archive_path.name)
        return True

    except (ValueError, OSError, ImportError) as e:
        logger.error(
            "Normalization failed for %s: %s", record.file_name, e
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
            "Converted: %s -> %s",
            archive_path.name,
            result.output_path,
        )
        return True
    else:
        # Check retry
        if record.retry_count < cfg.processing.max_retries:
            db.increment_retry(record_id)
            logger.warning(
                "KCC failed for %s (retry %d/%d): %s",
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

    # Build metadata for Calibre
    meta = CalibreMetadata(
        title=record.title or record.file_name,
        authors=record.author,
        series=record.series,
        series_index=record.volume,
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
            "Imported to Calibre: %s (book_id=%s)",
            record.file_name,
            result.book_id,
        )
        return True
    else:
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Calibre import failed: {result.stderr[:200]}",
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
