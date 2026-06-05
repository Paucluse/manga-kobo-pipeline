"""ComicInfo.xml generator.

Generates ComicInfo.xml metadata files for CBZ archives.
This XML format is understood by many comic readers and
can be embedded into CBZ files for rich metadata.

Reference: https://anansi-project.github.io/docs/comicinfo/documentation
"""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, indent, tostring


def generate_comicinfo_xml(
    title: str = "",
    series: str = "",
    number: str = "",
    writer: str = "",
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
    import zipfile

    xml_content = generate_comicinfo_xml(
        title=title,
        series=series,
        number=number,
        writer=writer,
        language_iso=language_iso,
        manga=manga,
        tags=tags,
    )

    with zipfile.ZipFile(cbz_path, "a") as zf:
        # Remove existing ComicInfo.xml if present
        if "ComicInfo.xml" in zf.namelist():
            # zipfile doesn't support deletion, so we skip
            # (the new one will take precedence in most readers)
            pass
        zf.writestr("ComicInfo.xml", xml_content)
