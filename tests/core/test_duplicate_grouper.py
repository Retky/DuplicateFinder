import unicodedata
from pathlib import Path

import pytest

from duplicate_finder.core.duplicate_grouper import build_groups
from duplicate_finder.core.filename_parser import ALL_PATTERNS
from duplicate_finder.core.models import CandidateStatus, FileEntry

FOLDER = Path(r"C:\Photos")
PENDING = CandidateStatus.PENDING_HASH
DIFFERENT_SIZE = CandidateStatus.DIFFERENT_SIZE
NO_ORIGINAL = CandidateStatus.NO_ORIGINAL
AMBIGUOUS = CandidateStatus.AMBIGUOUS


def entry(name: str, size: int = 100, folder: Path = FOLDER) -> FileEntry:
    return FileEntry(path=folder / name, name=name, size=size, mtime_ns=0)


def statuses(groups) -> dict[str, CandidateStatus]:
    return {c.entry.name: c.status for g in groups for c in g.candidates}


@pytest.mark.parametrize(
    "files, expected",
    [
        pytest.param([("Photo.jpg", 100), ("Photo (1).jpg", 100)],
                     {"Photo (1).jpg": PENDING}, id="original-and-copy"),
        pytest.param([("Photo.jpg", 100), ("Photo (1).jpg", 100), ("Photo (2).jpg", 100), ("Photo (3).jpg", 120)],
                     {"Photo (1).jpg": PENDING, "Photo (2).jpg": PENDING, "Photo (3).jpg": DIFFERENT_SIZE},
                     id="several-copies-one-different-size"),
        pytest.param([("Photo (1).jpg", 100), ("Photo (2).jpg", 100)],
                     {"Photo (1).jpg": NO_ORIGINAL, "Photo (2).jpg": NO_ORIGINAL}, id="no-original"),
        pytest.param([("Photo.jpg", 100), ("Photo (1).jpg", 200), ("Photo (2).jpg", 200)],
                     {"Photo (1).jpg": DIFFERENT_SIZE, "Photo (2).jpg": DIFFERENT_SIZE},
                     id="copies-not-compared-with-each-other"),
        pytest.param([("Photo.jpg", 100), ("Photo (1).png", 100)],
                     {"Photo (1).png": NO_ORIGINAL}, id="different-extension"),
        pytest.param([("Photo.jpg", 100), ("photo (1).JPG", 100)],
                     {"photo (1).JPG": PENDING}, id="case-insensitive"),
        pytest.param([("A.jpg", 100), ("B.jpg", 100)], {}, id="same-size-different-names"),
        pytest.param([("Photo 1.jpg", 100), ("Photo 2.jpg", 100), ("Photo 3.jpg", 100)], {}, id="sequence"),
        pytest.param([("Photo (1).jpg", 100), ("Photo (1) (1).jpg", 100)],
                     {"Photo (1).jpg": NO_ORIGINAL, "Photo (1) (1).jpg": NO_ORIGINAL}, id="stacked-no-root"),
        pytest.param([("Photo.jpg", 100), ("Photo - copia (1).jpg", 100)],
                     {"Photo - copia (1).jpg": PENDING}, id="stacked-with-root"),
    ],
)
def test_ticket_scenarios(files, expected):
    groups = build_groups(entry(name, size) for name, size in files)
    assert statuses(groups) == expected


def test_nfc_and_nfd_originals_are_ambiguous():
    nfc = unicodedata.normalize("NFC", "Canción.jpg")
    nfd = unicodedata.normalize("NFD", "Canción.jpg")
    groups = build_groups([entry(nfc), entry(nfd), entry("Canción (1).jpg")])

    (group,) = groups
    assert group.original is None
    assert statuses(groups) == {"Canción (1).jpg": AMBIGUOUS}


def test_group_contents():
    original = entry("Photo.jpg")
    copy = entry("Photo (1).jpg")

    (group,) = build_groups([copy, original])

    assert group.group_id == "g0001"
    assert group.key == ("photo", ".jpg")
    assert group.original == original
    assert group.original_sha256 is None
    assert [c.entry for c in group.candidates] == [copy]
    assert group.candidates[0].parsed.suffixes == ("paren_num",)
    assert group.candidates[0].sha256 is None


def test_different_extension_original_kept_out_of_group():
    groups = build_groups([entry("Photo.jpg"), entry("Photo (1).png")])
    (group,) = groups
    assert group.key == ("photo", ".png")
    assert group.original is None


def test_no_candidate_is_ever_confirmed():
    files = [entry("Photo.jpg"), entry("Photo (1).jpg"), entry("Photo - copy.jpg"), entry("Photo-2.jpg")]
    groups = build_groups(files)
    assert groups
    assert all(c.status is not CandidateStatus.CONFIRMED for g in groups for c in g.candidates)
    assert all(g.confirmed == () for g in groups)


def test_groups_without_copies_are_dropped():
    assert build_groups([entry("Photo.jpg"), entry("Other.jpg"), entry("Photo.png")]) == []


def test_empty_input():
    assert build_groups([]) == []


def test_group_ids_follow_sorted_keys():
    files = [entry("Zebra.jpg"), entry("Zebra (1).jpg"), entry("apple.jpg"), entry("apple (1).jpg"),
             entry("Mango.png"), entry("Mango (1).png")]
    groups = build_groups(files)
    assert [(g.group_id, g.key[0]) for g in groups] == [("g0001", "apple"), ("g0002", "mango"),
                                                         ("g0003", "zebra")]


def test_result_is_deterministic_regardless_of_input_order():
    files = [entry("Photo.jpg"), entry("Photo (2).jpg"), entry("photo (1).jpg"), entry("Photo copy.jpg"),
             entry("Other.png"), entry("Other [1].png")]
    assert build_groups(files) == build_groups(reversed(files))
    (_, photo) = build_groups(files)
    assert [c.entry.name for c in photo.candidates] == ["photo (1).jpg", "Photo (2).jpg", "Photo copy.jpg"]


def test_disabled_patterns_are_respected():
    files = [entry("Photo.jpg"), entry("Photo (1).jpg"), entry("Photo-1.jpg")]
    groups = build_groups(files, enabled=ALL_PATTERNS - {"dash_num"})
    assert statuses(groups) == {"Photo (1).jpg": PENDING}


def test_no_patterns_enabled_means_no_groups():
    files = [entry("Photo.jpg"), entry("Photo (1).jpg")]
    assert build_groups(files, enabled=frozenset()) == []


def test_duplicate_paths_rejected():
    with pytest.raises(ValueError):
        build_groups([entry("Photo.jpg"), entry("Photo.jpg")])


def test_entries_from_different_folders_rejected():
    with pytest.raises(ValueError):
        build_groups([entry("Photo.jpg"), entry("Photo (1).jpg", folder=Path(r"C:\Other"))])
