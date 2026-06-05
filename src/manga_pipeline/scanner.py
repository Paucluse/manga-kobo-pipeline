"""Inbox directory scanner.

Discovers new manga files and registers them in the state database.
Skips files that have already been processed (by file hash).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from manga_pipeline.database import Database
from manga_pipeline.logging_config import get_logger
from manga_pipeline.models import (
    SUPPORTED_EXTENSIONS,
    MangaRecord,
    ProcessingStatus,
)
from manga_pipeline.utils import compute_file_hash

logger = get_logger(__name__)


def scan_inbox(inbox_dir: Path, db: Database) -> list[MangaRecord]:
    """Scan inbox directory for new manga files.

    Walks the inbox directory, finds files with supported extensions,
    computes their hash, and inserts new records into the database.
    Files that have already been processed (same hash) are skipped.

    Args:
        inbox_dir: Path to the inbox directory.
        db: Database instance for state tracking.

    Returns:
        List of newly discovered MangaRecord entries.
    """
    if not inbox_dir.is_dir():
        logger.error("Inbox directory does not exist: %s", inbox_dir)
        return []

    discovered: list[MangaRecord] = []

    for file_path in sorted(inbox_dir.iterdir()):
        if file_path.is_dir():
            # TODO: Support image directories in future
            logger.debug("Skipping directory: %s", file_path.name)
            continue

        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            logger.debug(
                "Skipping unsupported file type: %s", file_path.name
            )
            continue

        # Compute hash for idempotency
        try:
            file_hash = compute_file_hash(file_path)
        except OSError as e:
            logger.warning(
                "Could not read file %s: %s", file_path.name, e
            )
            continue

        # Check if already in database
        existing = db.get_record_by_hash(file_hash)
        if existing is not None:
            logger.debug(
                "Already known (status=%s): %s",
                existing.current_status.value,
                file_path.name,
            )
            continue

        # Create new record
        record = MangaRecord(
            original_path=str(file_path),
            file_name=file_path.name,
            file_hash=file_hash,
            current_status=ProcessingStatus.DISCOVERED,
        )

        try:
            record_id = db.insert_record(record)
            record.id = record_id
            discovered.append(record)
            logger.info(
                "Discovered: %s (id=%d, hash=%s...)",
                file_path.name,
                record_id,
                file_hash[:12],
            )
        except sqlite3.IntegrityError:
            # Race condition: another process inserted the same hash
            logger.debug(
                "Hash already exists (race condition): %s",
                file_path.name,
            )
            continue

    logger.info(
        "Scan complete: %d new files discovered in %s",
        len(discovered),
        inbox_dir,
    )
    return discovered
