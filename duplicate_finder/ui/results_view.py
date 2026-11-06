"""Grouped results with a tick mark on each confirmed copy."""

import os
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk

from duplicate_finder.core.content_grouper import choose_keeper
from duplicate_finder.core.file_mover import ContentMove
from duplicate_finder.core.models import Candidate, CandidateStatus, DuplicateGroup, FileEntry, GroupKind, is_movable
from duplicate_finder.formatting import format_size
from duplicate_finder.ui.presenters import (
    can_toggle,
    content_status,
    format_timestamp,
    group_title,
    selection_mark,
    short_hash,
    status_text,
)

_ALL = "All"
_NAME = "Confirmed by name"
_CONTENT = "Confirmed by content"
_PENDING = "Pending review"
_FILTERS = (_ALL, _NAME, _CONTENT, _PENDING)


class ResultsView(ttk.Frame):
    def __init__(self, master: tk.Misc, on_selection_changed: Callable[[], None]) -> None:
        super().__init__(master)
        self._on_selection_changed = on_selection_changed
        self.selected: set[Path] = set()
        self._keepers: dict[str, Path] = {}
        self._groups: tuple[DuplicateGroup, ...] = ()
        self._candidates: dict[str, Candidate] = {}
        self._paths: dict[str, Path] = {}

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Show").pack(side="left")
        self._filter = tk.StringVar(value=_ALL)
        filter_box = ttk.Combobox(toolbar, textvariable=self._filter, values=_FILTERS, state="readonly", width=22)
        filter_box.pack(side="left", padx=(4, 0))
        filter_box.bind("<<ComboboxSelected>>", self._on_filter)
        ttk.Button(toolbar, text="Select visible", command=self.select_visible).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Clear visible", command=self.clear_visible).pack(side="left", padx=(4, 0))

        self.tree = ttk.Treeview(
            self, columns=("mark", "name", "size", "modified", "created", "status", "hash"), show="tree headings",
        )
        self.tree.heading("#0", text="Group")
        self.tree.column("#0", width=260, stretch=False)
        for column, title, width in (
            ("mark", "", 36),
            ("name", "Name", 180),
            ("size", "Size", 70),
            ("modified", "Modified", 120),
            ("created", "Created", 120),
            ("status", "Status", 240),
            ("hash", "SHA-256", 110),
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
        self._menu.add_command(label="Keep this file", command=self._keep_menu_target)
        self._menu_path: Path | None = None

    def show(self, groups: tuple[DuplicateGroup, ...]) -> None:
        self._groups = groups
        self._keepers.clear()
        self.selected = {candidate.entry.path for group in groups for candidate in group.confirmed}
        self._rebuild()

    def keep(self, path: Path) -> None:
        """Choose the file that stays. Clears ticks in that content group."""
        group = self._group_for(path)
        if group is None or group.kind is not GroupKind.CONTENT:
            return
        if path not in {candidate.entry.path for candidate in group.candidates}:
            return
        self._keepers[group.group_id] = path
        for candidate in group.candidates:
            self.selected.discard(candidate.entry.path)
        self._rebuild()
        self._on_selection_changed()

    def row_for(self, path: Path) -> str | None:
        for row_id, candidate in self._candidates.items():
            if candidate.entry.path == path:
                return row_id
        return None

    def set_filter(self, name: str) -> None:
        self._filter.set(name)
        self._on_filter()

    def select_visible(self) -> None:
        for candidate in self._candidates.values():
            group = self._group_for(candidate.entry.path)
            if group is not None and can_toggle(candidate, **self._toggle_flags(group, candidate)):
                self.selected.add(candidate.entry.path)
        self._rebuild()
        self._on_selection_changed()

    def clear_visible(self) -> None:
        for path in self.visible_paths():
            self.selected.discard(path)
        self._rebuild()
        self._on_selection_changed()

    def visible_paths(self) -> set[Path]:
        return {candidate.entry.path for candidate in self._candidates.values()}

    def move_paths(self) -> set[Path]:
        paths = {candidate.entry.path for candidate in self.selected_candidates()}
        paths.update(candidate.entry.path for move in self.content_moves() for candidate in move.selected)
        return paths

    def hidden_tick_count(self) -> int:
        visible = self.visible_paths()
        return sum(1 for path in self.selected if path not in visible)

    def selected_candidates(self) -> list[Candidate]:
        visible = self.visible_paths()
        return [candidate for group in self._groups for candidate in group.candidates
                if candidate.entry.path in self.selected
                and candidate.entry.path in visible
                and is_movable(candidate)]

    def content_moves(self) -> list[ContentMove]:
        protected = self._protected()
        visible = self.visible_paths()
        moves: list[ContentMove] = []
        for group in self._groups:
            if group.kind is not GroupKind.CONTENT:
                continue
            keeper = self._effective_keeper(group)
            if keeper is None:
                continue
            selected = tuple(
                candidate for candidate in group.candidates
                if candidate.entry.path in self.selected
                and candidate.entry.path in visible
                and candidate.entry.path != keeper.path
                and candidate.entry.path not in protected
            )
            if selected:
                moves.append(ContentMove(group.group_id, keeper, selected))
        return moves

    def has_move_selection(self) -> bool:
        return bool(self.selected_candidates() or self.content_moves())

    def toggle(self, row_id: str) -> None:
        candidate = self._candidates.get(row_id)
        group = self._group_for(candidate.entry.path) if candidate is not None else None
        if candidate is None or group is None or not can_toggle(candidate, **self._toggle_flags(group, candidate)):
            return
        path = candidate.entry.path
        if path in self.selected:
            self.selected.remove(path)
        else:
            self.selected.add(path)
        self.tree.set(row_id, "mark", selection_mark(candidate, self.selected, **self._toggle_flags(group, candidate)))
        self._on_selection_changed()

    def _on_filter(self, _event: tk.Event | None = None) -> None:
        self._rebuild()
        self._on_selection_changed()

    def _matches(self, candidate: Candidate) -> bool:
        current = self._filter.get()
        if current == _NAME:
            return candidate.status is CandidateStatus.CONFIRMED
        if current == _CONTENT:
            return candidate.status is CandidateStatus.CONTENT_MATCH
        if current == _PENDING:
            return candidate.status not in (CandidateStatus.CONFIRMED, CandidateStatus.CONTENT_MATCH)
        return True

    def _visible(self, group: DuplicateGroup) -> tuple[Candidate, ...]:
        return tuple(candidate for candidate in group.candidates if self._matches(candidate))

    def _rebuild(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._candidates.clear()
        self._paths.clear()
        for group in self._groups:
            rows = self._visible(group)
            if not rows:
                continue
            parent = self.tree.insert("", "end", iid=group.group_id, text=group_title(group), tags=("original",))
            if group.original is not None:
                self._paths[parent] = group.original.path
            for candidate in rows:
                row_id = f"row-{len(self._candidates)}"
                self._candidates[row_id] = candidate
                self._paths[row_id] = candidate.entry.path
                flags = self._toggle_flags(group, candidate)
                self.tree.insert(parent, "end", iid=row_id, values=(
                    selection_mark(candidate, self.selected, **flags),
                    candidate.entry.name,
                    format_size(candidate.entry.size),
                    format_timestamp(candidate.entry.mtime_ns),
                    format_timestamp(candidate.entry.created_ns),
                    self._row_status(group, candidate),
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
        content = self._group_for(path)
        keepable = content is not None and content.kind is GroupKind.CONTENT
        self._menu.entryconfig("Keep this file", state="normal" if keepable else "disabled")
        self._menu.tk_popup(event.x_root, event.y_root)

    def _keep_menu_target(self) -> None:
        if self._menu_path is not None:
            self.keep(self._menu_path)

    def _protected(self) -> set[Path]:
        return {
            group.original.path
            for group in self._groups
            if group.kind is GroupKind.NAME and group.original is not None
        }

    def _group_for(self, path: Path) -> DuplicateGroup | None:
        content_group = None
        name_group = None
        for group in self._groups:
            in_group = any(candidate.entry.path == path for candidate in group.candidates)
            is_original = group.original is not None and group.original.path == path
            if not in_group and not is_original:
                continue
            if group.kind is GroupKind.CONTENT and in_group:
                content_group = group
            else:
                name_group = group
        return content_group or name_group

    def _name_content_originals(self) -> set[Path]:
        """Unsuffixed files that have at least one hash-confirmed name copy."""
        return {
            group.original.path
            for group in self._groups
            if group.kind is GroupKind.NAME and group.original is not None and group.confirmed
        }

    def _effective_keeper(self, group: DuplicateGroup) -> FileEntry | None:
        """The one file in a content group that stays.

        A confirmed name original stays even if the user picks another file.
        Otherwise a manual choice wins, then the newest modified file.
        """
        if group.kind is not GroupKind.CONTENT or not group.candidates:
            return None
        entries = tuple(candidate.entry for candidate in group.candidates)
        originals = self._name_content_originals()
        preferred = choose_keeper(entries, originals)
        if preferred.path in originals:
            return preferred
        chosen = self._keepers.get(group.group_id)
        if chosen is not None:
            return next(candidate.entry for candidate in group.candidates if candidate.entry.path == chosen)
        return preferred

    def _toggle_flags(self, group: DuplicateGroup, candidate: Candidate) -> dict[str, bool]:
        keeper = self._effective_keeper(group)
        return {
            "keeper_chosen": keeper is not None,
            "is_keeper": keeper is not None and keeper.path == candidate.entry.path,
            "protected": candidate.entry.path in self._protected(),
        }

    def _row_status(self, group: DuplicateGroup, candidate: Candidate) -> str:
        if group.kind is not GroupKind.CONTENT:
            return status_text(candidate)
        keeper = self._keepers.get(group.group_id)
        entries = tuple(item.entry for item in group.candidates)
        suggested = choose_keeper(entries, self._name_content_originals()).path == candidate.entry.path
        if suggested and candidate.entry.path in self._name_content_originals():
            keeper = None
        return content_status(
            candidate,
            suggested=suggested and keeper is None,
            keeper=keeper == candidate.entry.path,
            protected=candidate.entry.path in self._protected(),
        )

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
