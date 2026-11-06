"""Content rows stay unticked until the user chooses a keeper."""

import tkinter as tk
from pathlib import Path

import pytest

from duplicate_finder.core.models import Candidate, CandidateStatus, DuplicateGroup, FileEntry, GroupKind, ParsedName
from duplicate_finder.ui.presenters import MARK_OFF, content_status, format_timestamp, summary_text
from duplicate_finder.ui.results_view import ResultsView

HASH = "b" * 64


def content_group(folder: Path) -> DuplicateGroup:
    def entry(name: str, modified: int, created: int | None) -> FileEntry:
        return FileEntry(folder / name, name, 4, modified, created_ns=created)

    def candidate(name: str, modified: int, created: int | None) -> Candidate:
        parsed = ParsedName(name.removesuffix(".jpg"), ".jpg", name.removesuffix(".jpg"), name.casefold(), ".jpg", ())
        return Candidate(entry(name, modified, created), parsed, CandidateStatus.CONTENT_MATCH, HASH)

    photo = candidate("Photo.jpg", 20, None)
    vacation = candidate("Vacation.jpg", 10, 5)
    return DuplicateGroup(
        "g0001", ("jpg", HASH), None, HASH, (photo, vacation), kind=GroupKind.CONTENT,
    )


def test_content_status_names_the_suggestion_and_a_missing_date():
    group = content_group(Path(r"C:\Photos"))
    photo, vacation = group.candidates
    assert "Suggested to keep" in content_status(photo, suggested=True)
    assert "The newer modified time is only a suggestion." in content_status(photo, suggested=True)
    assert "Keep" in content_status(vacation, keeper=True)
    assert content_status(vacation, keeper=True).endswith("The newer modified time is only a suggestion.")
    assert content_status(photo, protected=True).endswith("Name original, kept")
    assert format_timestamp(None) == "Unavailable"
    assert format_timestamp(vacation.entry.created_ns) != "Unavailable"


