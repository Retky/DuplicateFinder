"""Collects the folder trees folder matching is allowed to see.

This does not compare folders. The selected folder itself is never a candidate,
and the walk does not enter ``dups`` or a link.
"""

import os
import stat
import threading
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from duplicate_finder.core.models import FileEntry, ScanError, ScanResult, SkippedEntry, SkipReason
from duplicate_finder.core.scanner import (
    INCOMPLETE_DOWNLOAD_EXTENSIONS,
    ScanCancelled,
    _created_ns,
    classify,
    validate_folder,
)
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


@dataclass(frozen=True, slots=True)
class Observation:
    """One directory entry recorded by the shared walk, before either filter."""

    path: Path
    name: str
    relative: tuple[str, ...]
    is_dir: bool
    attributes: int
    size: int
    mtime_ns: int
    created_ns: int | None
    error: str | None = None


def observe_folder(
    folder: Path,
    on_progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[Path, tuple[Observation, ...]]:
    """Record every entry under ``folder``.

    Links and junctions are recorded and not followed. ``dups`` is recorded and
    not entered. Hidden, system-name, extension, and empty rules are not applied.
    """
    folder = validate_folder(folder)
    found: list[Observation] = []

    def walk(directory: Path, prefix: tuple[str, ...]) -> None:
        with os.scandir(to_os_path(directory)) as iterator:
            entries = list(iterator)
        for dir_entry in entries:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled()
            path = directory / dir_entry.name
            relative = prefix + (dir_entry.name,)
            try:
                st = dir_entry.stat(follow_symlinks=False)
            except OSError as exc:
                found.append(Observation(
                    path, dir_entry.name, relative, False, 0, 0, 0, None, exc.strerror or str(exc),
                ))
                continue
            linked = stat.S_ISLNK(st.st_mode) or bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
            is_dir = stat.S_ISDIR(st.st_mode) and not linked
            found.append(Observation(
                path, dir_entry.name, relative, is_dir, st.st_file_attributes,
                st.st_size, st.st_mtime_ns, _created_ns(st),
            ))
            if is_dir and dir_entry.name.casefold() != DUPS_NAME:
                walk(path, relative)

    walk(folder, ())
    if on_progress is not None and found:
        total = len(found)
        for index in range(1, total + 1):
            on_progress(index, total)
    return folder, tuple(found)


def files_from_observations(
    folder: Path,
    observations: Sequence[Observation],
    excluded_extensions: frozenset[str] = frozenset(),
    nested: bool = False,
) -> ScanResult:
    """Keep the files ``classify`` accepts.

    With ``nested`` off, only direct children are kept. Nested directories are
    not listed as skipped entries.
    """
    entries: list[FileEntry] = []
    skipped: list[SkippedEntry] = []
    errors: list[ScanError] = []
    for obs in observations:
        if not nested and len(obs.relative) != 1:
            continue
        if obs.error is not None:
            errors.append(ScanError(obs.path, obs.error))
            continue
        reason = classify(obs.name, obs.is_dir, obs.attributes, obs.size, excluded_extensions)
        if reason is SkipReason.DIRECTORY and len(obs.relative) > 1:
            continue
        if reason is None:
            entries.append(FileEntry(obs.path, obs.name, obs.size, obs.mtime_ns, obs.created_ns))
        else:
            skipped.append(SkippedEntry(obs.path, obs.name, reason))
    return ScanResult(
        folder=folder,
        entries=tuple(sorted(entries, key=lambda entry: entry.name)),
        skipped=tuple(sorted(skipped, key=lambda entry: entry.name)),
        errors=tuple(sorted(errors, key=lambda entry: entry.path.name)),
    )


def trees_from_observations(
    observations: Sequence[Observation],
    excluded_extensions: frozenset[str] = frozenset(),
    match_subfolders: bool = False,
) -> tuple[FolderTree, ...]:
    """Build candidate trees. ``match_subfolders`` alone decides which directories are candidates."""
    directories = [
        obs for obs in observations
        if obs.error is None and obs.is_dir and obs.name.casefold() != DUPS_NAME
    ]
    if not match_subfolders:
        directories = [obs for obs in directories if len(obs.relative) == 1]
    eligible = [obs for obs in observations if _folder_file(obs, excluded_extensions)]
    trees: list[FolderTree] = []
    for candidate in _ordered_directories(directories):
        prefix = candidate.relative
        files: list[FolderFile] = []
        hidden = False
        for obs in eligible:
            if len(obs.relative) <= len(prefix) or obs.relative[:len(prefix)] != prefix:
                continue
            if obs.attributes & _HIDDEN:
                hidden = True
            files.append(FolderFile(
                obs.relative[len(prefix):],
                FileEntry(obs.path, obs.name, obs.size, obs.mtime_ns, obs.created_ns),
            ))
        trees.append(FolderTree(
            candidate.path,
            tuple(sorted(files, key=lambda item: item.relative)),
            hidden,
        ))
    return tuple(trees)


def scan_folder_trees(
    folder: Path,
    excluded_extensions: frozenset[str] = frozenset(),
    match_subfolders: bool = False,
    cancel: threading.Event | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[FolderTree, ...]:
    """Return one tree per candidate. Nested files are recorded even when nested folders are not candidates."""
    _folder, observations = observe_folder(folder, on_progress=on_progress, cancel=cancel)
    return trees_from_observations(observations, excluded_extensions, match_subfolders)


def _folder_file(obs: Observation, excluded_extensions: frozenset[str]) -> bool:
    if obs.error is not None or obs.is_dir or obs.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    if obs.name.casefold() in METADATA_NAMES:
        return False
    extension = os.path.splitext(obs.name)[1].casefold()
    if extension in excluded_extensions or extension in INCOMPLETE_DOWNLOAD_EXTENSIONS or obs.size == 0:
        return False
    return True


def _ordered_directories(directories: Sequence[Observation]) -> list[Observation]:
    by_parent: dict[tuple[str, ...], list[Observation]] = defaultdict(list)
    for obs in directories:
        by_parent[obs.relative[:-1]].append(obs)
    ordered: list[Observation] = []

    def add(prefix: tuple[str, ...]) -> None:
        children = sorted(by_parent.get(prefix, []), key=lambda obs: obs.name.casefold())
        for child in children:
            ordered.append(child)
            add(child.relative)

    add(())
    return ordered
