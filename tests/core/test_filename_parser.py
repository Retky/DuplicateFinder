import unicodedata

import pytest

from duplicate_finder.core.filename_parser import (
    ALL_PATTERNS,
    PATTERNS,
    normalize_key,
    parse_name,
)

D02_PATTERN_IDS = {
    "paren_num", "paren_num_nospace", "bracket_num", "dash_num", "spaced_dash_num",
    "copy", "dash_copy", "paren_copy", "paren_copia", "dash_copia",
}

# (name, expected base, expected is_copy)
CORPUS = [
    ("Photo.jpg", "Photo", False),
    ("Photo (1).jpg", "Photo", True),
    ("Photo (99).jpg", "Photo", True),
    ("Photo (100).jpg", "Photo (100)", False),
    ("Photo (0).jpg", "Photo (0)", False),
    ("Photo (01).jpg", "Photo (01)", False),
    ("Batman (1989).cbz", "Batman (1989)", False),
    ("Photo(1).jpg", "Photo", True),
    ("Photo [1].jpg", "Photo", True),
    ("Photo-1.jpg", "Photo", True),
    ("Photo -1.jpg", "Photo -1", False),
    ("Photo - 1.jpg", "Photo", True),
    ("Photo copy.jpg", "Photo", True),
    ("Photo - copy.jpg", "Photo", True),
    ("Photo (copy).jpg", "Photo", True),
    ("Photo (copia).jpg", "Photo", True),
    ("Photo - copia.jpg", "Photo", True),
    ("Photo - Copia.JPG", "Photo", True),
    ("Photo - copia (2).jpg", "Photo", True),
    ("Photo (1) (1).jpg", "Photo", True),
    ("Photo-1-2.jpg", "Photo", True),
    ("Photo_1.jpg", "Photo_1", False),
    ("Photo 1.jpg", "Photo 1", False),
    ("Photo_copy.jpg", "Photo_copy", False),
    ("Photo-copy.jpg", "Photo-copy", False),
    ("Photo  (1).jpg", "Photo ", True),
    ("(1).jpg", "(1)", False),
    ("archive (1).tar.gz", "archive (1).tar", False),
    ("README", "README", False),
    (".gitignore", ".gitignore", False),
]


@pytest.mark.parametrize("name, expected_base, expected_is_copy", CORPUS, ids=[c[0] for c in CORPUS])
def test_reference_corpus(name, expected_base, expected_is_copy):
    parsed = parse_name(name)
    assert parsed.base == expected_base
    assert parsed.is_copy is expected_is_copy
    assert parsed.stem + parsed.ext == name


def test_extension_kept_as_on_disk():
    parsed = parse_name("Photo - Copia.JPG")
    assert parsed.stem == "Photo - Copia"
    assert parsed.ext == ".JPG"
    assert parsed.ext_key == ".jpg"
    assert parsed.base_key == "photo"


def test_only_last_extension_counts():
    parsed = parse_name("archive (1).tar.gz")
    assert parsed.stem == "archive (1).tar"
    assert parsed.ext == ".gz"


def test_specific_dash_copy_wins_over_plain_copy():
    assert parse_name("Photo - copy.jpg").suffixes == ("dash_copy",)


def test_stacked_suffixes_recorded_in_strip_order():
    assert parse_name("Photo - copia (2).jpg").suffixes == ("paren_num", "dash_copia")
    assert parse_name("Photo (1) (1).jpg").suffixes == ("paren_num", "paren_num")


def test_nfc_and_nfd_produce_same_key():
    nfc = unicodedata.normalize("NFC", "Canción.jpg")
    nfd = unicodedata.normalize("NFD", "Canción.jpg")
    assert nfc != nfd
    assert parse_name(nfc).base_key == parse_name(nfd).base_key


def test_keys_ignore_case():
    upper, lower = parse_name("PHOTO.JPG"), parse_name("photo.jpg")
    assert (upper.base_key, upper.ext_key) == (lower.base_key, lower.ext_key)
    assert normalize_key("Straße") == normalize_key("STRASSE")


def test_disabled_dash_copy_falls_back_safely():
    parsed = parse_name("Photo - copy.jpg", enabled=ALL_PATTERNS - {"dash_copy"})
    assert parsed.base == "Photo -"
    assert parsed.suffixes == ("copy",)
    assert parsed.base_key != parse_name("Photo.jpg").base_key


@pytest.mark.parametrize("name", [c[0] for c in CORPUS])
def test_no_patterns_enabled_means_no_copies(name):
    parsed = parse_name(name, enabled=frozenset())
    assert not parsed.is_copy
    assert parsed.base == parsed.stem


@pytest.mark.parametrize("pattern", PATTERNS, ids=[p.id for p in PATTERNS])
def test_each_pattern_matches_its_own_example(pattern):
    parsed = parse_name(pattern.example, enabled=frozenset({pattern.id}))
    assert parsed.suffixes == (pattern.id,)
    assert parsed.base == "Photo"


def test_pattern_ids_are_unique_and_match_d02():
    ids = [p.id for p in PATTERNS]
    assert len(ids) == len(set(ids))
    assert ALL_PATTERNS == D02_PATTERN_IDS


def test_pattern_evaluation_order():
    order = [p.id for p in PATTERNS]
    assert order.index("dash_copy") < order.index("copy")
    assert order.index("dash_copia") < order.index("copy")
    assert order.index("spaced_dash_num") < order.index("dash_num")
    assert order.index("paren_num") < order.index("dash_num")


def test_whitespace_only_base_is_not_a_copy():
    parsed = parse_name("  (1).jpg")
    assert not parsed.is_copy
    assert parsed.base == "  (1)"


def test_date_like_stem_is_stripped_by_dash_num():
    parsed = parse_name("Photo 2024-12-15.jpg")
    assert parsed.base == "Photo 2024"
    assert parsed.suffixes == ("dash_num", "dash_num")


def test_non_breaking_space_is_not_a_separator():
    parsed = parse_name("Photo\u00a0(1).jpg")
    assert not parsed.is_copy


def test_text_patterns_ignore_case():
    assert parse_name("Photo - COPY.jpg").base == "Photo"
    assert parse_name("Photo (Copia).jpg").base == "Photo"


@pytest.mark.parametrize("bad_name", ["", "folder/Photo.jpg", "folder\\Photo.jpg"])
def test_invalid_names_rejected(bad_name):
    with pytest.raises(ValueError):
        parse_name(bad_name)


def test_unknown_pattern_id_rejected():
    with pytest.raises(ValueError):
        parse_name("Photo (1).jpg", enabled=frozenset({"paren_num", "underscore_num"}))