def test_content_rows_start_unticked_and_keep_clears_them(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk is not available")
    root.withdraw()
    view = ResultsView(root, lambda: None)
    group = content_group(tmp_path)
    view.show((group,))

    marks = [view.tree.set(row, "mark") for row in view.tree.get_children(group.group_id)]
    assert marks == ["", MARK_OFF]
    assert view.content_moves() == []

    vacation_row = next(row for row in view.tree.get_children(group.group_id)
                        if view.tree.set(row, "name") == "Vacation.jpg")
    view.toggle(vacation_row)
    (suggested_move,) = view.content_moves()
    assert suggested_move.keeper.name == "Photo.jpg"
    assert [candidate.entry.name for candidate in suggested_move.selected] == ["Vacation.jpg"]

    vacation = group.candidates[1]
    view.keep(vacation.entry.path)
    photo = group.candidates[0]
    photo_row = next(row for row in view.tree.get_children(group.group_id)
                     if view.tree.set(row, "name") == "Photo.jpg")
    view.toggle(photo_row)

    (move,) = view.content_moves()
    assert move.keeper.path == vacation.entry.path
    assert [candidate.entry.name for candidate in move.selected] == ["Photo.jpg"]
    root.destroy()


def test_older_name_original_is_the_content_keeper(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk is not available")
    root.withdraw()
    folder = tmp_path
    photo = FileEntry(folder / "Photo.jpg", "Photo.jpg", 4, 100)
    copy = FileEntry(folder / "Photo (1).jpg", "Photo (1).jpg", 4, 200)
    vacation = FileEntry(folder / "Vacation.jpg", "Vacation.jpg", 4, 300)

    def parsed(name: str, suffixes: tuple[str, ...] = ()) -> ParsedName:
        stem = name.removesuffix(".jpg")
        return ParsedName(stem, ".jpg", "Photo", "photo", ".jpg", suffixes)

    confirmed = Candidate(copy, parsed("Photo (1).jpg", ("paren_num",)), CandidateStatus.CONFIRMED, HASH)
    name = DuplicateGroup("g0001", ("photo", ".jpg"), photo, HASH, (confirmed,))
    content_photo = Candidate(photo, parsed("Photo.jpg"), CandidateStatus.CONTENT_MATCH, HASH)
    content_vacation = Candidate(vacation, parsed("Vacation.jpg"), CandidateStatus.CONTENT_MATCH, HASH)
    content = DuplicateGroup(
        "g0002", ("jpg", HASH), None, HASH, (content_photo, content_vacation), kind=GroupKind.CONTENT,
    )
    view = ResultsView(root, lambda: None)
    view.show((name, content))
    view.set_filter("Confirmed by content")

    marks = {
        view.tree.set(row, "name"): view.tree.set(row, "mark")
        for row in view.tree.get_children(content.group_id)
    }
    assert marks["Photo.jpg"] == ""
    assert marks["Vacation.jpg"] == MARK_OFF
    view.toggle(next(row for row in view.tree.get_children(content.group_id)
                     if view.tree.set(row, "name") == "Vacation.jpg"))
    (move,) = view.content_moves()
    assert move.keeper.name == "Photo.jpg"
    assert [candidate.entry.name for candidate in move.selected] == ["Vacation.jpg"]
    root.destroy()


def mixed_groups(folder: Path) -> tuple[DuplicateGroup, DuplicateGroup]:
    def entry(name: str, size: int = 4) -> FileEntry:
        return FileEntry(folder / name, name, size, 1)

    def parsed(name: str, suffixes: tuple[str, ...] = ()) -> ParsedName:
        stem = name.removesuffix(".jpg")
        return ParsedName(stem, ".jpg", "Photo", "photo", ".jpg", suffixes)

    confirmed = Candidate(
        entry("Photo (1).jpg"), parsed("Photo (1).jpg", ("paren_num",)), CandidateStatus.CONFIRMED, HASH,
    )
    pending = Candidate(
        entry("Photo (2).jpg", 8), parsed("Photo (2).jpg", ("paren_num",)), CandidateStatus.DIFFERENT_SIZE,
    )
    name = DuplicateGroup("g0001", ("photo", ".jpg"), entry("Photo.jpg"), HASH, (confirmed, pending))
    album = Candidate(entry("Album.jpg"), parsed("Album.jpg"), CandidateStatus.CONTENT_MATCH, HASH)
    vacation = Candidate(entry("Vacation.jpg"), parsed("Vacation.jpg"), CandidateStatus.CONTENT_MATCH, HASH)
    content = DuplicateGroup("g0002", ("jpg", HASH), None, HASH, (album, vacation), kind=GroupKind.CONTENT)
    return name, content


def visible_names(view: ResultsView) -> set[str]:
    return {
        view.tree.set(row, "name")
        for parent in view.tree.get_children()
        for row in view.tree.get_children(parent)
    }


def test_filters_keep_ticks_and_move_only_what_is_visible(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk is not available")
    root.withdraw()
    view = ResultsView(root, lambda: None)
    name, content = mixed_groups(tmp_path)
    view.show((name, content))

    assert visible_names(view) == {"Photo (1).jpg", "Photo (2).jpg", "Album.jpg", "Vacation.jpg"}
    assert view.selected == {name.candidates[0].entry.path}
    assert [candidate.entry.name for candidate in view.selected_candidates()] == ["Photo (1).jpg"]

    stored = set(view.selected)
    view.set_filter("Confirmed by name")
    assert visible_names(view) == {"Photo (1).jpg"}
    assert view.selected == stored

    view.set_filter("Pending review")
    assert visible_names(view) == {"Photo (2).jpg"}
    assert view.selected == stored
    assert view.selected_candidates() == []
    pending_row = next(iter(view.tree.get_children(view.tree.get_children()[0])))
    view.toggle(pending_row)
    assert view.selected == stored

    view.set_filter("Confirmed by content")
    assert visible_names(view) == {"Album.jpg", "Vacation.jpg"}
    assert view.selected_candidates() == []
    assert view.content_moves() == []
    assert view.hidden_tick_count() == 1
    view.select_visible()
    assert content.candidates[1].entry.path in view.selected
    assert content.candidates[0].entry.path not in view.selected
    assert name.candidates[0].entry.path in view.selected
    (visible_move,) = view.content_moves()
    assert visible_move.keeper.name == "Album.jpg"
    assert [candidate.entry.name for candidate in visible_move.selected] == ["Vacation.jpg"]
    text = summary_text(2, 1, [name, content], view.move_paths(), view.hidden_tick_count())
    assert "1 selected" in text
    assert "1 ticked file(s) are hidden and will not be moved." in text

    view.keep(content.candidates[1].entry.path)
    view.select_visible()
    assert content.candidates[0].entry.path in view.selected
    assert content.candidates[1].entry.path not in view.selected
    (move,) = view.content_moves()
    assert [candidate.entry.name for candidate in move.selected] == ["Album.jpg"]
    assert name.candidates[0].entry.path in view.selected

    view.clear_visible()
    assert name.candidates[0].entry.path in view.selected
    assert content.candidates[0].entry.path not in view.selected
    assert view.content_moves() == []

    view.set_filter("All")
    assert [candidate.entry.name for candidate in view.selected_candidates()] == ["Photo (1).jpg"]
    root.destroy()
