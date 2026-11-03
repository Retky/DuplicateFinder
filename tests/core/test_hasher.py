import ctypes
import hashlib
import threading
from collections import Counter
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

import pytest

from duplicate_finder.core import hasher
from duplicate_finder.core.duplicate_grouper import build_groups
from duplicate_finder.core.hasher import sha256_file, verify_groups
from duplicate_finder.core.models import CandidateStatus
from duplicate_finder.core.scanner import scan_folder

CONFIRMED = CandidateStatus.CONFIRMED
DIFFERENT_CONTENT = CandidateStatus.DIFFERENT_CONTENT
PENDING = CandidateStatus.PENDING_HASH
READ_ERROR = CandidateStatus.READ_ERROR

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_INVALID_HANDLE = wintypes.HANDLE(-1).value


@contextmanager
def exclusive_lock(path: Path):
    handle = _kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        _kernel32.CloseHandle(handle)


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def groups_for(folder: Path):
    return build_groups(scan_folder(folder).entries)


def statuses(groups) -> dict[str, CandidateStatus]:
    return {c.entry.name: c.status for g in groups for c in g.candidates}


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(hasher, "CHUNK_SIZE", 4)


@pytest.mark.parametrize("data", [b"", b"hello", bytes(range(256)) * 5], ids=["empty", "small", "multi-block"])
def test_sha256_file_matches_hashlib(tmp_path, small_chunks, data):
    path = write(tmp_path / "file.bin", data)
    assert sha256_file(path) == hashlib.sha256(data).hexdigest()


def test_sha256_file_reports_bytes(tmp_path, small_chunks):
    path = write(tmp_path / "file.bin", b"0123456789")
    counts = []
    sha256_file(path, on_bytes=counts.append)
    assert counts == [4, 4, 2]


def test_identical_copy_is_confirmed(tmp_path):
    write(tmp_path / "Photo.jpg", b"same content")
    write(tmp_path / "Photo (1).jpg", b"same content")

    (group,) = verify_groups(groups_for(tmp_path))

    assert statuses([group]) == {"Photo (1).jpg": CONFIRMED}
    assert group.original_sha256 == hashlib.sha256(b"same content").hexdigest()
    assert group.candidates[0].sha256 == group.original_sha256
    assert group.movable_size == len(b"same content")


def test_one_byte_difference_is_different_content(tmp_path):
    write(tmp_path / "Photo.jpg", b"content A")
    write(tmp_path / "Photo (1).jpg", b"content B")

    (group,) = verify_groups(groups_for(tmp_path))

    assert statuses([group]) == {"Photo (1).jpg": DIFFERENT_CONTENT}
    assert group.candidates[0].sha256 == hashlib.sha256(b"content B").hexdigest()
    assert group.confirmed == ()


def test_decision_is_per_file(tmp_path):
    write(tmp_path / "Photo.jpg", b"original")
    write(tmp_path / "Photo (1).jpg", b"original")
    write(tmp_path / "Photo (2).jpg", b"modified")

    (group,) = verify_groups(groups_for(tmp_path))

    assert statuses([group]) == {"Photo (1).jpg": CONFIRMED, "Photo (2).jpg": DIFFERENT_CONTENT}


def test_same_size_bmp_with_different_pixels(tmp_path):
    header = b"BM" + bytes(52)
    write(tmp_path / "Img.bmp", header + bytes([0, 0, 255]) * 100)
    write(tmp_path / "Img (1).bmp", header + bytes([255, 0, 0]) * 100)

    (group,) = verify_groups(groups_for(tmp_path))

    assert statuses([group]) == {"Img (1).bmp": DIFFERENT_CONTENT}


def test_locked_candidate_is_read_error_others_continue(tmp_path):
    write(tmp_path / "Photo.jpg", b"data")
    locked = write(tmp_path / "Photo (1).jpg", b"data")
    write(tmp_path / "Photo (2).jpg", b"data")
    groups = groups_for(tmp_path)

    with exclusive_lock(locked):
        (group,) = verify_groups(groups)

    assert statuses([group]) == {"Photo (1).jpg": READ_ERROR, "Photo (2).jpg": CONFIRMED}
    assert group.candidates[0].error


def test_deleted_candidate_is_read_error(tmp_path):
    write(tmp_path / "Photo.jpg", b"data")
    deleted = write(tmp_path / "Photo (1).jpg", b"data")
    groups = groups_for(tmp_path)
    deleted.unlink()

    (group,) = verify_groups(groups)

    assert statuses([group]) == {"Photo (1).jpg": READ_ERROR}


