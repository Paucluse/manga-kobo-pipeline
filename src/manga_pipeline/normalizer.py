"""Archive normalizer.

Handles normalization of different archive formats:
- CBZ/ZIP: Already in target format, just rename
- CBR/RAR: Repack as CBZ
- 7Z: Repack as CBZ
- PDF: Extract embedded images with pdfimages, optionally rasterize as fallback
- Image directories: Pack as CBZ
"""

from __future__ import annotations

import contextlib
import html
import posixpath
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urldefrag

from manga_pipeline.config import PdfConfig
from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
PDFIMAGE_EXTENSIONS = IMAGE_EXTENSIONS | {".jp2", ".jbig2", ".ppm", ".pbm", ".pgm"}
EPUB_IMAGE_EXTENSIONS = IMAGE_EXTENSIONS | {".svg"}
EPUB_IMAGE_REF_RE = re.compile(
    r"""(?:src|href|xlink:href)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PdfImageInfo:
    """One image entry reported by pdfimages -list."""

    page: int
    number: int
    width: int
    height: int

    @property
    def area(self) -> int:
        """Pixel area for selecting the page's primary image."""
        return self.width * self.height


def normalize_to_cbz(
    input_path: Path,
    output_dir: Path,
    target_name: str = "",
    pdf_config: PdfConfig | None = None,
    pdfimages_cmd: str = "pdfimages",
    pdftoppm_cmd: str = "pdftoppm",
) -> Path:
    """Normalize a manga archive to CBZ format.

    Args:
        input_path: Path to the source archive.
        output_dir: Directory for the output CBZ file.
        target_name: Desired filename (without .cbz extension).
                     If empty, uses the original stem.
        pdf_config: PDF extraction/rasterization settings for .pdf inputs.
        pdfimages_cmd: Command path for pdfimages.
        pdftoppm_cmd: Command path for pdftoppm.

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

    if suffix == ".pdf":
        return _normalize_pdf_to_cbz(
            input_path,
            output_path,
            pdf_config or PdfConfig(),
            pdfimages_cmd,
            pdftoppm_cmd,
        )

    if suffix == ".epub":
        return _normalize_epub_to_cbz(input_path, output_path)

    if input_path.is_dir():
        return _pack_directory_to_cbz(input_path, output_path)

    msg = f"Unsupported format: {suffix}"
    raise ValueError(msg)


def _repack_rar_to_cbz(
    rar_path: Path, output_path: Path
) -> Path:
    """Repack RAR/CBR archive as CBZ."""
    logger.info("Repacking RAR -> CBZ: %s", rar_path.name)

    errors: list[str] = []
    for tool in ("unar", "7z"):
        if shutil.which(tool):
            try:
                return _repack_rar_with_external_tool(rar_path, output_path, tool)
            except ValueError as e:
                errors.append(str(e))

    try:
        import rarfile
    except ImportError as e:
        msg = "rarfile package required for RAR support"
        raise ImportError(msg) from e

    try:
        with (
            rarfile.RarFile(str(rar_path)) as rf,
            zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf,
        ):
            for entry in rf.infolist():
                if not entry.is_dir():
                    data = rf.read(entry.filename)
                    zf.writestr(entry.filename, data)
    except rarfile.Error as e:
        _remove_partial_output(output_path)
        errors.append(f"rarfile failed: {e}")
        msg = "RAR extraction failed"
        if errors:
            msg = f"{msg}: {'; '.join(errors)}"
        raise ValueError(msg) from e

    logger.info("Repacked to: %s", output_path.name)
    return output_path


def _repack_rar_with_external_tool(
    rar_path: Path,
    output_path: Path,
    tool: str,
) -> Path:
    with tempfile.TemporaryDirectory(prefix="rar-extract-") as tmp:
        extract_dir = Path(tmp)
        if tool == "unar":
            command = [
                tool,
                "-quiet",
                "-force-overwrite",
                "-output-directory",
                str(extract_dir),
                str(rar_path),
            ]
        elif tool == "7z":
            command = [tool, "x", "-y", f"-o{extract_dir}", str(rar_path)]
        else:
            msg = f"Unsupported RAR extraction tool: {tool}"
            raise ValueError(msg)

        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            detail = _short_process_output(result)
            msg = f"{tool} failed"
            if detail:
                msg = f"{msg}: {detail}"
            raise ValueError(msg)

        try:
            _pack_extracted_tree_to_cbz(extract_dir, output_path)
        except ValueError:
            _remove_partial_output(output_path)
            raise

    logger.info("Repacked to: %s", output_path.name)
    return output_path


def _remove_partial_output(output_path: Path) -> None:
    with contextlib.suppress(OSError):
        output_path.unlink()


def _pack_extracted_tree_to_cbz(extract_dir: Path, output_path: Path) -> None:
    files = sorted(path for path in extract_dir.rglob("*") if path.is_file())
    if not files:
        msg = "RAR extraction produced no files"
        raise ValueError(msg)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            zf.write(file_path, file_path.relative_to(extract_dir).as_posix())


def _short_process_output(result: subprocess.CompletedProcess[str]) -> str:
    text = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "; ".join(lines[-6:])[:800]


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

    with zipfile.ZipFile(
        output_path, "w", zipfile.ZIP_DEFLATED
    ) as zf:
        for img_path in sorted(dir_path.iterdir()):
            if (
                img_path.is_file()
                and img_path.suffix.lower() in IMAGE_EXTENSIONS
            ):
                zf.write(img_path, img_path.name)

    logger.info("Packed to: %s", output_path.name)
    return output_path


def _normalize_epub_to_cbz(epub_path: Path, output_path: Path) -> Path:
    """Extract image pages from an EPUB and pack them as a CBZ."""
    logger.info("Extracting EPUB images -> CBZ: %s", epub_path.name)

    try:
        with zipfile.ZipFile(epub_path) as epub:
            image_paths = _epub_ordered_image_paths(epub)
            if not image_paths:
                image_paths = _epub_fallback_image_paths(epub)
            if not image_paths:
                msg = "EPUB contains no extractable image pages"
                raise ValueError(msg)

            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as cbz:
                for index, image_path in enumerate(image_paths, start=1):
                    suffix = Path(image_path).suffix.lower()
                    cbz.writestr(f"{index:04d}{suffix}", epub.read(image_path))
    except zipfile.BadZipFile as e:
        msg = f"Invalid EPUB archive: {epub_path}"
        raise ValueError(msg) from e

    logger.info("Extracted EPUB to: %s", output_path.name)
    return output_path


def _epub_ordered_image_paths(epub: zipfile.ZipFile) -> list[str]:
    rootfile = _epub_rootfile_path(epub)
    if not rootfile:
        return []

    try:
        root = ET.fromstring(epub.read(rootfile))
    except (ET.ParseError, KeyError):
        return []

    manifest: dict[str, str] = {}
    spine: list[str] = []
    opf_dir = posixpath.dirname(rootfile)
    names = set(epub.namelist())

    for item in root.findall(".//{*}manifest/{*}item"):
        item_id = item.get("id")
        href = item.get("href")
        if item_id and href:
            manifest[item_id] = _resolve_epub_path(opf_dir, href)

    for itemref in root.findall(".//{*}spine/{*}itemref"):
        idref = itemref.get("idref")
        if idref:
            spine.append(idref)

    image_paths: list[str] = []
    seen: set[str] = set()
    for idref in spine:
        item_path = manifest.get(idref, "")
        for image_path in _epub_images_from_spine_item(epub, item_path, names):
            if image_path not in seen:
                image_paths.append(image_path)
                seen.add(image_path)

    return image_paths


def _epub_rootfile_path(epub: zipfile.ZipFile) -> str:
    try:
        container = ET.fromstring(epub.read("META-INF/container.xml"))
    except (ET.ParseError, KeyError):
        return ""

    rootfile = container.find(".//{*}rootfile")
    if rootfile is None:
        return ""
    return rootfile.get("full-path", "")


def _epub_images_from_spine_item(
    epub: zipfile.ZipFile,
    item_path: str,
    names: set[str],
) -> list[str]:
    if not item_path:
        return []
    if Path(item_path).suffix.lower() in EPUB_IMAGE_EXTENSIONS and item_path in names:
        return [item_path]
    if item_path not in names:
        return []

    try:
        text = epub.read(item_path).decode("utf-8", errors="ignore")
    except KeyError:
        return []

    item_dir = posixpath.dirname(item_path)
    image_paths: list[str] = []
    for match in EPUB_IMAGE_REF_RE.finditer(text):
        image_path = _resolve_epub_path(item_dir, match.group(1))
        if image_path in names and Path(image_path).suffix.lower() in EPUB_IMAGE_EXTENSIONS:
            image_paths.append(image_path)
    return image_paths


def _epub_fallback_image_paths(epub: zipfile.ZipFile) -> list[str]:
    return [
        name for name in sorted(epub.namelist())
        if Path(name).suffix.lower() in EPUB_IMAGE_EXTENSIONS
    ]


def _resolve_epub_path(base_dir: str, href: str) -> str:
    clean_href, _fragment = urldefrag(html.unescape(href))
    clean_href = unquote(clean_href)
    return posixpath.normpath(posixpath.join(base_dir, clean_href)).lstrip("/")


def _normalize_pdf_to_cbz(
    pdf_path: Path,
    output_path: Path,
    pdf_config: PdfConfig,
    pdfimages_cmd: str,
    pdftoppm_cmd: str,
) -> Path:
    """Normalize PDF according to the configured strategy."""
    if not pdf_config.enabled:
        msg = "PDF input is disabled"
        raise ValueError(msg)

    strategy = pdf_config.strategy.lower()
    if strategy == "extract_first":
        try:
            result = _extract_pdf_images_to_cbz(
                pdf_path,
                output_path,
                pdf_config,
                pdfimages_cmd,
            )
            _preserve_original_pdf(pdf_path, output_path, pdf_config)
            return result
        except ValueError:
            if not pdf_config.render_fallback:
                raise
            logger.warning("PDF image extraction failed; falling back to rasterization.")

    if strategy in {"render", "rasterize", "extract_first"}:
        result = _rasterize_pdf_to_cbz(
            pdf_path,
            output_path,
            pdf_config,
            pdftoppm_cmd,
        )
        _preserve_original_pdf(pdf_path, output_path, pdf_config)
        return result

    msg = f"Unsupported PDF strategy: {pdf_config.strategy}"
    raise ValueError(msg)


def _extract_pdf_images_to_cbz(
    pdf_path: Path,
    output_path: Path,
    pdf_config: PdfConfig,
    pdfimages_cmd: str,
) -> Path:
    """Extract embedded PDF images and pack the primary image from each page."""
    logger.info("Extracting PDF images -> CBZ: %s", pdf_path.name)

    image_infos = _list_pdf_images(pdf_path, pdfimages_cmd)
    primary_images = _select_primary_pdf_images(image_infos)
    if not primary_images:
        msg = "PDF contains no extractable page images"
        raise ValueError(msg)

    with tempfile.TemporaryDirectory(
        prefix=f".{output_path.stem}-pdfimg-",
        dir=output_path.parent,
    ) as tmp:
        tmp_dir = Path(tmp)
        output_prefix = tmp_dir / "image"
        command = [
            pdfimages_cmd,
            "-j",
            "-png",
            "-p",
            str(pdf_path),
            str(output_prefix),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "pdfimages failed").strip()
            msg = f"PDF image extraction failed: {error}"
            raise ValueError(msg)

        extracted = [
            path for path in tmp_dir.iterdir()
            if path.is_file() and path.suffix.lower() in PDFIMAGE_EXTENSIONS
        ]
        page_images = _match_extracted_pdf_images(
            extracted,
            primary_images,
            output_prefix.name,
        )
        if len(page_images) < len(primary_images):
            msg = (
                "PDF image extraction did not produce one primary image per page "
                f"({len(page_images)}/{len(primary_images)})"
            )
            raise ValueError(msg)

        _pack_pdf_pages_to_cbz(page_images, output_path)

    logger.info("Extracted PDF images to: %s", output_path.name)
    return output_path


def _list_pdf_images(pdf_path: Path, pdfimages_cmd: str) -> list[PdfImageInfo]:
    """Return parseable image rows from pdfimages -list."""
    result = subprocess.run(
        [pdfimages_cmd, "-list", str(pdf_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "pdfimages -list failed").strip()
        msg = f"PDF image listing failed: {error}"
        raise ValueError(msg)

    infos: list[PdfImageInfo] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or not parts[0].isdigit():
            continue
        if parts[2] != "image":
            continue
        try:
            infos.append(
                PdfImageInfo(
                    page=int(parts[0]),
                    number=int(parts[1]),
                    width=int(parts[3]),
                    height=int(parts[4]),
                )
            )
        except ValueError:
            continue

    return infos


def _select_primary_pdf_images(image_infos: list[PdfImageInfo]) -> list[PdfImageInfo]:
    """Pick the largest image from each page."""
    by_page: dict[int, PdfImageInfo] = {}
    for info in image_infos:
        current = by_page.get(info.page)
        if current is None or info.area > current.area:
            by_page[info.page] = info
    return [by_page[page] for page in sorted(by_page)]


def _match_extracted_pdf_images(
    extracted: list[Path],
    primary_images: list[PdfImageInfo],
    prefix_name: str,
) -> list[Path]:
    """Match extracted pdfimages files to selected image rows."""
    by_key: dict[tuple[int, int], Path] = {}
    by_page: dict[int, list[Path]] = {}

    for path in sorted(extracted, key=_pdf_page_sort_key):
        parsed = _parse_pdfimages_output_name(path, prefix_name)
        if parsed is None:
            continue
        page, number = parsed
        by_key[(page, number)] = path
        by_page.setdefault(page, []).append(path)

    matched: list[Path] = []
    for info in primary_images:
        page_candidates = [info.page, info.page - 1]
        selected = None
        for page in page_candidates:
            selected = by_key.get((page, info.number))
            if selected is not None:
                break
        if selected is None:
            for page in page_candidates:
                candidates = by_page.get(page)
                if candidates:
                    selected = candidates[0]
                    break
        if selected is not None:
            matched.append(selected)

    return matched


def _parse_pdfimages_output_name(path: Path, prefix_name: str) -> tuple[int, int] | None:
    """Parse page/image number from pdfimages -p output names."""
    stem = path.stem
    if stem.startswith(prefix_name):
        stem = stem[len(prefix_name):]
    numbers = [int(value) for value in re.findall(r"\d+", stem)]
    if len(numbers) < 2:
        return None
    return numbers[-2], numbers[-1]


def _preserve_original_pdf(
    pdf_path: Path,
    output_path: Path,
    pdf_config: PdfConfig,
) -> None:
    """Keep the source PDF beside the generated CBZ before inbox cleanup."""
    if not pdf_config.preserve_original:
        return
    preserved_path = output_path.with_suffix(".source.pdf")
    shutil.copy2(pdf_path, preserved_path)
    logger.info("Preserved source PDF: %s", preserved_path.name)


def _rasterize_pdf_to_cbz(
    pdf_path: Path,
    output_path: Path,
    pdf_config: PdfConfig,
    pdftoppm_cmd: str,
) -> Path:
    """Rasterize a PDF into page images and pack them as CBZ."""
    if not pdf_config.enabled:
        msg = "PDF input is disabled"
        raise ValueError(msg)

    image_format = pdf_config.image_format.lower()
    if image_format not in {"jpg", "jpeg", "png"}:
        msg = f"Unsupported PDF image format: {pdf_config.image_format}"
        raise ValueError(msg)

    logger.info(
        "Rasterizing PDF -> CBZ: %s (dpi=%s, format=%s)",
        pdf_path.name,
        pdf_config.dpi,
        image_format,
    )

    with tempfile.TemporaryDirectory(
        prefix=f".{output_path.stem}-pdf-",
        dir=output_path.parent,
    ) as tmp:
        tmp_dir = Path(tmp)
        output_prefix = tmp_dir / "page"
        command = [
            pdftoppm_cmd,
            "-r",
            str(pdf_config.dpi),
        ]
        if image_format in {"jpg", "jpeg"}:
            command.extend(
                [
                    "-jpeg",
                    "-jpegopt",
                    f"quality={pdf_config.jpeg_quality},progressive=n,optimize=y",
                ]
            )
            expected_suffixes = {".jpg", ".jpeg"}
        else:
            command.append("-png")
            expected_suffixes = {".png"}

        command.extend([str(pdf_path), str(output_prefix)])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "pdftoppm failed").strip()
            msg = f"PDF rasterization failed: {error}"
            raise ValueError(msg)

        page_images = [
            path for path in tmp_dir.iterdir()
            if path.is_file() and path.suffix.lower() in expected_suffixes
        ]
        if not page_images:
            msg = "PDF rasterization produced no page images"
            raise ValueError(msg)

        _pack_pdf_pages_to_cbz(page_images, output_path)

    logger.info("Rasterized PDF to: %s", output_path.name)
    return output_path


def _pack_pdf_pages_to_cbz(page_images: list[Path], output_path: Path) -> None:
    """Pack rasterized PDF pages into a CBZ with stable numeric names."""
    sorted_pages = sorted(page_images, key=_pdf_page_sort_key)
    suffix = sorted_pages[0].suffix.lower()
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for index, page in enumerate(sorted_pages, start=1):
            arcname = f"{index:04d}{suffix}"
            zf.write(page, arcname)


def _pdf_page_sort_key(path: Path) -> tuple[int, str]:
    """Sort pdftoppm output by trailing page number."""
    stem = path.stem
    number = stem.rsplit("-", 1)[-1]
    if number.isdigit():
        return int(number), path.name
    return 0, path.name
