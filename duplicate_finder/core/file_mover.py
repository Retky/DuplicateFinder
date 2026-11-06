"""Moves confirmed copies and selected duplicate folders without ever overwriting or renaming."""

import os
import stat
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from duplicate_finder.core.analysis import AnalysisResult
from duplicate_finder.core.folder_groups import FolderGroup
from duplicate_finder.core.models import (
    Candidate,
    DuplicateGroup,
    FileEntry,
    GroupKind,
    MoveOutcome,
    PlannedFolderMove,
    PlannedMove,
    is_movable,
)
from duplicate_finder.core.operation_logger import OperationLogger
from duplicate_finder.core.scanner import validate_folder
from duplicate_finder.core.winpath import to_os_path

DEST_FOLDER_NAME = "dups"
NAME_FOLDER_NAME = "01_Name"
CONTENT_FOLDER_NAME = "02_Content"
FOLDER_FOLDER_NAME = "03_Folders"
LOGS_FOLDER_NAME = "_logs"
OTHER_NAMES_FILE = "other-names.txt"

ProgressCallback = Callable[[int, int], None]


class DestinationError(Exception):
    """The destination folder can't be used safely. Nothing was moved and nothing was logged."""


@dataclass(frozen=True, slots=True)
class MoveResult:
    move: PlannedMove
    outcome: MoveOutcome
    detail: str | None


@dataclass(frozen=True, slots=True)
class FolderMoveResult:
    """One logged result for a folder rename."""

    move: PlannedFolderMove
    outcome: MoveOutcome
    detail: str | None


@dataclass(frozen=True, slots=True)
class NameRecord:
    """One folder name written, or not written, into the kept folder's name file."""

    original: Path
    path: Path
    name: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MoveReport:
    dest_folder: Path
    log_path: Path | None
    results: tuple[MoveResult, ...]
    cancelled: bool
    folder_results: tuple[FolderMoveResult, ...] = ()
    name_records: tuple[NameRecord, ...] = ()

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


def folder_destination(folder: Path) -> Path:
    """Folder for a selected duplicate directory. It is not created here."""
    return folder / DEST_FOLDER_NAME / FOLDER_FOLDER_NAME


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


def _record_folder(
    logger: OperationLogger,
    move: PlannedFolderMove,
    outcome: MoveOutcome,
    detail: str | None,
) -> FolderMoveResult:
    logger.folder_result(move, outcome, detail)
    return FolderMoveResult(move, outcome, detail)


def _is_inside(path: Path, folder: Path) -> bool:
    return path != folder and path.is_relative_to(folder)


def _build_folder_plan(
    analysis: AnalysisResult,
    folders: Sequence[Path],
    groups: Sequence[FolderGroup],
    dest_folder: Path,
) -> list[PlannedFolderMove]:
    originals = {group.original for group in groups}
    duplicates = {duplicate: group.original for group in groups for duplicate in group.duplicates}
    seen: set[Path] = set()
    plan: list[PlannedFolderMove] = []
    for source in folders:
        if source in seen:
            raise ValueError(f"folder selected twice: {source}")
        seen.add(source)
        if source in originals:
            raise ValueError(f"the original folder cannot be moved: {source}")
        original = duplicates.get(source)
        if original is None:
            raise ValueError(f"folder is not a duplicate in this analysis: {source}")
        if source == analysis.folder or not source.is_relative_to(analysis.folder):
            raise ValueError(f"folder is outside the analyzed folder: {source}")
        plan.append(PlannedFolderMove(original, source, dest_folder / source.name))
    for path in seen:
        if any(path != other and path.is_relative_to(other) for other in seen):
            raise ValueError(f"folder is inside another selected folder: {path}")
    return plan


def _without_folder_files(selected: Sequence[Candidate], folders: Sequence[Path]) -> list[Candidate]:
    return [
        candidate for candidate in selected
        if not any(_is_inside(candidate.entry.path, folder) for folder in folders)
    ]


