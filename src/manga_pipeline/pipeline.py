"""Pipeline orchestrator.

Coordinates the full manga processing pipeline:
discovered -> stable -> parse -> normalize -> archive -> convert -> import -> done
"""

from __future__ import annotations

import shutil
from pathlib import Path

from manga_pipeline.config import PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.filename_parser import parse_filename
from manga_pipeline.kcc import run_kcc
from manga_pipeline.komga import get_library_id, trigger_library_scan
from manga_pipeline.komf_client import trigger_series_match
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
    # Recover zombie tasks (stuck in intermediate states due to crash/restart)
    for status, fallback in [
        (ProcessingStatus.PROCESSING, ProcessingStatus.WAITING_STABLE),
        (ProcessingStatus.IMPORTING, ProcessingStatus.CONVERTED),
    ]:
        zombies = db.get_records_by_status(status)
        for z in zombies:
            logger.info("[ID:%s] Recovering zombie %s task to %s", z.id, status.value, fallback.value)
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


def _step_import_komga(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Import converted file into Komga library by moving it to the library directory."""
    converted_path = Path(record.converted_path)
    if not converted_path.is_file():
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Converted file missing: {converted_path}",
        )
        return False

    db.update_status(record_id, ProcessingStatus.IMPORTING)

    # Build destination path: komga_library / series_name / filename
    series_name = record.series or record.title or "Unknown"
    # Sanitize directory name
    series_name = _sanitize_dirname(series_name)
    series_dir = cfg.paths.komga_library / series_name
    series_dir.mkdir(parents=True, exist_ok=True)

    dest_path = series_dir / converted_path.name

    try:
        # Move (or copy + delete) the converted file into the Komga library
        shutil.move(str(converted_path), str(dest_path))
        logger.info(
            "[ID:%s] Moved to Komga library: %s -> %s",
            record_id,
            converted_path.name,
            dest_path,
        )
    except OSError as e:
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

    # Trigger Komga library scan
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
        library_book_id=series_name,
    )
    logger.info(
        "[ID:%s] Successfully imported to Komga: %s (series=%s)",
        record_id,
        record.file_name,
        series_name,
    )

    # Clean up: delete original from inbox
    if cfg.processing.delete_inbox_after_archive:
        inbox_path = Path(record.original_path)
        try:
            if inbox_path.is_file():
                inbox_path.unlink()
                logger.info("[ID:%s] Deleted inbox original: %s", record_id, inbox_path.name)
        except OSError as e:
            logger.warning(
                "[ID:%s] Could not delete inbox file %s: %s", record_id, inbox_path.name, e
            )

    # Trigger Komf metadata scraping (best-effort, non-blocking)
    if cfg.komf.enabled:
        try:
            # Komf needs the Komga series ID. We'll trigger a match by series name.
            # This is best-effort — if it fails, the book is still in Komga.
            trigger_series_match(
                komf_base_uri=cfg.komf.base_uri,
                series_id=series_name,  # Komf will search by name
            )
        except Exception:
            logger.warning("[ID:%s] Komf scraping failed (non-fatal).", record_id)

    return True


def _sanitize_dirname(name: str) -> str:
    """Sanitize a string for use as a directory name."""
    # Remove characters that are problematic in file paths
    forbidden = '<>:"/\\|?*'
    for ch in forbidden:
        name = name.replace(ch, "")
    # Collapse whitespace
    name = " ".join(name.split())
    return name.strip() or "Unknown"


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
