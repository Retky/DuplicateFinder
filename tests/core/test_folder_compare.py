"""Folder trees match by content and depth, not by names."""

import hashlib
import threading

from duplicate_finder.core.folder_compare import RelationKind, compare_folder_sets
from duplicate_finder.core.folder_scan import scan_folder_trees
from tests.winhelpers import write

SAME = b"same-bytes"
OTHER = b"other-bytes"


def relation(folder, left: str, right: str):
    trees = scan_folder_trees(folder)
    found = {
        (item.left.name, item.right.name): item.kind
        for item in compare_folder_sets(trees)
    }
    return found.get((left, right))


def test_different_names_match_when_the_depth_matches(tmp_path):
    write(tmp_path / "folderA" / "subfolder-1" / "photo-1.jpg", SAME)
    write(tmp_path / "folderA" / "subfolder-2" / "photo-2.jpg", OTHER)
    write(tmp_path / "folderB" / "album-x" / "photo-1-copy.jpg", SAME)
    write(tmp_path / "folderB" / "album-y" / "photo-2-copy.jpg", OTHER)

    assert relation(tmp_path, "folderA", "folderB") is RelationKind.EQUAL


def test_extension_must_match_and_an_extra_copy_makes_the_larger_set(tmp_path):
    write(tmp_path / "folderA" / "photo.jpg", SAME)
    write(tmp_path / "folderB" / "photo.jpeg", SAME)
    assert relation(tmp_path, "folderA", "folderB") is None

    write(tmp_path / "folderC" / "photo.jpg", SAME)
    write(tmp_path / "folderC" / "photo-copy.jpg", SAME)
    assert relation(tmp_path, "folderA", "folderC") is RelationKind.LEFT_SUBSET


def test_exclusive_files_on_both_sides_do_not_match(tmp_path):
    for name in ("i1.jpg", "i2.jpg", "i3.jpg", "i4.jpg"):
        write(tmp_path / "folderA" / name, name.encode())
    for name in ("i0.jpg", "i1.jpg", "i2.jpg", "i3.jpg"):
        write(tmp_path / "folderB" / name, name.encode())

    assert relation(tmp_path, "folderA", "folderB") is None


def test_the_same_bytes_at_different_depths_do_not_match(tmp_path):
    write(tmp_path / "folderA" / "folderA-a" / "photo-1.jpg", SAME)
    write(tmp_path / "folderB" / "photo-1.jpg", SAME)

    assert relation(tmp_path, "folderA", "folderB") is None


def test_two_empty_folders_do_not_match(tmp_path):
    (tmp_path / "folderA").mkdir()
    (tmp_path / "folderB").mkdir()

    assert compare_folder_sets(scan_folder_trees(tmp_path)) == ()


def test_identical_child_folders_match_either_way(tmp_path):
    write(tmp_path / "folderA" / "x" / "a.jpg", SAME)
    write(tmp_path / "folderA" / "y" / "a.jpg", SAME)
    write(tmp_path / "folderB" / "p" / "a.jpg", SAME)
    write(tmp_path / "folderB" / "q" / "a.jpg", SAME)

    assert relation(tmp_path, "folderA", "folderB") is RelationKind.EQUAL


def test_a_known_digest_is_not_read_again(tmp_path, monkeypatch):
    photo = write(tmp_path / "folderA" / "photo.jpg", SAME)
    other = write(tmp_path / "folderB" / "copy.jpg", SAME)
    trees = scan_folder_trees(tmp_path)
    digest = hashlib.sha256(SAME).hexdigest()

    def fail(*_args, **_kwargs):
        raise AssertionError("the file was read")

    monkeypatch.setattr("duplicate_finder.core.folder_compare.sha256_file", fail)
    found = compare_folder_sets(trees, {photo: digest, other: digest})
    assert [(item.left.name, item.right.name, item.kind) for item in found] == [
        ("folderA", "folderB", RelationKind.EQUAL),
    ]


def test_cancelled_hashing_returns_no_relations(tmp_path):
    write(tmp_path / "folderA" / "photo.jpg", SAME)
    write(tmp_path / "folderB" / "copy.jpg", SAME)
    cancel = threading.Event()
    cancel.set()

    assert compare_folder_sets(scan_folder_trees(tmp_path), cancel=cancel) == ()
