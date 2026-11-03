"""Groups scanned files by normalized base name and extension, and classifies copies by size.

Content is not checked here: same-size copies leave as PENDING_HASH and only the
hasher (T06) may confirm them.
"""

from collections import defaultdict
from collections.abc import Iterable

from duplicate_finder.core.filename_parser import ALL_PATTERNS, parse_name
from duplicate_finder.core.models import (
    Candidate,
    CandidateStatus,
    DuplicateGroup,
    FileEntry,
    ParsedName,
)


def _status_for(copy: FileEntry, originals: list[FileEntry]) -> CandidateStatus:
    if not originals:
        return CandidateStatus.NO_ORIGINAL
    if len(originals) > 1:
        return CandidateStatus.AMBIGUOUS
    if copy.size != originals[0].size:
        return CandidateStatus.DIFFERENT_SIZE
    return CandidateStatus.PENDING_HASH


def _sort_key(entry: FileEntry) -> tuple[str, str]:
    return entry.name.casefold(), entry.name


def build_groups(entries: Iterable[FileEntry], enabled: frozenset[str] = ALL_PATTERNS) -> list[DuplicateGroup]:
    entries = list(entries)
    if len({entry.path for entry in entries}) != len(entries):
        raise ValueError("entries contain duplicate paths")
    if len({entry.path.parent for entry in entries}) > 1:
        raise ValueError("entries must all come from the same folder")

    buckets: dict[tuple[str, str], list[tuple[FileEntry, ParsedName]]] = defaultdict(list)
    for entry in entries:
        parsed = parse_name(entry.name, enabled)
        buckets[(parsed.base_key, parsed.ext_key)].append((entry, parsed))

    groups: list[DuplicateGroup] = []
    for key in sorted(buckets):
        members = sorted(buckets[key], key=lambda member: _sort_key(member[0]))
        originals = [entry for entry, parsed in members if not parsed.is_copy]
        copies = [(entry, parsed) for entry, parsed in members if parsed.is_copy]
        if not copies:
            continue

        candidates = tuple(
            Candidate(entry=entry, parsed=parsed, status=_status_for(entry, originals))
            for entry, parsed in copies
        )
        groups.append(
            DuplicateGroup(
                group_id=f"g{len(groups) + 1:04d}",
                key=key,
                original=originals[0] if len(originals) == 1 else None,
                original_sha256=None,
                candidates=candidates,
            )
        )
    return groups