@pytest.mark.parametrize("problem", ["locked", "deleted"])
def test_unreadable_original_makes_all_pending_read_error(tmp_path, problem):
    original = write(tmp_path / "Photo.jpg", b"data")
    write(tmp_path / "Photo (1).jpg", b"data")
    write(tmp_path / "Photo (2).jpg", b"data")
    groups = groups_for(tmp_path)

    if problem == "locked":
        with exclusive_lock(original):
            (group,) = verify_groups(groups)
    else:
        original.unlink()
        (group,) = verify_groups(groups)

    assert statuses([group]) == {"Photo (1).jpg": READ_ERROR, "Photo (2).jpg": READ_ERROR}
    assert group.original_sha256 is None
    assert group.confirmed == ()
    assert all(c.error.startswith("Original could not be read") for c in group.candidates)


def test_candidate_changed_since_scan_is_read_error(tmp_path):
    write(tmp_path / "Photo.jpg", b"data")
    changed = write(tmp_path / "Photo (1).jpg", b"data")
    groups = groups_for(tmp_path)
    changed.write_bytes(b"data plus more")

    (group,) = verify_groups(groups)

    assert statuses([group]) == {"Photo (1).jpg": READ_ERROR}
    assert group.candidates[0].error == "File changed since scan"


def test_original_changed_since_scan_blocks_group(tmp_path):
    original = write(tmp_path / "Photo.jpg", b"data")
    write(tmp_path / "Photo (1).jpg", b"data")
    groups = groups_for(tmp_path)
    original.write_bytes(b"dat")

    (group,) = verify_groups(groups)

    assert statuses([group]) == {"Photo (1).jpg": READ_ERROR}
    assert group.original_sha256 is None


def test_groups_without_pending_are_untouched(tmp_path, monkeypatch):
    write(tmp_path / "Photo.jpg", b"data")
    write(tmp_path / "Photo (1).jpg", b"different size")
    write(tmp_path / "Other (1).jpg", b"no original")
    groups = groups_for(tmp_path)

    def fail(*args, **kwargs):
        raise AssertionError("no file should be hashed")

    monkeypatch.setattr(hasher, "sha256_file", fail)

    assert verify_groups(groups) == groups


def test_original_hashed_once_per_group(tmp_path, monkeypatch):
    write(tmp_path / "Photo.jpg", b"data")
    for i in range(1, 4):
        write(tmp_path / f"Photo ({i}).jpg", b"data")
    calls = Counter()
    real = hasher.sha256_file

    def spy(path, *args, **kwargs):
        calls[path.name] += 1
        return real(path, *args, **kwargs)

    monkeypatch.setattr(hasher, "sha256_file", spy)
    verify_groups(groups_for(tmp_path))

    assert calls == {"Photo.jpg": 1, "Photo (1).jpg": 1, "Photo (2).jpg": 1, "Photo (3).jpg": 1}


def test_progress_reaches_total(tmp_path, small_chunks):
    write(tmp_path / "Photo.jpg", b"0123456789")
    write(tmp_path / "Photo (1).jpg", b"0123456789")
    write(tmp_path / "Photo (2).jpg", b"012345678")
    write(tmp_path / "Other.jpg", b"abc")
    write(tmp_path / "Other (1).jpg", b"abc")
    calls = []

    verify_groups(groups_for(tmp_path), on_progress=lambda done, total: calls.append((done, total)))

    expected_total = 10 + 10 + 3 + 3
    assert calls[-1] == (expected_total, expected_total)
    assert all(total == expected_total for _, total in calls)
    dones = [done for done, _ in calls]
    assert dones == sorted(dones)


def test_cancel_before_start_leaves_everything_pending(tmp_path):
    write(tmp_path / "Photo.jpg", b"data")
    write(tmp_path / "Photo (1).jpg", b"data")
    groups = groups_for(tmp_path)
    cancel = threading.Event()
    cancel.set()

    result = verify_groups(groups, cancel=cancel)

    assert result == groups
    assert statuses(result) == {"Photo (1).jpg": PENDING}


def test_cancel_midway_never_confirms_unverified(tmp_path, small_chunks):
    for prefix in ("A", "B"):
        write(tmp_path / f"{prefix}.jpg", b"0123456789")
        for i in range(1, 4):
            write(tmp_path / f"{prefix} ({i}).jpg", b"0123456789")
    groups = groups_for(tmp_path)
    cancel = threading.Event()

    def cancel_after_some_bytes(done, total):
        if done >= 25:
            cancel.set()

    result = verify_groups(groups, on_progress=cancel_after_some_bytes, cancel=cancel)

    assert [g.group_id for g in result] == [g.group_id for g in groups]
    all_statuses = list(statuses(result).values())
    assert PENDING in all_statuses
    assert CONFIRMED in all_statuses
    for group in result:
        for candidate in group.confirmed:
            assert candidate.sha256 == group.original_sha256
    assert result[1] == groups[1]


def test_verification_does_not_modify_files(tmp_path):
    write(tmp_path / "Photo.jpg", b"data")
    write(tmp_path / "Photo (1).jpg", b"data")
    write(tmp_path / "Photo (2).jpg", b"diff")
    before = snapshot(tmp_path)

    verify_groups(groups_for(tmp_path))

    assert snapshot(tmp_path) == before
