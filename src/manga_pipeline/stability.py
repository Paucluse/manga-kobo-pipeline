"""File stability checker.

Ensures files are fully downloaded before processing,
by checking that file size remains constant over a configured period.
"""

from __future__ import annotations

import time
from pathlib import Path

from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)


def is_file_stable(
    file_path: Path,
    check_seconds: int = 30,
    check_interval: int = 5,
) -> bool:
    """Check if a file's size is stable (not being written to).

    Polls the file size at regular intervals. If the size doesn't
    change over the full check_seconds period, the file is considered
    stable and ready for processing.

    Args:
        file_path: Path to the file to check.
        check_seconds: Total time to monitor (seconds).
        check_interval: Time between size checks (seconds).

    Returns:
        True if file size is stable, False otherwise.
    """
    if not file_path.is_file():
        logger.warning("File does not exist: %s", file_path)
        return False

    try:
        initial_size = file_path.stat().st_size
    except OSError as e:
        logger.warning("Cannot stat file %s: %s", file_path, e)
        return False

    if initial_size == 0:
        logger.debug("File is empty: %s", file_path)
        return False

    elapsed = 0
    while elapsed < check_seconds:
        time.sleep(check_interval)
        elapsed += check_interval

        if not file_path.is_file():
            logger.warning("File disappeared during check: %s", file_path)
            return False

        try:
            current_size = file_path.stat().st_size
        except OSError:
            return False

        if current_size != initial_size:
            logger.debug(
                "File size changed (%d -> %d): %s",
                initial_size,
                current_size,
                file_path.name,
            )
            return False

    logger.debug(
        "File stable (size=%d, checked for %ds): %s",
        initial_size,
        check_seconds,
        file_path.name,
    )
    return True


def check_file_stable_quick(
    file_path: Path,
    check_interval: int = 2,
) -> bool:
    """Quick stability check: compare size at two points.

    Faster than is_file_stable, suitable for batch scanning.

    Args:
        file_path: Path to the file.
        check_interval: Seconds between two size checks.

    Returns:
        True if file size is the same after the interval.
    """
    if not file_path.is_file():
        return False

    try:
        size1 = file_path.stat().st_size
        if size1 == 0:
            return False
        time.sleep(check_interval)
        size2 = file_path.stat().st_size
        return size1 == size2
    except OSError:
        return False


def check_files_stable_batch(
    file_paths: list[Path],
    check_interval: int = 2,
) -> list[Path]:
    """Check stability for a batch of files concurrently.

    Args:
        file_paths: List of paths to check.
        check_interval: Seconds between two size checks.

    Returns:
        List of paths that are stable.
    """
    if not file_paths:
        return []

    initial_sizes = {}
    for path in file_paths:
        if path.is_file():
            try:
                size = path.stat().st_size
                if size > 0:
                    initial_sizes[path] = size
            except OSError:
                pass

    if not initial_sizes:
        return []

    time.sleep(check_interval)

    stable_paths = []
    for path, initial_size in initial_sizes.items():
        if path.is_file():
            try:
                if path.stat().st_size == initial_size:
                    stable_paths.append(path)
            except OSError:
                pass

    return stable_paths
