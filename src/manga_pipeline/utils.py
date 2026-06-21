"""Utility functions.

Shared helpers: file hashing, path operations, etc.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_file_hash(file_path: Path, algorithm: str = "sha256") -> str:
    """Compute hash of a file.

    Reads file in chunks to handle large files efficiently.

    Args:
        file_path: Path to the file.
        algorithm: Hash algorithm name (default: sha256).

    Returns:
        Hex digest string.
    """
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def compute_directory_hash(
    dir_path: Path,
    extensions: set[str],
    algorithm: str = "sha256",
) -> str:
    """Compute a stable hash for direct child files in a directory.

    Only direct files whose suffix is in ``extensions`` are included. This keeps
    inbox collection handling intentionally one level deep.
    """
    h = hashlib.new(algorithm)
    for file_path in sorted(dir_path.iterdir()):
        if not file_path.is_file() or file_path.suffix.lower() not in extensions:
            continue
        h.update(file_path.name.encode("utf-8"))
        h.update(b"\0")
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()
