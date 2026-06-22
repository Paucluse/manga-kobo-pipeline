"""Data models and enumerations.

Defines ProcessingStatus enum and MangaRecord dataclass.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class ProcessingStatus(enum.StrEnum):
    """Processing pipeline status for a manga file."""

    DISCOVERED = "discovered"
    WAITING_STABLE = "waiting_stable"
    PROCESSING = "processing"
    NORMALIZED = "normalized"
    METADATA_PARSED = "metadata_parsed"
    AWAITING_METADATA_APPROVAL = "awaiting_metadata_approval"
    NEEDS_REVIEW = "needs_review"
    ARCHIVED = "archived"
    CONVERTED = "converted"
    IMPORTING = "importing"
    IMPORTED = "imported"
    DONE = "done"
    FAILED = "failed"


@dataclass
class MangaRecord:
    """Represents a manga file being processed through the pipeline."""

    id: int | None = None
    original_path: str = ""
    file_name: str = ""
    file_hash: str = ""
    current_status: ProcessingStatus = ProcessingStatus.DISCOVERED
    title: str = ""
    author: str = ""
    series: str = ""
    volume: str = ""
    publisher: str = ""
    collection_title: str = ""
    summary: str = ""
    cover_url: str = ""
    source_url: str = ""
    isbn: str = ""
    page_count: str = ""
    confidence: float = 0.0
    archive_path: str = ""
    converted_path: str = ""
    library_book_id: str = ""
    error_message: str = ""
    retry_count: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )


# Supported manga file extensions
SUPPORTED_EXTENSIONS: set[str] = {
    ".zip", ".cbz",
    ".rar", ".cbr",
    ".7z",
    ".pdf",
    ".epub",
}
