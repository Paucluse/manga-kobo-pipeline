"""Tests for calibredb command construction."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from manga_pipeline.calibre import (
    CalibreMetadata,
    _extract_book_id,
    build_calibredb_add_command,
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
