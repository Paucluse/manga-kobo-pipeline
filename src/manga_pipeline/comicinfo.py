"""ComicInfo.xml generator.

Generates ComicInfo.xml metadata files for CBZ archives.
This XML format is understood by many comic readers and
can be embedded into CBZ files for rich metadata.

Reference: https://anansi-project.github.io/docs/comicinfo/documentation
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, indent, tostring


def generate_comicinfo_xml(
    title: str = "",
    series: str = "",
    number: str = "",
    writer: str = "",
    publisher: str = "",
    summary: str = "",
    web: str = "",
    language_iso: str = "zh",
    manga: bool = True,
    tags: list[str] | None = None,
) -> str:
    """Generate ComicInfo.xml content.

    Args:
        title: Comic title.
        series: Series name.
        number: Volume/issue number.
        writer: Author/writer name.
        language_iso: ISO 639-1 language code.
        manga: If True, sets Manga=YesAndRightToLeft.
        tags: List of tags/genres.

    Returns:
        XML string content.
    """
    root = Element("ComicInfo")
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set(
        "xmlns:xsd", "http://www.w3.org/2001/XMLSchema"
    )

    if title:
        SubElement(root, "Title").text = title
    if series:
        SubElement(root, "Series").text = series
    if number:
        SubElement(root, "Number").text = number
    if writer:
        SubElement(root, "Writer").text = writer
    if publisher:
        SubElement(root, "Publisher").text = publisher
    if summary:
        SubElement(root, "Summary").text = summary
    if web:
        SubElement(root, "Web").text = web
    if language_iso:
        SubElement(root, "LanguageISO").text = language_iso

    if manga:
        SubElement(root, "Manga").text = "YesAndRightToLeft"

    if tags:
        SubElement(root, "Tags").text = ",".join(tags)

    indent(root, space="  ")
    xml_bytes = tostring(root, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + xml_bytes


def write_comicinfo_to_cbz(
    cbz_path: Path,
    title: str = "",
    series: str = "",
    number: str = "",
    writer: str = "",
    publisher: str = "",
    summary: str = "",
    web: str = "",
    language_iso: str = "zh",
    manga: bool = True,
    tags: list[str] | None = None,
) -> None:
    """Write ComicInfo.xml into an existing CBZ archive.

    Args:
        cbz_path: Path to the CBZ file.
        title: Comic title.
        series: Series name.
        number: Volume/issue number.
        writer: Author/writer name.
        language_iso: ISO 639-1 language code.
        manga: If True, sets Manga=YesAndRightToLeft.
        tags: List of tags/genres.
    """
    xml_content = generate_comicinfo_xml(
        title=title,
        series=series,
        number=number,
        writer=writer,
        publisher=publisher,
        summary=summary,
        web=web,
        language_iso=language_iso,
        manga=manga,
        tags=tags,
    )

    _replace_zip_member(cbz_path, "ComicInfo.xml", xml_content.encode("utf-8"))


def _replace_zip_member(zip_path: Path, member_name: str, content: bytes) -> None:
    """Replace a member in a zip archive without leaving duplicate entries."""
    with tempfile.NamedTemporaryFile(
        dir=zip_path.parent,
        prefix=f".{zip_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(zip_path, "r") as src, zipfile.ZipFile(tmp_path, "w") as dst:
            for item in src.infolist():
                if item.filename == member_name:
                    continue
                dst.writestr(item, src.read(item.filename))
            dst.writestr(member_name, content)
        tmp_path.replace(zip_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
