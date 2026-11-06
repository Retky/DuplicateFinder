"""Moves confirmed, selected copies into ``dups/01_Name`` without ever overwriting or renaming."""

import os
import stat
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from duplicate_finder.core.analysis import AnalysisResult
from duplicate_finder.core.models import (
    Candidate,
    DuplicateGroup,
    FileEntry,
    GroupKind,
    MoveOutcome,
    PlannedMove,
    is_movable,
)
from duplicate_finder.core.operation_logger import OperationLogger
from duplicate_finder.core.scanner import validate_folder
from duplicate_finder.core.winpath import to_os_path

DEST_FOLDER_NAME = "dups"
NAME_FOLDER_NAME = "01_Name"
CONTENT_FOLDER_NAME = "02_Content"
LOGS_FOLDER_NAME = "_logs"

ProgressCallback = Callable[[int, int], None]


class DestinationError(Exception):
    """The destination folder can't be used safely. Nothing was moved and nothing was logged."""


@dataclass(frozen=True, slots=True)
class MoveResult:
    move: PlannedMove
    outcome: MoveOutcome
    detail: str | None


@dataclass(frozen=True, slots=True)
class MoveReport:
    dest_folder: Path
    log_path: Path | None
    results: tuple[MoveResult, ...]
    cancelled: bool

    @property
    def totals(self) -> dict[str, int]:
        totals = {outcome.value: 0 for outcome in MoveOutcome}
        for result in self.results:
            totals[result.outcome.value] += 1
        return totals

    @property
    def moved_count(self) -> int:
        return sum(1 for result in self.results if result.outcome is MoveOutcome.MOVED)

    @property
    def moved_bytes(self) -> int:
        return sum(result.move.candidate.entry.size for result in self.results if result.outcome is MoveOutcome.MOVED)


def _now() -> datetime:
    return datetime.now().astimezone()


def _candidates_by_path(groups: Sequence[DuplicateGroup]) -> dict[Path, tuple[DuplicateGroup, Candidate]]:
    return {candidate.entry.path: (group, candidate) for group in groups for candidate in group.candidates}


def _build_plan(analysis: AnalysisResult, selected: Sequence[Candidate], dest_folder: Path) -> list[PlannedMove]:
    known = _candidates_by_path(analysis.groups)
    seen: set[Path] = set()
    plan: list[PlannedMove] = []
    for candidate in selected:
        source = candidate.entry.path
        if source in seen:
            raise ValueError(f"candidate selected twice: {source}")
        seen.add(source)

        found = known.get(source)
        if found is None or found[1] != candidate:
            raise ValueError(f"candidate is not part of this analysis: {source}")
        group = found[0]
        if not is_movable(candidate):
            raise ValueError(f"only confirmed candidates can be moved: {source}")
        if group.original is None:
            raise ValueError(f"group {group.group_id} has no original: {source}")
        if source.parent != analysis.folder:
            raise ValueError(f"candidate is outside the analyzed folder: {source}")
        plan.append(PlannedMove(group.group_id, group.original, candidate, dest_folder / candidate.entry.name))
    return plan


@dataclass(frozen=True, slots=True)
class ContentMove:
    """Files from one content group to move after the user has chosen ``keeper``."""

    group_id: str
    keeper: FileEntry
    selected: tuple[Candidate, ...]


def _name_originals(groups: Sequence[DuplicateGroup]) -> set[Path]:
    return {group.original.path for group in groups if group.kind is GroupKind.NAME and group.original is not None}


def _build_content_plan(analysis: AnalysisResult, content: Sequence[ContentMove], dest_folder: Path) -> list[PlannedMove]:
    groups = {group.group_id: group for group in analysis.groups}
    protected = _name_originals(analysis.groups)
    plan: list[PlannedMove] = []
    seen: set[Path] = set()
    for move in content:
        group = groups.get(move.group_id)
        if group is None or group.kind is not GroupKind.CONTENT:
            raise ValueError(f"content group not found: {move.group_id}")
        members = {candidate.entry.path: candidate for candidate in group.candidates}
        keeper = members.get(move.keeper.path)
        if keeper is None:
            raise ValueError(f"keeper is not in {move.group_id}: {move.keeper.path}")
        if not move.selected:
            raise ValueError(f"content group {move.group_id} has no files selected")
        for candidate in move.selected:
            source = candidate.entry.path
            found = members.get(source)
            if found is None:
                raise ValueError(f"candidate is not in {move.group_id}: {source}")
            if source == move.keeper.path:
                raise ValueError(f"the keeper cannot be moved: {source}")
            if source in protected:
                raise ValueError(f"a name original cannot be moved: {source}")
            if source in seen:
                raise ValueError(f"candidate selected twice: {source}")
            if source.parent != analysis.folder:
                raise ValueError(f"candidate is outside the analyzed folder: {source}")
            seen.add(source)
            plan.append(PlannedMove(group.group_id, move.keeper, found, dest_folder / found.entry.name))
    return plan


def _require_real_directory(path: Path, st: os.stat_result) -> None:
    linked = stat.S_ISLNK(st.st_mode) or bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    if linked or not stat.S_ISDIR(st.st_mode):
        kind = "a link or junction" if linked else "not a folder"
        raise DestinationError(f"Destination is {kind}, so nothing was moved: {path}")


