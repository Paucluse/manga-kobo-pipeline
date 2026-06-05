"""Tests for state machine / database module."""

from pathlib import Path

from manga_pipeline.database import Database
from manga_pipeline.models import MangaRecord, ProcessingStatus


class TestDatabase:
    """Test database CRUD operations."""

    def test_create_database(self, tmp_path: Path) -> None:
        """Database should create file and tables on init."""
        db_path = tmp_path / "test.db"
        db = Database(db_path)
        assert db_path.is_file()
        db.close()

    def test_insert_and_retrieve_record(self, tmp_path: Path) -> None:
        """Should insert a record and retrieve it by ID."""
        db = Database(tmp_path / "test.db")
        record = MangaRecord(
            original_path="/inbox/test.cbz",
            file_name="test.cbz",
            file_hash="abc123",
            current_status=ProcessingStatus.DISCOVERED,
        )
        record_id = db.insert_record(record)
        assert record_id > 0

        retrieved = db.get_record_by_id(record_id)
        assert retrieved is not None
        assert retrieved.file_name == "test.cbz"
        assert retrieved.file_hash == "abc123"
        assert retrieved.current_status == ProcessingStatus.DISCOVERED
        db.close()

    def test_retrieve_by_hash(self, tmp_path: Path) -> None:
        """Should retrieve a record by file hash."""
        db = Database(tmp_path / "test.db")
        record = MangaRecord(
            original_path="/inbox/manga.cbz",
            file_name="manga.cbz",
            file_hash="unique_hash_123",
        )
        db.insert_record(record)

        found = db.get_record_by_hash("unique_hash_123")
        assert found is not None
        assert found.file_name == "manga.cbz"

        not_found = db.get_record_by_hash("nonexistent")
        assert not_found is None
        db.close()

    def test_duplicate_hash_rejected(self, tmp_path: Path) -> None:
        """Should reject duplicate file hashes (idempotency)."""
        import sqlite3

        import pytest

        db = Database(tmp_path / "test.db")
        record1 = MangaRecord(
            original_path="/inbox/a.cbz",
            file_name="a.cbz",
            file_hash="same_hash",
        )
        record2 = MangaRecord(
            original_path="/inbox/b.cbz",
            file_name="b.cbz",
            file_hash="same_hash",
        )
        db.insert_record(record1)

        with pytest.raises(sqlite3.IntegrityError):
            db.insert_record(record2)
        db.close()


class TestStatusTransitions:
    """Test status update operations."""

    def test_update_status(self, tmp_path: Path) -> None:
        """Should update record status."""
        db = Database(tmp_path / "test.db")
        record = MangaRecord(
            original_path="/inbox/test.cbz",
            file_name="test.cbz",
            file_hash="hash1",
        )
        record_id = db.insert_record(record)

        db.update_status(record_id, ProcessingStatus.PROCESSING)
        updated = db.get_record_by_id(record_id)
        assert updated is not None
        assert updated.current_status == ProcessingStatus.PROCESSING
        db.close()

    def test_update_status_with_error(self, tmp_path: Path) -> None:
        """Should record error message on failure."""
        db = Database(tmp_path / "test.db")
        record = MangaRecord(
            original_path="/inbox/test.cbz",
            file_name="test.cbz",
            file_hash="hash2",
        )
        record_id = db.insert_record(record)

        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message="KCC conversion failed",
        )
        updated = db.get_record_by_id(record_id)
        assert updated is not None
        assert updated.current_status == ProcessingStatus.FAILED
        assert updated.error_message == "KCC conversion failed"
        db.close()

    def test_update_status_with_extra_fields(
        self, tmp_path: Path
    ) -> None:
        """Should update extra fields alongside status."""
        db = Database(tmp_path / "test.db")
        record = MangaRecord(
            original_path="/inbox/test.cbz",
            file_name="test.cbz",
            file_hash="hash3",
        )
        record_id = db.insert_record(record)

        db.update_status(
            record_id,
            ProcessingStatus.ARCHIVED,
            archive_path="/archive/test.cbz",
        )
        updated = db.get_record_by_id(record_id)
        assert updated is not None
        assert updated.current_status == ProcessingStatus.ARCHIVED
        assert updated.archive_path == "/archive/test.cbz"
        db.close()

    def test_full_status_lifecycle(self, tmp_path: Path) -> None:
        """Should transition through the full pipeline lifecycle."""
        db = Database(tmp_path / "test.db")
        record = MangaRecord(
            original_path="/inbox/test.cbz",
            file_name="test.cbz",
            file_hash="hash4",
        )
        record_id = db.insert_record(record)

        lifecycle = [
            ProcessingStatus.DISCOVERED,
            ProcessingStatus.WAITING_STABLE,
            ProcessingStatus.PROCESSING,
            ProcessingStatus.METADATA_PARSED,
            ProcessingStatus.ARCHIVED,
            ProcessingStatus.CONVERTED,
            ProcessingStatus.IMPORTING,
            ProcessingStatus.IMPORTED,
            ProcessingStatus.DONE,
        ]

        for status in lifecycle[1:]:  # Skip DISCOVERED (already set)
            db.update_status(record_id, status)
            updated = db.get_record_by_id(record_id)
            assert updated is not None
            assert updated.current_status == status
        db.close()


class TestStatusCounts:
    """Test status counting and queries."""

    def test_status_counts(self, tmp_path: Path) -> None:
        """Should count records by status."""
        db = Database(tmp_path / "test.db")
        for i in range(3):
            db.insert_record(
                MangaRecord(
                    original_path=f"/inbox/{i}.cbz",
                    file_name=f"{i}.cbz",
                    file_hash=f"hash_{i}",
                )
            )
        # Update one to processing
        db.update_status(1, ProcessingStatus.PROCESSING)

        counts = db.get_status_counts()
        assert counts["discovered"] == 2
        assert counts["processing"] == 1
        db.close()

    def test_get_records_by_status(self, tmp_path: Path) -> None:
        """Should filter records by status."""
        db = Database(tmp_path / "test.db")
        for i in range(3):
            db.insert_record(
                MangaRecord(
                    original_path=f"/inbox/{i}.cbz",
                    file_name=f"{i}.cbz",
                    file_hash=f"hash_{i}",
                )
            )
        db.update_status(2, ProcessingStatus.FAILED, "error")

        discovered = db.get_records_by_status(
            ProcessingStatus.DISCOVERED
        )
        assert len(discovered) == 2

        failed = db.get_records_by_status(ProcessingStatus.FAILED)
        assert len(failed) == 1
        assert failed[0].error_message == "error"
        db.close()

    def test_increment_retry(self, tmp_path: Path) -> None:
        """Should increment retry counter."""
        db = Database(tmp_path / "test.db")
        record = MangaRecord(
            original_path="/inbox/test.cbz",
            file_name="test.cbz",
            file_hash="hash_retry",
        )
        record_id = db.insert_record(record)

        db.increment_retry(record_id)
        db.increment_retry(record_id)

        updated = db.get_record_by_id(record_id)
        assert updated is not None
        assert updated.retry_count == 2
        db.close()
