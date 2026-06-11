"""
codesuture.rewind — Crash forensics black-box recorder.

Provides a rolling window of function call/return/exception snapshots
so engineers can see what happened in the seconds leading up to a crash.
"""

from codesuture.rewind.buffer import RewindBuffer, FrameSnapshot
from codesuture.rewind.tracer import enable_rewind, disable_rewind, get_buffer

__all__ = [
    "RewindBuffer",
    "FrameSnapshot",
    "enable_rewind",
    "disable_rewind",
    "get_buffer",
]
