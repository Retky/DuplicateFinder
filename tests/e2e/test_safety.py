"""End-to-end checks that analysis plus move never loses or renames a file."""

import hashlib
import os
import stat
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import pytest

from duplicate_finder.core.analysis import AnalysisResult, analyze
from duplicate_finder.core.file_mover import DestinationError, MoveReport, move_duplicates
from duplicate_finder.core.filename_parser import parse_name
from duplicate_finder.core.models import MoveOutcome
from duplicate_finder.core.operation_logger import read_log
from duplicate_finder.core.winpath import to_os_path
from tests.e2e.fixtures import build_folder
from tests.winhelpers import add_attributes, exclusive_lock

SAME = b"same-bytes"


@dataclass(frozen=True, slots=True)
class Item:
    name: str
    sha256: str
    location: str


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(to_os_path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(directory: Path, location: str) -> list[Item]:
    items: list[Item] = []
    with os.scandir(to_os_path(directory)) as iterator:
        for entry in iterator:
            if entry.is_dir(follow_symlinks=False) or not entry.is_file(follow_symlinks=False):
                continue
            items.append(Item(entry.name, _hash(directory / entry.name), location))
    return items


def _is_directory(path: Path) -> bool:
    try:
        info = os.lstat(to_os_path(path))
    except OSError:
        return False
    linked = stat.S_ISLNK(info.st_mode) or bool(info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return not linked and stat.S_ISDIR(info.st_mode)


def snapshot(folder: Path) -> tuple[Item, ...]:
    """Files in the source folder, loose files in ``_dups``, and files in the destination subfolders.

    The operation log is not part of the data.
    """
    items = _files(folder, "source")
    dups = folder / "_dups"
    if _is_directory(dups):
        items.extend(_files(dups, "dups"))
        for name in ("01_Name", "02_Content"):
            nested = dups / name
            if _is_directory(nested):
                items.extend(_files(nested, "dups"))
    return tuple(items)


def confirmed(analysis: AnalysisResult):
    return [candidate for group in analysis.groups for candidate in group.confirmed]


def moved_names(report: MoveReport | None) -> set[str]:
    if report is None:
        return set()
    return {result.move.candidate.entry.name for result in report.results
            if result.outcome is MoveOutcome.MOVED}


def outcomes(report: MoveReport) -> dict[str, MoveOutcome]:
    return {result.move.candidate.entry.name: result.outcome for result in report.results}


def assert_safe(before: tuple[Item, ...], after: tuple[Item, ...], report: MoveReport | None) -> None:
    assert Counter((item.name, item.sha256) for item in before) == Counter(
        (item.name, item.sha256) for item in after
    )

    before_source = {(item.name, item.sha256) for item in before if item.location == "source"}
    before_dups = {item.name for item in before if item.location == "dups"}
    after_source = {(item.name, item.sha256) for item in after if item.location == "source"}
    remaining_hashes = {item.sha256 for item in after if item.location == "source"}

    for item in before:
        if item.location == "source" and not parse_name(item.name).is_copy:
            assert (item.name, item.sha256) in after_source

    arrived: list[Item] = []
    for item in after:
        if item.location == "dups" and item.name not in before_dups:
            assert (item.name, item.sha256) in before_source
            assert item.sha256 in remaining_hashes
            arrived.append(item)

    if report is None or report.log_path is None:
        assert arrived == []
        if report is not None:
            assert report.results == ()
        return

    events = read_log(report.log_path)
    planned = [event for event in events if event["event"] == "planned"]
    results = [event for event in events if event["event"] == "result"]
    assert [event["source_path"] for event in planned] == [event["source_path"] for event in results]
    assert len(report.results) == len(planned)
    assert events[-1]["event"] == "run_end"
    assert events[-1]["unresolved"] == 0


def _assert_inside(root: Path, folder: Path, report: MoveReport | None) -> None:
    assert folder.is_relative_to(root)
    if report is None:
        return
    assert report.dest_folder.is_relative_to(root)
    if report.log_path is not None:
        assert report.log_path.is_relative_to(root)
    for result in report.results:
        assert result.move.original.path.is_relative_to(root)
        assert result.move.candidate.entry.path.is_relative_to(root)
        assert result.move.dest_path.is_relative_to(root)


def exercise(
    folder: Path,
    root: Path,
    *,
    after_analyze: Callable[[AnalysisResult], None] | None = None,
    around_move: Callable[[], Iterator[None]] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    expect_destination_error: bool = False,
) -> tuple[AnalysisResult, MoveReport | None]:
    analysis = analyze(folder)
    if after_analyze is not None:
        after_analyze(analysis)
    before = snapshot(folder)
    selected = confirmed(analysis)
    with around_move() if around_move is not None else nullcontext():
        try:
            report: MoveReport | None = move_duplicates(
                analysis, selected, on_progress=on_progress, cancel=cancel,
            )
        except DestinationError:
            if not expect_destination_error:
                raise
            report = None
        else:
            if expect_destination_error:
                raise AssertionError("expected DestinationError")
    after = snapshot(folder)
    assert_safe(before, after, report)
    _assert_inside(root, folder, report)
    return analysis, report


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        pytest.param({"Photo.jpg": SAME, "Photo (1).jpg": SAME}, {"Photo (1).jpg"}, id="identical-copy"),
        pytest.param(
            {"Photo.jpg": SAME, "Photo (1).jpg": SAME, "Photo (2).jpg": SAME, "Photo (3).jpg": SAME},
            {"Photo (1).jpg", "Photo (2).jpg", "Photo (3).jpg"},
            id="several-copies",
        ),
        pytest.param({"Photo.jpg": b"short", "Photo (1).jpg": b"much-longer"}, set(), id="different-size"),
        pytest.param({"Photo.jpg": SAME, "Other.jpg": SAME}, set(), id="different-names"),
        pytest.param(
            {"Pag.jpg": SAME, "Pag-1.jpg": SAME, "Pag 1.jpg": SAME, "Pag-2.jpg": b"diff-bytes"},
            {"Pag-1.jpg"},
            id="sequential-names",
        ),
        pytest.param({"Photo (1).jpg": SAME, "Photo (2).jpg": SAME}, set(), id="no-original"),
        pytest.param({"Photo.jpg": SAME, "Photo (1).png": SAME}, set(), id="different-extension"),
        pytest.param({"Photo.jpg": b"AAAA", "Photo (1).jpg": b"BBBB"}, set(), id="different-content"),
        pytest.param(
            {"Photo.jpg": SAME, "Photo (1).jpg": SAME, "Photo (2).jpg": b"diff-bytes"},
            {"Photo (1).jpg"},
            id="mixed-group",
        ),
        pytest.param({"Foto.jpg": SAME, "foto (1).JPG": SAME}, {"foto (1).JPG"}, id="different-case"),
        pytest.param(
            {"Foto.jpg": SAME, "Foto - copia (2).jpg": SAME, "Foto (1) (1).jpg": SAME},
            {"Foto - copia (2).jpg", "Foto (1) (1).jpg"},
            id="stacked-suffixes",
        ),
        pytest.param({"Batman.cbz": SAME, "Batman (1989).cbz": SAME}, set(), id="year-is-not-a-suffix"),
    ],
)
def test_exact_copies_only(tmp_path, files, expected):
    folder = tmp_path / "library"
    build_folder(folder, files)
    _analysis, report = exercise(folder, tmp_path)
    assert moved_names(report) == expected


