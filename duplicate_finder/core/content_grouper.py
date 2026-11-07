"""Groups files that share an extension and a SHA-256, even when the names are unrelated.

Name-confirmed copies are excluded. Nothing here chooses a keeper or moves a file.
"""

import threading
from collections import defaultdict
from collections.abc import Callable, Collection, Sequence
from pathlib import Path

from duplicate_finder.core.filename_parser import parse_name
from duplicate_finder.core.hasher import HashCancelled, sha256_file
from duplicate_finder.core.models import (
    Candidate,
    CandidateStatus,
    DuplicateGroup,
    FileEntry,
    GroupKind,
)


ProgressCallback = Callable[[int, int], None]


def suggest_keeper(entries: Sequence[FileEntry]) -> FileEntry:
    """The newest modified file. Ties break by case-folded name, then the full name.

    Creation time is intentionally ignored.
    """
    if not entries:
        raise ValueError("a content group needs at least one file")
    return min(entries, key=lambda entry: (-entry.mtime_ns, entry.name.casefold(), entry.name))


def choose_keeper(entries: Sequence[FileEntry], name_originals: Collection[Path] = ()) -> FileEntry:
    """The file that stays in one same-hash set.

    A confirmed name original wins, even when another file was modified later.
    Otherwise the newest file stays.
    """
    originals = [entry for entry in entries if entry.path in name_originals]
    if originals:
        return suggest_keeper(originals)
    return suggest_keeper(entries)


def _confirmed_copies(groups: Sequence[DuplicateGroup]) -> set:
    return {
        candidate.entry.path
        for group in groups
        if group.kind is GroupKind.NAME
        for candidate in group.candidates
        if candidate.status is CandidateStatus.CONFIRMED
    }


def _known_digests(groups: Sequence[DuplicateGroup]) -> dict:
    digests = {}
    for group in groups:
        if group.kind is GroupKind.NAME and group.original is not None and group.original_sha256:
            digests[group.original.path] = group.original_sha256
    return digests


def build_content_groups(
    entries: Sequence[FileEntry],
    name_groups: Sequence[DuplicateGroup],
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    digests: dict[Path, str] | None = None,
) -> tuple[list[DuplicateGroup], bool]:
    """Return content groups and whether hashing was cancelled.

    A cancelled pass returns no content groups. Files with a unique size are not hashed.
    """
    excluded = _confirmed_copies(name_groups)
    eligible = [entry for entry in entries if entry.path not in excluded]
    buckets: dict[tuple[str, int], list[FileEntry]] = defaultdict(list)
    parsed = {}
    for entry in eligible:
        name = parse_name(entry.name)
        parsed[entry.path] = name
        buckets[(name.ext_key, entry.size)].append(entry)

    pending = [entry for members in buckets.values() if len(members) >= 2 for entry in members]
    if not pending:
        return [], False

    known = _known_digests(name_groups)
    if digests is not None:
        for path, digest in digests.items():
            known.setdefault(path, digest)
    total = sum(entry.size for entry in pending if entry.path not in known)
    done = 0

    def report(count: int) -> None:
        nonlocal done
        done += count
        if on_progress is not None:
            on_progress(done, total)

    found: dict = {}
    for entry in pending:
        if entry.path in known:
            found[entry.path] = known[entry.path]
            continue
        if cancel is not None and cancel.is_set():
            return [], True
        try:
            read = 0

            def on_bytes(count: int, _report: Callable[[int], None] = report) -> None:
                nonlocal read
                read += count
                _report(count)

            digest = sha256_file(entry.path, cancel, on_bytes)
        except HashCancelled:
            return [], True
        except (OSError, FileChangedError):
            continue
        if read != entry.size:
            continue
        found[entry.path] = digest
        if digests is not None:
            digests[entry.path] = digest

    grouped: dict[tuple[str, str], list[FileEntry]] = defaultdict(list)
    for entry in pending:
        digest = found.get(entry.path)
        if digest is None:
            continue
        grouped[(parsed[entry.path].ext_key, digest)].append(entry)

    groups: list[DuplicateGroup] = []
    next_id = len(name_groups) + 1
    for key in sorted(grouped):
        members = grouped[key]
        if len(members) < 2:
            continue
        digest = key[1]
        ordered = tuple(sorted(members, key=lambda entry: (entry.name.casefold(), entry.name)))
        candidates = tuple(
            Candidate(entry, parsed[entry.path], CandidateStatus.CONTENT_MATCH, digest)
            for entry in ordered
        )
        groups.append(DuplicateGroup(
            group_id=f"g{next_id:04d}",
            key=key,
            original=None,
            original_sha256=digest,
            candidates=candidates,
            kind=GroupKind.CONTENT,
        ))
        next_id += 1
    return groups, False
