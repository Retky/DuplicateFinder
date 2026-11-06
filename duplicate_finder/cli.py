"""Console entry point. Only supports a read-only dry run: nothing is ever moved from here."""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from duplicate_finder import __version__
from duplicate_finder.core.analysis import AnalysisResult, Phase, analyze
from duplicate_finder.core.models import CandidateStatus, DuplicateGroup, is_movable
from duplicate_finder.core.scanner import FolderValidationError
from duplicate_finder.formatting import format_size

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

SizeFormatter = Callable[[int], str]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="duplicate_finder",
        description="Find duplicate copies in a folder. The console only supports a dry run; nothing is moved.",
    )
    parser.add_argument("folder", nargs="?", type=Path, help="folder to analyze (top level only)")
    parser.add_argument("--dry-run", action="store_true", help="analyze and report without moving anything")
    parser.add_argument("--show-skipped", action="store_true", help="list skipped files and the reason")
    parser.add_argument("--bytes", action="store_true", help="show exact sizes in bytes")
    parser.add_argument("--version", action="version", version=f"Duplicate Finder {__version__}")
    return parser


def _use_utf8_when_redirected(stream: TextIO) -> None:
    if stream.isatty():
        return
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _group_title(group: DuplicateGroup, size: SizeFormatter) -> str:
    if group.original is not None:
        return f"[{group.group_id}] {group.original.name} ({size(group.original.size)})"
    first = group.candidates[0]
    name = first.parsed.base + first.parsed.ext
    if first.status is CandidateStatus.AMBIGUOUS:
        return f"[{group.group_id}] {name} (ambiguous original)"
    return f"[{group.group_id}] {name} (no original)"


def _candidate_lines(group: DuplicateGroup, name_width: int, size_width: int, size: SizeFormatter) -> list[str]:
    lines = []
    for candidate in group.candidates:
        action = "[move]" if is_movable(candidate) else "[keep]"
        status = candidate.status.value.upper()
        if candidate.error:
            status = f"{status}: {candidate.error}"
        lines.append(
            f"  {action} {candidate.entry.name.ljust(name_width)}  "
            f"{size(candidate.entry.size).rjust(size_width)}  {status}"
        )
    return lines


def format_report(result: AnalysisResult, show_skipped: bool = False, exact_bytes: bool = False) -> str:
    size: SizeFormatter = (lambda n: f"{n} bytes") if exact_bytes else format_size
    scan = result.scan
    lines = [
        f"Folder: {result.folder}",
        f"Analyzed: {len(scan.entries)}   Skipped: {len(scan.skipped)}   Errors: {len(scan.errors)}",
    ]

    for group in result.groups:
        name_width = max(len(c.entry.name) for c in group.candidates)
        size_width = max(len(size(c.entry.size)) for c in group.candidates)
        lines += ["", _group_title(group, size), *_candidate_lines(group, name_width, size_width, size)]

    if scan.errors:
        lines += ["", "Scan errors:"]
        lines += [f"  {error.path.name}: {error.message}" for error in scan.errors]

    if show_skipped and scan.skipped:
        width = max(len(item.name) for item in scan.skipped)
        lines += ["", "Skipped files:"]
        lines += [f"  {item.name.ljust(width)}  {item.reason.value}" for item in scan.skipped]

    lines.append("")
    if not result.groups:
        lines.append("No duplicate candidates found.")
    lines.append(
        f"Summary: {result.groups_with_confirmed} group(s) with confirmed duplicates, "
        f"{result.confirmed_count} file(s), {size(result.movable_bytes)} to move."
    )
    lines.append("No files were moved (dry run).")
    return "\n".join(lines)


def _progress_printer(stream: TextIO) -> Callable[[Phase, int, int], None] | None:
    if not stream.isatty():
        return None

    def show(phase: Phase, done: int, total: int) -> None:
        if phase is Phase.SCAN:
            text = f"Scanning... {done}/{total}"
        elif phase is Phase.FOLDERS:
            text = f"Comparing folders... {done}/{total}"
        else:
            percent = done * 100 // total if total else 100
            text = f"Verifying... {percent}% ({format_size(done)} of {format_size(total)})"
        stream.write("\r" + text.ljust(60))
        stream.flush()

    return show


def _clear_progress(stream: TextIO) -> None:
    if stream.isatty():
        stream.write("\r" + " " * 60 + "\r")
        stream.flush()


def main(argv: list[str]) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.folder is None:
        parser.print_usage(sys.stderr)
        print("error: a FOLDER is required", file=sys.stderr)
        return EXIT_USAGE
    if not args.dry_run:
        parser.print_usage(sys.stderr)
        print("error: only --dry-run is supported from the console; nothing is moved", file=sys.stderr)
        return EXIT_USAGE

    _use_utf8_when_redirected(sys.stdout)
    try:
        result = analyze(args.folder, on_progress=_progress_printer(sys.stderr))
    except FolderValidationError as exc:
        _clear_progress(sys.stderr)
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        _clear_progress(sys.stderr)
        print("Interrupted. No files were moved.", file=sys.stderr)
        return EXIT_INTERRUPTED
    _clear_progress(sys.stderr)

    print(format_report(result, show_skipped=args.show_skipped, exact_bytes=args.bytes))
    return EXIT_OK
