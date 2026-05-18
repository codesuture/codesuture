"""
Synthesises guard + original bytecode for all deterministic strategies.
"""
import ctypes
from bytecode import Bytecode, Instr, Label, Compare
from codesuture.pattern_matcher import PatchSpec

def _force_despecialize(func):
    """
    Force CPython 3.11+ to abandon its adaptive
    instruction cache for this function.
    After __code__ replacement, the interpreter must
    re-read the new bytecode from scratch.
    """
    try:
        # PyFunction_SetCode forces de-specialization
        # by going through the official C API path
        # rather than the Python attribute setter.
        ctypes.pythonapi.PyFunction_SetCode(
            ctypes.py_object(func),
            ctypes.py_object(func.__code__)
        )
    except Exception:
        pass  # Non-fatal: patch still applied, may not
              # take effect until next function cold-start


class PatchValidationError(Exception):
    pass

class PatchRejectedError(Exception):
    pass

def validate_patch(original_code, patched_code):
    import dis

    _SYNTH_INTERNAL_NAMES = frozenset({
        '_codesuture_cont', '_codesuture_key', '_lp_chain',
    })
    allowed = set(original_code.co_varnames) | _SYNTH_INTERNAL_NAMES
    for instr in dis.get_instructions(patched_code):
        if instr.opname == 'LOAD_FAST':
            name = instr.argval
            if name not in allowed:
                raise PatchValidationError(f"Patch rejected: LOAD_FAST '{name}' not in co_varnames â€” bytecode would corrupt frame. Patch was not applied.")

def propagate_patch(original_func, patched_code) -> int:
    import gc
    import logging
    if original_func is None:
        return 0
    if not hasattr(original_func, '__code__'):
        return 0
    name = getattr(original_func, '__qualname__', '') or \
           getattr(original_func, '__name__', '')
    if '<listcomp>' in name or '<genexpr>' in name or \
       '<dictcomp>' in name or '<setcomp>' in name:
        import logging
        logging.getLogger(__name__).debug(
            "[CodeSuture] Skipping %s — "
            "comprehensions are not patchable via __code__", name
        )
        return 0

    original_code = original_func.__code__
    propagated = 0

    for ref in gc.get_referrers(original_code):
        if ref is original_func:
            continue

        if hasattr(ref, '__func__') and hasattr(ref.__func__, '__code__'):
            if ref.__func__.__code__ is original_code:
                ref.__func__.__code__ = patched_code
                _force_despecialize(ref.__func__)
                propagated += 1

        elif hasattr(ref, '__code__') and ref.__code__ is original_code:
            ref.__code__ = patched_code
            _force_despecialize(ref)
            propagated += 1

    original_func.__code__ = patched_code
    _force_despecialize(original_func)

    if propagated > 0:
        print(f"[CodeSuture] Propagated patch to {propagated} additional "
              f"live reference(s) of {original_func.__qualname__}.")
    return propagated

def _is_inside_try_block(code):
    """Return True if any BINARY_SUBSCR or crash-relevant opcode
       falls inside an exception handler range (TryBegin/TryEnd).
       Uses the bytecode library's TryBegin/TryEnd markers."""
    import sys
    if sys.version_info < (3, 11):
        return False
    try:
        from bytecode import TryBegin, TryEnd
        bc = Bytecode.from_code(code)
        depth = 0
        has_subscr_in_try = False
        for item in bc:
            if isinstance(item, TryBegin):
                depth += 1
            elif isinstance(item, TryEnd):
                depth = max(0, depth - 1)
            elif depth > 0 and isinstance(item, Instr):
                if item.name in ('BINARY_SUBSCR', 'LOAD_ATTR', 'LOAD_METHOD',
                                 'BINARY_OP', 'BINARY_TRUE_DIVIDE'):
                    has_subscr_in_try = True
                    break
        return has_subscr_in_try
    except Exception:
        return False

