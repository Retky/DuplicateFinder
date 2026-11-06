"""Runs one analysis or move at a time and reports back through a queue. Never touches widgets."""

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Progress:
    text: str
    done: int
    total: int


@dataclass(frozen=True, slots=True)
class Succeeded:
    value: object


@dataclass(frozen=True, slots=True)
class Failed:
    error: Exception


class Worker:
    def __init__(self) -> None:
        self.messages: queue.Queue[Progress | Succeeded | Failed] = queue.Queue()
        self.cancel_event = threading.Event()
        self._thread: threading.Thread | None = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, function: Callable[..., object]) -> None:
        """Run ``function(cancel_event, report)`` on a background thread."""
        if self.running():
            raise RuntimeError("a task is already running")
        self.cancel_event.clear()

        def run() -> None:
            try:
                result = function(self.cancel_event, self.report)
            except Exception as exc:
                self.messages.put(Failed(exc))
            else:
                self.messages.put(Succeeded(result))

        self._thread = threading.Thread(target=run, name="duplicate-finder", daemon=True)
        self._thread.start()

    def report(self, progress: Progress) -> None:
        self.messages.put(progress)

    def cancel(self) -> None:
        self.cancel_event.set()

    def drain(self) -> list[Progress | Succeeded | Failed]:
        items: list[Progress | Succeeded | Failed] = []
        while True:
            try:
                items.append(self.messages.get_nowait())
            except queue.Empty:
                return items
