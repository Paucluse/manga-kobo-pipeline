"""Manual review handler.

Files with low-confidence metadata parsing are moved to
a manual-review directory for human inspection.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)


def move_to_review(
    file_path: Path,
    review_dir: Path,
    reason: str = "",
    parsed_metadata: dict | None = None,
) -> Path:
    """Move a file to the manual review directory.

    Creates a companion .review.json file with context about
    why the file was flagged for review.

    Args:
        file_path: Source file to move.
        review_dir: Manual review directory.
        reason: Why the file was flagged.
        parsed_metadata: The parsed metadata for context.

    Returns:
        New path of the moved file.
    """
    review_dir.mkdir(parents=True, exist_ok=True)
    dest_path = review_dir / file_path.name

    # Avoid overwriting
    if dest_path.exists():
        stem = file_path.stem
        suffix = file_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = review_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    shutil.move(str(file_path), str(dest_path))
    logger.info("Moved to review: %s -> %s", file_path.name, dest_path)

    # Write companion file with context
    review_info = {
        "original_path": str(file_path),
        "reason": reason,
        "parsed_metadata": parsed_metadata or {},
        "instructions": (
            "Review the filename and metadata above. "
            "Rename the file to match the expected pattern, "
            "then move back to the inbox directory."
        ),
    }
    info_path = dest_path.with_suffix(dest_path.suffix + ".review.json")
    info_path.write_text(
        json.dumps(review_info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return dest_path
