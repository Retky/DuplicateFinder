import stat
import threading
from pathlib import Path

import pytest

from duplicate_finder.core.analysis import Phase, analyze
from duplicate_finder.core.filename_parser import ALL_PATTERNS
from duplicate_finder.core.models import CandidateStatus, GroupKind
from duplicate_finder.core.scanner import FolderValidationError, ScanCancelled
from tests.winhelpers import add_attributes, write


def build_scenario(folder: Path) -> None:
    files = {
        "Photo.jpg": b"photo-content",
        "Photo (1).jpg": b"photo-content",
        "Photo - copia.jpg": b"photo-content",
        "Photo (3).jpg": b"photo-content-bigger",
        "Img.bmp": b"BM" + b"\x00" * 30,
        "Img (1).bmp": b"BM" + b"\xff" * 30,
        "Lonely (1).png": b"no original",
        "Clip.mp4": b"clip",
        "Clip [1].mp4": b"clip",
        "empty.txt": b"",
    }
    for name, data in files.items():
        (folder / name).write_bytes(data)
    (folder / "sub").mkdir()
    (folder / "sub" / "Photo (2).jpg").write_bytes(b"photo-content")


def statuses(result) -> dict[str, CandidateStatus]:
    return {c.entry.name: c.status for g in result.groups for c in g.candidates}


def test_full_analysis_statuses_and_totals(tmp_path):
    build_scenario(tmp_path)

    result = analyze(tmp_path)

    assert statuses(result) == {
        "Clip [1].mp4": CandidateStatus.CONFIRMED,
        "Img (1).bmp": CandidateStatus.DIFFERENT_CONTENT,
        "Lonely (1).png": CandidateStatus.NO_ORIGINAL,
        "Photo (1).jpg": CandidateStatus.CONFIRMED,
        "Photo - copia.jpg": CandidateStatus.CONFIRMED,
        "Photo (3).jpg": CandidateStatus.DIFFERENT_SIZE,
    }
    assert result.folder == tmp_path
    assert result.groups_with_confirmed == 2
    assert result.confirmed_count == 3
    assert result.movable_bytes == len(b"clip") + 2 * len(b"photo-content")
    assert not result.cancelled
    assert result.enabled == ALL_PATTERNS
    assert {s.name for s in result.scan.skipped} == {"empty.txt", "sub"}


def test_enabled_patterns_are_passed_through(tmp_path):
    build_scenario(tmp_path)

    result = analyze(tmp_path, enabled=ALL_PATTERNS - {"paren_num"})

    names = {candidate.entry.name for group in result.groups if group.kind is GroupKind.NAME
             for candidate in group.candidates}
    assert "Photo (1).jpg" not in names
    assert "Lonely (1).png" not in names
    assert "Photo - copia.jpg" in names
    content = {candidate.entry.name for group in result.groups if group.kind is GroupKind.CONTENT
               for candidate in group.candidates}
    assert "Photo (1).jpg" in content
    assert result.enabled == ALL_PATTERNS - {"paren_num"}


def test_progress_reports_both_phases(tmp_path):
    build_scenario(tmp_path)
    calls: dict[Phase, list[tuple[int, int]]] = {Phase.SCAN: [], Phase.VERIFY: []}

    analyze(tmp_path, on_progress=lambda phase, done, total: calls[phase].append((done, total)))

    for phase in Phase:
        assert calls[phase], phase
        done, total = calls[phase][-1]
        assert done == total


def test_cancel_during_verification_returns_safe_partial_result(tmp_path):
    build_scenario(tmp_path)
    cancel = threading.Event()

    def on_progress(phase, done, total):
        if phase is Phase.VERIFY:
            cancel.set()

    result = analyze(tmp_path, on_progress=on_progress, cancel=cancel)

    assert result.cancelled
    assert CandidateStatus.PENDING_HASH in statuses(result).values()
    for group in result.groups:
        for candidate in group.confirmed:
            assert candidate.sha256 == group.original_sha256


def test_pre_cancelled_scan_raises(tmp_path):
    build_scenario(tmp_path)
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(ScanCancelled):
        analyze(tmp_path, cancel=cancel)


def test_invalid_folder_raises(tmp_path):
    with pytest.raises(FolderValidationError):
        analyze(tmp_path / "missing")


def test_folder_matching_stays_off_until_requested(tmp_path):
    write(tmp_path / "Album" / "photo.jpg", b"photo")
    write(tmp_path / "Album (1)" / "photo.jpg", b"photo")

    off = analyze(tmp_path)
    assert off.folder_groups == ()
    assert off.folders_hidden is False

    on = analyze(tmp_path, match_folders=True)
    assert len(on.folder_groups) == 1
    assert on.folder_groups[0].original.name == "Album"
    assert [path.name for path in on.folder_groups[0].duplicates] == ["Album (1)"]
    assert on.folders_hidden is False


def test_hidden_folder_file_sets_the_warning_flag_and_desktop_ini_does_not(tmp_path):
    write(tmp_path / "Album" / "photo.jpg", b"photo")
    write(tmp_path / "Album" / "desktop.ini", b"ini")
    write(tmp_path / "Album (1)" / "photo.jpg", b"photo")

    metadata = analyze(tmp_path, match_folders=True)
    assert metadata.folders_hidden is False

    hidden = write(tmp_path / "Album" / "secret.jpg", b"secret")
    add_attributes(hidden, stat.FILE_ATTRIBUTE_HIDDEN)
    included = analyze(tmp_path, match_folders=True)
    assert included.folders_hidden is True
