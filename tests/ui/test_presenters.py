from pathlib import Path

import pytest

from duplicate_finder.core.analysis import Phase
from duplicate_finder.core.file_mover import MoveReport, NameRecord
from duplicate_finder.core.models import (
    Candidate,
    CandidateStatus,
    DuplicateGroup,
    FileEntry,
    MoveOutcome,
    ParsedName,
    PlannedMove,
)
from duplicate_finder.ui.presenters import (
    MARK_OFF,
    MARK_ON,
    STATUS_TEXT,
    analyze_progress_text,
    can_toggle,
    confirm_text,
    group_title,
    move_progress_text,
    move_report_text,
    selection_mark,
    short_hash,
    status_text,
    summary,
    summary_text,
)

FOLDER = Path(r"C:\Photos")
HASH_A = "a" * 64
HASH_B = "b" * 64


def entry(name: str, size: int = 100) -> FileEntry:
    return FileEntry(FOLDER / name, name, size, 1)


def parsed(name: str, suffixes: tuple[str, ...] = ()) -> ParsedName:
    stem, extension = name.rsplit(".", 1)
    return ParsedName(stem, "." + extension, "Photo", "photo", "." + extension, suffixes)


def candidate(name: str, status: CandidateStatus, size: int = 100, sha: str | None = None,
              error: str | None = None) -> Candidate:
    if status in (CandidateStatus.CONFIRMED, CandidateStatus.CONTENT_MATCH) and sha is None:
        sha = HASH_A
    return Candidate(entry(name, size), parsed(name, ("paren_num",)), status, sha, error)


def confirmed_group() -> DuplicateGroup:
    original = entry("Photo.jpg", 100)
    copies = (
        candidate("Photo (1).jpg", CandidateStatus.CONFIRMED, 100, HASH_A),
        candidate("Photo (2).jpg", CandidateStatus.CONFIRMED, 100, HASH_A),
        candidate("Photo (3).jpg", CandidateStatus.DIFFERENT_SIZE, 120),
    )
    return DuplicateGroup("g0001", ("photo", ".jpg"), original, HASH_A, copies)


def test_every_status_has_text():
    for status in CandidateStatus:
        item = candidate("Photo (1).jpg", status, sha=HASH_A if status is CandidateStatus.CONFIRMED else HASH_B)
        assert status_text(item) == STATUS_TEXT[status]


def test_read_error_appends_the_message():
    item = candidate("Photo (1).jpg", CandidateStatus.READ_ERROR, error="Access is denied")
    assert status_text(item) == "Kept: could not be read: Access is denied"


@pytest.mark.parametrize("status", list(CandidateStatus))
def test_only_confirmed_can_be_toggled(status):
    item = candidate("Photo (1).jpg", status)
    assert can_toggle(item) is (status is CandidateStatus.CONFIRMED)


def test_marks():
    chosen = candidate("Photo (1).jpg", CandidateStatus.CONFIRMED)
    kept = candidate("Photo (3).jpg", CandidateStatus.DIFFERENT_SIZE)
    selected = {chosen.entry.path}
    assert selection_mark(chosen, selected) == MARK_ON
    assert selection_mark(chosen, set()) == MARK_OFF
    assert selection_mark(kept, selected) == ""


def test_short_hash():
    assert short_hash(None) == ""
    assert short_hash(HASH_A) == "a" * 12


def test_summary_counts_only_movable_selected_files():
    group = confirmed_group()
    both = {group.candidates[0].entry.path, group.candidates[1].entry.path}
    assert summary([group], both) == summary([group], both | {group.candidates[2].entry.path})
    assert summary([group], both).file_count == 2
    assert summary([group], both).group_count == 1
    assert summary([group], both).total_bytes == 200
    assert summary([group], {group.candidates[0].entry.path}).total_bytes == 100
    assert summary([group], set()).file_count == 0


def test_summary_text():
    group = confirmed_group()
    selected = {group.candidates[0].entry.path}
    assert summary_text(1, 2, [group], selected) == "1 group(s), 2 confirmed, 1 selected, 100 B to move"


def test_confirm_text_names_the_destination():
    group = confirmed_group()
    text = confirm_text(FOLDER, [group], {group.candidates[0].entry.path})
    assert "1 file(s) from 1 group(s)" in text
    assert "100 B" in text
    assert str(FOLDER / "dups" / "01_Name") in text


def test_group_titles():
    group = confirmed_group()
    assert group_title(group) == "[g0001] Photo.jpg (100 B)"

    orphan = candidate("Photo (1).png", CandidateStatus.NO_ORIGINAL)
    orphan_group = DuplicateGroup("g0002", ("photo", ".png"), None, None, (orphan,))
    assert group_title(orphan_group) == "[g0002] Photo.png (no original)"

    ambiguous = candidate("Photo (1).jpg", CandidateStatus.AMBIGUOUS)
    ambiguous_group = DuplicateGroup("g0003", ("photo", ".jpg"), None, None, (ambiguous,))
    assert group_title(ambiguous_group) == "[g0003] Photo.jpg (ambiguous original)"


def test_move_report_lists_every_outcome(tmp_path):
    group = confirmed_group()
    move = PlannedMove("g0001", group.original, group.candidates[0], tmp_path / "dups" / "Photo (1).jpg")
    from duplicate_finder.core.file_mover import MoveResult

    report = MoveReport(tmp_path / "dups", tmp_path / "dups" / "_logs" / "run.jsonl",
                        (MoveResult(move, MoveOutcome.MOVED, None),), False)
    text = move_report_text(report)
    assert "Moved: 1" in text
    assert "Errors: 0" in text
    assert "Cancelled: 0" in text
    assert str(report.log_path) in text
    assert "Names recorded" not in text
    assert "Not recorded" not in text


def test_move_report_lists_recorded_names_per_kept_folder(tmp_path):
    group = confirmed_group()
    move = PlannedMove("g0001", group.original, group.candidates[0], tmp_path / "dups" / "Photo (1).jpg")
    from duplicate_finder.core.file_mover import MoveResult

    album = tmp_path / "Album" / "other-names.txt"
    other = tmp_path / "Set" / "other-names.txt"
    report = MoveReport(
        tmp_path / "dups", tmp_path / "dups" / "_logs" / "run.jsonl",
        (MoveResult(move, MoveOutcome.MOVED, None),), False,
        name_records=(
            NameRecord(album.parent, album, "Album (1)"),
            NameRecord(album.parent, album, "Album (2)"),
            NameRecord(other.parent, other, "Set (1)", "Permission denied"),
        ),
    )
    text = move_report_text(report)
    assert "Moved: 1" in text
    assert text.index(f"Names recorded in {album}:") < text.index("Album (1)") < text.index("Album (2)")
    assert f"Names recorded in {other}:" not in text
    assert "Not recorded: Set (1) — Permission denied" in text


def test_progress_text():
    assert analyze_progress_text(Phase.SCAN, 2, 10) == "Scanning... 2 of 10"
    assert analyze_progress_text(Phase.VERIFY, 50, 200) == "Verifying... 25%"
    assert move_progress_text(1, 4) == "Moving... 1 of 4"
