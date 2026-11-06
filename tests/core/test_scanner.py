import os
import stat
import threading
from pathlib import Path

import pytest

from duplicate_finder.core import scanner
from duplicate_finder.core.models import SkipReason
from duplicate_finder.core.scanner import (
    FolderValidationError,
    ScanCancelled,
    classify,
    scan_folder,
    validate_folder,
)
from duplicate_finder.core.winpath import to_os_path
from tests.winhelpers import add_attributes, make_junction, write

HIDDEN = stat.FILE_ATTRIBUTE_HIDDEN
SYSTEM = stat.FILE_ATTRIBUTE_SYSTEM
REPARSE = stat.FILE_ATTRIBUTE_REPARSE_POINT


def names(items) -> list[str]:
    return [item.name for item in items]


def reasons(result) -> dict[str, SkipReason]:
    return {item.name: item.reason for item in result.skipped}


def snapshot(folder: Path) -> dict[str, int]:
    return {str(p.relative_to(folder)): p.lstat().st_mtime_ns for p in folder.rglob("*")}


@pytest.mark.parametrize(
    "name, is_dir, attributes, size, expected",
    [
        ("photo.jpg", False, 0, 10, None),
        ("link", True, REPARSE, 0, SkipReason.LINK),
        ("link.jpg", False, REPARSE | HIDDEN, 0, SkipReason.LINK),
        ("dups", True, 0, 0, SkipReason.DIRECTORY),
        ("hidden-dir", True, HIDDEN, 0, SkipReason.DIRECTORY),
        ("hidden.jpg", False, HIDDEN, 10, SkipReason.HIDDEN_OR_SYSTEM),
        ("system.jpg", False, SYSTEM, 10, SkipReason.HIDDEN_OR_SYSTEM),
        ("hidden-empty.jpg", False, HIDDEN, 0, SkipReason.HIDDEN_OR_SYSTEM),
        ("desktop.ini", False, HIDDEN | SYSTEM, 10, SkipReason.HIDDEN_OR_SYSTEM),
        ("Desktop.INI", False, 0, 10, SkipReason.SYSTEM_NAME),
        ("thumbs.db", False, 0, 10, SkipReason.SYSTEM_NAME),
        ("thumbs.db", False, 0, 0, SkipReason.SYSTEM_NAME),
        ("video.part", False, 0, 0, SkipReason.INCOMPLETE_DOWNLOAD),
        ("file.CRDOWNLOAD", False, 0, 10, SkipReason.INCOMPLETE_DOWNLOAD),
        ("file.partial", False, 0, 10, SkipReason.INCOMPLETE_DOWNLOAD),
        ("file.download", False, 0, 10, SkipReason.INCOMPLETE_DOWNLOAD),
        ("file.tmp", False, 0, 10, SkipReason.INCOMPLETE_DOWNLOAD),
        ("empty.jpg", False, 0, 0, SkipReason.EMPTY),
        ("readonly.jpg", False, stat.FILE_ATTRIBUTE_READONLY, 10, None),
        ("archive.part.jpg", False, 0, 10, None),
    ],
)
def test_classify(name, is_dir, attributes, size, expected):
    assert classify(name, is_dir, attributes, size) == expected


def test_only_top_level_files_returned(tmp_path):
    write(tmp_path / "a.jpg")
    write(tmp_path / "b.jpg")
    write(tmp_path / "sub" / "c.jpg")
    write(tmp_path / "dups" / "a (1).jpg")

    result = scan_folder(tmp_path)

    assert names(result.entries) == ["a.jpg", "b.jpg"]
    assert reasons(result) == {"dups": SkipReason.DIRECTORY, "sub": SkipReason.DIRECTORY}
    assert result.errors == ()
    assert result.folder == tmp_path


def test_skip_reasons_on_disk(tmp_path):
    add_attributes(write(tmp_path / "hidden.jpg"), HIDDEN)
    add_attributes(write(tmp_path / "system.jpg"), SYSTEM)
    write(tmp_path / "desktop.ini")
    write(tmp_path / "Thumbs.db")
    write(tmp_path / "video.part")
    write(tmp_path / "file.crdownload")
    write(tmp_path / "empty.jpg", b"")
    write(tmp_path / "keep.jpg")

    result = scan_folder(tmp_path)

    assert names(result.entries) == ["keep.jpg"]
    assert reasons(result) == {
        "hidden.jpg": SkipReason.HIDDEN_OR_SYSTEM,
        "system.jpg": SkipReason.HIDDEN_OR_SYSTEM,
        "desktop.ini": SkipReason.SYSTEM_NAME,
        "Thumbs.db": SkipReason.SYSTEM_NAME,
        "video.part": SkipReason.INCOMPLETE_DOWNLOAD,
        "file.crdownload": SkipReason.INCOMPLETE_DOWNLOAD,
        "empty.jpg": SkipReason.EMPTY,
    }


