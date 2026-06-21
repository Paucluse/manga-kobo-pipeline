"""SQLite database for tracking processing state.

Provides CRUD operations for manga processing records.
Uses file_hash for idempotency — same file won't be re-imported.
"""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from pathlib import Path

from manga_pipeline.logging_config import get_logger
from manga_pipeline.models import MangaRecord, ProcessingStatus

logger = get_logger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS manga_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    current_status TEXT NOT NULL DEFAULT 'discovered',
    title TEXT DEFAULT '',
    author TEXT DEFAULT '',
    series TEXT DEFAULT '',
    volume TEXT DEFAULT '',
    publisher TEXT DEFAULT '',
    collection_title TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    cover_url TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    isbn TEXT DEFAULT '',
    page_count TEXT DEFAULT '',
    confidence REAL DEFAULT 0.0,
    archive_path TEXT DEFAULT '',
    converted_path TEXT DEFAULT '',
    library_book_id TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    retry_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class Database:
    """SQLite database wrapper for manga processing state."""

    def __init__(self, db_path: Path) -> None:
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.debug("Database initialized at %s", db_path)

    def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        self.conn.execute(CREATE_TABLE_SQL)
        # Migrate schema if needed
        for migration in [
            "ALTER TABLE manga_records ADD COLUMN publisher TEXT DEFAULT ''",
            "ALTER TABLE manga_records ADD COLUMN collection_title TEXT DEFAULT ''",
            "ALTER TABLE manga_records ADD COLUMN summary TEXT DEFAULT ''",
            "ALTER TABLE manga_records ADD COLUMN cover_url TEXT DEFAULT ''",
            "ALTER TABLE manga_records ADD COLUMN source_url TEXT DEFAULT ''",
            "ALTER TABLE manga_records ADD COLUMN isbn TEXT DEFAULT ''",
            "ALTER TABLE manga_records ADD COLUMN page_count TEXT DEFAULT ''",
            "ALTER TABLE manga_records RENAME COLUMN calibre_book_id TO library_book_id",
        ]:
            with suppress(sqlite3.OperationalError):
                self.conn.execute(migration)
        self.conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()

    def insert_record(self, record: MangaRecord) -> int:
        """Insert a new manga record.

        Args:
            record: MangaRecord to insert.

        Returns:
            The ID of the inserted record.

        Raises:
            sqlite3.IntegrityError: If file_hash already exists.
        """
        cursor = self.conn.execute(
            """
            INSERT INTO manga_records (
                original_path, file_name, file_hash, current_status,
                title, author, series, volume, publisher, collection_title,
                summary, cover_url, source_url, isbn, page_count, confidence,
                archive_path, converted_path, library_book_id,
                error_message, retry_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.original_path,
                record.file_name,
                record.file_hash,
                record.current_status.value,
                record.title,
                record.author,
                record.series,
                record.volume,
                record.publisher,
                record.collection_title,
                record.summary,
                record.cover_url,
                record.source_url,
                record.isbn,
                record.page_count,
                record.confidence,
                record.archive_path,
                record.converted_path,
                record.library_book_id,
                record.error_message,
                record.retry_count,
                record.created_at,
                record.updated_at,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def update_status(
        self,
        record_id: int,
        status: ProcessingStatus,
        error_message: str = "",
        **extra_fields: str,
    ) -> None:
        """Update the status of a record.

        Args:
            record_id: ID of the record to update.
            status: New status.
            error_message: Error message (for failed status).
            **extra_fields: Additional fields to update (e.g., archive_path).
        """
        from datetime import datetime

        updates = ["current_status = ?", "updated_at = ?", "error_message = ?"]
        values: list[str | int] = [
            status.value,
            datetime.now().isoformat(),
            error_message,
        ]

        for field_name, value in extra_fields.items():
            updates.append(f"{field_name} = ?")
            values.append(value)

        values.append(record_id)

        self.conn.execute(
            f"UPDATE manga_records SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        self.conn.commit()

    def get_record_by_id(self, record_id: int) -> MangaRecord | None:
        """Get a record by ID."""
        row = self.conn.execute(
            "SELECT * FROM manga_records WHERE id = ?", (record_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get_record_by_hash(self, file_hash: str) -> MangaRecord | None:
        """Get a record by file hash."""
        row = self.conn.execute(
            "SELECT * FROM manga_records WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get_records_by_status(
        self, status: ProcessingStatus
    ) -> list[MangaRecord]:
        """Get all records with a given status."""
        rows = self.conn.execute(
            "SELECT * FROM manga_records WHERE current_status = ? "
            "ORDER BY created_at",
            (status.value,),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_status_counts(self) -> dict[str, int]:
        """Get count of records per status."""
        rows = self.conn.execute(
            "SELECT current_status, COUNT(*) as cnt "
            "FROM manga_records GROUP BY current_status"
        ).fetchall()
        return {row["current_status"]: row["cnt"] for row in rows}

    def get_all_records(self) -> list[MangaRecord]:
        """Get all records ordered by creation time."""
        rows = self.conn.execute(
            "SELECT * FROM manga_records ORDER BY created_at"
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def increment_retry(self, record_id: int) -> None:
        """Increment the retry count for a record."""
        from datetime import datetime

        self.conn.execute(
            "UPDATE manga_records SET retry_count = retry_count + 1, "
            "updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), record_id),
        )
        self.conn.commit()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> MangaRecord:
        """Convert a database row to a MangaRecord."""
        return MangaRecord(
            id=row["id"],
            original_path=row["original_path"],
            file_name=row["file_name"],
            file_hash=row["file_hash"],
            current_status=ProcessingStatus(row["current_status"]),
            title=row["title"],
            author=row["author"],
            series=row["series"],
            volume=row["volume"],
            publisher=row["publisher"],
            collection_title=row["collection_title"],
            summary=row["summary"],
            cover_url=row["cover_url"],
            source_url=row["source_url"],
            isbn=row["isbn"],
            page_count=row["page_count"],
            confidence=row["confidence"],
            archive_path=row["archive_path"],
            converted_path=row["converted_path"],
            library_book_id=row["library_book_id"],
            error_message=row["error_message"],
            retry_count=row["retry_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
