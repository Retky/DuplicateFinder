import hashlib
import os
import stat
import threading
from pathlib import Path

import pytest

from duplicate_finder.core import file_mover
from duplicate_finder.core.analysis import analyze
from duplicate_finder.core.file_mover import DestinationError, move_duplicates
from duplicate_finder.core.models import CandidateStatus, MoveOutcome, SkipReason
from duplicate_finder.core.operation_logger import read_log
from duplicate_finder.core.winpath import to_os_path
from tests.winhelpers import add_attributes, exclusive_lock, make_junction, write

MOVED = MoveOutcome.MOVED
SKIPPED_EXISTS = MoveOutcome.SKIPPED_EXISTS
SKIPPED_CHANGED = MoveOutcome.SKIPPED_CHANGED
SKIPPED_ORIGINAL_MISSING = MoveOutcome.SKIPPED_ORIGINAL_MISSING
CANCELLED = MoveOutcome.CANCELLED
ERROR = MoveOutcome.ERROR


def candidate(analysis, name: str):
    for group in analysis.groups:
        for item in group.candidates:
            if item.entry.name == name:
                return item
    raise AssertionError(f"{name} is not a candidate")


def outcomes(report) -> dict[str, MoveOutcome]:
    return {result.move.candidate.entry.name: result.outcome for result in report.results}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_log_consistent(report) -> None:
    assert report.log_path is not None
    events = read_log(report.log_path)
    planned = [event for event in events if event["event"] == "planned"]
    results = [event for event in events if event["event"] == "result"]
    end = events[-1]
    assert end["event"] == "run_end"
    assert len(results) == len(planned) == len(report.results)
    assert end["unresolved"] == 0
    assert end["totals"] == report.totals
    assert end["moved_bytes"] == report.moved_bytes
    assert end["cancelled"] is report.cancelled
    assert [event["outcome"] for event in results] == [result.outcome.value for result in report.results]


def test_moves_confirmed_copy_unchanged(tmp_path):
    data = b"same-photo-bytes"
    original = write(tmp_path / "Photo.jpg", data)
    copy = write(tmp_path / "Photo (1).jpg", data)
    copied_mtime = copy.stat().st_mtime_ns
    analysis = analyze(tmp_path)

    report = move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])

    moved = tmp_path / "dups" / "Photo (1).jpg"
    assert outcomes(report) == {"Photo (1).jpg": MOVED}
    assert report.moved_count == 1
    assert report.moved_bytes == len(data)
    assert moved.parent == report.dest_folder
    assert report.log_path.parent == tmp_path / "dups" / "_logs"
    assert digest(moved) == digest(original) == hashlib.sha256(data).hexdigest()
    assert moved.stat().st_mtime_ns == copied_mtime
    assert not copy.exists()
    assert_log_consistent(report)


def test_existing_dups_folder_is_reused(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    write(tmp_path / "Photo (1).jpg", b"same")
    old = write(tmp_path / "dups" / "old.jpg", b"keep-me")
    old_mtime = old.stat().st_mtime_ns
    analysis = analyze(tmp_path)

    report = move_duplicates(analysis, analysis.groups[0].confirmed)

    assert outcomes(report) == {"Photo (1).jpg": MOVED}
    assert old.read_bytes() == b"keep-me"
    assert old.stat().st_mtime_ns == old_mtime
    assert_log_consistent(report)


def test_dups_as_file_moves_nothing(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    copy = write(tmp_path / "Photo (1).jpg", b"same")
    write(tmp_path / "dups", b"not a folder")
    analysis = analyze(tmp_path)

    with pytest.raises(DestinationError):
        move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])

    assert copy.exists()
    assert (tmp_path / "dups").read_bytes() == b"not a folder"
    assert not (tmp_path / "dups" / "_logs").exists()


def test_dups_as_junction_moves_nothing(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    copy = write(tmp_path / "Photo (1).jpg", b"same")
    target = tmp_path / "elsewhere"
    target.mkdir()
    make_junction(tmp_path / "dups", target)
    analysis = analyze(tmp_path)

    with pytest.raises(DestinationError):
        move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])

    assert copy.exists()
    assert not (target / "_logs").exists()


