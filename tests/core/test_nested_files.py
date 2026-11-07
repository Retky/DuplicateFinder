"""Recursive file matching stays independent of folder matching."""

import stat
from collections import Counter

import pytest

from duplicate_finder.core import content_grouper, folder_compare, hasher
from duplicate_finder.core.analysis import analyze
from duplicate_finder.core.duplicate_grouper import build_groups
from duplicate_finder.core.file_mover import ContentMove, move_duplicates
from duplicate_finder.core.folder_compare import compare_folder_sets
from duplicate_finder.core.folder_scan import scan_folder_trees
from duplicate_finder.core.hasher import verify_groups
from duplicate_finder.core.models import CandidateStatus, GroupKind
from duplicate_finder.core.scanner import scan_folder
from tests.winhelpers import add_attributes, write

HIDDEN = stat.FILE_ATTRIBUTE_HIDDEN
PHOTO = b"photo-bytes"


def content_groups(analysis):
    return [group for group in analysis.groups if group.kind is GroupKind.CONTENT]


def paths_of(analysis):
    found = {candidate.entry.path for group in analysis.groups for candidate in group.candidates}
    found.update(group.original.path for group in analysis.groups if group.original is not None)
    return found


def test_recursive_files_off_leaves_nested_files_out_of_file_groups(tmp_path):
    write(tmp_path / "photo.jpg", PHOTO)
    write(tmp_path / "Album" / "nested" / "photo.jpg", PHOTO)
    write(tmp_path / "Album (1)" / "nested" / "photo.jpg", PHOTO)
    shallow = scan_folder(tmp_path)

    off = analyze(tmp_path, match_nested_files=False)
    folders = analyze(tmp_path, match_folders=True, match_nested_files=False)

    assert [entry.path for entry in off.scan.entries] == [entry.path for entry in shallow.entries]
    assert [(item.name, item.reason) for item in folders.scan.skipped] == [
        (item.name, item.reason) for item in shallow.skipped
    ]
    assert tmp_path / "Album" / "nested" / "photo.jpg" not in paths_of(folders)
    assert len(folders.folder_groups) == 1
    grouped = {folders.folder_groups[0].original, *folders.folder_groups[0].duplicates}
    assert {path.name for path in grouped} == {"Album", "Album (1)"}
    assert all(path.parent == tmp_path for path in grouped)


def test_recursive_files_on_groups_a_nested_copy_without_folder_matching(tmp_path):
    first = write(tmp_path / "FolderA" / "photo.jpg", PHOTO)
    second = write(tmp_path / "FolderB" / "photo.jpg", PHOTO)
    hidden = write(tmp_path / "FolderA" / "secret.jpg", PHOTO)
    add_attributes(hidden, HIDDEN)
    write(tmp_path / "FolderA" / "desktop.ini", b"ini")
    write(tmp_path / "dups" / "photo.jpg", PHOTO)

    result = analyze(tmp_path, match_nested_files=True, match_folders=False)

    assert result.folder_groups == ()
    assert paths_of(result) == {first, second}
    assert all(entry.name != "secret.jpg" for entry in result.scan.entries)
    assert all(entry.name != "desktop.ini" for entry in result.scan.entries)
    assert all("dups" not in entry.path.parts for entry in result.scan.entries)


def test_both_options_keep_folder_rules_and_hash_a_shared_file_once(tmp_path, monkeypatch):
    write(tmp_path / "Album" / "nested" / "photo.jpg", PHOTO)
    write(tmp_path / "Album (1)" / "nested" / "photo.jpg", PHOTO)
    hidden = write(tmp_path / "Album" / "secret.jpg", b"secret")
    add_attributes(hidden, HIDDEN)
    other = write(tmp_path / "Album (1)" / "secret.jpg", b"secret")
    add_attributes(other, HIDDEN)
    calls = Counter()
    real = hasher.sha256_file

    def spy(path, *args, **kwargs):
        calls[path] += 1
        return real(path, *args, **kwargs)

    monkeypatch.setattr(hasher, "sha256_file", spy)
    monkeypatch.setattr(content_grouper, "sha256_file", spy)
    monkeypatch.setattr(folder_compare, "sha256_file", spy)

    off = analyze(tmp_path, match_nested_files=True, match_folders=True, match_subfolders=False)
    assert off.folders_hidden is True
    assert {path.name for path in paths_of(off)} == {"photo.jpg"}
    grouped = {off.folder_groups[0].original, *off.folder_groups[0].duplicates}
    assert {path.name for path in grouped} == {"Album", "Album (1)"}
    assert all(path.parent == tmp_path for path in grouped)
    assert calls
    assert set(calls.values()) == {1}

    calls.clear()
    on = analyze(tmp_path, match_nested_files=True, match_folders=True, match_subfolders=True)
    assert {path.name for path in paths_of(on)} == {"photo.jpg"}
    assert {group.original.name for group in on.folder_groups} == {"Album"}
    assert set(calls.values()) == {1}


