from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from duplicate_finder import __version__
from duplicate_finder.core import operation_logger
from duplicate_finder.core.models import (
    Candidate,
    CandidateStatus,
    FileEntry,
    MoveOutcome,
    ParsedName,
    PlannedMove,
)
from duplicate_finder.core.operation_logger import OperationLogger, read_log

HASH = "c" * 64
FIXED = datetime(2026, 11, 2, 20, 15, 30, 123000, tzinfo=timezone(timedelta(hours=-6)))
PATTERNS = frozenset({"paren_num", "dash_copia"})


def clock() -> datetime:
    return FIXED


def make_move(folder: Path, name: str, size: int = 10, group_id: str = "g0001") -> PlannedMove:
    stem, ext = name.rsplit(".", 1)
    entry = FileEntry(path=folder / name, name=name, size=size, mtime_ns=111)
    parsed = ParsedName(stem=stem, ext="." + ext, base="Photo", base_key="photo", ext_key="." + ext,
                        suffixes=("paren_num",))
    candidate = Candidate(entry=entry, parsed=parsed, status=CandidateStatus.CONFIRMED, sha256=HASH)
    original = FileEntry(path=folder / f"Photo.{ext}", name=f"Photo.{ext}", size=size, mtime_ns=100)
    return PlannedMove(group_id=group_id, original=original, candidate=candidate,
                       dest_path=folder / "_dups" / name)


@pytest.fixture
def logs_dir(tmp_path) -> Path:
    (tmp_path / "_dups").mkdir()
    return tmp_path / "_dups" / "_logs"


def test_full_run_round_trip(tmp_path, logs_dir):
    moves = [make_move(tmp_path, "Photo (1).jpg", 10), make_move(tmp_path, "Photo (2).jpg", 20),
             make_move(tmp_path, "Photo (3).jpg", 30)]

    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, moves)
        log.result(moves[0], MoveOutcome.MOVED)
        log.result(moves[1], MoveOutcome.SKIPPED_EXISTS)
        log.result(moves[2], MoveOutcome.ERROR, "Access is denied")
        summary = log.finish(cancelled=False)

    assert log.path == logs_dir / "run_20261102_201530.jsonl"
    events = read_log(log.path)
    assert [e["event"] for e in events] == ["run_start", "planned", "planned", "planned",
                                            "result", "result", "result", "run_end"]
    assert {e["run_id"] for e in events} == {log.run_id}
    assert all(e["timestamp"] == "2026-11-02T20:15:30.123-06:00" for e in events)

    start = events[0]
    assert start["app_version"] == __version__
    assert start["folder"] == str(tmp_path)
    assert start["dest_folder"] == str(tmp_path / "_dups")
    assert start["enabled_patterns"] == ["dash_copia", "paren_num"]
    assert start["planned_count"] == 3
    assert start["planned_bytes"] == 60

    planned = events[1]
    assert planned == {
        "event": "planned", "timestamp": planned["timestamp"], "run_id": log.run_id,
        "group_id": "g0001",
        "original_path": str(tmp_path / "Photo.jpg"),
        "source_path": str(tmp_path / "Photo (1).jpg"),
        "dest_path": str(tmp_path / "_dups" / "Photo (1).jpg"),
        "file_name": "Photo (1).jpg",
        "size_bytes": 10,
        "mtime_ns": 111,
        "sha256": HASH,
    }

    moved, skipped, failed = events[4:7]
    assert moved["outcome"] == "moved" and "error" not in moved
    assert skipped["outcome"] == "skipped_exists"
    assert failed["outcome"] == "error" and failed["error"] == "Access is denied"
    assert moved["dest_path"] == str(tmp_path / "_dups" / "Photo (1).jpg")
    assert moved["sha256"] == HASH and moved["size_bytes"] == 10

    end = events[-1]
    assert end["totals"] == {"moved": 1, "skipped_exists": 1, "skipped_changed": 0,
                             "skipped_original_missing": 0, "cancelled": 0, "error": 1}
    assert end["moved_bytes"] == 10
    assert end["cancelled"] is False
    assert end["unresolved"] == 0
    assert summary == {k: end[k] for k in ("totals", "moved_bytes", "cancelled", "unresolved")}


def test_unicode_names_preserved(tmp_path, logs_dir):
    move = make_move(tmp_path, "Canción 📷 (1).jpg")
    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, [move])
        log.result(move, MoveOutcome.MOVED)
        log.finish(cancelled=False)

    raw = log.path.read_bytes().decode("utf-8")
    assert "Canción 📷 (1).jpg" in raw
    assert "\\u" not in raw
    assert read_log(log.path)[1]["file_name"] == "Canción 📷 (1).jpg"


def test_interrupted_run_is_readable(tmp_path, logs_dir):
    moves = [make_move(tmp_path, "Photo (1).jpg"), make_move(tmp_path, "Photo (2).jpg")]
    log = OperationLogger.create(logs_dir, clock)
    log.start(tmp_path, tmp_path / "_dups", PATTERNS, moves)
    log.result(moves[0], MoveOutcome.MOVED)
    log.close()

    events = read_log(log.path)
    assert [e["event"] for e in events] == ["run_start", "planned", "planned", "result"]

    with open(log.path, "a", encoding="utf-8") as f:
        f.write('{"event": "result", "source_pa')
    assert read_log(log.path) == events