def test_existing_destination_name_is_not_overwritten(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    source = write(tmp_path / "Photo (1).jpg", b"same")
    blocking = write(tmp_path / "dups" / "Photo (1).jpg", b"already here")
    analysis = analyze(tmp_path)

    report = move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])

    assert outcomes(report) == {"Photo (1).jpg": SKIPPED_EXISTS}
    assert source.read_bytes() == b"same"
    assert blocking.read_bytes() == b"already here"
    assert_log_consistent(report)


def test_copy_changed_after_analysis_stays(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    copy = write(tmp_path / "Photo (1).jpg", b"same")
    analysis = analyze(tmp_path)
    copy.write_bytes(b"changed")

    report = move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])

    assert outcomes(report) == {"Photo (1).jpg": SKIPPED_CHANGED}
    assert report.results[0].detail == "file changed since scan"
    assert copy.read_bytes() == b"changed"
    assert_log_consistent(report)


def test_missing_original_skips_the_copy(tmp_path):
    original = write(tmp_path / "Photo.jpg", b"same")
    copy = write(tmp_path / "Photo (1).jpg", b"same")
    analysis = analyze(tmp_path)
    original.unlink()

    report = move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])

    assert outcomes(report) == {"Photo (1).jpg": SKIPPED_ORIGINAL_MISSING}
    assert copy.exists()
    assert_log_consistent(report)


def test_changed_original_skips_the_copy(tmp_path):
    original = write(tmp_path / "Photo.jpg", b"same")
    copy = write(tmp_path / "Photo (1).jpg", b"same")
    analysis = analyze(tmp_path)
    original.write_bytes(b"edited")

    report = move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])

    assert outcomes(report) == {"Photo (1).jpg": SKIPPED_CHANGED}
    assert report.results[0].detail == "original changed since scan"
    assert copy.read_bytes() == b"same"
    assert_log_consistent(report)


def test_locked_copy_is_reported_and_the_next_one_moves(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    locked = write(tmp_path / "Photo (1).jpg", b"same")
    write(tmp_path / "Photo (2).jpg", b"same")
    analysis = analyze(tmp_path)
    selected = [candidate(analysis, "Photo (1).jpg"), candidate(analysis, "Photo (2).jpg")]

    with exclusive_lock(locked):
        report = move_duplicates(analysis, selected)

    assert outcomes(report) == {"Photo (1).jpg": ERROR, "Photo (2).jpg": MOVED}
    assert locked.exists()
    assert (tmp_path / "dups" / "Photo (2).jpg").exists()
    assert report.results[0].detail
    assert_log_consistent(report)


def test_open_copy_is_reported_as_error(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    copy = write(tmp_path / "Photo (1).jpg", b"same")
    analysis = analyze(tmp_path)

    with open(copy, "rb") as handle:
        report = move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])
        handle.read(1)

    assert outcomes(report) == {"Photo (1).jpg": ERROR}
    assert copy.exists()
    assert_log_consistent(report)


def test_read_only_copy_moves_and_keeps_the_attribute(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    copy = write(tmp_path / "Photo (1).jpg", b"same")
    add_attributes(copy, stat.FILE_ATTRIBUTE_READONLY)
    analysis = analyze(tmp_path)
    moved = tmp_path / "dups" / "Photo (1).jpg"
    try:
        report = move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])
        assert outcomes(report) == {"Photo (1).jpg": MOVED}
        assert not copy.exists()
        assert moved.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        assert_log_consistent(report)
    finally:
        if moved.exists():
            os.chmod(moved, stat.S_IWRITE)


def test_unconfirmed_candidate_moves_nothing(tmp_path):
    write(tmp_path / "Photo.jpg", b"aaaaaaaa")
    write(tmp_path / "Photo (1).jpg", b"bbbbbbbb")
    analysis = analyze(tmp_path)
    rejected = candidate(analysis, "Photo (1).jpg")
    assert rejected.status is CandidateStatus.DIFFERENT_CONTENT

    with pytest.raises(ValueError):
        move_duplicates(analysis, [rejected])

    assert not (tmp_path / "dups").exists()
    assert (tmp_path / "Photo (1).jpg").exists()


