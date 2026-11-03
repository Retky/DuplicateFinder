import dataclasses
import json
from pathlib import Path

import pytest

from duplicate_finder.core.models import (
    Candidate,
    CandidateStatus,
    DuplicateGroup,
    FileEntry,
    MoveOutcome,
    ParsedName,
    PlannedMove,
    ScanResult,
    SkippedEntry,
    SkipReason,
    is_movable,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def make_entry(folder: Path, name: str, size: int = 100) -> FileEntry:
    return FileEntry(path=folder / name, name=name, size=size, mtime_ns=0)


def make_parsed(suffixes: tuple[str, ...] = ("paren_num",)) -> ParsedName:
    return ParsedName(
        stem="Foto (1)", ext=".jpg", base="Foto", base_key="foto", ext_key=".jpg", suffixes=suffixes
    )


def make_candidate(folder: Path, name: str, status: CandidateStatus, sha256: str | None = None,
                   size: int = 100) -> Candidate:
    return Candidate(entry=make_entry(folder, name, size), parsed=make_parsed(), status=status, sha256=sha256)


def make_group(folder: Path, candidates: tuple[Candidate, ...], original_sha256: str | None = HASH_A
               ) -> DuplicateGroup:
    return DuplicateGroup(
        group_id="g0001",
        key=("foto", ".jpg"),
        original=make_entry(folder, "Foto.jpg"),
        original_sha256=original_sha256,
        candidates=candidates,
    )


@pytest.mark.parametrize("status", list(CandidateStatus))
def test_is_movable_only_for_confirmed(tmp_path, status):
    sha256 = HASH_A if status is CandidateStatus.CONFIRMED else None
    candidate = make_candidate(tmp_path, "Foto (1).jpg", status, sha256)
    assert is_movable(candidate) is (status is CandidateStatus.CONFIRMED)


def test_non_confirmed_with_hash_is_not_movable(tmp_path):
    candidate = make_candidate(tmp_path, "Foto (1).jpg", CandidateStatus.DIFFERENT_CONTENT, HASH_B)
    assert not is_movable(candidate)


def test_confirmed_requires_sha256(tmp_path):
    with pytest.raises(ValueError):
        make_candidate(tmp_path, "Foto (1).jpg", CandidateStatus.CONFIRMED, None)


@pytest.mark.parametrize("bad_hash", ["", "abc", "A" * 64, "g" * 64, "a" * 63, "a" * 65])
def test_malformed_sha256_rejected(tmp_path, bad_hash):
    with pytest.raises(ValueError):
        make_candidate(tmp_path, "Foto (1).jpg", CandidateStatus.CONFIRMED, bad_hash)
    with pytest.raises(ValueError):
        make_group(tmp_path, (), original_sha256=bad_hash)


def test_group_rejects_confirmed_with_mismatched_hash(tmp_path):
    candidate = make_candidate(tmp_path, "Foto (1).jpg", CandidateStatus.CONFIRMED, HASH_B)
    with pytest.raises(ValueError):
        make_group(tmp_path, (candidate,), original_sha256=HASH_A)


def test_group_rejects_confirmed_without_original_hash(tmp_path):
    candidate = make_candidate(tmp_path, "Foto (1).jpg", CandidateStatus.CONFIRMED, HASH_A)
    with pytest.raises(ValueError):
        make_group(tmp_path, (candidate,), original_sha256=None)


def test_group_rejects_confirmed_without_original(tmp_path):
    candidate = make_candidate(tmp_path, "Foto (1).jpg", CandidateStatus.CONFIRMED, HASH_A)
    with pytest.raises(ValueError):
        DuplicateGroup(group_id="g0001", key=("foto", ".jpg"), original=None,
                       original_sha256=HASH_A, candidates=(candidate,))


def test_confirmed_and_movable_size_count_only_confirmed(tmp_path):
    candidates = (
        make_candidate(tmp_path, "Foto (1).jpg", CandidateStatus.CONFIRMED, HASH_A, size=100),
        make_candidate(tmp_path, "Foto (2).jpg", CandidateStatus.CONFIRMED, HASH_A, size=100),
        make_candidate(tmp_path, "Foto (3).jpg", CandidateStatus.DIFFERENT_SIZE, size=250),
        make_candidate(tmp_path, "Foto (4).jpg", CandidateStatus.DIFFERENT_CONTENT, HASH_B, size=100),
    )
    group = make_group(tmp_path, candidates)
    assert [c.entry.name for c in group.confirmed] == ["Foto (1).jpg", "Foto (2).jpg"]
    assert group.movable_size == 200


def test_empty_group_has_nothing_to_move(tmp_path):
    group = make_group(tmp_path, ())
    assert group.confirmed == ()
    assert group.movable_size == 0


def test_group_candidates_must_be_tuple(tmp_path):
    with pytest.raises(TypeError):
        make_group(tmp_path, [])


def test_models_are_frozen(tmp_path):
    candidate = make_candidate(tmp_path, "Foto (1).jpg", CandidateStatus.PENDING_HASH)
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.status = CandidateStatus.CONFIRMED


def test_replace_creates_new_validated_instance(tmp_path):
    pending = make_candidate(tmp_path, "Foto (1).jpg", CandidateStatus.PENDING_HASH)

    confirmed = dataclasses.replace(pending, status=CandidateStatus.CONFIRMED, sha256=HASH_A)
    assert confirmed is not pending
    assert pending.status is CandidateStatus.PENDING_HASH
    assert is_movable(confirmed)

    with pytest.raises(ValueError):
        dataclasses.replace(pending, status=CandidateStatus.CONFIRMED)


@pytest.mark.parametrize("suffixes, expected", [((), False), (("paren_num",), True),
                                                (("paren_num", "dash_copia"), True)])
def test_is_copy_follows_suffixes(suffixes, expected):
    assert make_parsed(suffixes).is_copy is expected


def test_parsed_suffixes_must_be_tuple():
    with pytest.raises(TypeError):
        make_parsed(["paren_num"])


def test_file_entry_rejects_relative_path():
    with pytest.raises(ValueError):
        FileEntry(path=Path("Foto.jpg"), name="Foto.jpg", size=1, mtime_ns=0)


def test_file_entry_rejects_mismatched_name(tmp_path):
    with pytest.raises(ValueError):
        FileEntry(path=tmp_path / "Foto.jpg", name="Other.jpg", size=1, mtime_ns=0)


def test_file_entry_rejects_negative_size(tmp_path):
    with pytest.raises(ValueError):
        make_entry(tmp_path, "Foto.jpg", size=-1)


def test_scan_result_collections_must_be_tuples(tmp_path):
    with pytest.raises(TypeError):
        ScanResult(folder=tmp_path, entries=[], skipped=(), errors=())


def test_enums_serialize_as_plain_strings(tmp_path):
    skipped = SkippedEntry(path=tmp_path / "desktop.ini", name="desktop.ini", reason=SkipReason.SYSTEM_NAME)
    payload = json.dumps({"status": CandidateStatus.CONFIRMED, "reason": skipped.reason,
                          "outcome": MoveOutcome.SKIPPED_EXISTS})
    assert json.loads(payload) == {"status": "confirmed", "reason": "system_name", "outcome": "skipped_exists"}


def make_planned(folder: Path, status: CandidateStatus = CandidateStatus.CONFIRMED,
                 dest: Path | None = None) -> PlannedMove:
    sha256 = HASH_A if status is CandidateStatus.CONFIRMED else None
    candidate = make_candidate(folder, "Foto (1).jpg", status, sha256)
    return PlannedMove(
        group_id="g0001",
        original=make_entry(folder, "Foto.jpg"),
        candidate=candidate,
        dest_path=dest if dest is not None else folder / "dups" / "Foto (1).jpg",
    )


def test_planned_move_accepts_confirmed_same_name(tmp_path):
    move = make_planned(tmp_path)
    assert move.dest_path.name == move.candidate.entry.name


@pytest.mark.parametrize("status", [s for s in CandidateStatus if s is not CandidateStatus.CONFIRMED])
def test_planned_move_rejects_non_confirmed(tmp_path, status):
    with pytest.raises(ValueError):
        make_planned(tmp_path, status)


def test_planned_move_rejects_rename(tmp_path):
    with pytest.raises(ValueError, match="never renamed"):
        make_planned(tmp_path, dest=tmp_path / "dups" / "Foto (1) (2).jpg")


def test_planned_move_rejects_same_folder(tmp_path):
    with pytest.raises(ValueError):
        make_planned(tmp_path, dest=tmp_path / "Foto (1).jpg")


def test_planned_move_rejects_relative_destination(tmp_path):
    with pytest.raises(ValueError):
        make_planned(tmp_path, dest=Path("dups") / "Foto (1).jpg")
