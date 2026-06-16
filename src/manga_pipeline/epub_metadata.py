"""EPUB/KEPUB metadata updater for Komga imports."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"

ET.register_namespace("", OPF_NS)
ET.register_namespace("dc", DC_NS)


def write_epub_metadata(
    epub_path: Path,
    title: str = "",
    series: str = "",
    number: str = "",
    writer: str = "",
    language_iso: str = "zh",
    summary: str = "",
    manga: bool = True,
) -> None:
    """Update OPF metadata in an EPUB/KEPUB file.

    Komga imports EPUB metadata from OPF fields. The collection metadata is
    intentionally OPF3-style because Komga uses it for series title and book
    number.
    """
    with zipfile.ZipFile(epub_path, "r") as zf:
        opf_path = _find_opf_path(zf)
        opf_bytes = zf.read(opf_path)

    updated = _update_opf(
        opf_bytes,
        title=title,
        series=series,
        number=number,
        writer=writer,
        language_iso=language_iso,
        summary=summary,
        manga=manga,
    )
    _replace_zip_member(epub_path, opf_path, updated)


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None:
        raise ValueError("EPUB container.xml does not contain a rootfile entry")
    full_path = rootfile.attrib.get("full-path", "")
    if not full_path:
        raise ValueError("EPUB rootfile entry is missing full-path")
    return full_path


def _update_opf(
    opf_bytes: bytes,
    title: str = "",
    series: str = "",
    number: str = "",
    writer: str = "",
    language_iso: str = "zh",
    summary: str = "",
    manga: bool = True,
) -> bytes:
    root = ET.fromstring(opf_bytes)
    ns = _namespace(root.tag)
    metadata = root.find(_tag(ns, "metadata"))
    if metadata is None:
        metadata = ET.SubElement(root, _tag(ns, "metadata"))

    _set_text(metadata, DC_NS, "title", title)
    _set_text(metadata, DC_NS, "creator", writer)
    _set_text(metadata, DC_NS, "language", language_iso)
    _set_text(metadata, DC_NS, "description", summary)

    if series:
        _remove_collection_metadata(metadata)
        collection = ET.SubElement(
            metadata,
            _tag(ns, "meta"),
            {"id": "manga-pipeline-series", "property": "belongs-to-collection"},
        )
        collection.text = series
        collection_type = ET.SubElement(
            metadata,
            _tag(ns, "meta"),
            {"refines": "#manga-pipeline-series", "property": "collection-type"},
        )
        collection_type.text = "series"
        if number:
            group_position = ET.SubElement(
                metadata,
                _tag(ns, "meta"),
                {"refines": "#manga-pipeline-series", "property": "group-position"},
            )
            group_position.text = number

    if manga:
        spine = root.find(_tag(ns, "spine"))
        if spine is not None:
            spine.set("page-progression-direction", "rtl")

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _set_text(parent: ET.Element, namespace: str, tag: str, value: str) -> None:
    for child in list(parent):
        if child.tag == _tag(namespace, tag):
            parent.remove(child)
    if value:
        element = ET.Element(_tag(namespace, tag))
        element.text = value
        parent.insert(0, element)


def _remove_collection_metadata(metadata: ET.Element) -> None:
    for child in list(metadata):
        if _local_name(child.tag) != "meta":
            continue
        if child.attrib.get("property") == "belongs-to-collection":
            metadata.remove(child)
            continue
        if child.attrib.get("refines") == "#manga-pipeline-series":
            metadata.remove(child)


def _replace_zip_member(zip_path: Path, member_name: str, content: bytes) -> None:
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


def _namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag


def _tag(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}" if namespace else name