def name_destination(folder: Path) -> Path:
    """Folder for copies confirmed by name. It is not created here."""
    return folder / DEST_FOLDER_NAME / NAME_FOLDER_NAME


def content_destination(folder: Path) -> Path:
    """Folder for copies confirmed by content. It is not created here."""
    return folder / DEST_FOLDER_NAME / CONTENT_FOLDER_NAME


def _prepare_directory(dest: Path) -> None:
    os_dest = to_os_path(dest)
    try:
        st = os.lstat(os_dest)
    except FileNotFoundError:
        try:
            os.mkdir(os_dest)
        except FileExistsError:
            st = os.lstat(os_dest)
        else:
            st = os.lstat(os_dest)
    _require_real_directory(dest, st)


def _error_text(exc: OSError) -> str:
    return exc.strerror or str(exc)


def _attempt(move: PlannedMove) -> tuple[MoveOutcome, str | None]:
    original = move.original
    try:
        current = os.lstat(to_os_path(original.path))
    except FileNotFoundError:
        return MoveOutcome.SKIPPED_ORIGINAL_MISSING, "original no longer exists"
    except OSError as exc:
        return MoveOutcome.ERROR, _error_text(exc)
    if current.st_size != original.size or current.st_mtime_ns != original.mtime_ns:
        return MoveOutcome.SKIPPED_CHANGED, "original changed since scan"

    entry = move.candidate.entry
    try:
        current = os.lstat(to_os_path(entry.path))
    except FileNotFoundError:
        return MoveOutcome.SKIPPED_CHANGED, "file no longer exists"
    except OSError as exc:
        return MoveOutcome.ERROR, _error_text(exc)
    if current.st_size != entry.size or current.st_mtime_ns != entry.mtime_ns:
        return MoveOutcome.SKIPPED_CHANGED, "file changed since scan"

    try:
        os.rename(to_os_path(entry.path), to_os_path(move.dest_path))
    except FileExistsError:
        return MoveOutcome.SKIPPED_EXISTS, "a file with the same name is already in the destination"
    except OSError as exc:
        return MoveOutcome.ERROR, _error_text(exc)
    return MoveOutcome.MOVED, None


def _record(logger: OperationLogger, move: PlannedMove, outcome: MoveOutcome, detail: str | None) -> MoveResult:
    logger.result(move, outcome, detail)
    return MoveResult(move, outcome, detail)


def move_duplicates(
    analysis: AnalysisResult,
    selected: Sequence[Candidate],
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    clock: Callable[[], datetime] = _now,
    *,
    content: Sequence[ContentMove] = (),
) -> MoveReport:
    """Move selected name copies into ``01_Name`` and an explicit content selection into ``02_Content``.

    Every planned file gets exactly one logged result, including when the run is
    cancelled or fails unexpectedly. ``02_Content`` is created only for a content plan.
    """
    dups = analysis.folder / DEST_FOLDER_NAME
    name_folder = dups / NAME_FOLDER_NAME
    content_folder = dups / CONTENT_FOLDER_NAME
    name_plan = _build_plan(analysis, selected, name_folder)
    content_plan = _build_content_plan(analysis, content, content_folder)
    plan = name_plan + content_plan
    if name_plan and content_plan:
        report_dest = dups
    elif content_plan:
        report_dest = content_folder
    else:
        report_dest = name_folder
    if not plan:
        return MoveReport(report_dest, None, (), False)

    folder = validate_folder(analysis.folder)
    if folder != analysis.folder:
        raise ValueError(f"analyzed folder is not absolute: {analysis.folder}")
    _prepare_directory(dups)
    if name_plan:
        _prepare_directory(name_folder)
    if content_plan:
        _prepare_directory(content_folder)

    logger = OperationLogger.create(dups / LOGS_FOLDER_NAME, clock)
    results: list[MoveResult] = []
    resolved: set[Path] = set()
    cancelled = False
    aborted: BaseException | None = None
    total = len(plan)
    try:
        logger.start(folder, report_dest, analysis.enabled, plan)
        for done, move in enumerate(plan, start=1):
            if cancel is not None and cancel.is_set():
                cancelled = True
                for remaining_done, remaining in enumerate(plan[done - 1:], start=done):
                    results.append(_record(logger, remaining, MoveOutcome.CANCELLED, None))
                    resolved.add(remaining.candidate.entry.path)
                    if on_progress is not None:
                        on_progress(remaining_done, total)
                break

            outcome, detail = _attempt(move)
            results.append(_record(logger, move, outcome, detail))
            resolved.add(move.candidate.entry.path)
            if on_progress is not None:
                on_progress(done, total)
    except Exception as exc:
        aborted = exc
    finally:
        for move in plan:
            source = move.candidate.entry.path
            if source in resolved:
                continue
            detail = f"aborted: {aborted}" if aborted is not None else "aborted"
            try:
                results.append(_record(logger, move, MoveOutcome.ERROR, detail))
            except Exception:
                pass
            resolved.add(source)
        try:
            logger.finish(cancelled=cancelled)
        finally:
            logger.close()

    if aborted is not None:
        raise aborted
    return MoveReport(report_dest, logger.path, tuple(results), cancelled)
