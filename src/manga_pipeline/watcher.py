"""File system watcher using watchdog.

Watches the inbox directory and triggers processing
when new manga files are added.
"""

from __future__ import annotations

import time
from pathlib import Path

from manga_pipeline.config import PipelineConfig
from manga_pipeline.database import Database
from manga_pipeline.logging_config import get_logger
from manga_pipeline.pipeline import process_all_pending
from manga_pipeline.scanner import scan_inbox

logger = get_logger(__name__)


def watch_inbox(cfg: PipelineConfig, db: Database) -> None:
    """Watch inbox directory and process new files via polling.

    Runs indefinitely until interrupted.

    Args:
        cfg: Pipeline configuration.
        db: Database instance.
    """
    inbox_dir = cfg.paths.inbox
    if not inbox_dir.is_dir():
        logger.error("Inbox directory not found: %s", inbox_dir)
        return

    logger.info("Starting pipeline in polling mode: %s", inbox_dir)
    logger.info("Press Ctrl+C to stop.")

    try:
        while True:
            try:
                # 1. Scan for new files
                discovered = scan_inbox(inbox_dir, db)
                if discovered:
                    logger.info(
                        "Discovered %d new file(s). Waiting %ds for stability...",
                        len(discovered),
                        cfg.processing.stable_check_seconds
                    )
                    # Give files time to stabilize (e.g. XFTP still uploading)
                    time.sleep(cfg.processing.stable_check_seconds)

                # 2. Process all pending tasks
                completed = process_all_pending(cfg, db)
                if completed > 0:
                    logger.info("Processed %d file(s) in this cycle.", completed)

            except Exception:
                logger.exception("Unexpected error in watch loop")

            # 3. Wait before next poll
            time.sleep(10)
            
    except KeyboardInterrupt:
        logger.info("Shutting down watcher...")
