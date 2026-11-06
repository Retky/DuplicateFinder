"""Confirms same-name, same-size candidates by comparing SHA-256 digests with their original.

This is the only module that may mark a candidate CONFIRMED.
"""

import dataclasses
import hashlib
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from duplicate_finder.core.models import Candidate, CandidateStatus, DuplicateGroup, FileEntry
from duplicate_finder.core.winpath import to_os_path

CHUNK_SIZE = 1024 * 1024

ProgressCallback = Callable[[int, int], None]


class HashCancelled(Exception):
    pass


class FileChangedError(OSError):
    pass


def sha256_file(
    path: Path,
    cancel: threading.Event | None = None,
    on_bytes: Callable[[int], None] | None = None,
) -> str:
    digest = hashlib.sha256()
    buffer = bytearray(CHUNK_SIZE)
    view = memoryview(buffer)
    with open(to_os_path(path), "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise HashCancelled()
            count = f.readinto(buffer)
            if not count:
                break
            digest.update(view[:count])
            if on_bytes is not None:
                on_bytes(count)
    return digest.hexdigest()


def _message(exc: OSError) -> str:
    return exc.strerror or str(exc)


def _pending(group: DuplicateGroup) -> list[Candidate]:
    return [c for c in group.candidates if c.status is CandidateStatus.PENDING_HASH]


def _hash_entry(entry: FileEntry, report: Callable[[int], None], cancel: threading.Event | None) -> str:
    read = 0

    def on_bytes(count: int) -> None:
        nonlocal read
        read += count
        report(count)

    digest = sha256_file(entry.path, cancel, on_bytes)
    if read != entry.size:
        raise FileChangedError("File changed since scan")
    return digest


def _verify_group(
    group: DuplicateGroup,
    report: Callable[[int], None],
    cancel: threading.Event | None,
) -> tuple[DuplicateGroup, bool]:
    """Return the verified group and whether verification was cancelled."""
    if group.original is None:
        raise ValueError(f"group {group.group_id} has pending candidates but no original")

    try:
        original_sha256 = _hash_entry(group.original, report, cancel)
    except HashCancelled:
        return group, True
    except OSError as exc:
        error = f"Original could not be read: {_message(exc)}"
        candidates = tuple(
            dataclasses.replace(c, status=CandidateStatus.READ_ERROR, error=error)
            if c.status is CandidateStatus.PENDING_HASH else c
            for c in group.candidates
        )
        return dataclasses.replace(group, candidates=candidates), False

    candidates: list[Candidate] = []
    cancelled = False
    for candidate in group.candidates:
        if cancelled or candidate.status is not CandidateStatus.PENDING_HASH:
            candidates.append(candidate)
            continue
        try:
            digest = _hash_entry(candidate.entry, report, cancel)
        except HashCancelled:
            cancelled = True
            candidates.append(candidate)
            continue
        except OSError as exc:
            candidates.append(dataclasses.replace(candidate, status=CandidateStatus.READ_ERROR, error=_message(exc)))
            continue
        status = CandidateStatus.CONFIRMED if digest == original_sha256 else CandidateStatus.DIFFERENT_CONTENT
        candidates.append(dataclasses.replace(candidate, status=status, sha256=digest))

    verified = dataclasses.replace(group, original_sha256=original_sha256, candidates=tuple(candidates))
    return verified, cancelled


def verify_groups(
    groups: Sequence[DuplicateGroup],
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> list[DuplicateGroup]:
    """Verify every PENDING_HASH candidate.

    On cancellation the full list is still returned: candidates already decided keep
    their status and everything not yet verified stays PENDING_HASH (not movable).
    """
    total = sum(
        group.original.size + sum(c.entry.size for c in pending)
        for group in groups
        if (pending := _pending(group)) and group.original is not None
    )
    done = 0

    def report(count: int) -> None:
        nonlocal done
        done += count
        if on_progress is not None:
            on_progress(done, total)

    result: list[DuplicateGroup] = []
    cancelled = False
    for group in groups:
        if cancelled or not _pending(group):
            result.append(group)
            continue
        verified, cancelled = _verify_group(group, report, cancel)
        result.append(verified)
    return result
