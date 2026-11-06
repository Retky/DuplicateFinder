"""Folder groups appear under All, and only a ticked duplicate can move."""

import tkinter as tk
from pathlib import Path

import pytest

from duplicate_finder.core.config import Config
from duplicate_finder.core.filename_parser import ALL_PATTERNS
from duplicate_finder.core.folder_groups import FolderGroup
from duplicate_finder.ui.presenters import MARK_OFF, MARK_ON, confirm_text
from duplicate_finder.ui.results_view import HIDDEN_FOLDER_WARNING, ResultsView
from duplicate_finder.ui.settings_panel import SettingsPanel


def open_root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk is not available")
    root.withdraw()
    return root


def folder_group(folder: Path) -> FolderGroup:
    return FolderGroup(folder / "Album", (folder / "Album (1)",))


def row_named(view: ResultsView, name: str) -> str:
    for group_id in view.tree.get_children(""):
        for row in view.tree.get_children(group_id):
            if view.tree.set(row, "name") == name:
                return row
    raise AssertionError(f"{name} is not shown")


def test_subfolders_stay_disabled_until_folder_matching_is_on(tmp_path):
    root = open_root()
    try:
        panel = SettingsPanel(root, Config(ALL_PATTERNS, None), lambda: None, tmp_path)
        assert panel.match_folders() is False
        assert panel.match_subfolders() is False
        assert "disabled" in panel._subfolder_check.state()

        panel._match_folders.set(True)
        panel._folders_changed()

        assert panel.match_folders() is True
        assert "disabled" not in panel._subfolder_check.state()
    finally:
        root.destroy()


def test_hidden_folder_warning_appears_only_when_the_scan_included_one(tmp_path):
    root = open_root()
    try:
        view = ResultsView(root, lambda: None)
        view.show((), (folder_group(tmp_path),), folders_hidden=False)
        assert view.warning.cget("text") == ""
        assert view.warning.winfo_manager() == ""

        view.show((), (folder_group(tmp_path),), folders_hidden=True)
        assert view.warning.cget("text") == HIDDEN_FOLDER_WARNING
        assert view.warning.winfo_manager() == "pack"
    finally:
        root.destroy()


def test_original_folder_has_no_tick_and_confirm_names_the_folder_destination(tmp_path):
    root = open_root()
    try:
        view = ResultsView(root, lambda: None)
        view.show((), (folder_group(tmp_path),))
        original = row_named(view, "Album")
        duplicate = row_named(view, "Album (1)")

        assert view.tree.set(original, "mark") == ""
        assert view.tree.set(duplicate, "mark") == MARK_OFF
        assert view.selected_folder_paths() == []
        assert "03_Folders" not in confirm_text(tmp_path, [], set(), view.selected_folder_paths())

        view.toggle(original)
        assert view.tree.set(original, "mark") == ""
        assert view.selected_folder_paths() == []

        view.toggle(duplicate)
        chosen = view.selected_folder_paths()
        text = confirm_text(tmp_path, [], set(), chosen)
        assert chosen == [tmp_path / "Album (1)"]
        assert str(tmp_path / "dups" / "03_Folders") in text
        assert "01_Name" not in text
        assert "02_Content" not in text
    finally:
        root.destroy()


def test_select_visible_ticks_duplicate_folders_and_the_filter_keeps_those_ticks(tmp_path):
    root = open_root()
    try:
        view = ResultsView(root, lambda: None)
        group = folder_group(tmp_path)
        view.show((), (group,))
        view.select_visible()

        assert view.tree.set(row_named(view, "Album"), "mark") == ""
        assert view.tree.set(row_named(view, "Album (1)"), "mark") == MARK_ON
        assert group.original not in view.selected_folders
        assert group.duplicates[0] in view.selected_folders

        view.set_filter("Confirmed by name")

        assert group.duplicates[0] in view.selected_folders
        assert group.original not in view.selected_folders
        assert view.selected_folder_paths() == []
        assert view.tree.get_children("") == ()

        view.select_visible()
        assert group.original not in view.selected_folders
        assert group.duplicates[0] in view.selected_folders
    finally:
        root.destroy()
