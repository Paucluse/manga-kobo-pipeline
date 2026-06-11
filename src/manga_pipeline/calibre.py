"""Calibre database command wrapper.

Builds and executes calibredb commands for importing
manga into the Calibre library with metadata.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class CalibreImportResult:
    """Result of a Calibre import operation."""

    success: bool
    book_id: str = ""
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0


@dataclass
class CalibreMetadata:
    """Metadata for Calibre import."""

    title: str = ""
    authors: str = ""
    series: str = ""
    series_index: str = ""
    publisher: str = ""
    languages: str = "zho"
    tags: str = "manga,chinese-translation,kobo-sync"


def build_calibredb_add_command(
    file_path: Path,
    library_path: Path,
    metadata: CalibreMetadata,
    calibredb_cmd: str = "calibredb",
) -> list[str]:
    """Build the calibredb add command line.

    Args:
        file_path: Path to the file to import.
        library_path: Path to the Calibre library.
        metadata: Metadata to set on import.
        calibredb_cmd: Path or name of calibredb executable.

    Returns:
        List of command arguments.
    """
    cmd = [calibredb_cmd, "add"]

    # Library path
    cmd.extend(["--with-library", str(library_path)])

    # Metadata fields
    if metadata.title:
        cmd.extend(["--title", metadata.title])
    if metadata.authors:
        cmd.extend(["--authors", metadata.authors])
    if metadata.series:
        cmd.extend(["--series", metadata.series])
    if metadata.series_index:
        cmd.extend(["--series-index", metadata.series_index])
    if metadata.publisher:
        cmd.extend(["--publisher", metadata.publisher])
    if metadata.languages:
        cmd.extend(["--languages", metadata.languages])
    if metadata.tags:
        cmd.extend(["--tags", metadata.tags])

    # File to add
    cmd.append(str(file_path))

    return cmd


def run_calibredb_add(
    file_path: Path,
    library_path: Path,
    metadata: CalibreMetadata,
    calibredb_cmd: str = "calibredb",
) -> CalibreImportResult:
    """Execute calibredb add to import a file.

    Args:
        file_path: Path to the file to import.
        library_path: Path to the Calibre library.
        metadata: Metadata to set on import.
        calibredb_cmd: Path or name of calibredb executable.

    Returns:
        CalibreImportResult with import outcome.
    """
    cmd = build_calibredb_add_command(
        file_path, library_path, metadata, calibredb_cmd
    )
    logger.info("Running calibredb: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            book_id = _extract_book_id(result.stdout)
            logger.info(
                "Calibre import successful (book_id=%s): %s",
                book_id or "unknown",
                file_path.name,
            )
            return CalibreImportResult(
                success=True,
                book_id=book_id,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )
        else:
            logger.error(
                "calibredb failed (rc=%d): %s",
                result.returncode,
                result.stderr[:500],
            )
            return CalibreImportResult(
                success=False,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )

    except FileNotFoundError:
        logger.error("calibredb not found: %s", calibredb_cmd)
        return CalibreImportResult(
            success=False,
            stderr=f"Command not found: {calibredb_cmd}",
            return_code=-1,
        )
    except subprocess.TimeoutExpired:
        logger.error("calibredb timed out for: %s", file_path)
        return CalibreImportResult(
            success=False,
            stderr="Import timed out after 120s",
            return_code=-2,
        )


def _extract_book_id(stdout: str) -> str:
    """Extract book ID from calibredb add output.

    calibredb add typically outputs something like:
        Added book ids: 42
    """
    match = re.search(r"Added book ids?:\s*(\d+)", stdout)
    if match:
        return match.group(1)

    # Alternative pattern
    match = re.search(r"id:\s*(\d+)", stdout)
    if match:
        return match.group(1)

    return ""