def _build_entry_point_null_guard(original_code, var_name, default):
    """Build a guard injected at the function entry point (after RESUME).
       Checks if var_name is None and replaces it with default.
       Safe for use when the crash site is inside a try block."""
    bc = Bytecode.from_code(original_code)
    skip = Label()
    patch = [
        Instr('LOAD_FAST', var_name),
        Instr('LOAD_CONST', None),
        Instr('IS_OP', 0),
        Instr('POP_JUMP_FORWARD_IF_FALSE', skip),
        Instr('LOAD_CONST', default),
        Instr('RETURN_VALUE'),
        skip
    ]
    idx = 0
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'RESUME':
            idx = i + 1
            break
    for instr in reversed(patch):
        bc.insert(idx, instr)
    return bc

# Strategies that inject inline at the crash site (replace BINARY_SUBSCR etc.)
# These are the ones that can corrupt exception tables when inside try blocks.
_INLINE_STRATEGIES = frozenset({
})

def synthesize_guarded_code(original_code, spec: PatchSpec) -> Bytecode:
    # Bug 3 fix: If the crash site is inside a try/except block and we
    # would normally inject inline, redirect to an entry-point guard
    # to avoid corrupting co_exceptiontable offsets on CPython 3.11+.
    if spec.strategy in _INLINE_STRATEGIES and _is_inside_try_block(original_code):
        import logging
        logging.getLogger(__name__).debug(
            "[CodeSuture] Crash inside try block — redirecting %s to entry-point guard",
            spec.strategy
        )
        res = _build_entry_point_null_guard(original_code, spec.var_name, spec.default_value)
    elif spec.strategy in ('subscript_guard', 'key_guard', 'dict_get_guard'):
        res = _build_subscript_guarded_code(original_code, spec.var_name, spec.key_name, spec.default_value)
    elif spec.strategy == 'chain_subscript_guard':
        res = _build_chain_subscript_guarded_code(original_code, spec.var_name, spec.key_name, spec.default_value)
    elif spec.strategy == 'division_guard':
        res = _build_division_guarded_code(original_code, spec.var_name, spec.default_value)
    elif spec.strategy == 'null_guard':
        if spec.key_name is not None:
            res = _build_attr_null_guarded_code(original_code, spec.var_name, spec.key_name, spec.default_value)
        else:
            res = _build_null_guarded_code(original_code, spec.var_name, spec.default_value)
    elif spec.strategy in ('index_guard', 'list_bound_guard'):
        res = _build_index_guarded_code(original_code, spec.var_name, spec.list_len_var, spec.default_value)
    elif spec.strategy == 'file_guard':
        res = _build_file_guarded_code(original_code, spec.var_name, spec.default_value)
    elif spec.strategy == 'str_coerce_guard':
        res = _build_str_coerce_guarded_code(original_code, spec.var_name)
    elif spec.strategy == 'callable_guard':
        res = _build_callable_guarded_code(original_code, spec.var_name, spec.default_value)
    elif spec.strategy == 'type_coercion_guard':
        res = _build_type_coercion_guarded_code(original_code, spec.var_name, spec.default_value)
    elif spec.strategy == 'return_guard':
        res = _build_return_guarded_code(original_code, spec.default_value)
    elif spec.strategy == 'autonomous_rule':
        new_module_code = compile(spec.default_value, "<autonomous>", "exec")
        found = False
        for const in new_module_code.co_consts:
            if type(const).__name__ == 'code' and const.co_name == original_code.co_name:
                res = Bytecode.from_code(const)
                found = True
                break
        if not found:
            raise ValueError("Could not find replacement function code in autonomous rule.")
    else:
        raise ValueError(f"Unknown strategy: {spec.strategy}")

    if getattr(spec, 'is_async', False):
        _ensure_resume_first(res)

    patched_code = res.to_code()
    validate_patch(original_code, patched_code)

    from codesuture.diff_guard import semantic_diff
    diff = semantic_diff(original_code, patched_code, spec.strategy)
    if diff.rejected:
        print(f"[CodeSuture] {diff.reason}")
        raise PatchRejectedError(diff.reason)

    return res

