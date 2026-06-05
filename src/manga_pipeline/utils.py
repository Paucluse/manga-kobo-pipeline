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