def test_existing_dups_folder_keeps_its_files(tmp_path):
    folder = tmp_path / "library"
    build_folder(folder, {"Photo.jpg": SAME, "Photo (1).jpg": SAME})
    build_folder(folder / "_dups", {"keep.txt": b"keep-me"})

    _analysis, report = exercise(folder, tmp_path)

    assert moved_names(report) == {"Photo (1).jpg"}
    assert (folder / "_dups" / "keep.txt").read_bytes() == b"keep-me"
    assert (folder / "_dups" / "01_Name" / "Photo (1).jpg").read_bytes() == SAME


def test_name_already_in_dups_is_left_alone(tmp_path):
    folder = tmp_path / "library"
    build_folder(folder, {"Photo.jpg": SAME, "Photo (1).jpg": SAME})
    build_folder(folder / "_dups" / "01_Name", {"Photo (1).jpg": b"already-there"})

    _analysis, report = exercise(folder, tmp_path)

    assert report is not None
    assert outcomes(report) == {"Photo (1).jpg": MoveOutcome.SKIPPED_EXISTS}
    assert (folder / "Photo (1).jpg").read_bytes() == SAME
    assert (folder / "_dups" / "01_Name" / "Photo (1).jpg").read_bytes() == b"already-there"


def test_locked_copy_is_reported_and_the_run_continues(tmp_path):
    folder = tmp_path / "library"
    build_folder(folder, {"Photo.jpg": SAME, "Photo (1).jpg": SAME, "Photo (2).jpg": SAME})

    _analysis, report = exercise(folder, tmp_path, around_move=lambda: exclusive_lock(folder / "Photo (1).jpg"))

    assert report is not None
    assert outcomes(report) == {"Photo (1).jpg": MoveOutcome.ERROR, "Photo (2).jpg": MoveOutcome.MOVED}
    assert (folder / "Photo (1).jpg").read_bytes() == SAME
    assert (folder / "_dups" / "01_Name" / "Photo (2).jpg").read_bytes() == SAME


def test_ignored_files_stay_put(tmp_path):
    folder = tmp_path / "library"
    build_folder(folder, {
        "Photo.jpg": SAME,
        "Photo (1).jpg": SAME,
        "Photo (2).jpg": SAME,
        "desktop.ini": b"[.ShellClassInfo]",
        "download.part": b"partial",
        "empty.jpg": b"",
    })
    add_attributes(folder / "Photo (2).jpg", stat.FILE_ATTRIBUTE_HIDDEN)

    _analysis, report = exercise(folder, tmp_path)

    assert moved_names(report) == {"Photo (1).jpg"}
    for name in ("Photo (2).jpg", "desktop.ini", "download.part", "empty.jpg"):
        assert (folder / name).is_file()


