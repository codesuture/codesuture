import sys
import inspect
import os
import types
from codesuture.pattern_matcher import analyze_exception
from codesuture.guard_synthesizer import synthesize_guarded_code
from codesuture.code_replacer import replace_function_code, get_function_from_frame
from codesuture.frame_rewind import rewind_frame_to_start

def apply_fix(exc_type_name: str = None, exc_msg: str = None) -> str:

    exc_info = sys.exc_info()
    target_frame = None
    tb = None

    if exc_info[0] is not None:
        if exc_type_name is None:
            exc_type_name = exc_info[0].__name__
        if exc_msg is None:
            exc_msg = str(exc_info[1])

        tb = exc_info[2]
        curr_tb = tb
        while curr_tb and curr_tb.tb_next:
            curr_tb = curr_tb.tb_next
        if curr_tb:
            target_frame = curr_tb.tb_frame

    if target_frame is None:
        if exc_type_name is None or exc_msg is None:
            return "ERROR: No active exception. You must provide exc_type_name and exc_msg if paused on an unhandled exception."

        for fi in inspect.stack():
            frame = fi.frame
            filename = frame.f_code.co_filename
            func_name = frame.f_code.co_name

            if 'pydevd' in filename.lower() and func_name in ('evaluate_expression', 'new_func', '_run_with_unblock_threads', '_run_with_interrupt_thread'):
                potential_frame = frame.f_locals.get('frame')
                if isinstance(potential_frame, types.FrameType):
                    target_frame = potential_frame
                    break

            if func_name in ('apply_fix', 'apply_fix_with_info', '<module>', 'Exec', 'exec'):
                continue
            if any(x in filename.lower() for x in ('pydevd', 'debugpy', 'threading', 'importlib')):
                continue

            basename = os.path.basename(filename)
            internal_files = (
                'codesuture_fix.py', 'pattern_matcher.py', 'guard_synthesizer.py',
                'code_replacer.py', 'rewind.py', 'tracer.py', 'debuggee.py'
            )
            if basename in internal_files:
                continue

            target_frame = frame
            break

    if target_frame is None:
        return "ERROR: No paused frame found"

    class FakeExc:
        def __init__(self, msg):
            self._msg = msg
        def __str__(self):
            return self._msg

    exc_type = FakeExc
    exc_type.__name__ = exc_type_name
    exc_value = FakeExc(exc_msg)

    spec = analyze_exception(target_frame, exc_type, exc_value, tb)
    if spec is None:
        return f"ERROR: No deterministic patch for {exc_type_name}"

    try:
        func = get_function_from_frame(target_frame)
        new_bc = synthesize_guarded_code(target_frame.f_code, spec)
        new_code = new_bc.to_code()
        replace_function_code(func, new_code)
        rewind_frame_to_start(target_frame, new_code)
        return f"OK: patched {target_frame.f_code.co_name} ({spec.strategy} on {spec.var_name})"
    except Exception as e:
        return f"ERROR: {e}"