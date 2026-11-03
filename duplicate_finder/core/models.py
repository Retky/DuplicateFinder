"""Shared, immutable data structures used across the core modules and the UI."""

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CandidateStatus(StrEnum):
    CONFIRMED = "confirmed"
    PENDING_HASH = "pending_hash"
    DIFFERENT_SIZE = "different_size"
    DIFFERENT_CONTENT = "different_content"
    NO_ORIGINAL = "no_original"
    AMBIGUOUS = "ambiguous"
    READ_ERROR = "read_error"


class SkipReason(StrEnum):
    DIRECTORY = "directory"
    LINK = "link"
    HIDDEN_OR_SYSTEM = "hidden_or_system"
    SYSTEM_NAME = "system_name"
    INCOMPLETE_DOWNLOAD = "incomplete_download"
    EMPTY = "empty"


def _check_sha256(value: str | None, field_name: str) -> None:
    if value is not None and not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be 64 lowercase hex characters, got {value!r}")


def _check_tuple(value: object, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple, got {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: Path
    name: str
    size: int
    mtime_ns: int

    def __post_init__(self) -> None:
        if not self.path.is_absolute():
            raise ValueError(f"path must be absolute: {self.path}")
        if self.name != self.path.name:
            raise ValueError(f"name {self.name!r} does not match path {self.path}")
        if self.size < 0:
            raise ValueError(f"size must be >= 0, got {self.size}")


@dataclass(frozen=True, slots=True)
class ParsedName:
    stem: str
    ext: str
    base: str
    base_key: str
    ext_key: str
    suffixes: tuple[str, ...]

    def __post_init__(self) -> None:
        _check_tuple(self.suffixes, "suffixes")

    @property
    def is_copy(self) -> bool:
        return bool(self.suffixes)


@dataclass(frozen=True, slots=True)
class Candidate:
    entry: FileEntry
    parsed: ParsedName
    status: CandidateStatus
    sha256: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _check_sha256(self.sha256, "sha256")
        if self.status is CandidateStatus.CONFIRMED and self.sha256 is None:
            raise ValueError(f"confirmed candidate without sha256: {self.entry.path}")


def is_movable(candidate: Candidate) -> bool:
    """Single source of truth for whether a candidate may be moved."""
    return candidate.status is CandidateStatus.CONFIRMED and candidate.sha256 is not None


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    group_id: str
    key: tuple[str, str]
    original: FileEntry | None
    original_sha256: str | None
    candidates: tuple[Candidate, ...]

    def __post_init__(self) -> None:
        _check_tuple(self.candidates, "candidates")
        _check_sha256(self.original_sha256, "original_sha256")
        for candidate in self.candidates:
            if candidate.status is not CandidateStatus.CONFIRMED:
                continue
            if self.original is None or self.original_sha256 is None:
                raise ValueError(f"group {self.group_id} has a confirmed candidate but no hashed original")
            if candidate.sha256 != self.original_sha256:
                raise ValueError(
                    f"group {self.group_id}: confirmed candidate {candidate.entry.path} "
                    "does not match the original's sha256"
                )

    @property
    def confirmed(self) -> tuple[Candidate, ...]:
        return tuple(c for c in self.candidates if is_movable(c))

    @property
    def movable_size(self) -> int:
        return sum(c.entry.size for c in self.confirmed)


@dataclass(frozen=True, slots=True)
class SkippedEntry:
    path: Path
    name: str
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class ScanError:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    folder: Path
    entries: tuple[FileEntry, ...]
    skipped: tuple[SkippedEntry, ...]
    errors: tuple[ScanError, ...]

    def __post_init__(self) -> None:
        _check_tuple(self.entries, "entries")
        _check_tuple(self.skipped, "skipped")
        _check_tuple(self.errors, "errors")
