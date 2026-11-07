import tkinter as tk
from pathlib import Path

import pytest

from duplicate_finder.core.analysis import AnalysisResult
from duplicate_finder.core.config import Config
from duplicate_finder.core.file_mover import MoveReport
from duplicate_finder.core.filename_parser import ALL_PATTERNS
from duplicate_finder.core.models import (
    Candidate,
    CandidateStatus,
    DuplicateGroup,
    FileEntry,
    ParsedName,
    ScanResult,
)
from duplicate_finder.ui.main_window import MainWindow
from duplicate_finder.ui.worker import Progress

HASH = "a" * 64


def sample(folder: Path) -> AnalysisResult:
    original = FileEntry(folder / "Photo.jpg", "Photo.jpg", 100, 1)
    confirmed_file = FileEntry(folder / "Photo (1).jpg", "Photo (1).jpg", 100, 1)
    other_file = FileEntry(folder / "Photo (2).jpg", "Photo (2).jpg", 120, 1)

    def parsed(name: str) -> ParsedName:
        return ParsedName(name.removesuffix(".jpg"), ".jpg", "Photo", "photo", ".jpg", ("paren_num",))

    confirmed = Candidate(confirmed_file, parsed("Photo (1).jpg"), CandidateStatus.CONFIRMED, HASH)
    different = Candidate(other_file, parsed("Photo (2).jpg"), CandidateStatus.DIFFERENT_SIZE)
    group = DuplicateGroup("g0001", ("photo", ".jpg"), original, HASH, (confirmed, different))
    scan = ScanResult(folder, (original, confirmed_file, other_file), (), ())
    return AnalysisResult(scan, (group,), ALL_PATTERNS, False)


def test_window_shows_results_and_toggles_the_confirmed_row(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    try:
        window = MainWindow(root, Config(ALL_PATTERNS, None), config_directory=tmp_path)
        result = sample(tmp_path)
        window.show_analysis(result)
        root.update_idletasks()

        copy = tmp_path / "Photo (1).jpg"
        row = window.results.row_for(copy)
        assert row is not None
        assert window.results.tree.set(row, "mark") == "☑"
        assert "1 selected" in window.summary_var.get()
        assert "100 B to move" in window.summary_var.get()
        assert not window.move_button.instate(["disabled"])

        window.results.toggle(row)
        root.update_idletasks()

        assert window.results.tree.set(row, "mark") == "☐"
        assert "0 selected" in window.summary_var.get()
        assert window.move_button.instate(["disabled"])
        assert window.results.row_for(tmp_path / "Photo (2).jpg") is not None
        assert window.results.tree.set(window.results.row_for(tmp_path / "Photo (2).jpg"), "mark") == ""
    finally:
        root.destroy()


def test_move_progress_fills_part_of_the_bar(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    try:
        window = MainWindow(root, Config(ALL_PATTERNS, None), config_directory=tmp_path)
        window.progress.configure(maximum=1000, value=1000)
        window.worker.messages.put(Progress("Moving... 1 of 4", 1, 4))
        window._poll()
        root.update_idletasks()

        assert window.status_var.get() == "Moving... 1 of 4"
        assert int(float(window.progress["maximum"])) == 1000
        assert int(float(window.progress["value"])) == 250
    finally:
        root.destroy()


def test_move_success_leaves_the_list_and_does_not_analyze(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    try:
        window = MainWindow(root, Config(ALL_PATTERNS, None), config_directory=tmp_path)
        result = sample(tmp_path)
        window.show_analysis(result)
        window.operation = "move"
        window.busy = True
        monkeypatch.setattr(window, "_show_move_report", lambda _report: None)

        def fail_analyze() -> None:
            raise AssertionError("analyze was started")

        monkeypatch.setattr(window, "analyze", fail_analyze)
        window._on_success(MoveReport(tmp_path / "_dups", None, (), False))
        root.update_idletasks()

        assert window.busy is False
        assert window.stale is True
        assert window.operation is None
        assert window.move_button.instate(["disabled"])
        assert not window.analyze_button.instate(["disabled"])
        assert not window.browse_button.instate(["disabled"])
        assert window.results.row_for(tmp_path / "Photo (1).jpg") is not None
        assert window.analysis is result
        assert window.status_var.get() == "Move finished. The list stays as it was until you analyze again."
    finally:
        root.destroy()