def _without_folder_content(content: Sequence[ContentMove], folders: Sequence[Path]) -> list[ContentMove]:
    kept: list[ContentMove] = []
    for move in content:
        selected = tuple(
            candidate for candidate in move.selected
            if not any(_is_inside(candidate.entry.path, folder) for folder in folders)
        )
        if selected:
            kept.append(ContentMove(move.group_id, move.keeper, selected))
    return kept


def _attempt_folder(move: PlannedFolderMove) -> tuple[MoveOutcome, str | None]:
    try:
        current = os.lstat(to_os_path(move.original))
    except FileNotFoundError:
        return MoveOutcome.SKIPPED_ORIGINAL_MISSING, "original no longer exists"
    except OSError as exc:
        return MoveOutcome.ERROR, _error_text(exc)
    if not stat.S_ISDIR(current.st_mode):
        return MoveOutcome.SKIPPED_ORIGINAL_MISSING, "original no longer exists"

    try:
        current = os.lstat(to_os_path(move.source))
    except FileNotFoundError:
        return MoveOutcome.SKIPPED_CHANGED, "folder no longer exists"
    except OSError as exc:
        return MoveOutcome.ERROR, _error_text(exc)
    if not stat.S_ISDIR(current.st_mode):
        return MoveOutcome.SKIPPED_CHANGED, "folder no longer exists"

    try:
        os.rename(to_os_path(move.source), to_os_path(move.dest_path))
    except FileExistsError:
        return MoveOutcome.SKIPPED_EXISTS, "a folder with the same name is already in the destination"
    except OSError as exc:
        return MoveOutcome.ERROR, _error_text(exc)
    return MoveOutcome.MOVED, None


def _record_folder_name(move: PlannedFolderMove) -> NameRecord:
    """Append the moved folder's name to the kept folder. A failed write leaves the move done."""
    path = move.original / OTHER_NAMES_FILE
    try:
        _append_name(path, move.source.name)
    except OSError as exc:
        return NameRecord(move.original, path, move.source.name, _error_text(exc))
    return NameRecord(move.original, path, move.source.name)