def test_corrupt_middle_line_raises(tmp_path, logs_dir):
    move = make_move(tmp_path, "Photo (1).jpg")
    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, [move])
    lines = log.path.read_text(encoding="utf-8").splitlines()
    lines.insert(1, "{not json")
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError):
        read_log(log.path)


def test_same_second_creates_distinct_files(tmp_path, logs_dir):
    first = OperationLogger.create(logs_dir, clock)
    first.start(tmp_path, tmp_path / "_dups", PATTERNS, [])
    first.close()
    content = first.path.read_bytes()

    second = OperationLogger.create(logs_dir, clock)
    third = OperationLogger.create(logs_dir, clock)
    second.close()
    third.close()

    assert [p.name for p in (first.path, second.path, third.path)] == [
        "run_20261102_201530.jsonl", "run_20261102_201530_2.jsonl", "run_20261102_201530_3.jsonl"]
    assert first.path.read_bytes() == content
    assert second.run_id != first.run_id


def test_existing_log_is_never_overwritten(tmp_path, logs_dir):
    logs_dir.mkdir()
    existing = logs_dir / "run_20261102_201530.jsonl"
    existing.write_text("previous run\n", encoding="utf-8")

    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, [])

    assert existing.read_text(encoding="utf-8") == "previous run\n"
    assert log.path.name == "run_20261102_201530_2.jsonl"


def test_only_logs_level_is_created(tmp_path):
    with pytest.raises(FileNotFoundError):
        OperationLogger.create(tmp_path / "_dups" / "_logs", clock)
    assert not (tmp_path / "_dups").exists()


def test_fsync_after_every_line(tmp_path, logs_dir, monkeypatch):
    calls = []
    real_fsync = operation_logger.os.fsync
    monkeypatch.setattr(operation_logger.os, "fsync", lambda fd: (calls.append(fd), real_fsync(fd)))
    moves = [make_move(tmp_path, "Photo (1).jpg"), make_move(tmp_path, "Photo (2).jpg")]

    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, moves)
        log.result(moves[0], MoveOutcome.MOVED)
        log.result(moves[1], MoveOutcome.MOVED)
        log.finish(cancelled=False)

    assert len(calls) == len(read_log(log.path)) == 6


def test_result_before_start_raises(tmp_path, logs_dir):
    move = make_move(tmp_path, "Photo (1).jpg")
    with OperationLogger.create(logs_dir, clock) as log:
        with pytest.raises(RuntimeError):
            log.result(move, MoveOutcome.MOVED)


def test_start_twice_raises(tmp_path, logs_dir):
    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, [])
        with pytest.raises(RuntimeError):
            log.start(tmp_path, tmp_path / "_dups", PATTERNS, [])


def test_duplicate_planned_source_raises(tmp_path, logs_dir):
    move = make_move(tmp_path, "Photo (1).jpg")
    with OperationLogger.create(logs_dir, clock) as log:
        with pytest.raises(ValueError):
            log.start(tmp_path, tmp_path / "_dups", PATTERNS, [move, move])


def test_duplicate_result_raises(tmp_path, logs_dir):
    move = make_move(tmp_path, "Photo (1).jpg")
    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, [move])
        log.result(move, MoveOutcome.MOVED)
        with pytest.raises(ValueError):
            log.result(move, MoveOutcome.MOVED)


def test_result_for_unplanned_move_raises(tmp_path, logs_dir):
    planned = make_move(tmp_path, "Photo (1).jpg")
    other = make_move(tmp_path, "Photo (2).jpg")
    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, [planned])
        with pytest.raises(ValueError):
            log.result(other, MoveOutcome.MOVED)


def test_error_outcome_rules(tmp_path, logs_dir):
    move = make_move(tmp_path, "Photo (1).jpg")
    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, [move])
        with pytest.raises(ValueError):
            log.result(move, MoveOutcome.ERROR)
        with pytest.raises(ValueError):
            log.result(move, MoveOutcome.MOVED, "unexpected")


def test_no_writes_after_finish_or_close(tmp_path, logs_dir):
    move = make_move(tmp_path, "Photo (1).jpg")
    log = OperationLogger.create(logs_dir, clock)
    log.start(tmp_path, tmp_path / "_dups", PATTERNS, [move])
    log.finish(cancelled=True)
    with pytest.raises(RuntimeError):
        log.result(move, MoveOutcome.MOVED)
    with pytest.raises(RuntimeError):
        log.finish(cancelled=True)
    log.close()
    log.close()


def test_unresolved_moves_are_counted(tmp_path, logs_dir):
    moves = [make_move(tmp_path, "Photo (1).jpg"), make_move(tmp_path, "Photo (2).jpg")]
    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, moves)
        log.result(moves[0], MoveOutcome.CANCELLED)
        summary = log.finish(cancelled=True)

    assert summary["unresolved"] == 1
    assert summary["cancelled"] is True
    assert summary["totals"]["cancelled"] == 1
    assert summary["moved_bytes"] == 0


def test_paths_stored_without_extended_prefix(tmp_path, logs_dir):
    move = make_move(tmp_path, "Photo (1).jpg")
    with OperationLogger.create(logs_dir, clock) as log:
        log.start(tmp_path, tmp_path / "_dups", PATTERNS, [move])
        log.result(move, MoveOutcome.MOVED)

    assert "\\\\?\\" not in log.path.read_text(encoding="utf-8")
    assert not str(log.path).startswith("\\\\?\\")