def _ensure_resume_first(bc: Bytecode):

    instrs = list(bc)

    resume_idx = None
    for i, instr in enumerate(instrs):
        if isinstance(instr, Instr) and instr.name == 'RESUME' and instr.arg == 0:
            resume_idx = i
            break

    if resume_idx is None:

        bc.insert(0, Instr('RESUME', 0))
        return

    if resume_idx == 0:

        return

    resume_instr = instrs.pop(resume_idx)
    instrs.insert(0, resume_instr)
    bc.clear()
    bc.extend(instrs)

def _build_null_guarded_code(original_code, var_name, default):
    bc = Bytecode.from_code(original_code)
    instrs = list(bc)

    for idx in range(len(instrs) - 1):
        instr = instrs[idx]
        next_instr = instrs[idx + 1]
        if (
            isinstance(instr, Instr)
            and isinstance(next_instr, Instr)
            and instr.name == 'LOAD_CONST'
            and instr.arg is None
            and next_instr.name == 'STORE_FAST'
            and next_instr.arg == var_name
        ):
            bc[idx] = Instr('LOAD_CONST', default, lineno=instr.lineno)
            return bc

    crash_idx = None
    for idx in range(len(instrs) - 1):
        instr = instrs[idx]
        next_instr = instrs[idx + 1]
        if (isinstance(instr, Instr) and instr.name == 'LOAD_FAST' and instr.arg == var_name
            and isinstance(next_instr, Instr) and next_instr.name in ('LOAD_ATTR', 'LOAD_METHOD')):
            crash_idx = idx
            break

    insert_after_idx = None
    search_end = crash_idx if crash_idx is not None else len(instrs)
    for idx in range(search_end - 1, -1, -1):
        instr = instrs[idx]
        if isinstance(instr, Instr) and instr.name == 'STORE_FAST' and instr.arg == var_name:
            insert_after_idx = idx
            break

    skip = Label()
    patch = [
        Instr('LOAD_FAST', var_name),
        Instr('LOAD_CONST', None),
        Instr('IS_OP', 0),
        Instr('POP_JUMP_FORWARD_IF_FALSE', skip),
        Instr('LOAD_CONST', default),
        Instr('STORE_FAST', var_name),
        skip
    ]

    if insert_after_idx is not None:

        pos = insert_after_idx + 1
    else:

        pos = 0
        for i, instr in enumerate(bc):
            if isinstance(instr, Instr) and instr.name == 'RESUME':
                pos = i + 1
                break

    for instr in reversed(patch):
        bc.insert(pos, instr)
    return bc

