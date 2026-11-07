"""A selected duplicate folder moves in one step, and does not take a file move with it."""

import threading

import pytest

from duplicate_finder.core.analysis import analyze
from duplicate_finder.core.file_mover import (
    OTHER_NAMES_FILE,
    ContentMove,
    folder_destination,
    move_duplicates,
)
from duplicate_finder.core.folder_groups import group_folder_sets
from duplicate_finder.core.folder_scan import scan_folder_trees
from duplicate_finder.core.models import MoveOutcome
from duplicate_finder.core.operation_logger import read_log
from duplicate_finder.ui.presenters import move_report_text
from tests.winhelpers import write

PHOTO = b"photo-bytes"
NOTE = b"note-bytes"
COPY = b"copy-bytes"


def candidate(analysis, name: str):
    for group in analysis.groups:
        for item in group.candidates:
            if item.entry.name == name:
                return item
    raise AssertionError(f"{name} is not a candidate")


def pair(folder):
    for name in ("Album", "Album (1)"):
        write(folder / name / "Photo.jpg", PHOTO)
        write(folder / name / "Photo (1).jpg", PHOTO)
        write(folder / name / "notes" / "read.txt", NOTE)


def groups_of(folder):
    return group_folder_sets(scan_folder_trees(folder))


def test_selected_duplicate_folder_moves_intact(tmp_path):
    pair(tmp_path)
    duplicate = tmp_path / "Album (1)"
    analysis = analyze(tmp_path)

    report = move_duplicates(analysis, [], folders=[duplicate], folder_groups=groups_of(tmp_path))

    moved = folder_destination(tmp_path) / "Album (1)"
    assert report.dest_folder == moved.parent
    assert report.results == ()
    assert [result.outcome for result in report.folder_results] == [MoveOutcome.MOVED]
    assert (moved / "Photo.jpg").read_bytes() == PHOTO
    assert (moved / "notes" / "read.txt").read_bytes() == NOTE
    assert not duplicate.exists()
    assert (tmp_path / "Album" / "Photo.jpg").read_bytes() == PHOTO
    assert (tmp_path / "Album" / "notes" / "read.txt").read_bytes() == NOTE
    assert not (tmp_path / "_dups" / "01_Name").exists()
    assert not (tmp_path / "_dups" / "02_Content").exists()
    assert (tmp_path / "Album" / OTHER_NAMES_FILE).read_text(encoding="utf-8") == "Album (1)\n"

    events = read_log(report.log_path)
    recorded = [event for event in events if event["event"] == "result"]
    assert len(recorded) == 1
    assert recorded[0]["kind"] == "folder"
    assert recorded[0]["outcome"] == "moved"
    assert recorded[0]["source_path"] == str(duplicate)
    assert "sha256" not in recorded[0]
    assert events[-1]["moved_bytes"] == 0


def test_existing_destination_name_skips_the_folder(tmp_path):
    pair(tmp_path)
    duplicate = tmp_path / "Album (1)"
    blocking = write(folder_destination(tmp_path) / "Album (1)" / "marker.txt", b"keep")
    analysis = analyze(tmp_path)

    report = move_duplicates(analysis, [], folders=[duplicate], folder_groups=groups_of(tmp_path))

    assert [result.outcome for result in report.folder_results] == [MoveOutcome.SKIPPED_EXISTS]
    assert (duplicate / "Photo.jpg").read_bytes() == PHOTO
    assert blocking.read_bytes() == b"keep"
    assert (tmp_path / "Album").is_dir()
    assert not (tmp_path / "Album" / OTHER_NAMES_FILE).exists()
    assert report.name_records == ()


def test_a_file_inside_the_selected_folder_is_not_moved_again(tmp_path):
    pair(tmp_path)
    write(tmp_path / "Photo.jpg", COPY)
    kept = write(tmp_path / "Photo (1).jpg", COPY)
    duplicate = tmp_path / "Album (1)"
    inner = candidate(analyze(duplicate), "Photo (1).jpg")
    analysis = analyze(tmp_path)

    report = move_duplicates(
        analysis,
        [candidate(analysis, "Photo (1).jpg"), inner],
        folders=[duplicate],
        folder_groups=groups_of(tmp_path),
    )

    assert [result.move.candidate.entry.path for result in report.results] == [kept]
    assert report.results[0].outcome is MoveOutcome.MOVED
    assert (tmp_path / "_dups" / "01_Name" / "Photo (1).jpg").read_bytes() == COPY
    assert (folder_destination(tmp_path) / "Album (1)" / "Photo (1).jpg").read_bytes() == PHOTO
    assert not (tmp_path / "_dups" / "02_Content").exists()
    assert (tmp_path / "Album" / "Photo.jpg").is_file()


