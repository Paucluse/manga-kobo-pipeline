"""File system watcher using watchdog.

Watches the inbox directory and triggers processing
when new manga files are added.
"""

from __future__ import annotations

import threading
from pathlib import Path

from watchdog.events import (
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from manga_pipeline.calibre import calibre_book_exists
from manga_pipeline.config import PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.logging_config import get_logger
from manga_pipeline.models import SUPPORTED_EXTENSIONS
from manga_pipeline.pipeline import process_all_pending
from manga_pipeline.scanner import scan_inbox

logger = get_logger(__name__)


class MangaFileHandler(FileSystemEventHandler):
    """Handler for new manga file events."""

    def __init__(self, wake_up_event: threading.Event) -> None:
        self.wake_up_event = wake_up_event

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory:
            self._trigger(event.src_path)

    def on_moved(self, event: FileMovedEvent) -> None:
        if not event.is_directory:
            # For XFTP and other tools that rename temp files to final files
            self._trigger(event.dest_path)

    def on_modified(self, event: FileModifiedEvent) -> None:
        if not event.is_directory:
            self._trigger(event.src_path)

    def _trigger(self, path: str) -> None:
        """Trigger the wake up event if the file is a supported manga file."""
        if Path(path).suffix.lower() in SUPPORTED_EXTENSIONS:
            self.wake_up_event.set()


def watch_inbox(cfg: PipelineConfig, db: Database) -> None:
    """Watch inbox directory and process new files using an event listener.

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
    scan_inbox(
        inbox_dir,
        db,
        stability_check_interval=cfg.processing.stable_check_interval,
        calibre_record_exists=lambda record: calibre_book_exists(
            record.calibre_book_id,
            cfg.paths.calibre_library,
            cfg.commands.calibredb,
        ),
    )
    process_all_pending(cfg, db)

    # Set up watcher
    wake_up_event = threading.Event()
    handler = MangaFileHandler(wake_up_event)
    observer = Observer()
    observer.schedule(handler, str(inbox_dir), recursive=False)
    observer.start()

    logger.info(
        "Starting pipeline in event-driven mode with %ds polling fallback: %s",
        cfg.processing.poll_interval_seconds,
        inbox_dir,
    )
    logger.info("Press Ctrl+C to stop.")

    try:
        while True:
            # Filesystem events provide low latency; the timeout is the
            # correctness path for FTP, Docker bind mounts, and missed events.
            wake_up_event.wait(
                timeout=float(cfg.processing.poll_interval_seconds)
            )
            wake_up_event.clear()

            try:
                # 1. Scan for new files
                discovered = scan_inbox(
                    inbox_dir,
                    db,
                    stability_check_interval=cfg.processing.stable_check_interval,
                    calibre_record_exists=lambda record: calibre_book_exists(
                        record.calibre_book_id,
                        cfg.paths.calibre_library,
                        cfg.commands.calibredb,
                    ),
                )
                if discovered:
                    logger.info(
                        "Discovered and verified %d new stable file(s). "
                        "Starting processing immediately.",
                        len(discovered),
                    )

                # 2. Process pending tasks
                completed = process_all_pending(cfg, db)
                if completed > 0:
                    logger.info("Processed %d file(s) in this cycle.", completed)

            except Exception:
                logger.exception("Unexpected error in watch loop")

    except KeyboardInterrupt:
        logger.info("Shutting down watcher...")
        observer.stop()

    observer.join()
    logger.info("Watcher stopped.")
