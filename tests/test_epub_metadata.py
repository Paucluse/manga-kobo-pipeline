"""Tests for EPUB/KEPUB metadata updates."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from manga_pipeline.epub_metadata import write_epub_metadata


def test_write_epub_metadata_updates_opf(tmp_path: Path) -> None:
    epub_path = tmp_path / "book.kepub.epub"
    _write_minimal_epub(epub_path)

    write_epub_metadata(
        epub_path,
        title="三只眼 卷1",
        series="三只眼",
        number="1",
        writer="高田裕三",
        language_iso="zho",
        manga=True,
    )

    with zipfile.ZipFile(epub_path) as zf:
        opf = ET.fromstring(zf.read("OEBPS/content.opf"))

    ns = {
        "opf": "http://www.idpf.org/2007/opf",
        "dc": "http://purl.org/dc/elements/1.1/",
    }
    assert opf.findtext(".//dc:title", namespaces=ns) == "三只眼 卷1"
    assert opf.findtext(".//dc:creator", namespaces=ns) == "高田裕三"
    assert opf.findtext(".//dc:language", namespaces=ns) == "zho"
    collection = opf.find(".//opf:meta[@property='belongs-to-collection']", namespaces=ns)
    group_position = opf.find(".//opf:meta[@property='group-position']", namespaces=ns)
    spine = opf.find(".//opf:spine", namespaces=ns)
    assert collection is not None
    assert group_position is not None
    assert spine is not None
    assert collection.text == "三只眼"
    assert group_position.text == "1"
    assert spine.attrib["page-progression-direction"] == "rtl"


def _write_minimal_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        zf.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Old Title</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest/>
  <spine/>
</package>
""",
        )
