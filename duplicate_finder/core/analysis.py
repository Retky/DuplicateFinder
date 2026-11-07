"""Runs the full read-only analysis of a folder: scan, group and verify."""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from duplicate_finder.core.content_grouper import build_content_groups
from duplicate_finder.core.duplicate_grouper import build_groups
from duplicate_finder.core.filename_parser import ALL_PATTERNS
from duplicate_finder.core.folder_groups import FolderGroup, group_folder_sets
from duplicate_finder.core.folder_scan import files_from_observations, observe_folder, trees_from_observations
from duplicate_finder.core.hasher import verify_groups
from duplicate_finder.core.models import DuplicateGroup, ScanResult
from duplicate_finder.core.scanner import scan_folder


class Phase(StrEnum):
    SCAN = "scan"
    VERIFY = "verify"
    FOLDERS = "folders"


PhaseProgressCallback = Callable[[Phase, int, int], None]


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    scan: ScanResult
    groups: tuple[DuplicateGroup, ...]
    enabled: frozenset[str]
    cancelled: bool
    folder_groups: tuple[FolderGroup, ...] = ()
    folders_hidden: bool = False

    @property
    def folder(self) -> Path:
        return self.scan.folder

    @property
    def groups_with_confirmed(self) -> int:
        return sum(1 for group in self.groups if group.confirmed)

    @property
    def confirmed_count(self) -> int:
        return sum(len(group.confirmed) for group in self.groups)

    @property
    def movable_bytes(self) -> int:
        return sum(group.movable_size for group in self.groups)


def analyze(
    folder: Path,
    enabled: frozenset[str] = ALL_PATTERNS,
    on_progress: PhaseProgressCallback | None = None,
    cancel: threading.Event | None = None,
    *,
    match_names: bool = True,
    match_content: bool = True,
    excluded_extensions: frozenset[str] = frozenset(),
    match_folders: bool = False,
    match_subfolders: bool = False,
    match_nested_files: bool = False,
) -> AnalysisResult:
    """Analyze a folder without modifying anything.

    FolderValidationError and ScanCancelled propagate. A cancelled verification
    returns a result with ``cancelled=True`` where unverified copies stay PENDING_HASH.
    Content groups are omitted when content hashing is cancelled. Folder groups are
    included only when folder matching is on, and a cancelled folder comparison
    leaves those groups empty. ``match_nested_files`` includes files inside child
    folders. It does not change which folders are candidates.
    """

    def report(phase: Phase) -> Callable[[int, int], None] | None:
        if on_progress is None:
            return None
        return lambda done, total: on_progress(phase, done, total)

    observations = None
    if match_nested_files or match_folders:
        folder, observations = observe_folder(
            folder, on_progress=report(Phase.SCAN), cancel=cancel,
        )
        scan = files_from_observations(
            folder, observations, excluded_extensions=excluded_extensions, nested=match_nested_files,
        )
    else:
        scan = scan_folder(
            folder, on_progress=report(Phase.SCAN), cancel=cancel, excluded_extensions=excluded_extensions,
        )
    digests: dict[Path, str] = {}
    name_groups = build_groups(scan.entries, enabled, root=scan.folder) if match_names else []
    verified = verify_groups(name_groups, on_progress=report(Phase.VERIFY), cancel=cancel, digests=digests)
    cancelled = cancel is not None and cancel.is_set()
    content: list[DuplicateGroup] = []
    if match_content and not cancelled:
        content, content_cancelled = build_content_groups(
            scan.entries, verified, on_progress=report(Phase.VERIFY), cancel=cancel, digests=digests,
        )
        cancelled = cancelled or content_cancelled
    folder_groups: tuple[FolderGroup, ...] = ()
    folders_hidden = False
    if match_folders and not cancelled and observations is not None:
        folder_report = report(Phase.FOLDERS)
        if folder_report is not None:
            folder_report(0, 1)
        trees = trees_from_observations(
            observations, excluded_extensions=excluded_extensions, match_subfolders=match_subfolders,
        )
        folders_hidden = any(tree.hidden_included for tree in trees)
        if cancel is not None and cancel.is_set():
            cancelled = True
        else:
            folder_groups = group_folder_sets(trees, cancel, on_progress=folder_report, digests=digests)
            if cancel is not None and cancel.is_set():
                folder_groups = ()
                cancelled = True
    return AnalysisResult(
        scan=scan,
        groups=tuple(verified) + tuple(content),
        enabled=enabled,
        cancelled=cancelled,
        folder_groups=folder_groups,
        folders_hidden=folders_hidden,
    )