def _build_attr_null_guarded_code(original_code, local_var, attr_chain, default):

    bc = Bytecode.from_code(original_code)
    instrs = list(bc)

    has_store = any(
        isinstance(instr, Instr) and instr.name == 'STORE_FAST' and instr.arg == local_var
        for instr in instrs
    )

    if has_store:

        crash_idx = None
        for idx in range(len(instrs) - 1):
            instr = instrs[idx]
            next_instr = instrs[idx + 1]
            if (isinstance(instr, Instr) and instr.name == 'LOAD_FAST' and instr.arg == local_var
                and isinstance(next_instr, Instr) and next_instr.name in ('LOAD_ATTR', 'LOAD_METHOD')):
                crash_idx = idx
                break

        insert_after_idx = None
        search_end = crash_idx if crash_idx is not None else len(instrs)
        for idx in range(search_end - 1, -1, -1):
            instr = instrs[idx]
            if isinstance(instr, Instr) and instr.name == 'STORE_FAST' and instr.arg == local_var:
                insert_after_idx = idx
                break

        skip = Label()
        patch = [
            Instr('LOAD_FAST', local_var),
            Instr('LOAD_CONST', None),
            Instr('IS_OP', 0),
            Instr('POP_JUMP_FORWARD_IF_FALSE', skip),
            Instr('LOAD_CONST', default),
            Instr('RETURN_VALUE'),
            skip
        ]

        pos = (insert_after_idx + 1) if insert_after_idx is not None else 0
        for instr in reversed(patch):
            bc.insert(pos, instr)
        return bc

    return_default = Label()
    end_guard = Label()

    patch = [Instr('LOAD_FAST', local_var)]
    for attr in attr_chain:
        patch.extend([
            Instr('COPY', 1),
            Instr('LOAD_CONST', None),
            Instr('IS_OP', 0),
            Instr('POP_JUMP_FORWARD_IF_TRUE', return_default),
            Instr('LOAD_ATTR', attr)
        ])

    patch.extend([
        Instr('COPY', 1),
        Instr('LOAD_CONST', None),
        Instr('IS_OP', 0),
        Instr('POP_JUMP_FORWARD_IF_TRUE', return_default),

        Instr('POP_TOP'),
        Instr('JUMP_FORWARD', end_guard),

        return_default,

        Instr('POP_TOP'),
        Instr('LOAD_CONST', default),
        Instr('RETURN_VALUE'),

        end_guard
    ])

    idx = 0
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'RESUME':
            idx = i + 1
            break
    for instr in reversed(patch):
        bc.insert(idx, instr)
    return bc

def _build_division_guarded_code(original_code, var_name, default):
    bc = Bytecode.from_code(original_code)
    new_instrs = []
    replaced_count = 0
    for instr in bc:
        if isinstance(instr, Instr) and (instr.name == 'BINARY_TRUE_DIVIDE' or (instr.name == 'BINARY_OP' and instr.arg == 11)):
            skip = Label()
            new_instrs.append(Instr('COPY', 1))
            new_instrs.append(Instr('LOAD_CONST', 0))
            new_instrs.append(Instr('COMPARE_OP', Compare.GT))
            new_instrs.append(Instr('POP_JUMP_FORWARD_IF_TRUE', skip))
            new_instrs.append(Instr('POP_TOP'))
            new_instrs.append(Instr('LOAD_CONST', default))
            new_instrs.append(skip)
            new_instrs.append(instr)
            replaced_count += 1
        else:
            new_instrs.append(instr)
    if replaced_count > 0:
        print(f"[CodeSuture] Patched {replaced_count} occurrences of the failing expression pattern in {original_code.co_name}.")
    bc.clear()
    bc.extend(new_instrs)
    return bc

def _build_subscript_guarded_code(original_code, container_var, key_name_or_var, default):
    bc = Bytecode.from_code(original_code)
    instrs = list(bc)
    new_instrs = []
    replaced_count = 0
    for pos, instr in enumerate(instrs):
        key_matches = True
        if key_name_or_var is not None:
            prev = instrs[pos - 1] if pos > 0 else None
            key_matches = (
                isinstance(prev, Instr) and
                ((prev.name == 'LOAD_CONST' and prev.arg == key_name_or_var) or
                 (prev.name == 'LOAD_FAST' and prev.arg == key_name_or_var))
            )
        if isinstance(instr, Instr) and instr.name == 'BINARY_SUBSCR' and replaced_count == 0 and key_matches:
            skip_none = Label()
            end = Label()
            new_instrs.append(Instr('STORE_FAST', '_codesuture_key'))
            new_instrs.append(Instr('STORE_FAST', '_codesuture_cont'))
            new_instrs.append(Instr('LOAD_FAST', '_codesuture_cont'))
            new_instrs.append(Instr('LOAD_CONST', None))
            new_instrs.append(Instr('COMPARE_OP', Compare.EQ))
            new_instrs.append(Instr('POP_JUMP_FORWARD_IF_FALSE', skip_none))
            new_instrs.append(Instr('LOAD_CONST', default))
            new_instrs.append(Instr('JUMP_FORWARD', end))
            new_instrs.append(skip_none)
            new_instrs.append(Instr('LOAD_FAST', '_codesuture_cont'))
            new_instrs.append(Instr('LOAD_METHOD', 'get'))
            new_instrs.append(Instr('LOAD_FAST', '_codesuture_key'))
            new_instrs.append(Instr('LOAD_CONST', default))
            new_instrs.append(Instr('PRECALL', 2))
            new_instrs.append(Instr('CALL', 2))
            new_instrs.append(end)
            replaced_count += 1
        else:
            new_instrs.append(instr)
    if replaced_count > 0:
        print(f"[CodeSuture] Patched {replaced_count} occurrences of the failing expression pattern in {original_code.co_name}.")
    bc.clear()
    bc.extend(new_instrs)
    return bc

