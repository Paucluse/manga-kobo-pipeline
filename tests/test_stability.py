"""Tests for file stability checks."""

import time
from pathlib import Path

from manga_pipeline.stability import check_files_stable_batch


def test_check_files_stable_batch(tmp_path: Path, monkeypatch) -> None:
    """Should correctly identify stable and unstable files."""
    file1 = tmp_path / "stable.cbz"
    file2 = tmp_path / "growing.cbz"

    file1.write_bytes(b"static data")
    file2.write_bytes(b"initial data")

    # Mock time.sleep to simulate file growth during the sleep
    original_sleep = time.sleep

    def mock_sleep(seconds: int) -> None:
        file2.write_bytes(b"initial data + new data")
        original_sleep(0.01)  # tiny sleep just to yield

    monkeypatch.setattr(time, "sleep", mock_sleep)

    stable_files = check_files_stable_batch([file1, file2], check_interval=1)

    assert len(stable_files) == 1
    assert file1 in stable_files
    assert file2 not in stable_files
