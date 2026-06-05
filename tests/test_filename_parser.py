"""Tests for filename_parser module.

Covers all example filenames from the spec plus edge cases.
"""

from manga_pipeline.filename_parser import ParseResult, parse_filename


class TestAuthorExtraction:
    """Test extracting author from [brackets]."""

    def test_author_in_brackets_japanese(self) -> None:
        result = parse_filename("[桜場コハル] みなみけ 第01巻.cbz")
        assert result.author == "桜場コハル"
        assert result.title == "みなみけ"
        assert result.volume == "1"

    def test_author_in_brackets_ascii(self) -> None:
        result = parse_filename("[author] title vol.01.cbz")
        assert result.author == "author"
        assert result.title == "title"
        assert result.volume == "1"

    def test_no_author(self) -> None:
        result = parse_filename("みなみけ 第01巻.zip")
        assert result.author == ""
        assert result.title == "みなみけ"


class TestVolumePatterns:
    """Test different volume number patterns."""

    def test_dai_kan_pattern(self) -> None:
        """第XX巻 pattern."""
        result = parse_filename("みなみけ 第01巻.cbz")
        assert result.volume == "1"
        assert result.title == "みなみけ"

    def test_dai_kan_three_digits(self) -> None:
        """第001巻 pattern."""
        result = parse_filename("よつばと! 第001巻.cbz")
        assert result.volume == "1"
        assert result.title == "よつばと!"

    def test_v_pattern(self) -> None:
        """vXX pattern."""
        result = parse_filename("みなみけ v01.cbz")
        assert result.volume == "1"
        assert result.title == "みなみけ"

    def test_vol_dot_pattern(self) -> None:
        """vol.XX pattern."""
        result = parse_filename("[author] title vol.01.cbz")
        assert result.volume == "1"

    def test_vol_space_pattern(self) -> None:
        """vol XX pattern."""
        result = parse_filename("manga vol 03.cbz")
        assert result.volume == "3"

    def test_bare_number(self) -> None:
        """Trailing number only."""
        result = parse_filename("ダンジョン飯 01.cbz")
        assert result.volume == "1"
        assert result.title == "ダンジョン飯"

    def test_volume_10(self) -> None:
        """Double digit volume."""
        result = parse_filename("みなみけ 第10巻.cbz")
        assert result.volume == "10"

    def test_volume_100(self) -> None:
        """Triple digit volume."""
        result = parse_filename("ワンピース 第100巻.cbz")
        assert result.volume == "100"

    def test_no_volume(self) -> None:
        """No volume number in filename."""
        result = parse_filename("oneshot_manga.cbz")
        assert result.volume == ""

    def test_chinese_juan_pattern(self) -> None:
        """第XX卷 Chinese volume pattern."""
        result = parse_filename("进击的巨人 第01卷.cbz")
        assert result.volume == "1"
        assert result.title == "进击的巨人"

    def test_chinese_juan_three_digits(self) -> None:
        """第001卷 Chinese three-digit volume."""
        result = parse_filename("海贼王 第100卷.cbz")
        assert result.volume == "100"
        assert result.title == "海贼王"

    def test_chinese_juan_with_author(self) -> None:
        """[Author] Title 第XX卷 Chinese pattern with author."""
        result = parse_filename("[尾田栄一郎] 海贼王 第01卷.cbz")
        assert result.author == "尾田栄一郎"
        assert result.title == "海贼王"
        assert result.volume == "1"
        assert result.confidence >= 0.85



