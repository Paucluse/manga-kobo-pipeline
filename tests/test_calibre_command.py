"""Tests for calibredb command construction."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from manga_pipeline.calibre import (
    CalibreMetadata,
    _extract_book_id,
    build_calibredb_add_command,
    build_calibredb_set_metadata_command,
    calibre_book_exists,
    run_calibredb_add,
)


class TestBuildCalibredbCommand:
    """Test calibredb add command construction."""

    def test_full_metadata(self) -> None:
        """Command with all metadata fields."""
        meta = CalibreMetadata(
            title="みなみけ",
            authors="桜場コハル",
            series="みなみけ",
            series_index="1",
            publisher="講談社",
            languages="zho",
            tags="manga,chinese-translation,kobo-sync",
        )
        cmd = build_calibredb_add_command(
            file_path=Path("/kepub/manga.epub"),
            library_path=Path("/calibre-library"),
            metadata=meta,
        )
        assert cmd[0] == "calibredb"
        assert cmd[1] == "add"
        assert "--with-library" in cmd
        assert "--title" in cmd
        assert cmd[cmd.index("--title") + 1] == "みなみけ"
        assert "--authors" in cmd
        assert cmd[cmd.index("--authors") + 1] == "桜場コハル"
        assert "--series" in cmd
        assert "--series-index" in cmd
        assert "--publisher" not in cmd
        assert "--languages" in cmd
        assert "--tags" in cmd
        assert cmd[-1] == str(Path("/kepub/manga.epub"))

    def test_minimal_metadata(self) -> None:
        """Command with only title."""
        meta = CalibreMetadata(
            title="test",
            authors="",
            series="",
            series_index="",
            languages="",
            tags="",
        )
        cmd = build_calibredb_add_command(
            file_path=Path("/file.epub"),
            library_path=Path("/lib"),
            metadata=meta,
        )
        assert "--title" in cmd
        assert "--authors" not in cmd
        assert "--series" not in cmd

    def test_custom_calibredb_path(self) -> None:
        """Custom calibredb executable path."""
        meta = CalibreMetadata(title="test")
        cmd = build_calibredb_add_command(
            file_path=Path("/file.epub"),
            library_path=Path("/lib"),
            metadata=meta,
            calibredb_cmd="/usr/bin/calibredb",
        )
        assert cmd[0] == "/usr/bin/calibredb"

    def test_library_path(self) -> None:
        """Library path should follow --with-library."""
        meta = CalibreMetadata(title="test")
        cmd = build_calibredb_add_command(
            file_path=Path("/file.epub"),
            library_path=Path("/data/calibre-library"),
            metadata=meta,
        )
        idx = cmd.index("--with-library")
        assert cmd[idx + 1] == str(Path("/data/calibre-library"))

    def test_set_metadata_publisher_command(self) -> None:
        """Publisher should be set with set_metadata after add."""
        cmd = build_calibredb_set_metadata_command(
            book_id="42",
            library_path=Path("/data/calibre-library"),
            field_name="publisher",
            field_value="長鴻出版社",
        )

        assert cmd == [
            "calibredb",
            "set_metadata",
            "--with-library",
            str(Path("/data/calibre-library")),
            "--field",
            "publisher:長鴻出版社",
            "42",
        ]


class TestExtractBookId:
    """Test book ID extraction from calibredb output."""

    def test_standard_output(self) -> None:
        assert _extract_book_id("Added book ids: 42") == "42"

    def test_multiple_ids(self) -> None:
        # Should get the first one
        assert _extract_book_id("Added book ids: 42") == "42"

    def test_id_pattern(self) -> None:
        assert _extract_book_id("id: 123") == "123"

    def test_no_id(self) -> None:
        assert _extract_book_id("No books added") == ""

    def test_empty_output(self) -> None:
        assert _extract_book_id("") == ""


class TestRunCalibredb:
    """Test calibredb execution with mocked subprocess."""

    @patch("manga_pipeline.calibre.subprocess.run")
    def test_successful_import(
        self, mock_run: MagicMock
    ) -> None:
        """Should return success with book_id."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Added book ids: 42",
            stderr="",
        )
        meta = CalibreMetadata(title="test")
        result = run_calibredb_add(
            Path("/file.epub"),
            Path("/lib"),
            meta,
        )
        assert result.success is True
        assert result.book_id == "42"
        assert result.return_code == 0

    @patch("manga_pipeline.calibre.subprocess.run")
    def test_missing_book_id_is_failure(
        self, mock_run: MagicMock
    ) -> None:
        """A zero return code without a book id usually means no book was added."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Not adding duplicate book",
            stderr="",
        )
        meta = CalibreMetadata(title="test")

        result = run_calibredb_add(
            Path("/file.epub"),
            Path("/lib"),
            meta,
        )

        assert result.success is False
        assert result.book_id == ""

    @patch("manga_pipeline.calibre.subprocess.run")
    def test_sets_publisher_after_successful_import(
        self, mock_run: MagicMock
    ) -> None:
        """Publisher is applied with set_metadata after book id is known."""
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="Added book ids: 42",
                stderr="",
            ),
            MagicMock(
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]
        meta = CalibreMetadata(title="test", publisher="長鴻出版社")

        result = run_calibredb_add(
            Path("/file.epub"),
            Path("/lib"),
            meta,
        )

        assert result.success is True
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[1].args[0] == [
            "calibredb",
            "set_metadata",
            "--with-library",
            str(Path("/lib")),
            "--field",
            "publisher:長鴻出版社",
            "42",
        ]

    @patch("manga_pipeline.calibre.subprocess.run")
    def test_publisher_update_failure_does_not_fail_import(
        self, mock_run: MagicMock
    ) -> None:
        """Do not retry the whole import if only set_metadata fails."""
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="Added book ids: 42",
                stderr="",
            ),
            MagicMock(
                returncode=1,
                stdout="",
                stderr="bad field",
            ),
        ]
        meta = CalibreMetadata(title="test", publisher="長鴻出版社")

        result = run_calibredb_add(
            Path("/file.epub"),
            Path("/lib"),
            meta,
        )

        assert result.success is True
        assert result.book_id == "42"

    @patch("manga_pipeline.calibre.subprocess.run")
    def test_failed_import(
        self, mock_run: MagicMock
    ) -> None:
        """Should return failure on non-zero rc."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="ERROR: Library locked",
        )
        meta = CalibreMetadata(title="test")
        result = run_calibredb_add(
            Path("/file.epub"),
            Path("/lib"),
            meta,
        )
        assert result.success is False
        assert result.return_code == 1

    @patch("manga_pipeline.calibre.subprocess.run")
    def test_command_not_found(
        self, mock_run: MagicMock
    ) -> None:
        """Should handle missing calibredb."""
        mock_run.side_effect = FileNotFoundError()
        meta = CalibreMetadata(title="test")
        result = run_calibredb_add(
            Path("/file.epub"),
            Path("/lib"),
            meta,
        )
        assert result.success is False
        assert result.return_code == -1

    @patch("manga_pipeline.calibre.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        """Should handle timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(
            "calibredb", 120
        )
        meta = CalibreMetadata(title="test")
        result = run_calibredb_add(
            Path("/file.epub"),
            Path("/lib"),
            meta,
        )
        assert result.success is False
        assert result.return_code == -2


class TestCalibreBookExists:
    """Test Calibre source-of-truth checks."""

    @patch("manga_pipeline.calibre.subprocess.run")
    def test_existing_book_id(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)

        assert calibre_book_exists("42", Path("/lib")) is True

    @patch("manga_pipeline.calibre.subprocess.run")
    def test_missing_book_id(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)

        assert calibre_book_exists("42", Path("/lib")) is False

    @patch("manga_pipeline.calibre.subprocess.run")
    def test_empty_book_id(self, mock_run: MagicMock) -> None:
        assert calibre_book_exists("", Path("/lib")) is False
        mock_run.assert_not_called()
