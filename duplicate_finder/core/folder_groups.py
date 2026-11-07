"""Collapses equal and subset relations into groups with one original folder.

A folder is in at most one group, and never as both the original and a duplicate.
This does not move anything.
"""

import threading
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from duplicate_finder.core.filename_parser import parse_name
from duplicate_finder.core.folder_compare import (
    FolderRelation,
    PairedFile,
    RelationKind,
    compare_folder_sets,
    equal_correspondence,
)
from duplicate_finder.core.folder_scan import FolderTree


@dataclass(frozen=True, slots=True)
class FolderGroup:
    """One folder that stays, and the folders that duplicate its set."""

    original: Path
    duplicates: tuple[Path, ...]


def group_folder_sets(
    trees: Sequence[FolderTree],
    cancel: threading.Event | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    digests: dict[Path, str] | None = None,
) -> tuple[FolderGroup, ...]:
    """Group subset relations so each group has one original and at least one duplicate.

    A cancelled comparison returns no groups.
    """
    if not trees or (cancel is not None and cancel.is_set()):
        return ()
    cache = {} if digests is None else digests
    by_path = {tree.path: tree for tree in trees}
    relations = compare_folder_sets(trees, known_digests=cache, cancel=cancel, on_progress=on_progress)
    if cancel is not None and cancel.is_set():
        return ()
    groups = _groups_from_relations(tuple(by_path), by_path, relations, cancel, on_progress, cache)
    if cancel is not None and cancel.is_set():
        return ()
    return tuple(_without_nested(groups))


def _groups_from_relations(
    paths: tuple[Path, ...],
    trees: dict[Path, FolderTree],
    relations: tuple[FolderRelation, ...],
    cancel: threading.Event | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    digests: dict[Path, str] | None = None,
) -> tuple[FolderGroup, ...]:
    contains: dict[Path, set[Path]] = defaultdict(set)
    parent = {path: path for path in paths}

    def find(path: Path) -> Path:
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    for relation in relations:
        if relation.kind is RelationKind.EQUAL:
            parent[find(relation.right)] = find(relation.left)
            continue
        smaller, larger = _smaller_and_larger(relation)
        contains[larger].add(smaller)

    smaller_paths = {path for contained in contains.values() for path in contained}
    maximals = [path for path in paths if path not in smaller_paths]
    classes: dict[Path, list[Path]] = defaultdict(list)
    for path in maximals:
        classes[find(path)].append(path)

    common = _common_parent(paths)
    attached: dict[Path, list[Path]] = defaultdict(list)
    for smaller in smaller_paths:
        options = [path for path in maximals if smaller in contains[path]]
        if options:
            attached[_prefer_container(options, common)].append(smaller)

    groups: list[FolderGroup] = []
    for members in classes.values():
        original = _original_of(members, trees, common, cancel, on_progress, digests)
        duplicates = [path for path in members if path != original]
        for path in members:
            duplicates.extend(attached[path])
        if duplicates:
            groups.append(FolderGroup(original, _ordered(duplicates, common)))
    return _ordered_groups(groups, common)


def _smaller_and_larger(relation: FolderRelation) -> tuple[Path, Path]:
    if relation.kind is RelationKind.LEFT_SUBSET:
        return relation.left, relation.right
    return relation.right, relation.left


def _original_of(
    members: list[Path],
    trees: dict[Path, FolderTree],
    common: Path,
    cancel: threading.Event | None,
    on_progress: Callable[[int, int], None] | None = None,
    digests: dict[Path, str] | None = None,
) -> Path:
    ordered = _ordered(members, common)
    winner = ordered[0]
    for other in ordered[1:]:
        winner = _prefer_equal(winner, other, trees, common, cancel, on_progress, digests)
    return winner


def _prefer_equal(
    left: Path,
    right: Path,
    trees: dict[Path, FolderTree],
    common: Path,
    cancel: threading.Event | None,
    on_progress: Callable[[int, int], None] | None = None,
    digests: dict[Path, str] | None = None,
) -> Path:
    left_copy = parse_name(left.name).is_copy
    right_copy = parse_name(right.name).is_copy
    if left_copy != right_copy:
        return right if left_copy else left
    later = _later_equal(trees[left], trees[right], cancel, on_progress, digests)
    if later is not None:
        return later
    return _ordered((left, right), common)[0]


def _prefer_container(options: list[Path], common: Path) -> Path:
    return min(options, key=lambda path: (parse_name(path.name).is_copy, _order_key(path, common)))


def _later_equal(
    left: FolderTree,
    right: FolderTree,
    cancel: threading.Event | None,
    on_progress: Callable[[int, int], None] | None = None,
    digests: dict[Path, str] | None = None,
) -> Path | None:
    pairs = equal_correspondence(
        left, right, known_digests=digests, cancel=cancel, on_progress=on_progress,
    )
    if not pairs:
        return None
    first_left, first_right = min(pairs, key=_file_key)
    if first_left.file.entry.mtime_ns == first_right.file.entry.mtime_ns:
        return None
    if first_left.file.entry.mtime_ns > first_right.file.entry.mtime_ns:
        return left.path
    return right.path


def _file_key(pair: tuple[PairedFile, PairedFile]) -> tuple[object, ...]:
    left, right = pair
    return (
        len(left.file.relative) - 1,
        left.file.entry.size,
        left.digest,
        tuple(part.casefold() for part in left.file.relative),
        tuple(part.casefold() for part in right.file.relative),
    )


def _without_nested(groups: tuple[FolderGroup, ...]) -> list[FolderGroup]:
    grouped = [group.original for group in groups]
    grouped.extend(duplicate for group in groups for duplicate in group.duplicates)
    kept: list[FolderGroup] = []
    for group in groups:
        if _is_under_any(group.original, grouped):
            continue
        duplicates = tuple(path for path in group.duplicates if not _is_under_any(path, grouped))
        if duplicates:
            kept.append(FolderGroup(group.original, duplicates))
    return kept


def _is_under_any(path: Path, grouped: list[Path]) -> bool:
    return any(path != other and path.is_relative_to(other) for other in grouped)


def _ordered(paths: Sequence[Path], common: Path) -> tuple[Path, ...]:
    return tuple(sorted(paths, key=lambda path: _order_key(path, common)))


def _ordered_groups(groups: list[FolderGroup], common: Path) -> tuple[FolderGroup, ...]:
    return tuple(sorted(groups, key=lambda group: _order_key(group.original, common)))


def _order_key(path: Path, common: Path) -> tuple[tuple[str, ...], str, str]:
    return (_relative_key(path, common), str(path).casefold(), str(path))


def _relative_key(path: Path, common: Path) -> tuple[str, ...]:
    try:
        relative = path.relative_to(common)
    except ValueError:
        relative = path
    return tuple(part.casefold() for part in relative.parts)


def _common_parent(paths: Sequence[Path]) -> Path:
    shared = paths[0].parts
    for path in paths[1:]:
        parts = path.parts
        count = 0
        for left, right in zip(shared, parts, strict=False):
            if left.casefold() != right.casefold():
                break
            count += 1
        shared = shared[:count]
        if not shared:
            break
    if not shared:
        return Path(paths[0].anchor)
    return Path(*shared)
