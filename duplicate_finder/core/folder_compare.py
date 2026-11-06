"""Decides whether one folder tree is the same set as another, or a complete subset.

Names do not matter. Relative depth does. This does not choose an original and
does not move anything.
"""

import os
import threading
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from duplicate_finder.core.folder_scan import FolderFile, FolderTree
from duplicate_finder.core.hasher import HashCancelled, sha256_file

Identity = tuple[object, ...]


class RelationKind(StrEnum):
    EQUAL = "equal"
    LEFT_SUBSET = "left_subset"
    RIGHT_SUBSET = "right_subset"


@dataclass(frozen=True, slots=True)
class FolderRelation:
    """``left`` is covered by ``right``, they are equal, or ``right`` is covered by ``left``."""

    left: Path
    right: Path
    kind: RelationKind


@dataclass(frozen=True, slots=True)
class PairedFile:
    """One file in an equal-folder correspondence."""

    file: FolderFile
    digest: str


@dataclass(frozen=True, slots=True)
class _Node:
    files: tuple[Identity, ...]
    children: tuple["_Node", ...]


@dataclass(frozen=True, slots=True)
class _Placed:
    file: FolderFile
    identity: Identity


@dataclass(frozen=True, slots=True)
class _Dir:
    files: tuple[_Placed, ...]
    children: tuple["_Dir", ...]


