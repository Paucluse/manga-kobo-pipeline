"""KCC (Kindle Comic Converter) command wrapper.

Builds and executes kcc-c2e commands for converting manga
to Kobo Sage compatible KEPUB/EPUB format.
"""

from __future__ import annotations

import subprocess
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
            logger.info("KCC conversion successful: %s", output_path)
            return KccResult(
                success=True,
                output_path=output_path,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )
        else:
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

    # Fallback: find any recently created epub in output dir
    epubs = sorted(
        output_dir.glob("*.epub"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if epubs:
        return str(epubs[0])

    return ""
