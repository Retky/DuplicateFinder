"""Pure presentation rules shared by the window. No Tkinter imports."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from duplicate_finder.core.analysis import Phase
from duplicate_finder.core.file_mover import MoveReport, content_destination, folder_destination, name_destination
from duplicate_finder.core.models import Candidate, CandidateStatus, DuplicateGroup, GroupKind, MoveOutcome, is_movable
from duplicate_finder.formatting import format_size

STATUS_TEXT = {
    CandidateStatus.CONFIRMED: "Confirmed duplicate",
    CandidateStatus.CONTENT_MATCH: "Confirmed by content",
    CandidateStatus.DIFFERENT_SIZE: "Kept: different size",
    CandidateStatus.DIFFERENT_CONTENT: "Kept: different content",
    CandidateStatus.NO_ORIGINAL: "Kept: no original",
    CandidateStatus.AMBIGUOUS: "Kept: ambiguous original",
    CandidateStatus.READ_ERROR: "Kept: could not be read",
    CandidateStatus.PENDING_HASH: "Kept: not verified",
}

OUTCOME_TEXT = {
    MoveOutcome.MOVED: "Moved",
    MoveOutcome.SKIPPED_EXISTS: "Skipped, name already in the destination",
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


def can_toggle(
    candidate: Candidate,
    *,
    keeper_chosen: bool = False,
    is_keeper: bool = False,
    protected: bool = False,
) -> bool:
    if candidate.status is CandidateStatus.CONFIRMED:
        return True
    return (
        candidate.status is CandidateStatus.CONTENT_MATCH
        and keeper_chosen
        and not is_keeper
        and not protected
    )


def selection_mark(candidate: Candidate, selected: set[Path], **flags: bool) -> str:
    if not can_toggle(candidate, **flags):
        return ""
    return MARK_ON if candidate.entry.path in selected else MARK_OFF


def content_status(candidate: Candidate, *, suggested: bool = False, keeper: bool = False,
                   protected: bool = False) -> str:
    text = status_text(candidate)
    if candidate.status is not CandidateStatus.CONTENT_MATCH:
        return text
    if protected:
        return f"{text}. Name original, kept"
    if keeper:
        return f"{text}. Keep. The newer modified time is only a suggestion."
    if suggested:
        return f"{text}. Suggested to keep. The newer modified time is only a suggestion."
    return text


def format_timestamp(ns: int | None) -> str:
    if ns is None:
        return "Unavailable"
    return datetime.fromtimestamp(ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M")


def short_hash(value: str | None) -> str:
    if not value:
        return ""
    return value[:HASH_PREFIX_LENGTH]


def group_title(group: DuplicateGroup) -> str:
    if group.kind is GroupKind.CONTENT and group.candidates:
        extension = group.candidates[0].parsed.ext or "(no extension)"
        return f"[{group.group_id}] Content match {extension} ({len(group.candidates)} files)"
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


def _selected_for_move(candidate: Candidate) -> bool:
    return is_movable(candidate) or candidate.status is CandidateStatus.CONTENT_MATCH


def summary(groups: tuple[DuplicateGroup, ...] | list[DuplicateGroup], selected: set[Path]) -> SelectionSummary:
    """Count only selected candidates that are still allowed to move."""
    group_count = 0
    file_count = 0
    total_bytes = 0
    for group in groups:
        chosen = [candidate for candidate in group.candidates
                  if candidate.entry.path in selected and _selected_for_move(candidate)]
        if not chosen:
            continue
        group_count += 1
        file_count += len(chosen)
        total_bytes += sum(candidate.entry.size for candidate in chosen)
    return SelectionSummary(group_count, file_count, total_bytes)


def summary_text(group_count: int, confirmed_count: int, groups, selected: set[Path],
                 hidden_count: int = 0) -> str:
    selection = summary(groups, selected)
    text = (
        f"{group_count} group(s), {confirmed_count} confirmed, "
        f"{selection.file_count} selected, {format_size(selection.total_bytes)} to move"
    )
    if hidden_count > 0:
        text += f" {hidden_count} ticked file(s) are hidden and will not be moved."
    return text


def confirm_text(folder: Path, groups, selected: set[Path], folders: Sequence[Path] = ()) -> str:
    selection = summary(groups, selected)
    name = content = False
    for group in groups:
        for candidate in group.candidates:
            if candidate.entry.path not in selected:
                continue
            if is_movable(candidate):
                name = True
            elif candidate.status is CandidateStatus.CONTENT_MATCH:
                content = True
    destinations = []
    if name or not (content or folders):
        destinations.append(str(name_destination(folder)))
    if content:
        destinations.append(str(content_destination(folder)))
    if folders:
        destinations.append(str(folder_destination(folder)))
    if folders and selection.file_count == 0:
        question = f"Move {len(folders)} folder(s)?"
    else:
        question = f"Move {selection.file_count} file(s) from {selection.group_count} group(s)?"
    return (
        f"{question}\n\n"
        f"Total size: {format_size(selection.total_bytes)}\n"
        f"Destination: {', '.join(destinations)}"
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
