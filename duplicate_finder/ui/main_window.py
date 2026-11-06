"""The main window: pick a folder, review the results, and confirm the move."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from duplicate_finder.core.analysis import AnalysisResult, analyze
from duplicate_finder.core.config import Config
from duplicate_finder.core.file_mover import DestinationError, MoveReport, move_duplicates
from duplicate_finder.core.scanner import FolderValidationError, ScanCancelled, validate_folder
from duplicate_finder.ui.presenters import (
    analyze_progress_text,
    confirm_text,
    move_progress_text,
    move_report_text,
    summary_text,
)
from duplicate_finder.ui.results_view import ResultsView
from duplicate_finder.ui.settings_panel import SettingsPanel
from duplicate_finder.ui.worker import Failed, Progress, Succeeded, Worker


class MainWindow(ttk.Frame):
    def __init__(self, root: tk.Tk, config: Config, config_directory: Path | None = None) -> None:
        super().__init__(root, padding=8)
        self.root = root
        self.pack(fill="both", expand=True)
        self.analysis: AnalysisResult | None = None
        self.stale = True
        self.busy = False
        self.operation: str | None = None
        self.close_after = False
        self.worker = Worker()

        self.path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose a folder and analyze it.")
        self.summary_var = tk.StringVar(value="No results yet.")

        path_row = ttk.Frame(self)
        path_row.pack(fill="x")
        self.path_entry = ttk.Entry(path_row, textvariable=self.path_var)
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.browse_button = ttk.Button(path_row, text="Browse...", command=self.browse)
        self.browse_button.pack(side="left", padx=4)
        self.analyze_button = ttk.Button(path_row, text="Analyze", command=self.analyze)
        self.analyze_button.pack(side="left")
        self.cancel_button = ttk.Button(path_row, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(4, 0))

        self.settings = SettingsPanel(self, config, self._mark_stale, config_directory)
        self.settings.pack(fill="x", pady=8)

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", pady=(2, 6))

        self.results = ResultsView(self, self._update_summary)
        self.results.pack(fill="both", expand=True)

        ttk.Label(self, textvariable=self.summary_var).pack(anchor="w", pady=4)
        self.move_button = ttk.Button(self, text="Move selected", command=self.move, state="disabled")
        self.move_button.pack(anchor="e")

        self.path_var.trace_add("write", lambda *_args: self._mark_stale())
        if config.last_folder is not None:
            self.path_var.set(str(config.last_folder))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll()

    def browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self.root, title="Choose a folder")
        if chosen:
            self.path_var.set(chosen)

    def analyze(self) -> None:
        text = self.path_var.get().strip()
        if not text:
            messagebox.showerror("Duplicate Finder", "Choose a folder first.", parent=self.root)
            return
        try:
            folder = validate_folder(Path(text))
        except FolderValidationError as exc:
            self.stale = True
            self._update_move_button()
            messagebox.showerror("Duplicate Finder", str(exc), parent=self.root)
            return
        self.settings.set_last_folder(folder)
        self._start("analyze", folder)

    def move(self) -> None:
        if self.analysis is None or self.stale:
            return
        selected = self.results.selected_candidates()
        content = self.results.content_moves()
        if not selected and not content:
            return
        paths = {candidate.entry.path for candidate in selected}
        paths.update(candidate.entry.path for move in content for candidate in move.selected)
        if not messagebox.askyesno(
            "Move duplicates",
            confirm_text(self.analysis.folder, self.analysis.groups, paths),
            default="no",
            parent=self.root,
        ):
            return
        self._start("move", self.analysis, selected, content)

    def cancel(self) -> None:
        if self.busy:
            self.worker.cancel()
            self.status_var.set("Cancelling...")

    def on_close(self) -> None:
        if self.operation == "move":
            if not messagebox.askyesno(
                "Move in progress",
                "A move is in progress. Cancel and close?",
                default="no",
                parent=self.root,
            ):
                return
            self.close_after = True
            self.worker.cancel()
            self.status_var.set("Cancelling...")
            return
        if self.operation == "analyze":
            self.close_after = True
            self.worker.cancel()
            return
        self.root.destroy()

    def show_analysis(self, result: AnalysisResult) -> None:
        """Show results directly. Used by the worker callback and by tests."""
        self.analysis = result
        self.stale = False
        self.results.show(result.groups)
        self._update_summary()
        if not result.groups:
            self.status_var.set("No duplicate candidates found.")
        elif result.cancelled:
            self.status_var.set("Analysis cancelled. Unverified copies were not confirmed.")
        else:
            self.status_var.set("Analysis finished.")

    def _start(self, operation: str, *args: object) -> None:
        self.operation = operation
        self.busy = True
        self._apply_busy_state()
        self.progress.configure(value=0, maximum=1)
        enabled = self.settings.enabled()
        match_names = self.settings.match_names()
        match_content = self.settings.match_content()
        excluded_extensions = self.settings.excluded_extensions()

        def task(cancel, report) -> object:
            if operation == "analyze":
                folder = args[0]
                assert isinstance(folder, Path)

                def on_progress(phase, done, total):
                    report(Progress(analyze_progress_text(phase, done, total), done, total))

                return analyze(
                    folder, enabled, on_progress=on_progress, cancel=cancel,
                    match_names=match_names, match_content=match_content,
                    excluded_extensions=excluded_extensions,
                )

            analysis, selected, content = args

            def on_move_progress(done, total):
                report(Progress(move_progress_text(done, total), done, total))

            return move_duplicates(
                analysis, selected, on_progress=on_move_progress, cancel=cancel, content=content,
            )

        self.worker.start(task)

    def _poll(self) -> None:
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        for message in self.worker.drain():
            if isinstance(message, Progress):
                self.status_var.set(message.text)
                maximum = message.total if message.total else 1
                self.progress.configure(maximum=maximum, value=min(message.done, maximum))
            elif isinstance(message, Succeeded):
                self._on_success(message.value)
            elif isinstance(message, Failed):
                self._on_failure(message.error)
        try:
            if self.root.winfo_exists():
                self.root.after(100, self._poll)
        except tk.TclError:
            return

    def _on_success(self, value: object) -> None:
        operation = self.operation
        closing = self.close_after
        self.operation = None
        self.busy = False
        if closing:
            self.root.destroy()
            return
        self._apply_busy_state()
        if operation == "analyze" and isinstance(value, AnalysisResult):
            self.show_analysis(value)
            return
        if operation == "move" and isinstance(value, MoveReport):
            self.stale = True
            self._update_move_button()
            self._show_move_report(value)
            if self.root.winfo_exists():
                self.analyze()

    def _on_failure(self, error: Exception) -> None:
        closing = self.close_after
        self.operation = None
        self.busy = False
        if not closing:
            self._apply_busy_state()
            if isinstance(error, ScanCancelled):
                self.status_var.set("Analysis cancelled.")
            elif isinstance(error, DestinationError):
                messagebox.showerror("Duplicate Finder", str(error), parent=self.root)
            else:
                messagebox.showerror("Duplicate Finder", f"Something went wrong: {error}", parent=self.root)
        if closing or not self.root.winfo_exists():
            if self.root.winfo_exists():
                self.root.destroy()

    def _show_move_report(self, report: MoveReport) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Move finished")
        dialog.transient(self.root)
        ttk.Label(dialog, text=move_report_text(report), justify="left").pack(padx=12, pady=12, anchor="w")
        buttons = ttk.Frame(dialog)
        buttons.pack(padx=12, pady=(0, 12))
        if report.log_path is not None:
            ttk.Button(buttons, text="Open log", command=lambda: self._open_log(report.log_path)).pack(side="left")
        ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side="left", padx=(4, 0))
        dialog.grab_set()
        self.root.wait_window(dialog)

    def _open_log(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            import os
            os.startfile(path)
        except OSError as exc:
            messagebox.showerror("Duplicate Finder", str(exc), parent=self.root)

    def _mark_stale(self) -> None:
        if self.analysis is None:
            self.stale = True
            return
        self.stale = True
        self.status_var.set("Settings changed. Analyze again.")
        self._update_move_button()

    def _update_summary(self) -> None:
        if self.analysis is None:
            self.summary_var.set("No results yet.")
        else:
            self.summary_var.set(summary_text(
                len(self.analysis.groups),
                self.analysis.confirmed_count,
                self.analysis.groups,
                self.results.move_paths(),
                self.results.hidden_tick_count(),
            ))
        self._update_move_button()

    def _apply_busy_state(self) -> None:
        state = "disabled" if self.busy else "normal"
        self.analyze_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.path_entry.configure(state=state)
        self.settings.set_enabled(not self.busy)
        self.cancel_button.configure(state="normal" if self.busy else "disabled")
        self._update_move_button()

    def _update_move_button(self) -> None:
        ready = (
            not self.busy
            and not self.stale
            and self.analysis is not None
            and self.results.has_move_selection()
        )
        self.move_button.configure(state="normal" if ready else "disabled")