def _build_chain_subscript_guarded_code(original_code, root_var, keys, default):

    bc = Bytecode.from_code(original_code)
    instrs = list(bc)
    new_instrs = []
    num_keys = len(keys)
    pattern_len = 1 + num_keys * 2  

    i = 0
    replaced_count = 0
    while i < len(instrs):
        if _match_chain(instrs, i, root_var, keys):
            new_instrs.extend(_gen_chain_get(original_code, root_var, keys, default))
            i += pattern_len
            replaced_count += 1
            continue
        new_instrs.append(instrs[i])
        i += 1

    if replaced_count > 0:
        print(f"[CodeSuture] Patched {replaced_count} occurrences of the failing expression pattern in {original_code.co_name}.")
    bc.clear()
    bc.extend(new_instrs)
    return bc

def _global_name(arg):
    if isinstance(arg, tuple):
        return arg[1] if len(arg) > 1 else arg[0]
    return arg


def _match_chain(instrs, start, root_var, keys):

    pos = start
    if pos >= len(instrs):
        return False
    i0 = instrs[pos]
    if not (
        isinstance(i0, Instr) and
        ((i0.name == 'LOAD_FAST' and i0.arg == root_var) or
         (i0.name == 'LOAD_GLOBAL' and _global_name(i0.arg) == root_var))
    ):
        return False
    pos += 1
    for key in keys:
        if pos + 1 >= len(instrs):
            return False
        ld = instrs[pos]
        if not isinstance(ld, Instr):
            return False
        if not ((ld.name == 'LOAD_CONST' and ld.arg == key) or
                (ld.name == 'LOAD_FAST' and ld.arg == key)):
            return False
        pos += 1
        bs = instrs[pos]
        if not (isinstance(bs, Instr) and bs.name == 'BINARY_SUBSCR'):
            return False
        pos += 1
    return True

def _gen_chain_get(original_code, root_var, keys, default):

    out = []
    if root_var in original_code.co_varnames:
        out.append(Instr('LOAD_FAST', root_var))
    else:
        out.append(Instr('LOAD_GLOBAL', (False, root_var)))
    out.append(Instr('STORE_FAST', '_lp_chain'))

    for key in keys[:-1]:
        skip = Label()
        out.append(Instr('LOAD_FAST', '_lp_chain'))
        out.append(Instr('LOAD_CONST', None))
        out.append(Instr('COMPARE_OP', Compare.EQ))
        out.append(Instr('POP_JUMP_FORWARD_IF_TRUE', skip))
        out.append(Instr('LOAD_FAST', '_lp_chain'))
        out.append(Instr('LOAD_METHOD', 'get'))
        out.append(Instr('LOAD_CONST', key))
        out.append(Instr('LOAD_CONST', None))
        out.append(Instr('PRECALL', 2))
        out.append(Instr('CALL', 2))
        out.append(Instr('STORE_FAST', '_lp_chain'))
        out.append(skip)

    last = keys[-1]
    skip_last = Label()
    end = Label()
    out.append(Instr('LOAD_FAST', '_lp_chain'))
    out.append(Instr('LOAD_CONST', None))
    out.append(Instr('COMPARE_OP', Compare.EQ))
    out.append(Instr('POP_JUMP_FORWARD_IF_TRUE', skip_last))
    if isinstance(last, int):
        default_last = Label()
        load_last = Label()
        out.append(Instr('LOAD_CONST', last))
        out.append(Instr('LOAD_GLOBAL', (True, 'len')))
        out.append(Instr('LOAD_FAST', '_lp_chain'))
        out.append(Instr('PRECALL', 1))
        out.append(Instr('CALL', 1))
        out.append(Instr('COMPARE_OP', Compare.GE))
        out.append(Instr('POP_JUMP_FORWARD_IF_TRUE', default_last))
        out.append(load_last)
        out.append(Instr('LOAD_FAST', '_lp_chain'))
        out.append(Instr('LOAD_CONST', last))
        out.append(Instr('BINARY_SUBSCR'))
        out.append(Instr('JUMP_FORWARD', end))
        out.append(default_last)
        out.append(Instr('LOAD_CONST', default))
    else:
        out.append(Instr('LOAD_FAST', '_lp_chain'))
        out.append(Instr('LOAD_METHOD', 'get'))
        out.append(Instr('LOAD_CONST', last))
        out.append(Instr('LOAD_CONST', default))
        out.append(Instr('PRECALL', 2))
        out.append(Instr('CALL', 2))
    out.append(Instr('JUMP_FORWARD', end))
    out.append(skip_last)
    out.append(Instr('LOAD_CONST', default))
    out.append(end)
    return out