def test_subfolders_still_decide_which_nested_folders_are_candidates(tmp_path):
    write(tmp_path / "Album" / "unique.jpg", b"one")
    write(tmp_path / "Other" / "different.jpg", b"two")
    write(tmp_path / "Album" / "nested" / "photo.jpg", PHOTO)
    write(tmp_path / "Other" / "nested" / "photo.jpg", PHOTO)

    off = analyze(tmp_path, match_nested_files=True, match_folders=True, match_subfolders=False)
    on = analyze(tmp_path, match_nested_files=True, match_folders=True, match_subfolders=True)

    assert off.folder_groups == ()
    assert {path.name for group in on.folder_groups for path in (group.original, *group.duplicates)} == {"nested"}
    assert {path.name for path in paths_of(on)} == {"photo.jpg"}


def test_nested_copies_move_under_their_relative_paths(tmp_path):
    keeper = write(tmp_path / "keep.jpg", PHOTO)
    first = write(tmp_path / "FolderA" / "Album" / "photo.jpg", PHOTO)
    second = write(tmp_path / "FolderB" / "Other" / "photo.jpg", PHOTO)
    analysis = analyze(tmp_path, match_nested_files=True)
    (group,) = content_groups(analysis)
    by_path = {candidate.entry.path: candidate for candidate in group.candidates}

    move_duplicates(analysis, [], content=[ContentMove(
        group.group_id, by_path[keeper].entry, (by_path[first], by_path[second]),
    )])

    assert (tmp_path / "dups" / "02_Content" / "FolderA" / "Album" / "photo.jpg").read_bytes() == PHOTO
    assert (tmp_path / "dups" / "02_Content" / "FolderB" / "Other" / "photo.jpg").read_bytes() == PHOTO
    assert keeper.read_bytes() == PHOTO
    assert not first.exists()
    assert not second.exists()


def test_a_direct_child_still_moves_to_the_flat_name_folder(tmp_path):
    write(tmp_path / "Photo.jpg", PHOTO)
    copy = write(tmp_path / "Photo (1).jpg", PHOTO)
    analysis = analyze(tmp_path, match_nested_files=True)

    report = move_duplicates(analysis, [next(
        candidate for group in analysis.groups for candidate in group.candidates
        if candidate.entry.path == copy
    )])

    assert report.results[0].move.dest_path == tmp_path / "dups" / "01_Name" / "Photo (1).jpg"
    assert (tmp_path / "dups" / "01_Name" / "Photo (1).jpg").read_bytes() == PHOTO


def test_a_file_inside_a_selected_folder_is_not_also_a_file_move(tmp_path):
    inside = write(tmp_path / "Album (1)" / "photo.jpg", PHOTO)
    keeper = write(tmp_path / "Album" / "photo.jpg", PHOTO)
    loose = write(tmp_path / "loose" / "photo.jpg", PHOTO)
    analysis = analyze(tmp_path, match_nested_files=True, match_folders=True)
    (group,) = content_groups(analysis)
    by_path = {candidate.entry.path: candidate for candidate in group.candidates}
    duplicate = tmp_path / "Album (1)"

    report = move_duplicates(
        analysis, [],
        content=[ContentMove(group.group_id, by_path[keeper].entry, (by_path[inside], by_path[loose]))],
        folders=[duplicate],
        folder_groups=analysis.folder_groups,
    )

    moved = {result.move.candidate.entry.path for result in report.results}
    assert inside not in moved
    assert loose in moved
    assert (tmp_path / "dups" / "02_Content" / "loose" / "photo.jpg").read_bytes() == PHOTO
    assert (tmp_path / "dups" / "03_Folders" / "Album (1)" / "photo.jpg").read_bytes() == PHOTO
    assert not (tmp_path / "dups" / "02_Content" / "Album (1)" / "photo.jpg").exists()


def test_a_read_error_is_not_cached(tmp_path, monkeypatch):
    write(tmp_path / "Photo.jpg", PHOTO)
    write(tmp_path / "Photo (1).jpg", PHOTO)
    groups = build_groups(scan_folder(tmp_path).entries)
    cache: dict = {}

    def fail(*_args, **_kwargs):
        raise OSError("busy")

    monkeypatch.setattr(hasher, "sha256_file", fail)
    verified = verify_groups(groups, digests=cache)

    assert cache == {}
    assert verified[0].candidates[0].status is CandidateStatus.READ_ERROR


def test_a_failed_folder_read_is_not_cached_and_a_known_digest_is_reused(tmp_path, monkeypatch):
    photo = write(tmp_path / "Album" / "photo.jpg", PHOTO)
    other = write(tmp_path / "Album (1)" / "photo.jpg", PHOTO)
    trees = scan_folder_trees(tmp_path)
    cache: dict = {}

    def fail(*_args, **_kwargs):
        raise OSError("busy")

    monkeypatch.setattr(folder_compare, "sha256_file", fail)
    compare_folder_sets(trees, known_digests=cache)
    assert cache == {}

    monkeypatch.undo()
    filled: dict = {}
    compare_folder_sets(trees, known_digests=filled)
    assert set(filled) == {photo, other}

    def unexpected(*_args, **_kwargs):
        raise AssertionError("the file was read")

    monkeypatch.setattr(folder_compare, "sha256_file", unexpected)
    compare_folder_sets(trees, known_digests=filled)
    folder_compare.equal_correspondence(trees[0], trees[1], known_digests=filled)
