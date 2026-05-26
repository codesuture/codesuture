"""
Synthesises guard + original bytecode for all deterministic strategies.
"""
import ctypes
from bytecode import Bytecode, Instr, Label, Compare
from codesuture.pattern_matcher import PatchSpec
from codesuture.opcodes import (
    emit_call, emit_load_method, emit_jump_if_false, emit_jump_if_true,
    emit_load_global, make_load_global, JUMP_IF_FALSE, JUMP_IF_TRUE,
    LOAD_METHOD_OP, HAS_PRECALL,
)

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
                raise PatchValidationError(f"Patch rejected: LOAD_FAST '{name}' not in co_varnames — bytecode would corrupt frame. Patch was not applied.")

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
        emit_jump_if_false(skip),
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
    'subscript_guard', 'key_guard', 'dict_get_guard',
    'division_guard', 'chain_subscript_guard',
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
    elif spec.strategy == 'index_guard':
        res = _build_index_guarded_code(original_code, spec.var_name, spec.list_len_var, spec.default_value)
    elif spec.strategy == 'list_bound_guard':
        # var_name = the list variable, key_name = (const_int_index,)
        const_idx = spec.key_name[0] if spec.key_name else 0
        res = _build_list_bound_guarded_code(original_code, spec.var_name, const_idx, spec.default_value)
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
        emit_jump_if_false(skip),
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
            emit_jump_if_false(skip),
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
            emit_jump_if_true(return_default),
            Instr('LOAD_ATTR', attr)
        ])

    patch.extend([
        Instr('COPY', 1),
        Instr('LOAD_CONST', None),
        Instr('IS_OP', 0),
        emit_jump_if_true(return_default),

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
            new_instrs.append(Instr('COMPARE_OP', Compare.NE))
            new_instrs.append(emit_jump_if_true(skip))
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
            new_instrs.append(emit_jump_if_false(skip_none))
            new_instrs.append(Instr('LOAD_CONST', default))
            new_instrs.append(Instr('JUMP_FORWARD', end))
            new_instrs.append(skip_none)
            is_dict = Label()
            new_instrs.append(emit_load_global('isinstance', push_null=True))
            new_instrs.append(Instr('LOAD_FAST', '_codesuture_cont'))
            new_instrs.append(emit_load_global('dict', push_null=False))
            new_instrs.extend(emit_call(2))
            new_instrs.append(emit_jump_if_true(is_dict))
            # Not a dict — use direct subscript (lists, tuples, etc.)
            new_instrs.append(Instr('LOAD_FAST', '_codesuture_cont'))
            new_instrs.append(Instr('LOAD_FAST', '_codesuture_key'))
            new_instrs.append(Instr('BINARY_SUBSCR'))
            new_instrs.append(Instr('JUMP_FORWARD', end))
            new_instrs.append(is_dict)
            # Dict — use safe .get()
            new_instrs.append(Instr('LOAD_FAST', '_codesuture_cont'))
            new_instrs.append(emit_load_method('get'))
            new_instrs.append(Instr('LOAD_FAST', '_codesuture_key'))
            new_instrs.append(Instr('LOAD_CONST', default))
            new_instrs.extend(emit_call(2))
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
        out.append(emit_load_global(root_var, push_null=False))
    out.append(Instr('STORE_FAST', '_lp_chain'))

    for key in keys[:-1]:
        skip = Label()
        out.append(Instr('LOAD_FAST', '_lp_chain'))
        out.append(Instr('LOAD_CONST', None))
        out.append(Instr('COMPARE_OP', Compare.EQ))
        out.append(emit_jump_if_true(skip))
        out.append(Instr('LOAD_FAST', '_lp_chain'))
        out.append(emit_load_method('get'))
        out.append(Instr('LOAD_CONST', key))
        out.append(Instr('LOAD_CONST', None))
        out.extend(emit_call(2))
        out.append(Instr('STORE_FAST', '_lp_chain'))
        out.append(skip)

    last = keys[-1]
    skip_last = Label()
    end = Label()
    out.append(Instr('LOAD_FAST', '_lp_chain'))
    out.append(Instr('LOAD_CONST', None))
    out.append(Instr('COMPARE_OP', Compare.EQ))
    out.append(emit_jump_if_true(skip_last))
    if isinstance(last, int):
        default_last = Label()
        load_last = Label()
        out.append(Instr('LOAD_CONST', last))
        out.append(emit_load_global('len', push_null=True))
        out.append(Instr('LOAD_FAST', '_lp_chain'))
        out.extend(emit_call(1))
        out.append(Instr('COMPARE_OP', Compare.GE))
        out.append(emit_jump_if_true(default_last))
        out.append(load_last)
        out.append(Instr('LOAD_FAST', '_lp_chain'))
        out.append(Instr('LOAD_CONST', last))
        out.append(Instr('BINARY_SUBSCR'))
        out.append(Instr('JUMP_FORWARD', end))
        out.append(default_last)
        out.append(Instr('LOAD_CONST', default))
    else:
        out.append(Instr('LOAD_FAST', '_lp_chain'))
        out.append(emit_load_method('get'))
        out.append(Instr('LOAD_CONST', last))
        out.append(Instr('LOAD_CONST', default))
        out.extend(emit_call(2))
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
        emit_load_global('len', push_null=True),
        Instr('LOAD_FAST', list_var),
        *emit_call(1),
        Instr('COMPARE_OP', Compare.GE),
        emit_jump_if_false(skip),
        Instr('LOAD_CONST', default),
        Instr('RETURN_VALUE'),
        skip
    ]
    # Insert the guard AFTER both idx_var and list_var are assigned.
    # We must find the LAST store of either variable — whichever comes later
    # in the bytecode — so that both are bound when the guard executes.
    # If a variable is a parameter it has no STORE_FAST; treat its "last store"
    # position as -1 (already assigned at function entry, any position is fine).
    last_store_idx_var = -1
    last_store_list_var = -1
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'STORE_FAST':
            if instr.arg == idx_var:
                last_store_idx_var = i
            elif instr.arg == list_var:
                last_store_list_var = i

    last_store = max(last_store_idx_var, last_store_list_var)
    if last_store >= 0:
        insert_idx = last_store + 1
    else:
        # Both are parameters — insert right after RESUME
        insert_idx = 0
        for i, instr in enumerate(bc):
            if isinstance(instr, Instr) and instr.name == 'RESUME':
                insert_idx = i + 1
                break
    for instr in reversed(patch):
        bc.insert(insert_idx, instr)
    return bc

def _build_list_bound_guarded_code(original_code, list_var, const_idx, default):
    """Guard for list[const_int] — e.g. parts[5].

    Inserts: if len(list_var) <= const_idx: return default
    right after list_var is assigned (STORE_FAST list_var), guaranteeing
    list_var is bound before the guard executes. The constant index is embedded
    directly as a LOAD_CONST so no variable-binding issues are possible.
    """
    bc = Bytecode.from_code(original_code)
    skip = Label()
    patch = [
        emit_load_global('len', push_null=True),
        Instr('LOAD_FAST', list_var),
        *emit_call(1),
        Instr('LOAD_CONST', const_idx),
        Instr('COMPARE_OP', Compare.GT),   # len(list) > const_idx means index is safe
        emit_jump_if_true(skip),            # skip guard body if safe
        Instr('LOAD_CONST', default),
        Instr('RETURN_VALUE'),
        skip,
    ]
    # Insert right after the last STORE_FAST for list_var so it's bound.
    # If list_var is a parameter (no STORE_FAST), insert after RESUME.
    insert_idx = 0
    last_store = -1
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'STORE_FAST' and instr.arg == list_var:
            last_store = i
    if last_store >= 0:
        insert_idx = last_store + 1
    else:
        for i, instr in enumerate(bc):
            if isinstance(instr, Instr) and instr.name == 'RESUME':
                insert_idx = i + 1
                break
    for instr in reversed(patch):
        bc.insert(insert_idx, instr)
    return bc


def _build_file_guarded_code(original_code, path_var, default):
    bc = Bytecode.from_code(original_code)
    skip = Label()
    patch = [
        emit_load_global('os', push_null=False),
        Instr('LOAD_ATTR', 'path'),
        emit_load_method('exists'),
        Instr('LOAD_FAST', path_var),
        *emit_call(1),
        emit_jump_if_true(skip),

        Instr('LOAD_CONST', default),
        Instr('RETURN_VALUE'),
        skip
    ]
    # Find the last STORE_FAST for path_var — insert guard right after it
    insert_idx = None
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'STORE_FAST' and instr.arg == path_var:
            insert_idx = i + 1
    if insert_idx is None:
        # Fallback: path_var might be a parameter, insert after RESUME
        insert_idx = 0
        for i, instr in enumerate(bc):
            if isinstance(instr, Instr) and instr.name == 'RESUME':
                insert_idx = i + 1
                break
    for instr in reversed(patch):
        bc.insert(insert_idx, instr)
    return bc

def _build_str_coerce_guarded_code(original_code, var_name):
    bc = Bytecode.from_code(original_code)
    skip = Label()
    patch = [
        emit_load_global('isinstance', push_null=True),
        Instr('LOAD_FAST', var_name),
        emit_load_global('str', push_null=False),
        *emit_call(2),
        emit_jump_if_true(skip),
        emit_load_global('str', push_null=True),
        Instr('LOAD_FAST', var_name),
        *emit_call(1),
        Instr('STORE_FAST', var_name),
        skip
    ]
    # Find the last STORE_FAST for var_name — insert guard right after it
    # so the variable is guaranteed to be assigned before we check its type
    insert_idx = None
    for i, instr in enumerate(bc):
        if isinstance(instr, Instr) and instr.name == 'STORE_FAST' and instr.arg == var_name:
            insert_idx = i + 1
    if insert_idx is None:
        # Fallback: var_name might be a parameter, insert after RESUME
        insert_idx = 0
        for i, instr in enumerate(bc):
            if isinstance(instr, Instr) and instr.name == 'RESUME':
                insert_idx = i + 1
                break
    for instr in reversed(patch):
        bc.insert(insert_idx, instr)
    return bc

def _build_callable_guarded_code(original_code, var_name, replacement_func):

    bc = Bytecode.from_code(original_code)
    skip = Label()
    patch = [
        emit_load_global(var_name, push_null=False),
        Instr('LOAD_CONST', None),
        Instr('COMPARE_OP', Compare.EQ),
        emit_jump_if_false(skip),

        emit_load_global('__import__', push_null=True),
        Instr('LOAD_CONST', 'sys'),
        *emit_call(1),
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
            emit_load_global('isinstance', push_null=True),
            Instr('LOAD_FAST', var_name),
            emit_load_global('str', push_null=False),
            *emit_call(2),
            emit_jump_if_false(skip),

            Instr('LOAD_FAST', var_name),
            emit_load_method('lstrip'),
            Instr('LOAD_CONST', '-'),
            *emit_call(1),
            emit_load_method('isdigit'),
            *emit_call(0),
            emit_jump_if_true(skip2),

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
            emit_jump_if_false(skip),
            Instr('LOAD_CONST', default),
            Instr('STORE_FAST', var_name),
            skip
        ]
    else:

        patch = [
            Instr('LOAD_FAST', var_name),
            Instr('LOAD_CONST', None),
            Instr('IS_OP', 0),
            emit_jump_if_false(skip),
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
            new_instrs.append(emit_jump_if_false(skip))
            new_instrs.append(Instr('POP_TOP'))
            new_instrs.append(Instr('LOAD_CONST', default))
            new_instrs.append(skip)
            new_instrs.append(instr)
        else:
            new_instrs.append(instr)
    bc.clear()
    bc.extend(new_instrs)
    return bc
