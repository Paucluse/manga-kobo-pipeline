"""Inbox directory scanner.

Discovers new manga files and registers them in the state database.
Skips files that have already been processed (by file hash).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from manga_pipeline.database import Database
from manga_pipeline.logging_config import get_logger
from manga_pipeline.models import (
    SUPPORTED_EXTENSIONS,
    MangaRecord,
    ProcessingStatus,
)
from manga_pipeline.normalizer import IMAGE_EXTENSIONS
from manga_pipeline.utils import compute_directory_hash, compute_file_hash

logger = get_logger(__name__)


@dataclass(frozen=True)
class InboxCandidate:
    """One processable inbox item."""

    path: Path
    file_name: str
    collection_title: str = ""


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

    # 1. Find all potential new files/books. Top-level directories are treated
    # as collection folders and expanded by one level only.
    candidates = _discover_inbox_candidates(inbox_dir)

    # 2. Batch check stability (skips files actively being written)
    stable_paths = set(_check_paths_stable_batch([candidate.path for candidate in candidates]))
    stable_candidates = [
        candidate for candidate in candidates
        if candidate.path in stable_paths
    ]
    if len(stable_candidates) < len(candidates):
        logger.debug(
            "Found %d candidate(s), but only %d are stable. Waiting for the rest.",
            len(candidates),
            len(stable_candidates),
        )

    # 3. Process only stable files
    for candidate in stable_candidates:
        file_path = candidate.path
        # Compute hash for idempotency ONLY when stable
        try:
            file_hash = _compute_candidate_hash(candidate)
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
            file_name=candidate.file_name,
            file_hash=file_hash,
            current_status=ProcessingStatus.WAITING_STABLE, # Will be picked up immediately
            collection_title=candidate.collection_title,
        )

        try:
            record_id = db.insert_record(record)
            record.id = record_id
            discovered.append(record)
            logger.info(
                "Discovered and stable: %s (id=%d, hash=%s...)",
                candidate.file_name,
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


def _discover_inbox_candidates(inbox_dir: Path) -> list[InboxCandidate]:
    candidates: list[InboxCandidate] = []

    for path in sorted(inbox_dir.iterdir()):
        if path.is_file():
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                candidates.append(InboxCandidate(path=path, file_name=path.name))
            else:
                logger.debug("Skipping unsupported file type: %s", path.name)
            continue

        if not path.is_dir():
            continue

        collection_title = path.name
        for child in sorted(path.iterdir()):
            if child.is_file():
                if child.suffix.lower() in SUPPORTED_EXTENSIONS:
                    candidates.append(
                        InboxCandidate(
                            path=child,
                            file_name=child.name,
                            collection_title=collection_title,
                        )
                    )
                else:
                    logger.debug("Skipping unsupported collection file: %s", child)
                continue

            if child.is_dir() and _is_single_book_image_dir(child):
                candidates.append(
                    InboxCandidate(
                        path=child,
                        file_name=child.name,
                        collection_title=collection_title,
                    )
                )
            elif child.is_dir():
                logger.debug("Skipping nested/non-image directory: %s", child)

    return candidates


def _is_single_book_image_dir(path: Path) -> bool:
    return any(
        child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS
        for child in path.iterdir()
    )


def _check_paths_stable_batch(paths: list[Path], check_interval: int = 2) -> list[Path]:
    if not paths:
        return []

    initial = {
        path: snapshot
        for path in paths
        if (snapshot := _path_snapshot(path)) is not None
    }
    if not initial:
        return []

    time.sleep(check_interval)

    stable: list[Path] = []
    for path, snapshot in initial.items():
        if _path_snapshot(path) == snapshot:
            stable.append(path)
    return stable


def _path_snapshot(path: Path) -> tuple[tuple[str, int], ...] | None:
    try:
        if path.is_file():
            size = path.stat().st_size
            if size <= 0:
                return None
            return ((path.name, size),)

        if path.is_dir():
            entries: list[tuple[str, int]] = []
            for child in sorted(path.iterdir()):
                if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
                    size = child.stat().st_size
                    if size <= 0:
                        return None
                    entries.append((child.name, size))
            return tuple(entries) if entries else None
    except OSError:
        return None

    return None


def _compute_candidate_hash(candidate: InboxCandidate) -> str:
    if candidate.path.is_dir():
        return compute_directory_hash(candidate.path, IMAGE_EXTENSIONS)
    return compute_file_hash(candidate.path)