def test_candidate_from_another_folder_is_rejected(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    write(tmp_path / "Photo (1).jpg", b"same")
    other = tmp_path / "other"
    write(other / "Photo.jpg", b"same")
    write(other / "Photo (1).jpg", b"same")
    analysis = analyze(tmp_path)

    with pytest.raises(ValueError):
        move_duplicates(analysis, [candidate(analyze(other), "Photo (1).jpg")])

    assert not (tmp_path / "dups").exists()


def test_duplicate_selection_is_rejected(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    write(tmp_path / "Photo (1).jpg", b"same")
    analysis = analyze(tmp_path)
    selected = candidate(analysis, "Photo (1).jpg")

    with pytest.raises(ValueError):
        move_duplicates(analysis, [selected, selected])

    assert not (tmp_path / "dups").exists()


def test_only_the_selected_copy_moves(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    write(tmp_path / "Photo (1).jpg", b"same")
    kept = write(tmp_path / "Photo (2).jpg", b"same")
    analysis = analyze(tmp_path)

    report = move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])

    assert outcomes(report) == {"Photo (1).jpg": MOVED}
    assert kept.exists()
    assert not (tmp_path / "dups" / "Photo (2).jpg").exists()


def test_empty_selection_creates_nothing(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    write(tmp_path / "Photo (1).jpg", b"same")
    analysis = analyze(tmp_path)
    before = sorted(path.name for path in tmp_path.iterdir())

    report = move_duplicates(analysis, [])

    assert report.results == ()
    assert report.log_path is None
    assert report.moved_count == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_cancel_after_first_file_leaves_the_rest(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    for index in range(1, 4):
        write(tmp_path / f"Photo ({index}).jpg", b"same")
    analysis = analyze(tmp_path)
    selected = [candidate(analysis, f"Photo ({index}).jpg") for index in range(1, 4)]
    cancel = threading.Event()

    def stop_after_first(done, total):
        if done == 1:
            cancel.set()

    report = move_duplicates(analysis, selected, on_progress=stop_after_first, cancel=cancel)

    assert report.cancelled
    assert outcomes(report) == {"Photo (1).jpg": MOVED, "Photo (2).jpg": CANCELLED, "Photo (3).jpg": CANCELLED}
    assert (tmp_path / "Photo (2).jpg").exists()
    assert (tmp_path / "Photo (3).jpg").exists()
    assert_log_consistent(report)


def test_unexpected_error_keeps_the_log_consistent(tmp_path, monkeypatch):
    write(tmp_path / "Photo.jpg", b"same")
    write(tmp_path / "Photo (1).jpg", b"same")
    write(tmp_path / "Photo (2).jpg", b"same")
    analysis = analyze(tmp_path)
    real_rename = file_mover.os.rename

    def fail_on_second(src, dst):
        if "Photo (2).jpg" in src:
            raise RuntimeError("boom")
        return real_rename(src, dst)

    monkeypatch.setattr(file_mover.os, "rename", fail_on_second)
    selected = [candidate(analysis, "Photo (1).jpg"), candidate(analysis, "Photo (2).jpg")]

    with pytest.raises(RuntimeError, match="boom"):
        move_duplicates(analysis, selected)

    assert (tmp_path / "dups" / "Photo (1).jpg").exists()
    assert (tmp_path / "Photo (2).jpg").exists()
    log = next((tmp_path / "dups" / "_logs").glob("*.jsonl"))
    events = read_log(log)
    results = [event for event in events if event["event"] == "result"]
    assert [event["outcome"] for event in results] == ["moved", "error"]
    assert "boom" in results[1]["error"]
    assert events[-1]["unresolved"] == 0


def test_long_path_move(tmp_path):
    folder = tmp_path
    while len(str(folder)) < 280:
        folder = folder / ("d" * 50)
    os.makedirs(to_os_path(folder))
    for name, data in (("Photo.jpg", b"same"), ("Photo (1).jpg", b"same")):
        with open(to_os_path(folder / name), "wb") as handle:
            handle.write(data)
    analysis = analyze(folder)

    report = move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])

    assert outcomes(report) == {"Photo (1).jpg": MOVED}
    assert os.path.exists(to_os_path(folder / "dups" / "Photo (1).jpg"))
    assert_log_consistent(report)


def test_second_analysis_finds_nothing_left_to_move(tmp_path):
    write(tmp_path / "Photo.jpg", b"same")
    write(tmp_path / "Photo (1).jpg", b"same")
    analysis = analyze(tmp_path)
    move_duplicates(analysis, [candidate(analysis, "Photo (1).jpg")])

    again = analyze(tmp_path)

    assert again.confirmed_count == 0
    assert {item.name: item.reason for item in again.scan.skipped}["dups"] is SkipReason.DIRECTORY
