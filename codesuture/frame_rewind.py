"""
codesuture/frame_rewind.py
Low-level frame manipulation for CPython 3.11+.

Suppresses the active exception and rewinds the frame so the
interpreter re-executes the function from line 1 with patched bytecode.
"""
import ctypes
import logging
import sys

_log = logging.getLogger(__name__)

_F_LINENO_OFFSET = None

def _detect_f_lineno_offset(frame):
    """Scan PyFrameObject memory to find the f_lineno field offset at runtime."""
    global _F_LINENO_OFFSET
    known_lineno = frame.f_lineno
    base = id(frame)
    for offset in range(32, 76, 4):
        try:
            val = ctypes.c_int.from_address(base + offset).value
            if val == known_lineno:
                _F_LINENO_OFFSET = offset
                return offset
        except Exception:
            continue
    _log.warning(
        "codesuture.frame_rewind: could not detect f_lineno offset in "
        "PyFrameObject; ctypes fallback disabled"
    )
    return None

def rewind_frame(frame, new_code):
    """
    After code-object replacement, suppress the in-flight exception and
    force the interpreter to restart from the first line of the function.

    Returns True on success, False if the rewind could not be performed.
    """
    try:

        ctypes.pythonapi.PyErr_Clear()

        #    In CPython 3.11+ this internally updates prev_instr

        frame.f_lineno = new_code.co_firstlineno
        return True
    except (ValueError, OSError):
        pass

    # Fallback: ctypes struct-level write (CPython 3.11 ONLY)

    if sys.version_info >= (3, 12):
        return False
    try:
        global _F_LINENO_OFFSET
        offset = _F_LINENO_OFFSET
        if offset is None:
            offset = _detect_f_lineno_offset(frame)
        if offset is None:
            return False
        addr = id(frame) + offset
        ctypes.c_int.from_address(addr).value = new_code.co_firstlineno
        return True
    except Exception:
        return False

def rewind_frame_to_start(frame, code):
    """Legacy wrapper kept for backward compatibility."""
    return rewind_frame(frame, code)
