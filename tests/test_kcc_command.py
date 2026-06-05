"""Tests for KCC command construction."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from manga_pipeline.config import KoboConfig
from manga_pipeline.kcc import build_kcc_command, run_kcc


class TestBuildKccCommand:
    """Test KCC command line construction."""

    def test_default_command(self) -> None:
        """Default command with KoS profile."""
        cmd = build_kcc_command(
            input_path=Path("/inbox/manga.cbz"),
            output_dir=Path("/kepub_ready"),
        )
        assert cmd[0] == "kcc-c2e"
        assert "-p" in cmd
        assert cmd[cmd.index("-p") + 1] == "KoS"
        assert "-m" in cmd  # manga style
        assert "-q" in cmd  # high quality
        assert "-f" in cmd
        assert cmd[cmd.index("-f") + 1] == "EPUB"
        assert "-o" in cmd
        assert cmd[-1] == str(Path("/inbox/manga.cbz"))

    def test_custom_profile(self) -> None:
        """Custom Kobo profile."""
        config = KoboConfig(profile="KoF", format="CBZ")
        cmd = build_kcc_command(
            input_path=Path("/manga.cbz"),
            output_dir=Path("/output"),
            kobo_config=config,
        )
        assert cmd[cmd.index("-p") + 1] == "KoF"
        assert cmd[cmd.index("-f") + 1] == "CBZ"

    def test_no_manga_style(self) -> None:
        """Disable manga style (left-to-right)."""
        config = KoboConfig(manga_style=False)
        cmd = build_kcc_command(
            input_path=Path("/manga.cbz"),
            output_dir=Path("/output"),
            kobo_config=config,
        )
        assert "-m" not in cmd

    def test_no_high_quality(self) -> None:
        """Disable high quality."""
        config = KoboConfig(high_quality=False)
        cmd = build_kcc_command(
            input_path=Path("/manga.cbz"),
            output_dir=Path("/output"),
            kobo_config=config,
        )
        assert "-q" not in cmd

    def test_custom_kcc_path(self) -> None:
        """Custom kcc-c2e executable path."""
        cmd = build_kcc_command(
            input_path=Path("/manga.cbz"),
            output_dir=Path("/output"),
            kcc_cmd="/usr/local/bin/kcc-c2e",
        )
        assert cmd[0] == "/usr/local/bin/kcc-c2e"

    def test_output_dir_in_command(self) -> None:
        """Output directory should be after -o flag."""
        cmd = build_kcc_command(
            input_path=Path("/manga.cbz"),
            output_dir=Path("/data/kepub_ready"),
        )
        o_idx = cmd.index("-o")
        assert cmd[o_idx + 1] == str(Path("/data/kepub_ready"))


class TestRunKcc:
    """Test KCC execution with mocked subprocess."""

    @patch("manga_pipeline.kcc.subprocess.run")
    def test_successful_conversion(
        self, mock_run: MagicMock
    ) -> None:
        """Should return success on rc=0."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Conversion complete",
            stderr="",
        )
        result = run_kcc(
            Path("/inbox/manga.cbz"),
            Path("/output"),
        )
        assert result.success is True
        assert result.return_code == 0
        mock_run.assert_called_once()

    @patch("manga_pipeline.kcc.subprocess.run")
    def test_failed_conversion(
        self, mock_run: MagicMock
    ) -> None:
        """Should return failure on non-zero rc."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: invalid input",
        )
        result = run_kcc(
            Path("/inbox/manga.cbz"),
            Path("/output"),
        )
        assert result.success is False
        assert result.return_code == 1
        assert "invalid input" in result.stderr

    @patch("manga_pipeline.kcc.subprocess.run")
    def test_command_not_found(
        self, mock_run: MagicMock
    ) -> None:
        """Should handle missing kcc-c2e gracefully."""
        mock_run.side_effect = FileNotFoundError()
        result = run_kcc(
            Path("/inbox/manga.cbz"),
            Path("/output"),
        )
        assert result.success is False
        assert result.return_code == -1
        assert "not found" in result.stderr.lower()

    @patch("manga_pipeline.kcc.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        """Should handle timeout gracefully."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(
            "kcc-c2e", 600
        )
        result = run_kcc(
            Path("/inbox/manga.cbz"),
            Path("/output"),
        )
        assert result.success is False
        assert result.return_code == -2
        assert "timed out" in result.stderr.lower()
