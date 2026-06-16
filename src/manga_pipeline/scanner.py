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
from manga_pipeline.stability import check_files_stable_batch
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

    # 1. Find all potential new files
    potential_files = []
    for file_path in sorted(inbox_dir.iterdir()):
        if file_path.is_dir():
            logger.debug("Skipping directory: %s", file_path.name)
            continue
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            logger.debug("Skipping unsupported file type: %s", file_path.name)
            continue
        potential_files.append(file_path)

    # 2. Batch check stability (skips files actively being written)
    stable_files = check_files_stable_batch(potential_files)
    if len(stable_files) < len(potential_files):
        logger.debug(
            "Found %d files, but only %d are stable. Waiting for the rest.",
            len(potential_files),
            len(stable_files),
        )

    # 3. Process only stable files
    for file_path in stable_files:
        # Compute hash for idempotency ONLY when stable
        try:
            file_hash = compute_file_hash(file_path)
        except OSError as e:
            logger.warning("Could not read file %s: %s", file_path.name, e)
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

        # Create new record. Skip DISCOVERED and WAITING_STABLE since we just proved stability
        record = MangaRecord(
            original_path=str(file_path),
            file_name=file_path.name,
            file_hash=file_hash,
            current_status=ProcessingStatus.WAITING_STABLE, # Will be picked up immediately
        )

        try:
            record_id = db.insert_record(record)
            record.id = record_id
            discovered.append(record)
            logger.info(
                "Discovered and stable: %s (id=%d, hash=%s...)",
                file_path.name,
                record_id,
                file_hash[:12],
            )
        except sqlite3.IntegrityError:
            # Race condition: another process inserted the same hash
            logger.debug("Hash already exists (race condition): %s", file_path.name)
            continue

    if discovered:
        logger.info(
            "Scan complete: %d new stable files discovered in %s",
            len(discovered),
            inbox_dir,
        )
    return discovered
