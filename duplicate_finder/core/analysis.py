"""Runs the full read-only analysis of a folder: scan, group and verify."""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from duplicate_finder.core.duplicate_grouper import build_groups
from duplicate_finder.core.filename_parser import ALL_PATTERNS
from duplicate_finder.core.hasher import verify_groups
from duplicate_finder.core.models import DuplicateGroup, ScanResult
from duplicate_finder.core.scanner import scan_folder


class Phase(StrEnum):
    SCAN = "scan"
    VERIFY = "verify"


PhaseProgressCallback = Callable[[Phase, int, int], None]


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    scan: ScanResult
    groups: tuple[DuplicateGroup, ...]
    enabled: frozenset[str]
    cancelled: bool

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
) -> AnalysisResult:
    """Analyze a folder without modifying anything.

    FolderValidationError and ScanCancelled propagate. A cancelled verification
    returns a result with ``cancelled=True`` where unverified copies stay PENDING_HASH.
    """

    def report(phase: Phase) -> Callable[[int, int], None] | None:
        if on_progress is None:
            return None
        return lambda done, total: on_progress(phase, done, total)

    scan = scan_folder(folder, on_progress=report(Phase.SCAN), cancel=cancel)
    groups = build_groups(scan.entries, enabled)
    verified = verify_groups(groups, on_progress=report(Phase.VERIFY), cancel=cancel)
    return AnalysisResult(
        scan=scan,
        groups=tuple(verified),
        enabled=enabled,
        cancelled=cancel is not None and cancel.is_set(),
    )