def _append_name(path: Path, name: str) -> None:
    os_path = to_os_path(path)
    prefix = _leading_newline(os_path)
    with open(os_path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{prefix}{name}\n")
        handle.flush()
        os.fsync(handle.fileno())


def _leading_newline(os_path: str) -> str:
    try:
        with open(os_path, "rb") as existing:
            existing.seek(0, os.SEEK_END)
            if existing.tell() == 0:
                return ""
            existing.seek(-1, os.SEEK_END)
            if existing.read(1) in (b"\n", b"\r"):
                return ""
    except FileNotFoundError:
        return ""
    return "\n"


def move_duplicates(
    analysis: AnalysisResult,
    selected: Sequence[Candidate],
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    clock: Callable[[], datetime] = _now,
    *,
    content: Sequence[ContentMove] = (),
    folders: Sequence[Path] = (),
    folder_groups: Sequence[FolderGroup] = (),
) -> MoveReport:
    """Move selected copies and selected duplicate folders.

    The file plan and the folder plan are validated before any destination is
    created. A file inside a selected folder is left out of the file plan.
    ``02_Content`` is created only for a content plan, and ``03_Folders`` only
    for a folder plan. Every planned file and folder gets one logged result.
    Each folder that moves has its name appended to ``other-names.txt`` in the
    kept folder before the next move starts.
    """
    dups = analysis.folder / DEST_FOLDER_NAME
    name_folder = dups / NAME_FOLDER_NAME
    content_folder = dups / CONTENT_FOLDER_NAME
    folders_folder = dups / FOLDER_FOLDER_NAME
    folder_plan = _build_folder_plan(analysis, folders, folder_groups, folders_folder)
    selected_folders = [move.source for move in folder_plan]
    name_plan = _build_plan(analysis, _without_folder_files(selected, selected_folders), name_folder)
    content_plan = _build_content_plan(
        analysis, _without_folder_content(content, selected_folders), content_folder,
    )
    plan = name_plan + content_plan
    destinations = [name_plan, content_plan, folder_plan]
    if sum(bool(item) for item in destinations) > 1:
        report_dest = dups
    elif folder_plan:
        report_dest = folders_folder
    elif content_plan:
        report_dest = content_folder
    else:
        report_dest = name_folder
    if not plan and not folder_plan:
        return MoveReport(report_dest, None, (), False)

    folder = validate_folder(analysis.folder)
    if folder != analysis.folder:
        raise ValueError(f"analyzed folder is not absolute: {analysis.folder}")
    _prepare_directory(dups)
    if name_plan:
        _prepare_directory(name_folder)
    if content_plan:
        _prepare_directory(content_folder)
    if folder_plan:
        _prepare_directory(folders_folder)

    logger = OperationLogger.create(dups / LOGS_FOLDER_NAME, clock)
    results: list[MoveResult] = []
    folder_results: list[FolderMoveResult] = []
    name_records: list[NameRecord] = []
    resolved: set[Path] = set()
    resolved_folders: set[Path] = set()
    cancelled = False
    aborted: BaseException | None = None
    work: list[tuple[str, PlannedMove | PlannedFolderMove]] = [
        *([("folder", move) for move in folder_plan]),
        *([("file", move) for move in plan]),
    ]
    total = len(work)
    try:
        logger.start(folder, report_dest, analysis.enabled, plan, folder_plan)
        for done, (kind, move) in enumerate(work, start=1):
            if cancel is not None and cancel.is_set():
                cancelled = True
                rest = work[done - 1:]
                for remaining_done, (remaining_kind, remaining) in enumerate(rest, start=done):
                    _record_cancelled(
                        logger, remaining_kind, remaining, results, folder_results, resolved, resolved_folders,
                    )
                    if on_progress is not None:
                        on_progress(remaining_done, total)
                break

            if kind == "folder":
                assert isinstance(move, PlannedFolderMove)
                outcome, detail = _attempt_folder(move)
                folder_results.append(_record_folder(logger, move, outcome, detail))
                resolved_folders.add(move.source)
                if outcome is MoveOutcome.MOVED:
                    name_records.append(_record_folder_name(move))
            else:
                assert isinstance(move, PlannedMove)
                outcome, detail = _attempt(move)
                results.append(_record(logger, move, outcome, detail))
                resolved.add(move.candidate.entry.path)
            if on_progress is not None:
                on_progress(done, total)
    except Exception as exc:
        aborted = exc
    finally:
        detail = f"aborted: {aborted}" if aborted is not None else "aborted"
        for move in folder_plan:
            if move.source in resolved_folders:
                continue
            try:
                folder_results.append(_record_folder(logger, move, MoveOutcome.ERROR, detail))
            except Exception:
                pass
            resolved_folders.add(move.source)
        for move in plan:
            source = move.candidate.entry.path
            if source in resolved:
                continue
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
    return MoveReport(
        report_dest, logger.path, tuple(results), cancelled, tuple(folder_results), tuple(name_records),
    )


def _record_cancelled(
    logger: OperationLogger,
    kind: str,
    move: PlannedMove | PlannedFolderMove,
    results: list[MoveResult],
    folder_results: list[FolderMoveResult],
    resolved: set[Path],
    resolved_folders: set[Path],
) -> None:
    if kind == "folder":
        assert isinstance(move, PlannedFolderMove)
        folder_results.append(_record_folder(logger, move, MoveOutcome.CANCELLED, None))
        resolved_folders.add(move.source)
        return
    assert isinstance(move, PlannedMove)
    results.append(_record(logger, move, MoveOutcome.CANCELLED, None))
    resolved.add(move.candidate.entry.path)