def _build_index_guarded_code(original_code, idx_var, list_var, default):
    bc = Bytecode.from_code(original_code)
    skip = Label()
    patch = [
        Instr('LOAD_FAST', idx_var),
        Instr('LOAD_GLOBAL', (True, 'len')),
        Instr('LOAD_FAST', list_var),
        Instr('PRECALL', 1),
        Instr('CALL', 1),
        Instr('COMPARE_OP', Compare.GE),
        Instr('POP_JUMP_FORWARD_IF_FALSE', skip),
        Instr('LOAD_CONST', 0),
        Instr('STORE_FAST', idx_var),
        skip
    ]
    idx = 0
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'RESUME':
            idx = i + 1
            break
    for instr in reversed(patch):
        bc.insert(idx, instr)
    return bc

def _build_file_guarded_code(original_code, path_var, default):
    bc = Bytecode.from_code(original_code)
    skip = Label()
    patch = [
        Instr('LOAD_GLOBAL', (False, 'os')),
        Instr('LOAD_ATTR', 'path'),
        Instr('LOAD_METHOD', 'exists'),
        Instr('LOAD_FAST', path_var),
        Instr('PRECALL', 1),
        Instr('CALL', 1),
        Instr('POP_JUMP_FORWARD_IF_TRUE', skip),

        Instr('LOAD_CONST', default),
        Instr('RETURN_VALUE'),
        skip
    ]
    idx = 0
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'RESUME':
            idx = i + 1
            break
    for instr in reversed(patch):
        bc.insert(idx, instr)
    return bc

def _build_str_coerce_guarded_code(original_code, var_name):
    bc = Bytecode.from_code(original_code)
    skip = Label()
    patch = [
        Instr('LOAD_GLOBAL', (True, 'isinstance')),
        Instr('LOAD_FAST', var_name),
        Instr('LOAD_GLOBAL', (False, 'str')),
        Instr('PRECALL', 2),
        Instr('CALL', 2),
        Instr('POP_JUMP_FORWARD_IF_TRUE', skip),
        Instr('LOAD_GLOBAL', (True, 'str')),
        Instr('LOAD_FAST', var_name),
        Instr('PRECALL', 1),
        Instr('CALL', 1),
        Instr('STORE_FAST', var_name),
        skip
    ]
    idx = 0
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'RESUME':
            idx = i + 1
            break
    for instr in reversed(patch):
        bc.insert(idx, instr)
    return bc