def test_file_changed_after_analysis_stays(tmp_path):
    folder = tmp_path / "library"
    build_folder(folder, {"Photo.jpg": SAME, "Photo (1).jpg": SAME})
    edited = b"edited-after-scan"

    def rewrite(_analysis: AnalysisResult) -> None:
        with open(to_os_path(folder / "Photo (1).jpg"), "wb") as handle:
            handle.write(edited)

    _analysis, report = exercise(folder, tmp_path, after_analyze=rewrite)

    assert report is not None
    assert outcomes(report) == {"Photo (1).jpg": MoveOutcome.SKIPPED_CHANGED}
    assert (folder / "Photo (1).jpg").read_bytes() == edited
    assert (folder / "Photo.jpg").read_bytes() == SAME


def test_cancel_mid_move_leaves_the_rest(tmp_path):
    folder = tmp_path / "library"
    build_folder(folder, {
        "Photo.jpg": SAME,
        "Photo (1).jpg": SAME,
        "Photo (2).jpg": SAME,
        "Photo (3).jpg": SAME,
    })
    cancel = threading.Event()

    def stop_after_first(done: int, _total: int) -> None:
        if done == 1:
            cancel.set()

    analysis, report = exercise(folder, tmp_path, on_progress=stop_after_first, cancel=cancel)

    assert report is not None and report.cancelled
    selected = [candidate.entry.name for candidate in confirmed(analysis)]
    assert selected == ["Photo (1).jpg", "Photo (2).jpg", "Photo (3).jpg"]
    assert outcomes(report) == {
        "Photo (1).jpg": MoveOutcome.MOVED,
        "Photo (2).jpg": MoveOutcome.CANCELLED,
        "Photo (3).jpg": MoveOutcome.CANCELLED,
    }
    assert (folder / "Photo (2).jpg").is_file()
    assert (folder / "Photo (3).jpg").is_file()


def test_dups_as_a_file_moves_nothing(tmp_path):
    folder = tmp_path / "library"
    build_folder(folder, {"Photo.jpg": SAME, "Photo (1).jpg": SAME, "_dups": b"not-a-folder"})

    _analysis, report = exercise(folder, tmp_path, expect_destination_error=True)

    assert report is None
    assert not (folder / "_dups" / "_logs").exists()
    assert (folder / "_dups").is_file()
    assert (folder / "Photo.jpg").read_bytes() == SAME
    assert (folder / "Photo (1).jpg").read_bytes() == SAME


def test_long_path_loses_nothing(tmp_path):
    folder = tmp_path
    while len(str(folder)) < 280:
        folder = folder / ("d" * 50)
    build_folder(folder, {"Photo.jpg": SAME, "Photo (1).jpg": SAME})
    assert len(str(folder)) > 260

    _analysis, report = exercise(folder, tmp_path)

    assert report is not None
    copy = folder / "Photo (1).jpg"
    if moved_names(report):
        assert moved_names(report) == {"Photo (1).jpg"}
        assert os.path.exists(to_os_path(folder / "_dups" / "01_Name" / "Photo (1).jpg"))
    else:
        assert outcomes(report) == {"Photo (1).jpg": MoveOutcome.ERROR}
        assert os.path.exists(to_os_path(copy))


def test_second_run_moves_nothing_new(tmp_path):
    folder = tmp_path / "library"
    build_folder(folder, {"Photo.jpg": SAME, "Photo (1).jpg": SAME})

    _first, first_report = exercise(folder, tmp_path)
    assert moved_names(first_report) == {"Photo (1).jpg"}

    again, second_report = exercise(folder, tmp_path)

    assert again.confirmed_count == 0
    assert second_report is not None
    assert second_report.results == ()
    assert second_report.log_path is None
    assert (folder / "Photo.jpg").read_bytes() == SAME
    assert (folder / "_dups" / "01_Name" / "Photo (1).jpg").read_bytes() == SAME


def test_nfc_and_nfd_originals_move_nothing(tmp_path):
    folder = tmp_path / "library"
    nfc = unicodedata.normalize("NFC", "Canción.jpg")
    nfd = unicodedata.normalize("NFD", "Canción.jpg")
    copy = unicodedata.normalize("NFC", "Canción (1).jpg")
    assert nfc != nfd
    build_folder(folder, {nfc: SAME, nfd: SAME, copy: SAME})
    present = {item.name for item in snapshot(folder)}
    assert {nfc, nfd, copy} <= present

    _analysis, report = exercise(folder, tmp_path)

    assert moved_names(report) == set()
    assert {item.name for item in snapshot(folder) if item.location == "source"} >= {nfc, nfd, copy}
