"""
codesuture/rewind.py
Low-level frame manipulation for CPython 3.11+.

Suppresses the active exception and rewinds the frame so the
interpreter re-executes the function from line 1 with patched bytecode.
"""
import ctypes
import sys


def rewind_frame(frame, new_code):
    """
    After code-object replacement, suppress the in-flight exception and
    force the interpreter to restart from the first line of the function.

    Returns True on success, False if the rewind could not be performed.
    """
    try:
        # 1. Clear the active exception from the thread state
        ctypes.pythonapi.PyErr_Clear()

        # 2. Rewind via the Python-level f_lineno setter.
        #    In CPython 3.11+ this internally updates prev_instr
        #    in _PyInterpreterFrame.  PyErr_Clear() above removes the
        #    "exception event" flag so the setter accepts the jump.
        frame.f_lineno = new_code.co_firstlineno
        return True
    except (ValueError, OSError):
        pass

    # Fallback: ctypes struct-level write (less safe, best-effort)
    try:
        # Python 3.11 PyFrameObject layout:
        #   PyObject_HEAD  (16 bytes)
        #   f_back         (8 bytes)  offset 16
        #   f_frame        (8 bytes)  offset 24
        #   f_trace        (8 bytes)  offset 32
        #   f_lineno       (4 bytes)  offset 40
        addr = id(frame) + 40
        ctypes.c_int.from_address(addr).value = new_code.co_firstlineno
        return True
    except Exception:
        return False


def rewind_frame_to_start(frame, code):
    """Legacy wrapper kept for backward compatibility."""
    return rewind_frame(frame, code)