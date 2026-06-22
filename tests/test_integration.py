"""Tests for normalizer, review, comicinfo, and scanner modules."""

import subprocess
import zipfile
from pathlib import Path

import pytest

from manga_pipeline.comicinfo import generate_comicinfo_xml, write_comicinfo_to_cbz
from manga_pipeline.config import PdfConfig
from manga_pipeline.database import Database
from manga_pipeline.models import ProcessingStatus
from manga_pipeline.normalizer import normalize_to_cbz
from manga_pipeline.review import move_to_review
from manga_pipeline.scanner import scan_inbox


class TestNormalizeCbz:
    """Test archive normalization."""

    def test_copy_zip_as_cbz(self, tmp_path: Path) -> None:
        """ZIP files should be copied as CBZ."""
        # Create a dummy zip
        src = tmp_path / "input" / "manga.zip"
        src.parent.mkdir()
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr("page01.jpg", b"fake image data")

        out_dir = tmp_path / "archive"
        result = normalize_to_cbz(src, out_dir)

        assert result.suffix == ".cbz"
        assert result.is_file()
        # Verify it's a valid zip
        with zipfile.ZipFile(result) as zf:
            assert "page01.jpg" in zf.namelist()

    def test_copy_cbz_as_cbz(self, tmp_path: Path) -> None:
        """CBZ files should be copied directly."""
        src = tmp_path / "input" / "manga.cbz"
        src.parent.mkdir()
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr("page01.png", b"fake image")

        out_dir = tmp_path / "archive"
        result = normalize_to_cbz(src, out_dir)

        assert result.name == "manga.cbz"
        assert result.is_file()

    def test_custom_target_name(self, tmp_path: Path) -> None:
        """Should use custom target name when provided."""
        src = tmp_path / "input" / "messy_name.zip"
        src.parent.mkdir()
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr("p1.jpg", b"data")

        out_dir = tmp_path / "archive"
        result = normalize_to_cbz(
            src, out_dir, target_name="[Author] Title v01"
        )

        assert result.name == "[Author] Title v01.cbz"

    def test_unsupported_format_raises(self, tmp_path: Path) -> None:
        """Should raise ValueError for unsupported formats."""
        src = tmp_path / "file.txt"
        src.write_bytes(b"not manga")

        with pytest.raises(ValueError, match="Unsupported"):
            normalize_to_cbz(src, tmp_path / "out")

    def test_extract_pdf_images_to_cbz(
        self,
        tmp_path: Path,
        monkeypatch: object,
    ) -> None:
        """PDF files should be extracted with pdfimages by default."""
        src = tmp_path / "input" / "manga.pdf"
        src.parent.mkdir()
        src.write_bytes(b"%PDF fake")
        commands: list[list[str]] = []

        def fake_run(
            command: list[str],
            capture_output: bool,
            text: bool,
            check: bool,
        ) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            if command[1] == "-list":
                stdout = "\n".join(
                    [
                        "page   num  type   width height color comp bpc  enc interp object ID",
                        "1      0    image  1200  1800   rgb   3    8    jpeg no     10 0",
                        "1      1    image  120   180    rgb   3    8    image no    12 0",
                        "2      0    image  1200  1800   rgb   3    8    jpeg no     11 0",
                    ]
                )
                return subprocess.CompletedProcess(command, 0, stdout, "")

            prefix = Path(command[-1])
            (prefix.parent / "image-001-000.jpg").write_bytes(b"page 1")
            (prefix.parent / "image-001-001.png").write_bytes(b"thumbnail")
            (prefix.parent / "image-002-000.jpg").write_bytes(b"page 2")
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr("manga_pipeline.normalizer.subprocess.run", fake_run)

        result = normalize_to_cbz(
            src,
            tmp_path / "archive",
            pdf_config=PdfConfig(strategy="extract_first"),
            pdfimages_cmd="pdfimages",
        )

        assert result.name == "manga.cbz"
        assert commands[0] == ["pdfimages", "-list", str(src)]
        assert commands[1][:4] == ["pdfimages", "-j", "-png", "-p"]
        assert commands[1][-2] == str(src)
        assert result.with_suffix(".source.pdf").is_file()
        with zipfile.ZipFile(result) as zf:
            assert zf.namelist() == ["0001.jpg", "0002.jpg"]
            assert zf.read("0001.jpg") == b"page 1"
            assert zf.read("0002.jpg") == b"page 2"

    def test_pdf_extract_without_fallback_raises(
        self,
        tmp_path: Path,
        monkeypatch: object,
    ) -> None:
        """PDF extraction failure should not render unless fallback is enabled."""
        src = tmp_path / "input" / "manga.pdf"
        src.parent.mkdir()
        src.write_bytes(b"%PDF fake")
        commands: list[list[str]] = []

        def fake_run(
            command: list[str],
            capture_output: bool,
            text: bool,
            check: bool,
        ) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr("manga_pipeline.normalizer.subprocess.run", fake_run)

        with pytest.raises(ValueError, match="no extractable"):
            normalize_to_cbz(
                src,
                tmp_path / "archive",
                pdf_config=PdfConfig(strategy="extract_first", render_fallback=False),
            )

        assert commands == [["pdfimages", "-list", str(src)]]

    def test_rasterize_pdf_to_cbz(self, tmp_path: Path, monkeypatch: object) -> None:
        """PDF files can still be rasterized with pdftoppm when requested."""
        src = tmp_path / "input" / "manga.pdf"
        src.parent.mkdir()
        src.write_bytes(b"%PDF fake")
        commands: list[list[str]] = []

        def fake_run(
            command: list[str],
            capture_output: bool,
            text: bool,
            check: bool,
        ) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            prefix = Path(command[-1])
            (prefix.parent / "page-2.jpg").write_bytes(b"page 2")
            (prefix.parent / "page-1.jpg").write_bytes(b"page 1")
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr("manga_pipeline.normalizer.subprocess.run", fake_run)

        result = normalize_to_cbz(
            src,
            tmp_path / "archive",
            pdf_config=PdfConfig(
                strategy="render",
                dpi=180,
                image_format="jpg",
                jpeg_quality=92,
            ),
            pdftoppm_cmd="pdftoppm",
        )

        assert result.name == "manga.cbz"
        assert commands[0][:6] == [
            "pdftoppm",
            "-r",
            "180",
            "-jpeg",
            "-jpegopt",
            "quality=92,progressive=n,optimize=y",
        ]
        assert commands[0][-2] == str(src)
        with zipfile.ZipFile(result) as zf:
            assert zf.namelist() == ["0001.jpg", "0002.jpg"]
            assert zf.read("0001.jpg") == b"page 1"
            assert zf.read("0002.jpg") == b"page 2"

    def test_pdf_disabled_raises(self, tmp_path: Path) -> None:
        """PDF support should be configurable."""
        src = tmp_path / "manga.pdf"
        src.write_bytes(b"%PDF fake")

        with pytest.raises(ValueError, match="disabled"):
            normalize_to_cbz(
                src,
                tmp_path / "archive",
                pdf_config=PdfConfig(enabled=False),
            )

    def test_extract_epub_images_to_cbz(self, tmp_path: Path) -> None:
        """EPUB image pages should be copied losslessly into a CBZ."""
        src = tmp_path / "input" / "manga.epub"
        src.parent.mkdir()
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0"?>
                <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
                  <rootfiles>
                    <rootfile full-path="OPS/content.opf"
                      media-type="application/oebps-package+xml"/>
                  </rootfiles>
                </container>
                """,
            )
            zf.writestr(
                "OPS/content.opf",
                """<?xml version="1.0"?>
                <package xmlns="http://www.idpf.org/2007/opf">
                  <manifest>
                    <item id="p1" href="Text/page1.xhtml" media-type="application/xhtml+xml"/>
                    <item id="p2" href="Text/page2.xhtml" media-type="application/xhtml+xml"/>
                    <item id="img1" href="Images/p001.jpg" media-type="image/jpeg"/>
                    <item id="img2" href="Images/p002.png" media-type="image/png"/>
                  </manifest>
                  <spine>
                    <itemref idref="p2"/>
                    <itemref idref="p1"/>
                  </spine>
                </package>
                """,
            )
            zf.writestr(
                "OPS/Text/page1.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                '<body><img src="../Images/p001.jpg"/></body></html>',
            )
            zf.writestr(
                "OPS/Text/page2.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                '<body><img src="../Images/p002.png"/></body></html>',
            )
            zf.writestr("OPS/Images/p001.jpg", b"original jpg bytes")
            zf.writestr("OPS/Images/p002.png", b"original png bytes")

        result = normalize_to_cbz(src, tmp_path / "archive")

        assert result.name == "manga.cbz"
        with zipfile.ZipFile(result) as zf:
            assert zf.namelist() == ["0001.png", "0002.jpg"]
            assert zf.read("0001.png") == b"original png bytes"
            assert zf.read("0002.jpg") == b"original jpg bytes"

    def test_pack_directory(self, tmp_path: Path) -> None:
        """Should pack image directory as CBZ."""
        img_dir = tmp_path / "manga_images"
        img_dir.mkdir()
        (img_dir / "page01.jpg").write_bytes(b"jpg data")
        (img_dir / "page02.png").write_bytes(b"png data")
        (img_dir / "readme.txt").write_bytes(b"not an image")

        out_dir = tmp_path / "archive"
        result = normalize_to_cbz(img_dir, out_dir)

        with zipfile.ZipFile(result) as zf:
            names = zf.namelist()
            assert "page01.jpg" in names
            assert "page02.png" in names
            # Non-image files should be excluded
            assert "readme.txt" not in names

    def test_output_dir_created(self, tmp_path: Path) -> None:
        """Output directory should be created if missing."""
        src = tmp_path / "manga.cbz"
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr("p.jpg", b"data")

        deep_dir = tmp_path / "a" / "b" / "c"
        result = normalize_to_cbz(src, deep_dir)
        assert deep_dir.is_dir()
        assert result.is_file()


class TestReview:
    """Test manual review handler."""

    def test_move_to_review(self, tmp_path: Path) -> None:
        """Should move file to review directory."""
        src = tmp_path / "inbox" / "mystery.cbz"
        src.parent.mkdir()
        src.write_bytes(b"manga data")

        review_dir = tmp_path / "review"
        result = move_to_review(
            src,
            review_dir,
            reason="Low confidence",
            parsed_metadata={"title": "mystery"},
        )

        assert result.is_file()
        assert not src.exists()
        assert result.parent == review_dir

    def test_companion_json_created(self, tmp_path: Path) -> None:
        """Should create companion .review.json file."""
        import json

        src = tmp_path / "file.cbz"
        src.write_bytes(b"data")

        review_dir = tmp_path / "review"
        result = move_to_review(src, review_dir, reason="test")

        json_path = result.with_suffix(".cbz.review.json")
        assert json_path.is_file()

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["reason"] == "test"
        assert "instructions" in data

    def test_avoids_overwrite(self, tmp_path: Path) -> None:
        """Should not overwrite existing files in review dir."""
        review_dir = tmp_path / "review"
        review_dir.mkdir()

        # Pre-populate with a file of the same name
        (review_dir / "dupe.cbz").write_bytes(b"existing")

        src = tmp_path / "dupe.cbz"
        src.write_bytes(b"new file")

        result = move_to_review(src, review_dir)
        assert result.name == "dupe_1.cbz"


class TestComicInfo:
    """Test ComicInfo.xml generation."""

    def test_generate_full_xml(self) -> None:
        """Should generate valid XML with all fields."""
        xml = generate_comicinfo_xml(
            title="みなみけ",
            series="みなみけ",
            number="1",
            writer="桜場コハル",
            language_iso="zh",
            manga=True,
            tags=["manga", "slice-of-life"],
        )
        assert '<?xml version="1.0"' in xml
        assert "<Title>みなみけ</Title>" in xml
        assert "<Series>みなみけ</Series>" in xml
        assert "<Number>1</Number>" in xml
        assert "<Writer>桜場コハル</Writer>" in xml
        assert "<LanguageISO>zh</LanguageISO>" in xml
        assert "<Manga>YesAndRightToLeft</Manga>" in xml
        assert "<Tags>manga,slice-of-life</Tags>" in xml

    def test_generate_minimal_xml(self) -> None:
        """Should handle empty fields gracefully."""
        xml = generate_comicinfo_xml(title="test")
        assert "<Title>test</Title>" in xml
        assert "<Writer>" not in xml  # Empty, should be omitted

    def test_manga_flag(self) -> None:
        """Manga=False should omit the Manga element."""
        xml = generate_comicinfo_xml(
            title="comic", manga=False
        )
        assert "Manga" not in xml

    def test_write_to_cbz(self, tmp_path: Path) -> None:
        """Should embed ComicInfo.xml into a CBZ file."""
        cbz_path = tmp_path / "test.cbz"
        with zipfile.ZipFile(cbz_path, "w") as zf:
            zf.writestr("page01.jpg", b"image data")

        write_comicinfo_to_cbz(
            cbz_path,
            title="Test Manga",
            writer="Author",
            number="5",
        )

        with zipfile.ZipFile(cbz_path) as zf:
            assert "ComicInfo.xml" in zf.namelist()
            content = zf.read("ComicInfo.xml").decode("utf-8")
            assert "<Title>Test Manga</Title>" in content
            assert "<Writer>Author</Writer>" in content


class TestScannerIntegration:
    """Integration tests for the scanner module."""

    def test_scan_discovers_cbz(self, tmp_path: Path) -> None:
        """Should discover .cbz files in inbox."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "manga1.cbz").write_bytes(b"data1")
        (inbox / "manga2.zip").write_bytes(b"data2")
        (inbox / "manga3.pdf").write_bytes(b"data3")
        (inbox / "readme.txt").write_bytes(b"not manga")

        db = Database(tmp_path / "state" / "test.db")
        discovered = scan_inbox(inbox, db)

        assert len(discovered) == 3
        names = {r.file_name for r in discovered}
        assert "manga1.cbz" in names
        assert "manga2.zip" in names
        assert "manga3.pdf" in names
        db.close()

    def test_scan_idempotent(self, tmp_path: Path) -> None:
        """Running scan twice should not duplicate records."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "manga.cbz").write_bytes(b"data")

        db = Database(tmp_path / "state" / "test.db")
        first = scan_inbox(inbox, db)
        second = scan_inbox(inbox, db)

        assert len(first) == 1
        assert len(second) == 0  # No new files
        assert len(db.get_all_records()) == 1
        db.close()

    def test_scan_empty_inbox(self, tmp_path: Path) -> None:
        """Empty inbox should return no results."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        db = Database(tmp_path / "state" / "test.db")
        discovered = scan_inbox(inbox, db)
        assert len(discovered) == 0
        db.close()

    def test_scan_nonexistent_inbox(self, tmp_path: Path) -> None:
        """Non-existent inbox should return empty list."""
        db = Database(tmp_path / "state" / "test.db")
        discovered = scan_inbox(
            tmp_path / "nonexistent", db
        )
        assert len(discovered) == 0
        db.close()

    def test_scan_records_have_correct_status(
        self, tmp_path: Path
    ) -> None:
        """Scanned stable files should be ready for processing."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "test.cbz").write_bytes(b"test data")

        db = Database(tmp_path / "state" / "test.db")
        discovered = scan_inbox(inbox, db)

        assert discovered[0].current_status == ProcessingStatus.WAITING_STABLE
        assert discovered[0].file_hash != ""
        db.close()

    def test_scan_records_have_hash(self, tmp_path: Path) -> None:
        """Each record should have a SHA-256 hash."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "test.cbz").write_bytes(b"unique content")

        db = Database(tmp_path / "state" / "test.db")
        discovered = scan_inbox(inbox, db)

        assert len(discovered[0].file_hash) == 64  # SHA-256 hex
        db.close()

    def test_scan_expands_collection_directory(self, tmp_path: Path) -> None:
        """A top-level directory should be treated as a manga collection."""
        inbox = tmp_path / "inbox"
        series_dir = inbox / "苍蓝钢铁战舰"
        volume_dir = series_dir / "4"
        volume_dir.mkdir(parents=True)
        (series_dir / "1.epub").write_bytes(b"volume 1")
        (series_dir / "2.zip").write_bytes(b"volume 2")
        (series_dir / "苍蓝钢铁战舰 第03卷.cbz").write_bytes(b"volume 3")
        (volume_dir / "001.jpg").write_bytes(b"page 1")
        (volume_dir / "002.jpg").write_bytes(b"page 2")

        db = Database(tmp_path / "state" / "test.db")
        discovered = scan_inbox(inbox, db)

        assert len(discovered) == 4
        by_name = {record.file_name: record for record in discovered}
        assert by_name["1.epub"].collection_title == "苍蓝钢铁战舰"
        assert by_name["2.zip"].collection_title == "苍蓝钢铁战舰"
        assert by_name["苍蓝钢铁战舰 第03卷.cbz"].collection_title == "苍蓝钢铁战舰"
        assert by_name["4"].collection_title == "苍蓝钢铁战舰"
        assert by_name["4"].original_path.endswith("/苍蓝钢铁战舰/4")
        db.close()

    def test_scan_collection_directory_ignores_nested_chapter_dirs(
        self,
        tmp_path: Path,
    ) -> None:
        """Only one level is supported; nested chapter directories are skipped."""
        inbox = tmp_path / "inbox"
        nested = inbox / "苍蓝钢铁战舰" / "5" / "chapter1"
        nested.mkdir(parents=True)
        (nested / "001.jpg").write_bytes(b"page")

        db = Database(tmp_path / "state" / "test.db")
        discovered = scan_inbox(inbox, db)

        assert discovered == []
        db.close()
