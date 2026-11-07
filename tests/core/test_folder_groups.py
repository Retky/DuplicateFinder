"""Subset relations collapse into groups with one original folder."""

import os

from duplicate_finder.core.folder_groups import FolderGroup, group_folder_sets
from duplicate_finder.core.folder_scan import scan_folder_trees
from tests.winhelpers import write

SAME = b"same-bytes"


def grouped(folder, **kwargs) -> tuple[FolderGroup, ...]:
    found = group_folder_sets(scan_folder_trees(folder, **kwargs))
    originals = [group.original for group in found]
    duplicates = [path for group in found for path in group.duplicates]
    assert all(group.duplicates for group in found)
    assert len(set(originals)) == len(originals)
    assert len(set(duplicates)) == len(duplicates)
    assert set(originals).isdisjoint(duplicates)
    return found


def test_a_subset_chain_has_one_original(tmp_path):
    write(tmp_path / "small" / "a.jpg", b"a")
    write(tmp_path / "medium" / "a.jpg", b"a")
    write(tmp_path / "medium" / "b.jpg", b"b")
    write(tmp_path / "large" / "a.jpg", b"a")
    write(tmp_path / "large" / "b.jpg", b"b")
    write(tmp_path / "large" / "c.jpg", b"c")

    found = grouped(tmp_path)

    assert len(found) == 1
    assert found[0].original.name == "large"
    assert {path.name for path in found[0].duplicates} == {"small", "medium"}


def test_a_shared_subset_joins_one_of_two_exclusive_folders(tmp_path):
    shared = b"shared"
    album = write(tmp_path / "Album" / "shared.jpg", shared)
    write(tmp_path / "Album" / "only-album.jpg", b"album")
    copy = write(tmp_path / "Album (1)" / "shared.jpg", shared)
    write(tmp_path / "Album (1)" / "only-copy.jpg", b"copy")
    write(tmp_path / "Small" / "shared.jpg", shared)
    os.utime(album, ns=(1, 1_000))
    os.utime(copy, ns=(1, 9_000_000_000))

    found = grouped(tmp_path)

    assert len(found) == 1
    assert found[0].original.name == "Album"
    assert tuple(path.name for path in found[0].duplicates) == ("Small",)


def test_equal_folders_prefer_the_cleaner_name_then_file_time_then_path(tmp_path):
    names = tmp_path / "names"
    album = write(names / "Album" / "photo.jpg", SAME)
    copy = write(names / "Album (1)" / "photo.jpg", SAME)
    os.utime(album, ns=(1, 1_000))
    os.utime(copy, ns=(1, 5_000))

    found = grouped(names)

    assert found[0].original.name == "Album"
    assert tuple(path.name for path in found[0].duplicates) == ("Album (1)",)

    times = tmp_path / "times"
    older = write(times / "folderA" / "x" / "small.jpg", SAME)
    write(times / "folderA" / "y" / "small.jpg", SAME)
    newer = write(times / "folderB" / "p" / "small.jpg", SAME)
    write(times / "folderB" / "q" / "small.jpg", SAME)
    os.utime(older, ns=(1, 100))
    os.utime(times / "folderA" / "y" / "small.jpg", ns=(1, 500))
    os.utime(newer, ns=(1, 400))
    os.utime(times / "folderB" / "q" / "small.jpg", ns=(1, 100))

    found = grouped(times)

    assert found[0].original.name == "folderB"
    assert tuple(path.name for path in found[0].duplicates) == ("folderA",)

    paths = tmp_path / "paths"
    alpha = write(paths / "alpha" / "photo.jpg", SAME)
    zebra = write(paths / "zebra" / "photo.jpg", SAME)
    os.utime(alpha, ns=(1, 1_000))
    os.utime(zebra, ns=(1, 1_000))

    found = grouped(paths)

    assert found[0].original.name == "alpha"
    assert tuple(path.name for path in found[0].duplicates) == ("zebra",)


def test_nested_candidates_under_either_side_are_suppressed(tmp_path):
    left = write(tmp_path / "folderA" / "album" / "photo.jpg", SAME)
    right = write(tmp_path / "folderB" / "album-copy" / "photo.jpg", SAME)
    os.utime(left, ns=(1, 1_000))
    os.utime(right, ns=(1, 1_000))

    found = grouped(tmp_path, match_subfolders=True)
    names = {path.name for group in found for path in (group.original, *group.duplicates)}

    assert names == {"folderA", "folderB"}
    assert found[0].original.name == "folderA"
    assert tuple(path.name for path in found[0].duplicates) == ("folderB",)


def test_nested_candidates_stay_when_their_parents_are_not_grouped(tmp_path):
    left = write(tmp_path / "folderA" / "album" / "photo.jpg", SAME)
    write(tmp_path / "folderA" / "only-a.jpg", b"only-a")
    right = write(tmp_path / "folderB" / "album-copy" / "photo.jpg", SAME)
    write(tmp_path / "folderB" / "only-b.jpg", b"only-b")
    os.utime(left, ns=(1, 1_000))
    os.utime(right, ns=(1, 1_000))

    found = grouped(tmp_path, match_subfolders=True)

    assert len(found) == 1
    assert found[0].original.name == "album"
    assert tuple(path.name for path in found[0].duplicates) == ("album-copy",)
