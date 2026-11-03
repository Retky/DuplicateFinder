"""Append-only JSON Lines log of each move run, readable after a crash and usable for a future restore."""

import json
import os
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from duplicate_finder import __version__
from duplicate_finder.core.models import MoveOutcome, PlannedMove
from duplicate_finder.core.winpath import to_os_path

_MAX_NAME_ATTEMPTS = 1000


def _now() -> datetime:
    return datetime.now().astimezone()


class OperationLogger:
    def __init__(self, path: Path, stream: TextIO, clock: Callable[[], datetime]) -> None:
        self.path = path
        self.run_id = uuid.uuid4().hex
        self._stream = stream
        self._clock = clock
        self._planned: dict[Path, PlannedMove] = {}
        self._results: dict[Path, MoveOutcome] = {}
        self._started = False
        self._finished = False
        self._closed = False

    @classmethod
    def create(cls, logs_dir: Path, clock: Callable[[], datetime] = _now) -> "OperationLogger":
        """Create a new, uniquely named log file. Existing logs are never overwritten."""
        try:
            os.mkdir(to_os_path(logs_dir))
        except FileExistsError:
            if not os.path.isdir(to_os_path(logs_dir)):
                raise
        stamp = clock().strftime("%Y%m%d_%H%M%S")
        for attempt in range(1, _MAX_NAME_ATTEMPTS + 1):
            suffix = "" if attempt == 1 else f"_{attempt}"
            path = logs_dir / f"run_{stamp}{suffix}.jsonl"
            try:
                stream = open(to_os_path(path), "x", encoding="utf-8", newline="\n")
            except FileExistsError:
                continue
            return cls(path, stream, clock)
        raise FileExistsError(f"could not find a free log name in {logs_dir}")

    def __enter__(self) -> "OperationLogger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _write(self, event: str, fields: dict[str, Any]) -> None:
        if self._closed or self._finished:
            raise RuntimeError("the log is already finished")
        record = {
            "event": event,
            "timestamp": self._clock().isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            **fields,
        }
        self._stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def start(
        self,
        folder: Path,
        dest_folder: Path,
        enabled_patterns: frozenset[str],
        planned: Sequence[PlannedMove],
    ) -> None:
        if self._started:
            raise RuntimeError("start() can only be called once")
        sources = [move.candidate.entry.path for move in planned]
        if len(set(sources)) != len(sources):
            raise ValueError("planned moves contain the same source twice")

        self._write("run_start", {
            "app_version": __version__,
            "folder": str(folder),
            "dest_folder": str(dest_folder),
            "enabled_patterns": sorted(enabled_patterns),
            "planned_count": len(planned),
            "planned_bytes": sum(move.candidate.entry.size for move in planned),
        })
        self._started = True
        for move in planned:
            entry = move.candidate.entry
            self._write("planned", {
                "group_id": move.group_id,
                "original_path": str(move.original.path),
                "source_path": str(entry.path),
                "dest_path": str(move.dest_path),
                "file_name": entry.name,
                "size_bytes": entry.size,
                "mtime_ns": entry.mtime_ns,
                "sha256": move.candidate.sha256,
            })
            self._planned[entry.path] = move

    def result(self, move: PlannedMove, outcome: MoveOutcome, error: str | None = None) -> None:
        if not self._started:
            raise RuntimeError("result() called before start()")
        source = move.candidate.entry.path
        if self._planned.get(source) != move:
            raise ValueError(f"move was not planned: {source}")
        if source in self._results:
            raise ValueError(f"result already recorded for {source}")
        if outcome is MoveOutcome.ERROR and not error:
            raise ValueError("an error outcome requires an error message")
        if outcome is MoveOutcome.MOVED and error:
            raise ValueError("a moved outcome cannot carry an error message")

        fields: dict[str, Any] = {
            "group_id": move.group_id,
            "source_path": str(source),
            "dest_path": str(move.dest_path),
            "file_name": move.candidate.entry.name,
            "size_bytes": move.candidate.entry.size,
            "sha256": move.candidate.sha256,
            "outcome": outcome.value,
        }
        if error:
            fields["error"] = error
        self._write("result", fields)
        self._results[source] = outcome

    def finish(self, cancelled: bool) -> dict[str, Any]:
        """Write run_end and return its fields. Safe to call even if some moves have no result."""
        totals = {outcome.value: 0 for outcome in MoveOutcome}
        for outcome in self._results.values():
            totals[outcome.value] += 1
        summary = {
            "totals": totals,
            "moved_bytes": sum(
                self._planned[source].candidate.entry.size
                for source, outcome in self._results.items()
                if outcome is MoveOutcome.MOVED
            ),
            "cancelled": cancelled,
            "unresolved": len(self._planned) - len(self._results),
        }
        self._write("run_end", summary)
        self._finished = True
        return summary

    def close(self) -> None:
        if not self._closed:
            self._stream.close()
            self._closed = True


def read_log(path: Path) -> list[dict[str, Any]]:
    """Read all events. A truncated last line (crash mid-write) is ignored; any other bad line raises."""
    with open(to_os_path(path), encoding="utf-8") as f:
        lines = f.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    events = []
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            if index == len(lines) - 1:
                break
            raise ValueError(f"invalid log line {index + 1} in {path}") from exc
    return events
