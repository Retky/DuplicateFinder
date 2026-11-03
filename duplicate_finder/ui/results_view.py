"""Grouped results with a tick mark on each confirmed copy."""

import os
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk

from duplicate_finder.core.models import Candidate, CandidateStatus, DuplicateGroup
from duplicate_finder.formatting import format_size
from duplicate_finder.ui.presenters import (
    can_toggle,
    group_title,
    selection_mark,
    short_hash,
    status_text,
)

_ALL = "All"
_CONFIRMED = "Confirmed only"


class ResultsView(ttk.Frame):
    def __init__(self, master: tk.Misc, on_selection_changed: Callable[[], None]) -> None:
        super().__init__(master)
        self._on_selection_changed = on_selection_changed
        self.selected: set[Path] = set()
        self._groups: tuple[DuplicateGroup, ...] = ()
        self._candidates: dict[str, Candidate] = {}
        self._paths: dict[str, Path] = {}

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Show").pack(side="left")
        self._filter = tk.StringVar(value=_ALL)
        filter_box = ttk.Combobox(toolbar, textvariable=self._filter, values=(_ALL, _CONFIRMED),
                                  state="readonly", width=18)
        filter_box.pack(side="left", padx=(4, 0))
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._rebuild())

        self.tree = ttk.Treeview(self, columns=("mark", "name", "size", "status", "hash"), show="tree headings")
        self.tree.heading("#0", text="Group")
        self.tree.column("#0", width=260, stretch=False)
        for column, title, width in (
            ("mark", "", 36),
            ("name", "Name", 220),
            ("size", "Size", 80),
            ("status", "Status", 240),
            ("hash", "SHA-256", 120),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, stretch=column == "status")
        self._bold_font = tkfont.nametofont("TkDefaultFont").copy()
        self._bold_font.configure(weight="bold")
        self.tree.tag_configure("original", font=self._bold_font)

        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self._menu = tk.Menu(self, tearoff=False)
        self._menu.add_command(label="Open", command=self._open_menu_target)
        self._menu.add_command(label="Show in Explorer", command=self._reveal_menu_target)
        self._menu_path: Path | None = None

    def show(self, groups: tuple[DuplicateGroup, ...]) -> None:
        self._groups = groups
        self.selected = {candidate.entry.path for group in groups for candidate in group.confirmed}
        self._rebuild()

    def row_for(self, path: Path) -> str | None:
        for row_id, candidate in self._candidates.items():
            if candidate.entry.path == path:
                return row_id
        return None

    def selected_candidates(self) -> list[Candidate]:
        return [candidate for group in self._groups for candidate in group.candidates
                if candidate.entry.path in self.selected and can_toggle(candidate)]

    def toggle(self, row_id: str) -> None:
        candidate = self._candidates.get(row_id)
        if candidate is None or not can_toggle(candidate):
            return
        path = candidate.entry.path
        if path in self.selected:
            self.selected.remove(path)
        else:
            self.selected.add(path)
        self.tree.set(row_id, "mark", selection_mark(candidate, self.selected))
        self._on_selection_changed()

    def _visible(self, group: DuplicateGroup) -> tuple[Candidate, ...]:
        if self._filter.get() == _CONFIRMED:
            return tuple(candidate for candidate in group.candidates
                         if candidate.status is CandidateStatus.CONFIRMED)
        return group.candidates

    def _rebuild(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._candidates.clear()
        self._paths.clear()
        for group in self._groups:
            rows = self._visible(group)
            if self._filter.get() == _CONFIRMED and not rows:
                continue
            parent = self.tree.insert("", "end", iid=group.group_id, text=group_title(group), tags=("original",))
            if group.original is not None:
                self._paths[parent] = group.original.path
            for candidate in rows:
                row_id = f"row-{len(self._candidates)}"
                self._candidates[row_id] = candidate
                self._paths[row_id] = candidate.entry.path
                self.tree.insert(parent, "end", iid=row_id, values=(
                    selection_mark(candidate, self.selected),
                    candidate.entry.name,
                    format_size(candidate.entry.size),
                    status_text(candidate),
                    short_hash(candidate.sha256),
                ))
            self.tree.item(parent, open=True)

    def _on_click(self, event: tk.Event) -> str | None:
        row_id = self.tree.identify_row(event.y)
        if self.tree.identify_column(event.x) == "#1" and row_id in self._candidates:
            self.toggle(row_id)
            return "break"
        return None

    def _on_space(self, _event: tk.Event) -> str:
        self.toggle(self.tree.focus())
        return "break"

    def _on_double_click(self, event: tk.Event) -> None:
        self._open(self._paths.get(self.tree.identify_row(event.y)))

    def _on_right_click(self, event: tk.Event) -> None:
        row_id = self.tree.identify_row(event.y)
        path = self._paths.get(row_id)
        if path is None:
            return
        self.tree.focus(row_id)
        self._menu_path = path
        self._menu.tk_popup(event.x_root, event.y_root)

    def _open_menu_target(self) -> None:
        self._open(self._menu_path)

    def _reveal_menu_target(self) -> None:
        if self._menu_path is not None:
            subprocess.Popen(["explorer", f'/select,"{self._menu_path}"'])

    def _open(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            os.startfile(path)
        except OSError as exc:
            messagebox.showerror("Duplicate Finder", f"Could not open {path.name}: {exc.strerror or exc}",
                                 parent=self.winfo_toplevel())
