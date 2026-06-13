"""Inbox directory scanner.

Discovers new manga files and registers them in the state database.
Skips files that have already been processed (by file hash).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
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

_logged_known_files: set[tuple[str, str, str]] = set()
SIMPLE_VOLUME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d{1,3}"),
    re.compile(r"v\.?[\s._-]*\d{1,3}", re.IGNORECASE),
    re.compile(r"vol\.?[\s._-]*\d{1,3}", re.IGNORECASE),
    re.compile(r"第\d{1,3}[卷巻]"),
)


@dataclass(frozen=True)
class ScanCandidate:
    """A concrete source path plus the filename used for metadata parsing."""

    path: Path
    record_name: str


def scan_inbox(
    inbox_dir: Path,
    db: Database,
    stability_check_interval: int = 2,
    calibre_record_exists: Callable[[MangaRecord], bool] | None = None,
) -> list[MangaRecord]:
    """Scan inbox directory for new manga files.

    Walks the inbox directory, finds files with supported extensions,
    computes their hash, and inserts new records into the database.
    Files that have already been processed (same hash) are skipped.

    Args:
        inbox_dir: Path to the inbox directory.
        db: Database instance for state tracking.
        stability_check_interval: Seconds between file size checks.
        calibre_record_exists: Optional source-of-truth check for completed
            records. If it returns False, the inbox file is re-queued.

    Returns:
        List of newly discovered MangaRecord entries.
    """
    if not inbox_dir.is_dir():
        logger.error("Inbox directory does not exist: %s", inbox_dir)
        return []

    discovered: list[MangaRecord] = []

    # 1. Find all potential new files. Top-level directories may provide
    # series metadata for simple numbered archive files inside them.
    candidates = _find_scan_candidates(inbox_dir)
    candidate_by_path = {candidate.path: candidate for candidate in candidates}
    potential_files = [candidate.path for candidate in candidates]

    # 2. Batch check stability (skips files actively being written)
    stable_files = check_files_stable_batch(
        potential_files,
        check_interval=stability_check_interval,
    )
    if len(stable_files) < len(potential_files):
        logger.debug(
            "Found %d files, but only %d are stable. Waiting for the rest.",
            len(potential_files),
            len(stable_files),
        )

    # 3. Process only stable files
    for file_path in stable_files:
        candidate = candidate_by_path[file_path]
        # Compute hash for idempotency ONLY when stable
        try:
            file_hash = compute_file_hash(file_path)
        except OSError as e:
            logger.warning("Could not read file %s: %s", file_path.name, e)
            continue

        # Check if already in database
        existing = db.get_record_by_hash(file_hash)
        if existing is not None:
            should_requeue = existing.current_status in {
                ProcessingStatus.FAILED,
                ProcessingStatus.NEEDS_REVIEW,
            }
            if (
                existing.current_status == ProcessingStatus.DONE
                and calibre_record_exists is not None
                and not calibre_record_exists(existing)
            ):
                should_requeue = True

            if should_requeue:
                db.update_status(
                    existing.id,  # type: ignore[arg-type]
                    ProcessingStatus.WAITING_STABLE,
                    original_path=str(file_path),
                    file_name=candidate.record_name,
                    calibre_book_id="",
                    converted_path="",
                    retry_count=0,
                )
                refreshed = db.get_record_by_id(existing.id)  # type: ignore[arg-type]
                if refreshed is not None:
                    discovered.append(refreshed)
                logger.info(
                    "Re-queued known file from %s because it needs processing: "
                    "%s (id=%s, hash=%s...)",
                    existing.current_status.value,
                    candidate.record_name,
                    existing.id,
                    file_hash[:12],
                )
                continue

            log_key = (
                str(file_path),
                file_hash,
                existing.current_status.value,
            )
            if log_key not in _logged_known_files:
                _logged_known_files.add(log_key)
                logger.info(
                    "Skipping already known file in inbox: %s "
                    "(id=%s, status=%s, hash=%s...)",
                    file_path.name,
                    existing.id,
                    existing.current_status.value,
                    file_hash[:12],
                )
            continue

        # Create new record. The file has already passed a quick stability check,
        # so the pipeline can parse it immediately on this cycle.
        record = MangaRecord(
            original_path=str(file_path),
            file_name=candidate.record_name,
            file_hash=file_hash,
            current_status=ProcessingStatus.WAITING_STABLE,
        )

        try:
            record_id = db.insert_record(record)
            record.id = record_id
            discovered.append(record)
            logger.info(
                "Discovered and stable: %s (id=%d, hash=%s...)",
                candidate.record_name,
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


def _find_scan_candidates(inbox_dir: Path) -> list[ScanCandidate]:
    """Find direct inbox files plus archives in first-level manga dirs."""
    candidates: list[ScanCandidate] = []

    for entry in sorted(inbox_dir.iterdir()):
        if entry.is_dir():
            directory_candidates = _find_directory_archive_candidates(entry)
            if directory_candidates:
                candidates.extend(directory_candidates)
            else:
                logger.debug("Skipping directory without simple archives: %s", entry.name)
            continue

        if entry.suffix.lower() not in SUPPORTED_EXTENSIONS:
            logger.debug("Skipping unsupported file type: %s", entry.name)
            continue

        candidates.append(ScanCandidate(path=entry, record_name=entry.name))

    return candidates


def _find_directory_archive_candidates(dir_path: Path) -> list[ScanCandidate]:
    """Find archive files directly inside a manga directory."""
    candidates: list[ScanCandidate] = []

    for child in sorted(dir_path.iterdir()):
        if not child.is_file():
            continue
        if child.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if _is_simple_volume_name(child.stem):
            candidates.append(
                ScanCandidate(
                    path=child,
                    record_name=f"{dir_path.name} {child.name}",
                )
            )
            continue

        candidates.append(
            ScanCandidate(
                path=child,
                record_name=child.name,
            )
        )

    return candidates


def _is_simple_volume_name(stem: str) -> bool:
    """Return True for filenames like 01, v01, vol_01, or 第01卷."""
    normalized = stem.strip()
    return any(pattern.fullmatch(normalized) for pattern in SIMPLE_VOLUME_PATTERNS)
