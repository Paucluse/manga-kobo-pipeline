"""Manga filename parser.

Parses manga filenames (Chinese-translated Japanese manga) to extract:
- title / series name
- author
- volume number
- confidence score

Supports patterns like:
    [桜場コハル] みなみけ 第01巻.cbz
    [尾田栄一郎] 海贼王 第01卷.cbz
    进击的巨人 第01卷.zip
    みなみけ v01.cbz
    [author] title vol.01.cbz
    ダンジョン飯 01.cbz
    一拳超人 01.cbz
    よつばと! 第001巻.cbz
"""


from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParseResult:
    """Result of parsing a manga filename."""

    title: str = ""
    author: str = ""
    series: str = ""
    volume: str = ""
    publisher: str = ""
    confidence: float = 0.0

    @property
    def display(self) -> str:
        """Human-readable summary."""
        parts = []
        if self.author:
            parts.append(f"[{self.author}]")
        if self.title:
            parts.append(self.title)
        if self.volume:
            parts.append(f"v{self.volume}")
        return " ".join(parts) if parts else "(unparsed)"


# --- Regex patterns ---

# Author in square brackets: [作者名]
RE_AUTHOR_BRACKET = re.compile(
    r"^\[([^\]]+)\]\s*"
)

# Volume patterns (ordered by specificity)
VOLUME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 第01巻, 第001巻 (Japanese)
    (re.compile(r"第(\d+)巻"), "kan"),
    # 第01卷, 第001卷 (Chinese)
    (re.compile(r"第(\d+)卷"), "juan"),
    # v01, v001
    (re.compile(r"v\.?(\d+)", re.IGNORECASE), "v"),
    # vol.01, vol 01, Vol_01
    (re.compile(r"vol\.?[\s_\-]*(\d+)", re.IGNORECASE), "vol"),
    # Standalone number after parent directory metadata: [Title][Author] 01.cbz
    (re.compile(r"^(\d{1,3})$"), "standalone"),
    # Trailing number: title 01.cbz, title 001.cbz
    (re.compile(r"\s(\d{1,3})$"), "bare"),
]


def parse_filename(filename: str) -> ParseResult:
    """Parse a manga filename to extract metadata.

    Args:
        filename: Filename string (with or without extension).

    Returns:
        ParseResult with extracted fields and confidence score.
    """
    result = ParseResult()
    confidence_parts: list[float] = []

    # Remove file extension
    name = _strip_extension(filename)

    # Clean up common artifacts
    name = name.strip()

    # --- Extract ALL bracket groups at the beginning ---
    brackets = []
    while True:
        m = RE_AUTHOR_BRACKET.match(name)
        if not m:
            break
        brackets.append(m.group(1).strip())
        name = name[m.end():].strip()

    # --- Extract volume number ---
    volume, _vol_pattern, name_after_vol = _extract_volume(name)
    if volume:
        result.volume = volume
        name = name_after_vol
        confidence_parts.append(0.3)

    # --- Title candidate is what remains ---
    title_candidate = name.strip()
    title_candidate = re.sub(r"[\s_\-]+$", "", title_candidate)
    title_candidate = re.sub(r"^[\s_\-]+", "", title_candidate)

    # --- Heuristics to assign Title, Author, Publisher ---
    if not title_candidate:
        # Title was inside the brackets! e.g., [Title][Author][Publisher] vol1.cbz
        if len(brackets) == 3:
            result.title = brackets[0]
            result.author = brackets[1]
            result.publisher = brackets[2]
            confidence_parts.append(0.3)
            confidence_parts.append(0.2)
        elif len(brackets) == 2:
            result.title = brackets[0]
            result.author = brackets[1]
            confidence_parts.append(0.3)
        elif len(brackets) == 1:
            result.title = brackets[0]
            confidence_parts.append(0.2)
    else:
        # Title is outside the brackets! e.g., [Group][Author] Title vol1.cbz
        result.title = title_candidate
        confidence_parts.append(0.3)
        if len(brackets) == 1:
            result.author = brackets[0]
            confidence_parts.append(0.2)
        elif len(brackets) >= 2:
            # Usually [Group][Author] Title
            result.author = brackets[1]
            result.publisher = brackets[0] # Using publisher field to hold Group
            confidence_parts.append(0.2)

    if result.title:
        result.series = result.title  # For manga, series = title

    # --- Calculate confidence ---
    if confidence_parts:
        result.confidence = min(sum(confidence_parts) + 0.1, 1.0)
    else:
        result.confidence = 0.0

    return result


def _strip_extension(filename: str) -> str:
    """Remove manga file extensions."""
    extensions = [
        ".cbz", ".cbr", ".zip", ".rar", ".7z",
        ".epub", ".kepub", ".kepub.epub",
    ]
    lower = filename.lower()
    for ext in sorted(extensions, key=len, reverse=True):
        if lower.endswith(ext):
            return filename[: len(filename) - len(ext)]
    return filename


def _extract_volume(
    name: str,
) -> tuple[str, str, str]:
    """Extract volume number from name string.

    Returns:
        Tuple of (volume_number, pattern_type, remaining_name).
        If no volume found, returns ("", "", original_name).
    """
    for pattern, ptype in VOLUME_PATTERNS:
        match = pattern.search(name)
        if match:
            vol_num = match.group(1).lstrip("0") or "0"
            # Remove the volume part from the name
            remaining = name[: match.start()] + name[match.end():]
            return vol_num, ptype, remaining

    return "", "", name
