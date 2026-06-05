"""File system watcher using watchdog.

Watches the inbox directory and triggers processing
when new manga files are added.
"""

from __future__ import annotations

import time
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from manga_pipeline.config import PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.logging_config import get_logger
from manga_pipeline.models import SUPPORTED_EXTENSIONS
from manga_pipeline.pipeline import process_all_pending
from manga_pipeline.scanner import scan_inbox

logger = get_logger(__name__)


class MangaFileHandler(FileSystemEventHandler):
    """Handler for new manga file events."""

    def __init__(self, cfg: PipelineConfig, db: Database) -> None:
        self.cfg = cfg
        self.db = db

    def on_created(self, event: FileCreatedEvent) -> None:
        """Handle new file creation in inbox."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return

        logger.info("New file detected: %s", file_path.name)

        # Scan and process
        try:
            discovered = scan_inbox(self.cfg.paths.inbox, self.db)
            if discovered:
                logger.info(
                    "Discovered %d new file(s), processing...",
                    len(discovered),
                )
                # Give files time to stabilize
                time.sleep(
                    self.cfg.processing.stable_check_seconds
                )
                completed = process_all_pending(self.cfg, self.db)
                logger.info(
                    "Processing complete: %d file(s) done",
                    completed,
                )
        except Exception:
            logger.exception(
                "Error processing file: %s", file_path.name
            )


def watch_inbox(cfg: PipelineConfig, db: Database) -> None:
    """Watch inbox directory and process new files.

    Runs indefinitely until interrupted.

    Args:
        cfg: Pipeline configuration.
        db: Database instance.
    """
    inbox_dir = cfg.paths.inbox
    if not inbox_dir.is_dir():
        logger.error("Inbox directory not found: %s", inbox_dir)
        return

    # First, process any existing files
    logger.info("Initial scan of inbox: %s", inbox_dir)
    scan_inbox(inbox_dir, db)
    process_all_pending(cfg, db)

    # Set up watcher
    handler = MangaFileHandler(cfg, db)
    observer = Observer()
    observer.schedule(handler, str(inbox_dir), recursive=False)
    observer.start()

    logger.info("Watching inbox for new files: %s", inbox_dir)
    logger.info("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down watcher...")
        observer.stop()

    observer.join()
    logger.info("Watcher stopped.")
