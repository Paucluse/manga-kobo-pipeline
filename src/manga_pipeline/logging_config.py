"""Logging configuration.

Sets up console + file logging with configurable levels.
Log files are written to the configured logs directory.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    log_dir: Path | None = None,
) -> None:
    """Configure logging for the pipeline.

    Sets up two handlers:
    - Console handler (stderr) for interactive use
    - File handler (if log_dir provided) for persistent logging

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        log_format: Log message format string.
        log_dir: Directory for log files. If None, file logging is disabled.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Root logger for the pipeline
    logger = logging.getLogger("manga_pipeline")
    logger.setLevel(log_level)

    # Clear any existing handlers (avoids duplicates on re-init)
    logger.handlers.clear()

    formatter = logging.Formatter(log_format)

    # Console handler — always enabled
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler — only if log_dir is provided and writable
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "pipeline.log"
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as e:
            logger.warning("Could not set up file logging at %s: %s", log_dir, e)


def get_logger(name: str = "manga_pipeline") -> logging.Logger:
    """Get a logger instance for the pipeline.

    Args:
        name: Logger name, typically module name like 'manga_pipeline.scanner'.

    Returns:
        Configured Logger instance.
    """
    return logging.getLogger(name)