def compare_folder_sets(
    trees: Sequence[FolderTree],
    known_digests: Mapping[Path, str] | None = None,
    cancel: threading.Event | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[FolderRelation, ...]:
    """Return every equal or subset pair. A cancelled hash returns no relations."""
    identities = _identities(
        trees, {} if known_digests is None else known_digests, cancel, on_progress,
    )
    if identities is None:
        return ()
    nodes = [(_tree_node(tree, identities), tree) for tree in trees]
    relations: list[FolderRelation] = []
    for index, (left, left_tree) in enumerate(nodes):
        if not _has_files(left):
            continue
        for right, right_tree in nodes[index + 1:]:
            if not _has_files(right):
                continue
            kind = _relation(left, right)
            if kind is not None:
                relations.append(FolderRelation(left_tree.path, right_tree.path, kind))
    return tuple(relations)


def _identities(
    trees: Sequence[FolderTree],
    known_digests: Mapping[Path, str],
    cancel: threading.Event | None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[Path, Identity] | None:
    files = [item for tree in trees for item in tree.files]
    sizes = Counter(item.entry.size for item in files)
    identities: dict[Path, Identity] = {}
    total = len(files)
    for index, item in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            return None
        entry = item.entry
        extension = os.path.splitext(entry.name)[1].casefold()
        if sizes[entry.size] == 1:
            identities[entry.path] = ("unique", extension, entry.size)
        else:
            digest = known_digests.get(entry.path)
            if digest is None:
                try:
                    read = 0

                    def on_bytes(count: int) -> None:
                        nonlocal read
                        read += count

                    digest = sha256_file(entry.path, cancel, on_bytes)
                except HashCancelled:
                    return None
                except OSError:
                    identities[entry.path] = ("unread", entry.path)
                    digest = None
                else:
                    if read != entry.size:
                        identities[entry.path] = ("unread", entry.path)
                        digest = None
            if digest is not None:
                identities[entry.path] = ("hash", extension, digest)
        if on_progress is not None and total:
            on_progress(index, total)
    return identities


def _tree_node(tree: FolderTree, identities: Mapping[Path, Identity]) -> _Node:
    items = [(folder_file.relative, identities[folder_file.entry.path]) for folder_file in tree.files]
    return _build(items)


def _build(items: list[tuple[tuple[str, ...], Identity]]) -> _Node:
    here: list[Identity] = []
    groups: dict[str, list[tuple[tuple[str, ...], Identity]]] = defaultdict(list)
    for relative, identity in items:
        if len(relative) == 1:
            here.append(identity)
        else:
            groups[relative[0]].append((relative[1:], identity))
    children = tuple(sorted((_build(members) for members in groups.values()), key=_signature))
    return _Node(tuple(sorted(here)), children)


def _signature(node: _Node) -> tuple[object, ...]:
    return (node.files, tuple(_signature(child) for child in node.children))


def _has_files(node: _Node) -> bool:
    return bool(node.files) or any(_has_files(child) for child in node.children)


def _relation(left: _Node, right: _Node) -> RelationKind | None:
    left_fits = _fits(left, right)
    right_fits = _fits(right, left)
    if left_fits and right_fits:
        return RelationKind.EQUAL
    if left_fits:
        return RelationKind.LEFT_SUBSET
    if right_fits:
        return RelationKind.RIGHT_SUBSET
    return None


def _fits(smaller: _Node, larger: _Node) -> bool:
    if not _files_fit(smaller.files, larger.files):
        return False
    return _assign(smaller.children, larger.children, 0, set())


def _files_fit(smaller: tuple[Identity, ...], larger: tuple[Identity, ...]) -> bool:
    needed = Counter(smaller)
    available = Counter(larger)
    return all(available[identity] >= count for identity, count in needed.items())


def _assign(smaller: tuple[_Node, ...], larger: tuple[_Node, ...], index: int, used: set[int]) -> bool:
    if index == len(smaller):
        return True
    for candidate, other in enumerate(larger):
        if candidate in used or not _fits(smaller[index], other):
            continue
        used.add(candidate)
        if _assign(smaller, larger, index + 1, used):
            return True
        used.remove(candidate)
    return False


def equal_correspondence(
    left: FolderTree,
    right: FolderTree,
    known_digests: Mapping[Path, str] | None = None,
    cancel: threading.Event | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[tuple[PairedFile, PairedFile], ...] | None:
    """Return one file pairing when the trees are equal.

    Several child pairings can fit. The one kept is the pairing whose paired
    relative paths sort first in case-folded order. Anything else returns None.
    """
    identities = _identities(
        (left, right), {} if known_digests is None else known_digests, cancel, on_progress,
    )
    if identities is None:
        return None
    return _equal_pairs(_directory(left, identities), _directory(right, identities))


def _directory(tree: FolderTree, identities: Mapping[Path, Identity]) -> _Dir:
    items = [(item.relative, _Placed(item, identities[item.entry.path])) for item in tree.files]
    return _build_dir(items)


def _build_dir(items: list[tuple[tuple[str, ...], _Placed]]) -> _Dir:
    here: list[_Placed] = []
    groups: dict[str, list[tuple[tuple[str, ...], _Placed]]] = defaultdict(list)
    for relative, placed in items:
        if len(relative) == 1:
            here.append(placed)
        else:
            groups[relative[0]].append((relative[1:], placed))
    children = tuple(
        _build_dir(members)
        for _, members in sorted(groups.items(), key=lambda item: item[0].casefold())
    )
    return _Dir(tuple(here), children)


def _equal_pairs(left: _Dir, right: _Dir) -> tuple[tuple[PairedFile, PairedFile], ...] | None:
    files = _pair_files(left.files, right.files)
    if files is None:
        return None
    children = _best_child_pairs(left.children, right.children, files)
    if children is None:
        return None
    return files + children


def _pair_files(
    left: tuple[_Placed, ...],
    right: tuple[_Placed, ...],
) -> tuple[tuple[PairedFile, PairedFile], ...] | None:
    left_groups: dict[Identity, list[_Placed]] = defaultdict(list)
    right_groups: dict[Identity, list[_Placed]] = defaultdict(list)
    for placed in left:
        left_groups[placed.identity].append(placed)
    for placed in right:
        right_groups[placed.identity].append(placed)
    left_counts = {identity: len(items) for identity, items in left_groups.items()}
    right_counts = {identity: len(items) for identity, items in right_groups.items()}
    if left_counts != right_counts:
        return None
    pairs: list[tuple[PairedFile, PairedFile]] = []
    for identity, left_items in left_groups.items():
        right_items = right_groups[identity]
        left_items.sort(key=lambda item: _relative_key(item.file.relative))
        right_items.sort(key=lambda item: _relative_key(item.file.relative))
        digest = identity[2] if identity[0] == "hash" else ""
        for left_item, right_item in zip(left_items, right_items, strict=True):
            pairs.append((PairedFile(left_item.file, str(digest)), PairedFile(right_item.file, str(digest))))
    return tuple(pairs)


def _best_child_pairs(
    left: tuple[_Dir, ...],
    right: tuple[_Dir, ...],
    fixed: tuple[tuple[PairedFile, PairedFile], ...],
) -> tuple[tuple[PairedFile, PairedFile], ...] | None:
    if len(left) != len(right):
        return None
    best_key: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] | None = None
    best: tuple[tuple[PairedFile, PairedFile], ...] | None = None

    def search(index: int, used: set[int], found: list[tuple[PairedFile, PairedFile]]) -> None:
        nonlocal best, best_key
        if index == len(left):
            key = _paired_paths([*fixed, *found])
            if best_key is None or key < best_key:
                best_key = key
                best = tuple(found)
            return
        for candidate, other in enumerate(right):
            if candidate in used:
                continue
            nested = _equal_pairs(left[index], other)
            if nested is None:
                continue
            used.add(candidate)
            found.extend(nested)
            search(index + 1, used, found)
            del found[-len(nested):]
            used.remove(candidate)

    search(0, set(), [])
    return best


def _paired_paths(
    pairs: list[tuple[PairedFile, PairedFile]],
) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    return tuple(sorted(
        (_relative_key(left.file.relative), _relative_key(right.file.relative))
        for left, right in pairs
    ))


def _relative_key(relative: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part.casefold() for part in relative)
