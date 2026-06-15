"""Shared test fixtures."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory structure for testing."""
    dirs = [
        "inbox",
        "processing",
        "archive_cbz",
        "kepub_ready",
        "komga-library",
        "state",
        "manual-review",
        "logs",
    ]
    for d in dirs:
        (tmp_path / d).mkdir()
    return tmp_path
