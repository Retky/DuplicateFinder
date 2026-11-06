"""Collects the folder trees folder matching is allowed to see.

This does not compare folders. The selected folder itself is never a candidate,
and the walk does not enter ``dups`` or a link.
"""

import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from duplicate_finder.core.models import FileEntry
from duplicate_finder.core.scanner import INCOMPLETE_DOWNLOAD_EXTENSIONS, _created_ns, validate_folder
from duplicate_finder.core.winpath import to_os_path

DUPS_NAME = "dups"
METADATA_NAMES = frozenset({"desktop.ini", "thumbs.db", "ehthumbs.db"})
_HIDDEN = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM


@dataclass(frozen=True, slots=True)
class FolderFile:
    """One eligible file, with its path relative to the candidate folder."""

    relative: tuple[str, ...]
    entry: FileEntry


@dataclass(frozen=True, slots=True)
class FolderTree:
    """The eligible files inside one candidate folder."""

    path: Path
    files: tuple[FolderFile, ...]
    hidden_included: bool


def scan_folder_trees(
    folder: Path,
    excluded_extensions: frozenset[str] = frozenset(),
    match_subfolders: bool = False,
    cancel: threading.Event | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[FolderTree, ...]:
    """Return one tree per candidate. Nested files are recorded even when nested folders are not candidates."""
    folder = validate_folder(folder)
    candidates = _candidates(folder, match_subfolders, cancel)
    trees: list[FolderTree] = []
    total = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        trees.append(_collect(candidate, excluded_extensions, cancel))
        if on_progress is not None and total:
            on_progress(index, total)
    return tuple(trees)


def _candidates(folder: Path, match_subfolders: bool, cancel: threading.Event | None) -> list[Path]:
    direct = _child_directories(folder)
    if not match_subfolders:
        return direct
    found: list[Path] = []

    def add(directories: list[Path]) -> None:
        for path in directories:
            if cancel is not None and cancel.is_set():
                return
            found.append(path)
            add(_child_directories(path))

    add(direct)
    return found


def _child_directories(folder: Path) -> list[Path]:
    children: list[Path] = []
    for entry in _entries(folder):
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if _is_link(st) or not stat.S_ISDIR(st.st_mode):
            continue
        if entry.name.casefold() == DUPS_NAME:
            continue
        children.append(folder / entry.name)
    return sorted(children, key=lambda path: path.name.casefold())


def _collect(candidate: Path, excluded_extensions: frozenset[str], cancel: threading.Event | None) -> FolderTree:
    files: list[FolderFile] = []
    hidden = False

    def walk(directory: Path, prefix: tuple[str, ...]) -> None:
        nonlocal hidden
        for entry in _entries(directory):
            if cancel is not None and cancel.is_set():
                return
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if _is_link(st):
                continue
            if stat.S_ISDIR(st.st_mode):
                if entry.name.casefold() == DUPS_NAME:
                    continue
                walk(directory / entry.name, prefix + (entry.name,))
                continue
            if entry.name.casefold() in METADATA_NAMES:
                continue
            extension = os.path.splitext(entry.name)[1].casefold()
            if extension in excluded_extensions or extension in INCOMPLETE_DOWNLOAD_EXTENSIONS or st.st_size == 0:
                continue
            if st.st_file_attributes & _HIDDEN:
                hidden = True
            files.append(FolderFile(
                prefix + (entry.name,),
                FileEntry(directory / entry.name, entry.name, st.st_size, st.st_mtime_ns, _created_ns(st)),
            ))

    walk(candidate, ())
    return FolderTree(
        candidate,
        tuple(sorted(files, key=lambda item: item.relative)),
        hidden,
    )


def _entries(folder: Path) -> list[os.DirEntry]:
    with os.scandir(to_os_path(folder)) as iterator:
        return list(iterator)


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
