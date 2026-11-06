"""Recognizes duplicate-copy suffixes in file names and derives comparison keys."""

import os.path
import re
import unicodedata
from dataclasses import dataclass

from duplicate_finder.core.models import ParsedName


@dataclass(frozen=True, slots=True)
class SuffixPattern:
    id: str
    regex: re.Pattern[str]
    example: str


def _pattern(pattern_id: str, expression: str, example: str) -> SuffixPattern:
    return SuffixPattern(pattern_id, re.compile(expression, re.IGNORECASE), example)


# Evaluation order matters: more specific patterns must come first so that
# "Photo - copy" is not read as the plain "copy" pattern with base "Photo -".
PATTERNS: tuple[SuffixPattern, ...] = (
    _pattern("dash_copy", r"(?P<base>.+) - copy", "Photo - copy.jpg"),
    _pattern("dash_copia", r"(?P<base>.+) - copia", "Photo - copia.jpg"),
    _pattern("paren_copy", r"(?P<base>.+) \(copy\)", "Photo (copy).jpg"),
    _pattern("paren_copia", r"(?P<base>.+) \(copia\)", "Photo (copia).jpg"),
    _pattern("copy", r"(?P<base>.+) copy", "Photo copy.jpg"),
    _pattern("spaced_dash_num", r"(?P<base>.+) - [1-9][0-9]?", "Photo - 1.jpg"),
    _pattern("paren_num", r"(?P<base>.+) \([1-9][0-9]?\)", "Photo (1).jpg"),
    _pattern("paren_num_nospace", r"(?P<base>.*\S)\([1-9][0-9]?\)", "Photo(1).jpg"),
    _pattern("bracket_num", r"(?P<base>.+) \[[1-9][0-9]?\]", "Photo [1].jpg"),
    _pattern("dash_num", r"(?P<base>.*\S)-[1-9][0-9]?", "Photo-1.jpg"),
)

ALL_PATTERNS: frozenset[str] = frozenset(p.id for p in PATTERNS)


def normalize_key(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _strip_one(stem: str, patterns: tuple[SuffixPattern, ...]) -> tuple[str, str] | None:
    for pattern in patterns:
        match = pattern.regex.fullmatch(stem)
        if match and match["base"].strip():
            return match["base"], pattern.id
    return None


def parse_name(name: str, enabled: frozenset[str] = ALL_PATTERNS) -> ParsedName:
    if not name:
        raise ValueError("name must not be empty")
    if "/" in name or "\\" in name:
        raise ValueError(f"name must not contain a path separator: {name!r}")
    unknown = enabled - ALL_PATTERNS
    if unknown:
        raise ValueError(f"unknown pattern ids: {sorted(unknown)}")

    stem, ext = os.path.splitext(name)
    active = tuple(p for p in PATTERNS if p.id in enabled)

    base = stem
    suffixes: list[str] = []
    while (stripped := _strip_one(base, active)) is not None:
        base, pattern_id = stripped
        suffixes.append(pattern_id)

    return ParsedName(
        stem=stem,
        ext=ext,
        base=base,
        base_key=normalize_key(base),
        ext_key=normalize_key(ext),
        suffixes=tuple(suffixes),
    )
