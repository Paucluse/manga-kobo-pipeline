"""KCC (Kindle Comic Converter) command wrapper.

Builds and executes kcc-c2e commands for converting manga
to Kobo Sage compatible KEPUB/EPUB format.
"""

from __future__ import annotations

import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from manga_pipeline.config import KoboConfig
from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class KccResult:
    """Result of a KCC conversion."""

    success: bool
    output_path: str = ""
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0


def build_kcc_command(
    input_path: Path,
    output_dir: Path,
    kcc_cmd: str = "kcc-c2e",
    kobo_config: KoboConfig | None = None,
) -> list[str]:
    """Build the kcc-c2e command line.

    Args:
        input_path: Path to the input manga file.
        output_dir: Directory for KCC output.
        kcc_cmd: Path or name of the kcc-c2e executable.
        kobo_config: Kobo conversion settings.

    Returns:
        List of command arguments.
    """
    if kobo_config is None:
        kobo_config = KoboConfig()

    cmd = [kcc_cmd]

    # Profile (e.g., KoS for Kobo Sage)
    cmd.extend(["-p", kobo_config.profile])

    # Manga style (right-to-left)
    if kobo_config.manga_style:
        cmd.append("-m")

    # High quality
    if kobo_config.high_quality:
        cmd.append("-q")

    # Output format
    cmd.extend(["-f", kobo_config.format])

    # Output directory
    cmd.extend(["-o", str(output_dir)])

    # Input file
    cmd.append(str(input_path))

    return cmd


def run_kcc(
    input_path: Path,
    output_dir: Path,
    kcc_cmd: str = "kcc-c2e",
    kobo_config: KoboConfig | None = None,
) -> KccResult:
    """Execute KCC to convert a manga file.

    Args:
        input_path: Path to the input manga file.
        output_dir: Directory for KCC output.
        kcc_cmd: Path or name of the kcc-c2e executable.
        kobo_config: Kobo conversion settings.

    Returns:
        KccResult with conversion outcome.
    """
    cmd = build_kcc_command(input_path, output_dir, kcc_cmd, kobo_config)
    logger.info("Running KCC: %s", " ".join(cmd))
    _remove_stale_outputs(output_dir, input_path)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )

        if result.returncode == 0:
            # Try to find the output file
            output_path = _find_output_file(output_dir, input_path)
            if output_path and kobo_config and kobo_config.format.upper() == "KEPUB":
                output_path = _ensure_kepub_extension(Path(output_path))
            if output_path and not _output_has_expected_pages(input_path, Path(output_path)):
                Path(output_path).unlink(missing_ok=True)
                msg = "KCC output has fewer image pages than the source archive"
                logger.error("%s: %s", msg, input_path)
                return KccResult(
                    success=False,
                    stdout=result.stdout,
                    stderr=msg,
                    return_code=1,
                )
            logger.info("KCC conversion successful: %s", output_path)
            return KccResult(
                success=True,
                output_path=output_path,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )
        else:
            _remove_stale_outputs(output_dir, input_path)
            logger.error(
                "KCC failed (rc=%d): %s",
                result.returncode,
                result.stderr[:500],
            )
            return KccResult(
                success=False,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )

    except FileNotFoundError:
        logger.error("KCC command not found: %s", kcc_cmd)
        return KccResult(
            success=False,
            stderr=f"Command not found: {kcc_cmd}",
            return_code=-1,
        )
    except subprocess.TimeoutExpired:
        logger.error("KCC conversion timed out for: %s", input_path)
        return KccResult(
            success=False,
            stderr="Conversion timed out after 600s",
            return_code=-2,
        )


def _find_output_file(output_dir: Path, input_path: Path) -> str:
    """Try to find the converted output file.

    KCC typically outputs a file with .epub or .kepub.epub extension
    in the output directory.
    """
    stem = input_path.stem
    # Check common output patterns
    for ext in [".kepub.epub", ".epub"]:
        candidate = output_dir / f"{stem}{ext}"
        if candidate.is_file():
            return str(candidate)

    return ""


def _ensure_kepub_extension(output_path: Path) -> str:
    """Rename KCC's Kobo output to the conventional .kepub.epub suffix."""
    if output_path.name.lower().endswith(".kepub.epub"):
        return str(output_path)

    if output_path.suffix.lower() != ".epub":
        return str(output_path)

    target = output_path.with_name(f"{output_path.stem}.kepub.epub")
    if target == output_path:
        return str(output_path)

    if target.exists():
        target.unlink()
    output_path.rename(target)
    return str(target)


def _remove_stale_outputs(output_dir: Path, input_path: Path) -> None:
    """Remove exact output paths before/after conversion to avoid stale imports."""
    stem = input_path.stem
    for ext in [".kepub.epub", ".epub"]:
        (output_dir / f"{stem}{ext}").unlink(missing_ok=True)


def _output_has_expected_pages(input_path: Path, output_path: Path) -> bool:
    """Sanity-check KCC did not leave a truncated EPUB behind."""
    source_images = _count_zip_images(input_path)
    output_images = _count_zip_images(output_path)
    if source_images == 0 or output_images == 0:
        return True
    return output_images >= source_images


def _count_zip_images(path: Path) -> int:
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
    try:
        with zipfile.ZipFile(path) as zf:
            return sum(
                1
                for name in zf.namelist()
                if Path(name).suffix.lower() in image_exts
            )
    except (OSError, zipfile.BadZipFile):
        return 0