class TestSpecExamples:
    """Test all examples from the user's specification."""

    def test_sakuraba_minami_ke(self) -> None:
        """[桜場コハル] みなみけ 第01巻.cbz"""
        result = parse_filename("[桜場コハル] みなみけ 第01巻.cbz")
        assert result.author == "桜場コハル"
        assert result.title == "みなみけ"
        assert result.volume == "1"
        assert result.confidence >= 0.85

    def test_minami_ke_no_author(self) -> None:
        """みなみけ 第01巻.zip"""
        result = parse_filename("みなみけ 第01巻.zip")
        assert result.title == "みなみけ"
        assert result.volume == "1"
        assert result.author == ""

    def test_minami_ke_v_pattern(self) -> None:
        """みなみけ v01.cbz"""
        result = parse_filename("みなみけ v01.cbz")
        assert result.title == "みなみけ"
        assert result.volume == "1"

    def test_author_title_vol(self) -> None:
        """[author] title vol.01.cbz"""
        result = parse_filename("[author] title vol.01.cbz")
        assert result.author == "author"
        assert result.title == "title"
        assert result.volume == "1"

    def test_dungeon_meshi(self) -> None:
        """ダンジョン飯 01.cbz"""
        result = parse_filename("ダンジョン飯 01.cbz")
        assert result.title == "ダンジョン飯"
        assert result.volume == "1"

    def test_yotsuba(self) -> None:
        """よつばと! 第001巻.cbz"""
        result = parse_filename("よつばと! 第001巻.cbz")
        assert result.title == "よつばと!"
        assert result.volume == "1"


class TestEdgeCases:
    """Test edge cases and unusual filenames."""

    def test_empty_filename(self) -> None:
        result = parse_filename("")
        assert result.title == ""
        assert result.confidence == 0.0

    def test_extension_only(self) -> None:
        result = parse_filename(".cbz")
        assert result.confidence == 0.0

    def test_no_extension(self) -> None:
        result = parse_filename("manga_title v05")
        assert result.title == "manga_title"
        assert result.volume == "5"

    def test_multiple_extensions(self) -> None:
        result = parse_filename("title.kepub.epub")
        assert result.title == "title"

    def test_underscores_in_title(self) -> None:
        result = parse_filename("my_manga_title v02.cbz")
        assert result.title == "my_manga_title"
        assert result.volume == "2"

    def test_title_with_spaces(self) -> None:
        result = parse_filename("[Author Name] Long Title Name 第05巻.cbz")
        assert result.author == "Author Name"
        assert result.title == "Long Title Name"
        assert result.volume == "5"

    def test_rar_extension(self) -> None:
        result = parse_filename("manga 03.rar")
        assert result.volume == "3"

    def test_cbr_extension(self) -> None:
        result = parse_filename("manga 03.cbr")
        assert result.volume == "3"

    def test_7z_extension(self) -> None:
        result = parse_filename("manga v02.7z")
        assert result.volume == "2"

    def test_series_equals_title(self) -> None:
        """Series should be same as title for manga."""
        result = parse_filename("みなみけ 第01巻.cbz")
        assert result.series == result.title


class TestConfidence:
    """Test confidence scoring."""

    def test_full_info_high_confidence(self) -> None:
        """Author + title + volume should give high confidence."""
        result = parse_filename("[桜場コハル] みなみけ 第01巻.cbz")
        assert result.confidence >= 0.85

    def test_title_and_volume_medium(self) -> None:
        """Title + volume without author."""
        result = parse_filename("みなみけ v01.cbz")
        assert 0.5 <= result.confidence < 0.9

    def test_title_only_low(self) -> None:
        """Title only, no volume or author."""
        result = parse_filename("some_manga.cbz")
        assert result.confidence < 0.6

    def test_empty_zero_confidence(self) -> None:
        result = parse_filename("")
        assert result.confidence == 0.0


class TestParseResultDisplay:
    """Test the display property."""

    def test_full_display(self) -> None:
        r = ParseResult(
            title="みなみけ", author="桜場コハル", volume="1"
        )
        assert "[桜場コハル]" in r.display
        assert "みなみけ" in r.display
        assert "v1" in r.display

    def test_no_author_display(self) -> None:
        r = ParseResult(title="みなみけ", volume="1")
        assert r.display == "みなみけ v1"

    def test_empty_display(self) -> None:
        r = ParseResult()
        assert r.display == "(unparsed)"
