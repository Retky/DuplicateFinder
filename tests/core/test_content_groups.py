"""Content groups stay unmoved until the caller chooses a keeper."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from duplicate_finder.core.analysis import analyze
from duplicate_finder.core.content_grouper import choose_keeper, suggest_keeper
from duplicate_finder.core.file_mover import ContentMove, move_duplicates
from duplicate_finder.core.models import FileEntry, GroupKind
from duplicate_finder.core.scanner import _created_ns
from tests.winhelpers import write

SAME = b"same-bytes"


def content_groups(result):
    return [group for group in result.groups if group.kind is GroupKind.CONTENT]


def test_unrelated_names_with_the_same_bytes_do_not_move(tmp_path):
    write(tmp_path / "Photo.jpg", SAME)
    write(tmp_path / "Vacation.jpg", SAME)
    analysis = analyze(tmp_path)
    (group,) = content_groups(analysis)

    with pytest.raises(ValueError):
        move_duplicates(analysis, list(group.candidates))

    assert not (tmp_path / "_dups").exists()


def test_extension_must_match_ignoring_case(tmp_path):
    write(tmp_path / "Photo.jpg", SAME)
    write(tmp_path / "Photo.jpeg", SAME)
    different = analyze(tmp_path)
    assert content_groups(different) == []

    write(tmp_path / "Holiday.JPG", SAME)
    same_case = analyze(tmp_path)
    (group,) = content_groups(same_case)
    assert {candidate.entry.name for candidate in group.candidates} == {"Photo.jpg", "Holiday.JPG"}


def test_suggestion_uses_modified_time_and_ignores_creation_time():
    folder = Path(r"C:\Photos")
    older = FileEntry(folder / "older.jpg", "older.jpg", 4, 100, created_ns=9_000)
    newer = FileEntry(folder / "newer.jpg", "newer.jpg", 4, 50, created_ns=1)
    assert suggest_keeper((older, newer)) is older

    tie_a = FileEntry(folder / "b.jpg", "b.jpg", 4, 100, created_ns=1)
    tie_b = FileEntry(folder / "a.jpg", "a.jpg", 4, 100, created_ns=9_000)
    assert suggest_keeper((tie_a, tie_b)) is tie_b


def test_chosen_keeper_moves_only_the_selected_files(tmp_path):
    for name, stamp in (("Keep.jpg", 300), ("Move.jpg", 200), ("Stay.jpg", 100)):
        path = write(tmp_path / name, SAME)
        os.utime(path, ns=(stamp, stamp))
    analysis = analyze(tmp_path)
    (group,) = content_groups(analysis)
    by_name = {candidate.entry.name: candidate for candidate in group.candidates}
    keeper = by_name["Keep.jpg"].entry

    report = move_duplicates(analysis, [], content=(
        ContentMove(group.group_id, keeper, (by_name["Move.jpg"],)),
    ))

    assert (tmp_path / "_dups" / "02_Content" / "Move.jpg").read_bytes() == SAME
    assert (tmp_path / "Keep.jpg").is_file()
    assert (tmp_path / "Stay.jpg").is_file()
    assert not (tmp_path / "_dups" / "01_Name").exists()
    assert report.moved_count == 1


def test_name_original_cannot_be_planned_and_name_copy_still_moves(tmp_path):
    for name in ("Photo.jpg", "Photo (1).jpg", "Vacation.jpg"):
        write(tmp_path / name, SAME)
    analysis = analyze(tmp_path)
    (content,) = content_groups(analysis)
    by_name = {candidate.entry.name: candidate for candidate in content.candidates}
    assert set(by_name) == {"Photo.jpg", "Vacation.jpg"}

    with pytest.raises(ValueError, match="name original"):
        move_duplicates(analysis, [], content=(
            ContentMove(content.group_id, by_name["Vacation.jpg"].entry, (by_name["Photo.jpg"],)),
        ))
    assert not (tmp_path / "_dups" / "02_Content").exists()

    name_copy = next(
        candidate for group in analysis.groups if group.kind is GroupKind.NAME for candidate in group.confirmed
    )
    move_duplicates(analysis, [name_copy])
    assert (tmp_path / "_dups" / "01_Name" / "Photo (1).jpg").read_bytes() == SAME
    assert not (tmp_path / "_dups" / "02_Content").exists()


def test_name_original_stays_when_a_newer_file_has_the_same_bytes(tmp_path):
    photo = write(tmp_path / "Photo.jpg", SAME)
    copy = write(tmp_path / "Photo (1).jpg", SAME)
    vacation = write(tmp_path / "Vacation.jpg", SAME)
    os.utime(photo, ns=(1, 100))
    os.utime(copy, ns=(1, 200))
    os.utime(vacation, ns=(1, 300))
    analysis = analyze(tmp_path)
    name_group = next(group for group in analysis.groups if group.kind is GroupKind.NAME)
    (content,) = content_groups(analysis)
    assert name_group.original is not None
    assert {candidate.entry.name for candidate in content.candidates} == {"Photo.jpg", "Vacation.jpg"}

    keeper = choose_keeper(
        tuple(candidate.entry for candidate in content.candidates),
        {name_group.original.path},
    )
    assert keeper.name == "Photo.jpg"
    vacation_row = next(candidate for candidate in content.candidates if candidate.entry.name == "Vacation.jpg")
    move_duplicates(analysis, list(name_group.confirmed), content=(
        ContentMove(content.group_id, keeper, (vacation_row,)),
    ))

    assert (tmp_path / "Photo.jpg").read_bytes() == SAME
    assert (tmp_path / "_dups" / "01_Name" / "Photo (1).jpg").read_bytes() == SAME
    assert (tmp_path / "_dups" / "02_Content" / "Vacation.jpg").read_bytes() == SAME


def test_match_flags_skip_each_kind_of_group(tmp_path):
    write(tmp_path / "Photo.jpg", SAME)
    write(tmp_path / "Photo (1).jpg", SAME)
    write(tmp_path / "Vacation.jpg", SAME)

    names_only = analyze(tmp_path, match_content=False)
    assert content_groups(names_only) == []
    assert any(group.kind is GroupKind.NAME and group.confirmed for group in names_only.groups)

    content_only = analyze(tmp_path, match_names=False)
    assert all(group.kind is GroupKind.CONTENT for group in content_only.groups)
    assert content_groups(content_only)

    neither = analyze(tmp_path, match_names=False, match_content=False)
    assert neither.groups == ()


def test_missing_birth_time_does_not_fail_the_scan(tmp_path):
    assert _created_ns(SimpleNamespace()) is None
    write(tmp_path / "Photo.jpg", SAME)
    result = analyze(tmp_path, match_content=False)
    created = result.scan.entries[0].created_ns
    assert created is None or isinstance(created, int)
