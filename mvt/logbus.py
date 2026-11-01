"""Thread-safe log bus.

The engine runs on worker threads, the GUI lives on the Qt main thread.
The log bus is the bridge: workers push messages into a queue, the GUI drains
the queue with a timer.  No locks are needed across threads because
``queue.Queue`` already provides the required synchronisation.
"""

from __future__ import annotations

import itertools
import queue
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


@dataclass
class LogMessage:
    level: str
    text: str
    timestamp: float
    thread_id: Optional[int] = None


class LogBus:
    """Fan-in point for log messages coming from any thread."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[LogMessage]" = queue.Queue()
        self._counter = itertools.count(1)
        self._listeners: List[Callable[[LogMessage], None]] = []

    # -- producing -------------------------------------------------------- #
    def log(
        self,
        level: str,
        text: str,
        thread_id: Optional[int] = None,
    ) -> LogMessage:
        message = LogMessage(
            level=level.upper(),
            text=text,
            timestamp=time.time(),
            thread_id=thread_id,
        )
        self._queue.put(message)
        for callback in list(self._listeners):
            try:
                callback(message)
            except Exception:
                pass
        return message

    def info(self, text: str, thread_id: Optional[int] = None) -> None:
        self.log("INFO", text, thread_id)

    def success(self, text: str, thread_id: Optional[int] = None) -> None:
        self.log("SUCCESS", text, thread_id)

    def warn(self, text: str, thread_id: Optional[int] = None) -> None:
        self.log("WARN", text, thread_id)

    def error(self, text: str, thread_id: Optional[int] = None) -> None:
        self.log("ERROR", text, thread_id)

    # -- consuming -------------------------------------------------------- #
    def drain(self, limit: int = 500) -> List[LogMessage]:
        """Pop up to ``limit`` pending messages."""
        messages: List[LogMessage] = []
        for _ in range(limit):
            try:
                messages.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return messages

    def clear(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def subscribe(self, callback: Callable[[LogMessage], None]) -> Callable[[], None]:
        """Register a callback that is invoked for every new message.

        Used by the engine to mirror messages into a Python-side listener
        (e.g. for headless usage without the GUI).
        """
        listeners = self._listeners
        listeners.append(callback)

        def unsubscribe() -> None:
            try:
                listeners.remove(callback)
            except ValueError:
                pass

        return unsubscribe


class TeeListener:
    """Fan-out helper that keeps a list of listener callbacks per instance."""

    def __init__(self) -> None:
        self._listeners: List[Callable[[LogMessage], None]] = []

    def add(self, callback: Callable[[LogMessage], None]) -> None:
        self._listeners.append(callback)

    def dispatch(self, message: LogMessage) -> None:
        for callback in list(self._listeners):
            try:
                callback(message)
            except Exception:
                pass