def _build_callable_guarded_code(original_code, var_name, replacement_func):

    bc = Bytecode.from_code(original_code)
    skip = Label()
    patch = [
        Instr('LOAD_GLOBAL', (False, var_name)),
        Instr('LOAD_CONST', None),
        Instr('COMPARE_OP', Compare.EQ),
        Instr('POP_JUMP_FORWARD_IF_FALSE', skip),

        Instr('LOAD_GLOBAL', (True, '__import__')),
        Instr('LOAD_CONST', 'sys'),
        Instr('PRECALL', 1),
        Instr('CALL', 1),
        Instr('LOAD_ATTR', 'modules'),
        Instr('LOAD_CONST', 'codesuture.pattern_matcher'),
        Instr('BINARY_SUBSCR'),
        Instr('LOAD_ATTR', '_ORIGINAL_INFER_DEFAULT'),
        Instr('STORE_GLOBAL', var_name),
        skip
    ]
    idx = 0
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'RESUME':
            idx = i + 1
            break
    for instr in reversed(patch):
        bc.insert(idx, instr)
    return bc

def _build_type_coercion_guarded_code(original_code, var_name, default):

    bc = Bytecode.from_code(original_code)
    skip = Label()

    if isinstance(default, int) and not isinstance(default, bool):

        skip2 = Label()
        patch = [
            Instr('LOAD_GLOBAL', (True, 'isinstance')),
            Instr('LOAD_FAST', var_name),
            Instr('LOAD_GLOBAL', (False, 'str')),
            Instr('PRECALL', 2),
            Instr('CALL', 2),
            Instr('POP_JUMP_FORWARD_IF_FALSE', skip),

            Instr('LOAD_FAST', var_name),
            Instr('LOAD_METHOD', 'lstrip'),
            Instr('LOAD_CONST', '-'),
            Instr('PRECALL', 1),
            Instr('CALL', 1),
            Instr('LOAD_METHOD', 'isdigit'),
            Instr('PRECALL', 0),
            Instr('CALL', 0),
            Instr('POP_JUMP_FORWARD_IF_TRUE', skip2),

            Instr('LOAD_CONST', default),
            Instr('STORE_FAST', var_name),
            skip2,
            skip
        ]
    elif isinstance(default, float):

        skip2 = Label()
        patch = [
            Instr('LOAD_FAST', var_name),
            Instr('LOAD_CONST', None),
            Instr('IS_OP', 0),
            Instr('POP_JUMP_FORWARD_IF_FALSE', skip),
            Instr('LOAD_CONST', default),
            Instr('STORE_FAST', var_name),
            skip
        ]
    else:

        patch = [
            Instr('LOAD_FAST', var_name),
            Instr('LOAD_CONST', None),
            Instr('IS_OP', 0),
            Instr('POP_JUMP_FORWARD_IF_FALSE', skip),
            Instr('LOAD_CONST', default),
            Instr('STORE_FAST', var_name),
            skip
        ]

    idx = 0
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'STORE_FAST' and instr.arg == var_name:
            idx = i + 1
            break
    else:
        for i, instr in enumerate(bc):
            if isinstance(instr, Instr) and instr.name == 'RESUME':
                idx = i + 1
                break
    for instr in reversed(patch):
        bc.insert(idx, instr)
    return bc

def _build_return_guarded_code(original_code, default):

    bc = Bytecode.from_code(original_code)
    new_instrs = []
    for instr in bc:
        if isinstance(instr, Instr) and instr.name == 'RETURN_VALUE':
            skip = Label()
            new_instrs.append(Instr('COPY', 1))
            new_instrs.append(Instr('LOAD_CONST', None))
            new_instrs.append(Instr('IS_OP', 0))
            new_instrs.append(Instr('POP_JUMP_FORWARD_IF_FALSE', skip))
            new_instrs.append(Instr('POP_TOP'))
            new_instrs.append(Instr('LOAD_CONST', default))
            new_instrs.append(skip)
            new_instrs.append(instr)
        else:
            new_instrs.append(instr)
    bc.clear()
    bc.extend(new_instrs)
    return bc
