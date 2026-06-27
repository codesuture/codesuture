"""
codesuture.rewind.buffer
Thread-safe ring buffer for execution-timeline frame snapshots.

Captures call / return / exception moments so that when a crash occurs
the most recent N seconds of activity can be inspected — like a
flight-data recorder for Python processes.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from collections import deque

@dataclass(slots=True)
class FrameSnapshot:
    """One captured moment in the execution timeline."""

    timestamp: float
    event: str
    function: str
    module: str
    lineno: int
    args: Dict[str, str]                # function arguments as safe repr strings
    locals_snapshot: Dict[str, str]     # local variables as safe repr strings
    return_value: Optional[str] = None
    exception: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for JSON encoding."""
        return {
            'timestamp': self.timestamp,
            'event': self.event,
            'function': self.function,
            'module': self.module,
            'lineno': self.lineno,
            'args': self.args,
            'locals': self.locals_snapshot,
            'return_value': self.return_value,
            'exception': self.exception,
        }

class RewindBuffer:
    """Thread-safe circular buffer of :class:`FrameSnapshot` objects.

    Parameters
    ----------
    max_frames:
        Maximum number of snapshots to retain.  Oldest entries are silently
        evicted when the buffer is full.
    max_age_seconds:
        When dumping the buffer only snapshots younger than this many seconds
        (measured by ``time.monotonic``) are returned.
    """

    def __init__(self, max_frames: int = 500, max_age_seconds: float = 60.0) -> None:
        self._buffer: deque[FrameSnapshot] = deque(maxlen=max_frames)
        self._lock = threading.Lock()
        self._max_age = max_age_seconds
        self._enabled = True

    def record(self, snapshot: FrameSnapshot) -> None:
        """Append a snapshot.  No-op when the buffer is disabled."""
        if not self._enabled:
            return
        with self._lock:
            self._buffer.append(snapshot)

    def dump(self, last_n: int = 50) -> List[FrameSnapshot]:
        """Return the *last_n* most-recent snapshots within the age window."""
        with self._lock:
            now = time.monotonic()
            recent = [s for s in self._buffer if now - s.timestamp <= self._max_age]
            return list(recent[-last_n:])

    def dump_for_function(self, func_name: str, last_n: int = 20) -> List[FrameSnapshot]:
        """Return snapshots whose *function* field contains *func_name*."""
        with self._lock:
            return [s for s in self._buffer if func_name in s.function][-last_n:]

    def clear(self) -> None:
        """Discard all recorded snapshots."""
        with self._lock:
            self._buffer.clear()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)
