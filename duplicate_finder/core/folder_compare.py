"""Decides whether one folder tree is the same set as another, or a complete subset.

Names do not matter. Relative depth does. This does not choose an original and
does not move anything.
"""

import os
import threading
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from duplicate_finder.core.folder_scan import FolderTree
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
class _Node:
    files: tuple[Identity, ...]
    children: tuple["_Node", ...]


def compare_folder_sets(
    trees: Sequence[FolderTree],
    known_digests: Mapping[Path, str] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[FolderRelation, ...]:
    """Return every equal or subset pair. A cancelled hash returns no relations."""
    identities = _identities(trees, {} if known_digests is None else known_digests, cancel)
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
) -> dict[Path, Identity] | None:
    files = [item for tree in trees for item in tree.files]
    sizes = Counter(item.entry.size for item in files)
    identities: dict[Path, Identity] = {}
    for item in files:
        if cancel is not None and cancel.is_set():
            return None
        entry = item.entry
        extension = os.path.splitext(entry.name)[1].casefold()
        if sizes[entry.size] == 1:
            identities[entry.path] = ("unique", extension, entry.size)
            continue
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
                continue
            if read != entry.size:
                identities[entry.path] = ("unread", entry.path)
                continue
        identities[entry.path] = ("hash", extension, digest)
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