def test_junction_is_skipped_as_link(tmp_path):
    root = tmp_path / "root"
    target = tmp_path / "target"
    write(target / "inside.jpg")
    write(root / "a.jpg")
    make_junction(root / "junction", target)

    result = scan_folder(root)

    assert names(result.entries) == ["a.jpg"]
    assert reasons(result) == {"junction": SkipReason.LINK}


def test_file_symlink_is_skipped_as_link(tmp_path):
    target = write(tmp_path / "target.jpg")
    root = tmp_path / "root"
    write(root / "a.jpg")
    try:
        os.symlink(target, root / "link.jpg")
    except OSError:
        pytest.skip("creating symlinks is not permitted on this machine")

    result = scan_folder(root)

    assert names(result.entries) == ["a.jpg"]
    assert reasons(result) == {"link.jpg": SkipReason.LINK}


def test_file_entry_data(tmp_path):
    path = write(tmp_path / "photo.jpg", b"x" * 1234)
    st = path.stat()

    (entry,) = scan_folder(tmp_path).entries

    assert entry.path == path
    assert entry.name == "photo.jpg"
    assert entry.size == 1234
    assert entry.mtime_ns == st.st_mtime_ns
    assert not str(entry.path).startswith("\\\\?\\")


def test_unicode_names_round_trip(tmp_path):
    expected = sorted(["Canción.jpg", "日本語.png", "photo 📷.jpg"])
    for name in expected:
        write(tmp_path / name)

    result = scan_folder(tmp_path)

    assert names(result.entries) == expected
    assert all(entry.path.exists() for entry in result.entries)


def test_long_path_folder(tmp_path):
    folder = tmp_path
    while len(str(folder)) < 280:
        folder = folder / ("d" * 50)
    os.makedirs(to_os_path(folder))
    for name in ("a.jpg", "a (1).jpg"):
        with open(to_os_path(folder / name), "wb") as f:
            f.write(b"data")

    result = scan_folder(folder)

    assert len(str(folder)) > 260
    assert names(result.entries) == ["a (1).jpg", "a.jpg"]
    assert all(entry.size == 4 for entry in result.entries)


def test_per_file_error_does_not_stop_scan(tmp_path, monkeypatch):
    write(tmp_path / "a.jpg")
    write(tmp_path / "broken.jpg")
    write(tmp_path / "c.jpg")
    original = scanner._stat_entry

    def flaky_stat(entry):
        if entry.name == "broken.jpg":
            raise PermissionError(13, "Access is denied")
        return original(entry)

    monkeypatch.setattr(scanner, "_stat_entry", flaky_stat)

    result = scan_folder(tmp_path)

    assert names(result.entries) == ["a.jpg", "c.jpg"]
    assert [e.path.name for e in result.errors] == ["broken.jpg"]
    assert result.errors[0].message == "Access is denied"


def test_progress_reaches_total(tmp_path):
    for name in ("a.jpg", "b.jpg", "empty.jpg"):
        write(tmp_path / name, b"" if name == "empty.jpg" else b"data")
    (tmp_path / "sub").mkdir()
    calls = []

    scan_folder(tmp_path, on_progress=lambda done, total: calls.append((done, total)))

    assert calls == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_cancel_raises(tmp_path):
    write(tmp_path / "a.jpg")
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(ScanCancelled):
        scan_folder(tmp_path, cancel=cancel)


def test_cancel_mid_scan_raises(tmp_path):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        write(tmp_path / name)
    cancel = threading.Event()

    with pytest.raises(ScanCancelled):
        scan_folder(tmp_path, on_progress=lambda done, total: cancel.set(), cancel=cancel)


def test_validate_returns_absolute_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "photos").mkdir()
    assert validate_folder(Path("photos")) == tmp_path / "photos"


def test_validate_missing_folder(tmp_path):
    with pytest.raises(FolderValidationError, match="does not exist"):
        validate_folder(tmp_path / "missing")


def test_validate_file_path(tmp_path):
    with pytest.raises(FolderValidationError, match="not a folder"):
        validate_folder(write(tmp_path / "a.jpg"))


def test_validate_junction(tmp_path):
    (tmp_path / "target").mkdir()
    make_junction(tmp_path / "junction", tmp_path / "target")
    with pytest.raises(FolderValidationError, match="link or junction"):
        validate_folder(tmp_path / "junction")


def test_validate_unlistable_folder(tmp_path, monkeypatch):
    def deny(path):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(scanner.os, "scandir", deny)
    with pytest.raises(FolderValidationError, match="Cannot read folder"):
        validate_folder(tmp_path)


def test_scan_does_not_modify_anything(tmp_path):
    write(tmp_path / "a.jpg")
    write(tmp_path / "a (1).jpg")
    write(tmp_path / "sub" / "b.jpg")
    before = snapshot(tmp_path)

    scan_folder(tmp_path)

    assert snapshot(tmp_path) == before