def test_a_file_inside_the_original_folder_can_still_move(tmp_path):
    write(tmp_path / "Album" / "Photo.jpg", PHOTO)
    write(tmp_path / "Album" / "Photo (1).jpg", PHOTO)
    write(tmp_path / "Album (1)" / "Photo.jpg", PHOTO)
    write(tmp_path / "Album (1)" / "Photo (1).jpg", PHOTO)
    duplicate = tmp_path / "Album (1)"
    original = tmp_path / "Album"

    move_duplicates(analyze(tmp_path), [], folders=[duplicate], folder_groups=groups_of(tmp_path))
    file_analysis = analyze(original)
    report = move_duplicates(file_analysis, [candidate(file_analysis, "Photo (1).jpg")])

    assert (original / "_dups" / "01_Name" / "Photo (1).jpg").read_bytes() == PHOTO
    assert (original / "Photo.jpg").is_file()
    assert not (original / "_dups" / "03_Folders").exists()
    assert (folder_destination(tmp_path) / "Album (1)" / "Photo (1).jpg").is_file()
    assert report.moved_count == 1
    assert report.folder_results == ()


def test_a_file_only_move_does_not_create_the_folder_destination(tmp_path):
    names = tmp_path / "names"
    write(names / "Photo.jpg", PHOTO)
    write(names / "Photo (1).jpg", PHOTO)
    analysis = analyze(names)
    move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])
    assert (names / "_dups" / "01_Name" / "Photo (1).jpg").is_file()
    assert not folder_destination(names).exists()

    content = tmp_path / "content"
    write(content / "Photo.jpg", PHOTO)
    write(content / "Vacation.jpg", PHOTO)
    analysis = analyze(content)
    group = next(item for item in analysis.groups if item.kind.value == "content")
    by_name = {item.entry.name: item for item in group.candidates}
    move_duplicates(analysis, [], content=(
        ContentMove(group.group_id, by_name["Photo.jpg"].entry, (by_name["Vacation.jpg"],)),
    ))
    assert (content / "_dups" / "02_Content" / "Vacation.jpg").is_file()
    assert not folder_destination(content).exists()


def test_selecting_the_original_or_the_same_folder_twice_creates_nothing(tmp_path):
    pair(tmp_path)
    analysis = analyze(tmp_path)
    groups = groups_of(tmp_path)
    original = tmp_path / "Album"
    duplicate = tmp_path / "Album (1)"

    with pytest.raises(ValueError):
        move_duplicates(analysis, [], folders=[original], folder_groups=groups)
    assert not (tmp_path / "_dups").exists()

    with pytest.raises(ValueError):
        move_duplicates(analysis, [], folders=[duplicate, duplicate], folder_groups=groups)
    assert not (tmp_path / "_dups").exists()


def albums(folder):
    for name in ("Album", "Album (1)", "Album (2)"):
        write(folder / name / "Photo.jpg", PHOTO)


def test_cancel_after_the_first_folder_records_only_that_name(tmp_path):
    albums(tmp_path)
    first = tmp_path / "Album (1)"
    second = tmp_path / "Album (2)"
    analysis = analyze(tmp_path)
    groups = groups_of(tmp_path)
    cancel = threading.Event()

    def stop_after_first(done: int, _total: int) -> None:
        if done == 1:
            cancel.set()

    report = move_duplicates(
        analysis, [], folders=[first, second], folder_groups=groups,
        on_progress=stop_after_first, cancel=cancel,
    )

    registry = tmp_path / "Album" / OTHER_NAMES_FILE
    assert report.cancelled
    assert [result.outcome for result in report.folder_results] == [MoveOutcome.MOVED, MoveOutcome.CANCELLED]
    assert registry.read_text(encoding="utf-8") == "Album (1)\n"
    assert second.is_dir()
    text = move_report_text(report)
    assert f"Names recorded in {registry}:" in text
    assert "Album (1)" in text
    assert "Album (2)" not in text


def test_a_later_move_appends_the_next_name(tmp_path):
    albums(tmp_path)
    first = tmp_path / "Album (1)"
    second = tmp_path / "Album (2)"
    analysis = analyze(tmp_path)
    groups = groups_of(tmp_path)

    move_duplicates(analysis, [], folders=[first], folder_groups=groups)
    move_duplicates(analysis, [], folders=[second], folder_groups=groups)

    registry = tmp_path / "Album" / OTHER_NAMES_FILE
    assert registry.read_text(encoding="utf-8") == "Album (1)\nAlbum (2)\n"


def test_a_name_file_without_a_trailing_newline_stays_one_name_per_line(tmp_path):
    pair(tmp_path)
    registry = write(tmp_path / "Album" / OTHER_NAMES_FILE, b"earlier")

    move_duplicates(analyze(tmp_path), [], folders=[tmp_path / "Album (1)"], folder_groups=groups_of(tmp_path))

    assert registry.read_bytes() == b"earlier\nAlbum (1)\n"


def test_a_directory_in_place_of_the_name_file_leaves_the_folder_moved(tmp_path):
    pair(tmp_path)
    blocked = tmp_path / "Album" / OTHER_NAMES_FILE
    blocked.mkdir()
    duplicate = tmp_path / "Album (1)"

    report = move_duplicates(analyze(tmp_path), [], folders=[duplicate], folder_groups=groups_of(tmp_path))

    assert [result.outcome for result in report.folder_results] == [MoveOutcome.MOVED]
    assert (folder_destination(tmp_path) / "Album (1)").is_dir()
    assert not duplicate.exists()
    assert blocked.is_dir()
    assert len(report.name_records) == 1
    assert report.name_records[0].name == "Album (1)"
    assert report.name_records[0].error
    assert f"Not recorded: Album (1) — {report.name_records[0].error}" in move_report_text(report)
