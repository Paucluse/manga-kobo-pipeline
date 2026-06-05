"""Archive normalizer.

Handles normalization of different archive formats:
- CBZ/ZIP: Already in target format, just rename
- CBR/RAR: Repack as CBZ
- 7Z: Repack as CBZ
- Image directories: Pack as CBZ
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)


def normalize_to_cbz(
    input_path: Path,
    output_dir: Path,
    target_name: str = "",
) -> Path:
    """Normalize a manga archive to CBZ format.

    Args:
        input_path: Path to the source archive.
        output_dir: Directory for the output CBZ file.
        target_name: Desired filename (without .cbz extension).
                     If empty, uses the original stem.

    Returns:
        Path to the output CBZ file.

    Raises:
        ValueError: If the input format is not supported.
        OSError: If file operations fail.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = target_name or input_path.stem
    output_path = output_dir / f"{stem}.cbz"

    suffix = input_path.suffix.lower()

    if suffix in (".cbz", ".zip"):
        # Already CBZ/ZIP — copy and rename
        shutil.copy2(input_path, output_path)
        logger.info("Copied %s -> %s", input_path.name, output_path.name)
        return output_path

    if suffix in (".cbr", ".rar"):
        return _repack_rar_to_cbz(input_path, output_path)

    if suffix == ".7z":
        return _repack_7z_to_cbz(input_path, output_path)

    if input_path.is_dir():
        return _pack_directory_to_cbz(input_path, output_path)

    msg = f"Unsupported format: {suffix}"
    raise ValueError(msg)


def _repack_rar_to_cbz(
    rar_path: Path, output_path: Path
) -> Path:
    """Repack RAR/CBR archive as CBZ."""
    try:
        import rarfile
    except ImportError as e:
        msg = "rarfile package required for RAR support"
        raise ImportError(msg) from e

    logger.info("Repacking RAR -> CBZ: %s", rar_path.name)

    with (
        rarfile.RarFile(str(rar_path)) as rf,
        zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf,
    ):
        for entry in rf.infolist():
            if not entry.is_dir():
                data = rf.read(entry.filename)
                zf.writestr(entry.filename, data)

    logger.info("Repacked to: %s", output_path.name)
    return output_path


def _repack_7z_to_cbz(
    sevenz_path: Path, output_path: Path
) -> Path:
    """Repack 7Z archive as CBZ."""
    try:
        import py7zr
    except ImportError as e:
        msg = "py7zr package required for 7z support"
        raise ImportError(msg) from e

    logger.info("Repacking 7Z -> CBZ: %s", sevenz_path.name)

    with (
        py7zr.SevenZipFile(str(sevenz_path), "r") as sz,
        zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf,
    ):
        for name, bio in sz.readall().items():
            zf.writestr(name, bio.read())

    logger.info("Repacked to: %s", output_path.name)
    return output_path


def _pack_directory_to_cbz(
    dir_path: Path, output_path: Path
) -> Path:
    """Pack an image directory as CBZ."""
    logger.info("Packing directory -> CBZ: %s", dir_path.name)

    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

    with zipfile.ZipFile(
        output_path, "w", zipfile.ZIP_DEFLATED
    ) as zf:
        for img_path in sorted(dir_path.rglob("*")):
            if (
                img_path.is_file()
                and img_path.suffix.lower() in image_extensions
            ):
                arcname = img_path.relative_to(dir_path)
                zf.write(img_path, arcname)

    logger.info("Packed to: %s", output_path.name)
    return output_path
