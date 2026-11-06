"""Lists the regular files directly inside a folder, skipping everything that must not be analyzed."""

import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path

from duplicate_finder.core.models import FileEntry, ScanError, ScanResult, SkippedEntry, SkipReason
from duplicate_finder.core.winpath import to_os_path

SYSTEM_NAMES = frozenset({"desktop.ini", "thumbs.db"})
INCOMPLETE_DOWNLOAD_EXTENSIONS = frozenset({".crdownload", ".part", ".partial", ".download", ".tmp"})

ProgressCallback = Callable[[int, int], None]


class FolderValidationError(ValueError):
    pass


class ScanCancelled(Exception):
    pass


def validate_folder(folder: Path) -> Path:
    """Return the absolute folder path, or raise FolderValidationError explaining why it can't be scanned."""
    absolute = Path(os.path.abspath(folder))
    os_path = to_os_path(absolute)
    try:
        st = os.lstat(os_path)
    except FileNotFoundError:
        raise FolderValidationError(f"Folder does not exist: {absolute}") from None
    except OSError as exc:
        raise FolderValidationError(f"Cannot access folder: {absolute} ({exc.strerror or exc})") from None

    if stat.S_ISLNK(st.st_mode) or st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise FolderValidationError(f"Folder is a link or junction, which is not supported: {absolute}")
    if not stat.S_ISDIR(st.st_mode):
        raise FolderValidationError(f"Path is not a folder: {absolute}")

    try:
        with os.scandir(os_path):
            pass
    except OSError as exc:
        raise FolderValidationError(f"Cannot read folder: {absolute} ({exc.strerror or exc})") from None
    return absolute


def classify(name: str, is_dir: bool, attributes: int, size: int,
             excluded_extensions: frozenset[str] = frozenset()) -> SkipReason | None:
    """Return why an entry must be skipped, or None if it is a file to analyze. First rule wins."""
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return SkipReason.LINK
    if is_dir:
        return SkipReason.DIRECTORY
    if attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM):
        return SkipReason.HIDDEN_OR_SYSTEM
    if name.casefold() in SYSTEM_NAMES:
        return SkipReason.SYSTEM_NAME
    if os.path.splitext(name)[1].casefold() in excluded_extensions:
        return SkipReason.EXCLUDED_EXTENSION
    if os.path.splitext(name)[1].casefold() in INCOMPLETE_DOWNLOAD_EXTENSIONS:
        return SkipReason.INCOMPLETE_DOWNLOAD
    if size == 0:
        return SkipReason.EMPTY
    return None


def _created_ns(st: object) -> int | None:
    value = getattr(st, "st_birthtime_ns", None)
    if isinstance(value, int):
        return value
    return None


def _stat_entry(entry: os.DirEntry) -> os.stat_result:
    return entry.stat(follow_symlinks=False)


def scan_folder(
    folder: Path,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    excluded_extensions: frozenset[str] = frozenset(),
) -> ScanResult:
    folder = validate_folder(folder)
    with os.scandir(to_os_path(folder)) as iterator:
        dir_entries = list(iterator)

    entries: list[FileEntry] = []
    skipped: list[SkippedEntry] = []
    errors: list[ScanError] = []
    total = len(dir_entries)

    for done, dir_entry in enumerate(dir_entries, start=1):
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()

        path = folder / dir_entry.name
        try:
            st = _stat_entry(dir_entry)
        except OSError as exc:
            errors.append(ScanError(path, exc.strerror or str(exc)))
        else:
            reason = classify(
                dir_entry.name, stat.S_ISDIR(st.st_mode), st.st_file_attributes, st.st_size, excluded_extensions,
            )
            if reason is None:
                entries.append(FileEntry(
                    path, dir_entry.name, st.st_size, st.st_mtime_ns, _created_ns(st),
                ))
            else:
                skipped.append(SkippedEntry(path, dir_entry.name, reason))

        if on_progress is not None:
            on_progress(done, total)

    return ScanResult(
        folder=folder,
        entries=tuple(sorted(entries, key=lambda e: e.name)),
        skipped=tuple(sorted(skipped, key=lambda e: e.name)),
        errors=tuple(sorted(errors, key=lambda e: e.path.name)),
    )
