"""Folder trees record eligible files and skip the destination and junctions."""

import stat

from duplicate_finder.core.folder_scan import scan_folder_trees
from duplicate_finder.core.scanner import scan_folder
from tests.winhelpers import add_attributes, make_junction, write

HIDDEN = stat.FILE_ATTRIBUTE_HIDDEN


def names(tree) -> set[tuple[str, ...]]:
    return set(item.relative for item in tree.files)


def test_direct_children_keep_nested_files_but_are_the_only_candidates(tmp_path):
    write(tmp_path / "album" / "nested" / "photo.jpg", b"photo")
    write(tmp_path / "loose.jpg", b"loose")

    trees = scan_folder_trees(tmp_path, match_subfolders=False)

    assert [tree.path.name for tree in trees] == ["album"]
    assert names(trees[0]) == {("nested", "photo.jpg")}
    assert trees[0].hidden_included is False


def test_subfolders_become_candidates_only_when_asked(tmp_path):
    write(tmp_path / "album" / "nested" / "photo.jpg", b"photo")

    trees = scan_folder_trees(tmp_path, match_subfolders=True)

    assert {tree.path.name for tree in trees} == {"album", "nested"}


def test_metadata_is_ignored_and_another_hidden_file_is_kept(tmp_path):
    album = tmp_path / "album"
    write(album / "desktop.ini", b"ini")
    write(album / "Thumbs.db", b"thumbs")
    hidden = write(album / "secret.jpg", b"secret")
    add_attributes(hidden, HIDDEN)
    write(tmp_path / "secret.jpg", b"secret")
    add_attributes(tmp_path / "secret.jpg", HIDDEN)

    (tree,) = scan_folder_trees(tmp_path)
    assert names(tree) == {("secret.jpg",)}
    assert tree.hidden_included is True
    assert [entry.name for entry in scan_folder(tmp_path).entries] == []


def test_excluded_extension_is_not_folder_content(tmp_path):
    write(tmp_path / "album" / "photo.jpg", b"photo")
    write(tmp_path / "album" / "cache.db", b"cache")
    write(tmp_path / "cache.db", b"cache")

    (tree,) = scan_folder_trees(tmp_path, excluded_extensions=frozenset({".db"}))
    assert names(tree) == {("photo.jpg",)}
    assert [entry.name for entry in scan_folder(tmp_path, excluded_extensions=frozenset({".db"})).entries] == []


def test_empty_candidate_is_recorded(tmp_path):
    (tmp_path / "empty").mkdir()
    (tree,) = scan_folder_trees(tmp_path)
    assert tree.path.name == "empty"
    assert tree.files == ()
    assert tree.hidden_included is False


def test_dups_and_junctions_are_not_entered(tmp_path):
    write(tmp_path / "dups" / "01_Name" / "photo.jpg", b"photo")
    target = tmp_path / "dups" / "target"
    write(target / "photo.jpg", b"photo")
    make_junction(tmp_path / "linked", target)
    write(tmp_path / "album" / "dups" / "secret.jpg", b"secret")
    write(tmp_path / "album" / "photo.jpg", b"photo")

    trees = scan_folder_trees(tmp_path, match_subfolders=True)

    assert {tree.path.name for tree in trees} == {"album"}
    assert names(trees[0]) == {("photo.jpg",)}
