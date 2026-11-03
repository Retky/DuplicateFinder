"""Pure presentation rules shared by the window. No Tkinter imports."""

from dataclasses import dataclass
from pathlib import Path

from duplicate_finder.core.analysis import Phase
from duplicate_finder.core.file_mover import DEST_FOLDER_NAME, MoveReport
from duplicate_finder.core.models import Candidate, CandidateStatus, DuplicateGroup, MoveOutcome, is_movable
from duplicate_finder.formatting import format_size

STATUS_TEXT = {
    CandidateStatus.CONFIRMED: "Confirmed duplicate",
    CandidateStatus.DIFFERENT_SIZE: "Kept: different size",
    CandidateStatus.DIFFERENT_CONTENT: "Kept: different content",
    CandidateStatus.NO_ORIGINAL: "Kept: no original",
    CandidateStatus.AMBIGUOUS: "Kept: ambiguous original",
    CandidateStatus.READ_ERROR: "Kept: could not be read",
    CandidateStatus.PENDING_HASH: "Kept: not verified",
}

OUTCOME_TEXT = {
    MoveOutcome.MOVED: "Moved",
    MoveOutcome.SKIPPED_EXISTS: "Skipped, name already in dups",
    MoveOutcome.SKIPPED_CHANGED: "Skipped, file changed",
    MoveOutcome.SKIPPED_ORIGINAL_MISSING: "Skipped, original missing",
    MoveOutcome.CANCELLED: "Cancelled",
    MoveOutcome.ERROR: "Errors",
}

MARK_ON = "☑"
MARK_OFF = "☐"
HASH_PREFIX_LENGTH = 12


def status_text(candidate: Candidate) -> str:
    text = STATUS_TEXT[candidate.status]
    if candidate.status is CandidateStatus.READ_ERROR and candidate.error:
        return f"{text}: {candidate.error}"
    return text


def can_toggle(candidate: Candidate) -> bool:
    return is_movable(candidate)


def selection_mark(candidate: Candidate, selected: set[Path]) -> str:
    if not can_toggle(candidate):
        return ""
    return MARK_ON if candidate.entry.path in selected else MARK_OFF


def short_hash(value: str | None) -> str:
    if not value:
        return ""
    return value[:HASH_PREFIX_LENGTH]


def group_title(group: DuplicateGroup) -> str:
    if group.original is not None:
        return f"[{group.group_id}] {group.original.name} ({format_size(group.original.size)})"
    first = group.candidates[0]
    name = first.parsed.base + first.parsed.ext
    if first.status is CandidateStatus.AMBIGUOUS:
        return f"[{group.group_id}] {name} (ambiguous original)"
    return f"[{group.group_id}] {name} (no original)"


@dataclass(frozen=True, slots=True)
class SelectionSummary:
    group_count: int
    file_count: int
    total_bytes: int


def summary(groups: tuple[DuplicateGroup, ...] | list[DuplicateGroup], selected: set[Path]) -> SelectionSummary:
    """Count only selected candidates that are still allowed to move."""
    group_count = 0
    file_count = 0
    total_bytes = 0
    for group in groups:
        chosen = [candidate for candidate in group.candidates
                  if candidate.entry.path in selected and is_movable(candidate)]
        if not chosen:
            continue
        group_count += 1
        file_count += len(chosen)
        total_bytes += sum(candidate.entry.size for candidate in chosen)
    return SelectionSummary(group_count, file_count, total_bytes)


def summary_text(group_count: int, confirmed_count: int, groups, selected: set[Path]) -> str:
    selection = summary(groups, selected)
    return (
        f"{group_count} group(s), {confirmed_count} confirmed, "
        f"{selection.file_count} selected, {format_size(selection.total_bytes)} to move"
    )


def confirm_text(folder: Path, groups, selected: set[Path]) -> str:
    selection = summary(groups, selected)
    return (
        f"Move {selection.file_count} file(s) from {selection.group_count} group(s)?\n\n"
        f"Total size: {format_size(selection.total_bytes)}\n"
        f"Destination: {folder / DEST_FOLDER_NAME}"
    )


def move_report_text(report: MoveReport) -> str:
    lines = [f"{OUTCOME_TEXT[outcome]}: {report.totals[outcome.value]}" for outcome in MoveOutcome]
    if report.log_path is not None:
        lines.append(f"Log: {report.log_path}")
    return "\n".join(lines)


def analyze_progress_text(phase: Phase, done: int, total: int) -> str:
    if phase is Phase.SCAN:
        return f"Scanning... {done} of {total}"
    percent = done * 100 // total if total else 100
    return f"Verifying... {percent}%"


def move_progress_text(done: int, total: int) -> str:
    return f"Moving... {done} of {total}"
