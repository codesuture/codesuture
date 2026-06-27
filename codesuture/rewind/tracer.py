"""
codesuture.rewind.tracer
sys.settrace-compatible callback that feeds frame events into a
:class:`~codesuture.rewind.buffer.RewindBuffer`.

Only *call*, *return*, and *exception* events are captured — never *line*
events — to keep the performance overhead negligible.

Usage::

    from codesuture.rewind.tracer import enable_rewind, rewind_trace_callback
    import sys

    buf = enable_rewind(max_frames=1000, max_age=30.0)
    sys.settrace(rewind_trace_callback)
    # … run application code …
    for snap in buf.dump(last_n=20):
        print(snap.to_dict())
"""

import sys
import time
from typing import Any, Dict, Optional

from codesuture.rewind.buffer import RewindBuffer, FrameSnapshot

_buffer: Optional[RewindBuffer] = None

_SKIP_MODULES: frozenset[str] = frozenset({
    'codesuture', 'importlib', '_frozen_importlib',
    '_frozen_importlib_external', 'zipimport', '_bootstrap',
    '_bootstrap_external', 'threading', 'abc', 'codecs',
    'encodings', 'io', 'os', 'posixpath', 'ntpath', 'stat',
    'genericpath', 'fnmatch', '_collections_abc', 'typing', 'enum',
})

def _should_skip_frame(frame) -> bool:
    """Return True for internal Python, codesuture, and stdlib frames."""
    module: str = frame.f_globals.get('__name__', '')
    if not module:
        return True
    first_part = module.split('.')[0]
    return first_part in _SKIP_MODULES

def _safe_repr(obj: object, max_len: int = 200) -> str:
    """``repr()`` that never raises and truncates long output."""
    try:
        r = repr(obj)
        if len(r) > max_len:
            return r[:max_len - 3] + '...'
        return r
    except Exception:
        try:
            return f'<{type(obj).__name__}>'
        except Exception:
            return '<unprintable>'

def _safe_copy_args(frame) -> Dict[str, str]:
    """Capture function arguments as safe repr strings."""
    try:
        code = frame.f_code
        nargs = code.co_argcount + code.co_posonlyargcount
        if code.co_flags & 0x04:
            nargs += 1
        if code.co_flags & 0x08:
            nargs += 1
        arg_names = code.co_varnames[:nargs]
        result: Dict[str, str] = {}
        for name in arg_names:
            if name in frame.f_locals:
                result[name] = _safe_repr(frame.f_locals[name])
        return result
    except Exception:
        return {}

def _safe_copy_locals(frame) -> Dict[str, str]:
    """Capture local variables as safe repr strings (capped at 20)."""
    try:
        result: Dict[str, str] = {}
        for i, (k, v) in enumerate(frame.f_locals.items()):
            if i >= 20:
                result['...'] = f'({len(frame.f_locals) - 20} more)'
                break
            result[k] = _safe_repr(v)
        return result
    except Exception:
        return {}

def get_buffer() -> Optional[RewindBuffer]:
    """Return the module-level buffer, or ``None`` if rewind is not enabled."""
    return _buffer

def enable_rewind(max_frames: int = 500, max_age: float = 60.0) -> RewindBuffer:
    """Create (or replace) the global :class:`RewindBuffer` and return it.

    .. note::
        The caller is still responsible for installing
        :func:`rewind_trace_callback` via ``sys.settrace``.
    """
    global _buffer
    _buffer = RewindBuffer(max_frames=max_frames, max_age_seconds=max_age)
    return _buffer

def disable_rewind() -> None:
    """Disable recording and discard the buffer."""
    global _buffer
    if _buffer is not None:
        _buffer.enabled = False
    _buffer = None

def rewind_trace_callback(frame, event: str, arg):
    """``sys.settrace`` compatible callback for rewind capture.

    Only processes *call*, *return*, and *exception* events.  Always
    returns itself so CPython continues to deliver per-function events
    without switching to expensive *line* tracing.
    """
    buf = _buffer
    if buf is None or not buf.enabled:
        return rewind_trace_callback

    if _should_skip_frame(frame):
        return rewind_trace_callback

    try:
        if event == 'call':
            snapshot = FrameSnapshot(
                timestamp=time.monotonic(),
                event='call',
                function=(
                    getattr(frame.f_code, 'co_qualname', '') or frame.f_code.co_name
                ),
                module=frame.f_globals.get('__name__', '?'),
                lineno=frame.f_lineno,
                args=_safe_copy_args(frame),
                locals_snapshot={},
            )
            buf.record(snapshot)

        elif event == 'return':
            snapshot = FrameSnapshot(
                timestamp=time.monotonic(),
                event='return',
                function=(
                    getattr(frame.f_code, 'co_qualname', '') or frame.f_code.co_name
                ),
                module=frame.f_globals.get('__name__', '?'),
                lineno=frame.f_lineno,
                args={},
                locals_snapshot=_safe_copy_locals(frame),
                return_value=_safe_repr(arg),
            )
            buf.record(snapshot)

        elif event == 'exception':
            exc_type, exc_value, _ = arg
            snapshot = FrameSnapshot(
                timestamp=time.monotonic(),
                event='exception',
                function=(
                    getattr(frame.f_code, 'co_qualname', '') or frame.f_code.co_name
                ),
                module=frame.f_globals.get('__name__', '?'),
                lineno=frame.f_lineno,
                args={},
                locals_snapshot=_safe_copy_locals(frame),
                exception=f'{exc_type.__name__}: {exc_value}',
            )
            buf.record(snapshot)

    except Exception:
        pass  # Never crash the tracer

    return rewind_trace_callback
