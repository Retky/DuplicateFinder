"""Checkboxes for the fixed list of duplicate-suffix patterns."""

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk

from duplicate_finder.core.config import Config, parse_excluded_extensions, save_config
from duplicate_finder.core.filename_parser import PATTERNS


class SettingsPanel(ttk.LabelFrame):
    def __init__(self, master: tk.Misc, config: Config, on_change: Callable[[], None],
                 config_directory: Path | None = None) -> None:
        super().__init__(master, text="Settings")
        self._on_change = on_change
        self._config_directory = config_directory
        self._last_folder = config.last_folder
        self._checks: list[ttk.Checkbutton] = []
        self._match_names = tk.BooleanVar(value=config.match_names)
        self._match_content = tk.BooleanVar(value=True)
        self._match_folders = tk.BooleanVar(value=config.match_folders)
        self._match_subfolders = tk.BooleanVar(value=config.match_subfolders and config.match_folders)
        self._match_nested_files = tk.BooleanVar(value=config.match_nested_files)
        self._excluded = tk.StringVar(value=", ".join(sorted(config.excluded_extensions)))
        names = ttk.Checkbutton(self, text="Match names", variable=self._match_names, command=self._changed)
        content = ttk.Checkbutton(self, text="Match content", variable=self._match_content)
        names.grid(row=0, column=0, sticky="w", padx=8, pady=2)
        content.grid(row=0, column=1, sticky="w", padx=8, pady=2)
        content.state(["disabled"])
        self._content_check = content
        self._checks.append(names)
        self._notice = ttk.Label(self, text="")
        self._notice.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))
        self._refresh_notice()
        folders = ttk.Checkbutton(
            self, text="Treat folders as files", variable=self._match_folders, command=self._folders_changed,
        )
        subfolders = ttk.Checkbutton(
            self, text="Check sub-folders", variable=self._match_subfolders, command=self._changed,
        )
        folders.grid(row=2, column=0, sticky="w", padx=8, pady=2)
        subfolders.grid(row=2, column=1, sticky="w", padx=8, pady=2)
        self._subfolder_check = subfolders
        nested = ttk.Checkbutton(
            self, text="Recursive file matching", variable=self._match_nested_files, command=self._changed,
        )
        nested.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        self._checks.extend((folders, subfolders, nested))
        ttk.Label(self, text="Excluded extensions").grid(row=4, column=0, sticky="w", padx=8, pady=2)
        self._extension_entry = ttk.Entry(self, textvariable=self._excluded)
        self._extension_entry.grid(row=4, column=1, sticky="ew", padx=8, pady=2)
        self._extension_entry.bind("<FocusOut>", lambda _event: self._changed())
        self._extension_entry.bind("<Return>", lambda _event: self._changed())
        self._apply_folder_state()
        self._vars: dict[str, tk.BooleanVar] = {}
        for index, pattern in enumerate(PATTERNS):
            variable = tk.BooleanVar(value=pattern.id in config.enabled_patterns)
            check = ttk.Checkbutton(self, text=pattern.example, variable=variable, command=self._changed)
            check.grid(row=5 + index // 2, column=index % 2, sticky="w", padx=8, pady=2)
            self._checks.append(check)
            self._vars[pattern.id] = variable

    def enabled(self) -> frozenset[str]:
        return frozenset(pattern_id for pattern_id, variable in self._vars.items() if variable.get())

    def match_names(self) -> bool:
        return bool(self._match_names.get())

    def match_content(self) -> bool:
        return True

    def match_folders(self) -> bool:
        return bool(self._match_folders.get())

    def match_subfolders(self) -> bool:
        return self.match_folders() and bool(self._match_subfolders.get())

    def match_nested_files(self) -> bool:
        return bool(self._match_nested_files.get())

    def excluded_extensions(self) -> frozenset[str]:
        return parse_excluded_extensions(self._excluded.get())

    def _refresh_notice(self) -> None:
        if self.match_names() and self.match_content():
            self._notice.configure(text="Name and content together are the certain matches.")
        else:
            self._notice.configure(text="")

    def set_last_folder(self, folder: Path) -> None:
        self._last_folder = folder
        self._save()

    def set_enabled(self, enabled: bool) -> None:
        for check in self._checks:
            check.state(["!disabled"] if enabled else ["disabled"])
        self._extension_entry.configure(state="normal" if enabled else "disabled")
        self._content_check.state(["disabled"])
        self._apply_folder_state(enabled)

    def _folders_changed(self) -> None:
        if not self.match_folders():
            self._match_subfolders.set(False)
        self._apply_folder_state(True)
        self._changed()

    def _apply_folder_state(self, enabled: bool = True) -> None:
        if enabled and self.match_folders():
            self._subfolder_check.state(["!disabled"])
        else:
            self._subfolder_check.state(["disabled"])

    def _changed(self) -> None:
        self._refresh_notice()
        self._save()
        self._on_change()

    def _save(self) -> None:
        try:
            extensions = self.excluded_extensions()
            save_config(Config(
                self.enabled(), self._last_folder, self.match_names(), self.match_content(),
                self.match_folders(), self.match_subfolders(), extensions,
                match_nested_files=self.match_nested_files(),
            ), self._config_directory)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Duplicate Finder", f"Could not save settings: {exc}",
                                 parent=self.winfo_toplevel())
