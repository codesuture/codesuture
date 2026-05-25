from types import FrameType
from typing import Optional, NamedTuple
import dis
import re

from codesuture.opcodes import (
    CALL_OPCODES, JUMP_FALSE_OPCODES, JUMP_TRUE_OPCODES, ALL_JUMP_OPCODES,
    METHOD_LOAD_OPCODES, SUBSCRIPT_OPCODES, ARITHMETIC_OPCODES,
    FORMAT_OPCODES, TERMINATOR_OPCODES,
)

class PatchSpec(NamedTuple):
    strategy: str
    var_name: str
    default_value: object
    key_name: Optional[str] = None
    list_len_var: Optional[str] = None
    is_async: bool = False
    inside_loop: bool = False
    target_func: Optional[object] = None
    target_name: Optional[str] = None

def analyze_exception(frame: FrameType, exc_type, exc_value, exc_tb) -> Optional[PatchSpec]:
    from codesuture.code_replacer import get_function_from_frame
    func = get_function_from_frame(frame)
    if func is not None:
        func_name = getattr(func, '__qualname__', func.__name__)
        exc_type_name = getattr(exc_type, '__name__', str(exc_type))
        spec = check_learned_rules(func_name, exc_type_name, str(exc_value))
        if spec:

            if frame.f_code.co_flags & 0x100:
                spec = spec._replace(is_async=True)

            instructions = list(dis.get_instructions(frame.f_code))
            crash_instr = _find_target_instr(instructions, frame.f_lasti)
            if crash_instr is not None:
                inside_loop = False
                for instr in instructions:
                    if instr.offset >= crash_instr.offset:
                        break
                    if instr.opname == 'FOR_ITER':

                        if getattr(instr, 'argval', 0) > crash_instr.offset:
                            inside_loop = True

                if inside_loop:
                    spec = spec._replace(inside_loop=True)
                    print(f"[CodeSuture WARNING] Crash detected inside a for-loop at offset {crash_instr.offset}.\n  The patched function will restart from the beginning.\n  If the loop has side effects (DB writes, API calls, counters),\n  items processed before the crash will be reprocessed.\n  Consider adding idempotency checks in the loop body.")

            return spec

    msg = str(exc_value)
    type_name = getattr(exc_type, '__name__', str(exc_type))

    spec = None

    if type_name == 'AttributeError' and "'NoneType' object has no attribute" in msg:
        spec = _null_guard_spec(frame)

    elif type_name == 'ZeroDivisionError':
        spec = _division_guard_spec(frame)

    elif type_name == 'TypeError':
        if "'NoneType' object is not subscriptable" in msg:
            spec = _none_subscript_spec(frame)
        elif "'NoneType' object is not callable" in msg:
            spec = _callable_guard_spec(frame)
        elif "can only concatenate str" in msg:
            spec = _str_concat_spec(frame, msg)

    if spec is None and type_name in ('TypeError', 'ValueError'):

        coerce = _type_coercion_spec(frame, msg)
        if coerce is not None:
            spec = coerce

    if spec is None and type_name == 'IndexError' and "list index out of range" in msg:
        spec = _index_bound_spec(frame)

    if spec is None and type_name == 'KeyError':
        spec = _dict_get_spec(frame, msg)

    if spec is None and type_name == 'FileNotFoundError':
        spec = _file_guard_spec(frame)

    if spec is not None and frame.f_code.co_flags & 0x100:
        spec = spec._replace(is_async=True)

    return spec

def check_learned_rules(func_name, exc_type_name, exc_message):
    from codesuture.knowledge import load_learned_rules
    rules = load_learned_rules()
    for rule in rules:
        if rule["func_name"] == func_name and rule["exc_type_name"] == exc_type_name:
            return PatchSpec(
                strategy='autonomous_rule',
                var_name=func_name,
                default_value=rule["new_source"]
            )
    return None

def _find_target_instr(instructions, lasti):
    target = None
    for instr in instructions:
        if instr.offset <= lasti:
            target = instr
        else:
            break
    return target

def _check_property_origin(frame, instructions, crash_idx):
    load_attr_instr = None
    load_attr_idx = -1
    for i in range(crash_idx - 1, -1, -1):
        if instructions[i].opname in METHOD_LOAD_OPCODES:
            load_attr_instr = instructions[i]
            load_attr_idx = i
            break
        elif instructions[i].opname in ('LOAD_CONST', 'LOAD_FAST', 'LOAD_GLOBAL', 'LOAD_DEREF'):
            pass
        else:
            break
    if not load_attr_instr:
        return None
    attr_name = load_attr_instr.argval
    obj_instr = None
    for i in range(load_attr_idx - 1, -1, -1):
        if instructions[i].opname in ('LOAD_FAST', 'LOAD_DEREF', 'LOAD_GLOBAL'):
            obj_instr = instructions[i]
            break
        elif instructions[i].opname in METHOD_LOAD_OPCODES:
            pass
        else:
            break
    if not obj_instr:
        return None
    obj = None
    if obj_instr.opname in ('LOAD_FAST', 'LOAD_DEREF'):
        var_name = frame.f_code.co_varnames[obj_instr.arg] if obj_instr.opname == 'LOAD_FAST' else ""
        if obj_instr.opname == 'LOAD_DEREF':
             names = frame.f_code.co_freevars + frame.f_code.co_cellvars
             var_name = names[obj_instr.arg] if obj_instr.arg < len(names) else ""
        obj = frame.f_locals.get(var_name)
    elif obj_instr.opname == 'LOAD_GLOBAL':
        var_name = obj_instr.argval
        obj = frame.f_globals.get(var_name, frame.f_builtins.get(var_name))
    if obj is None:
        return None
    prop = getattr(type(obj), attr_name, None)
    if isinstance(prop, property) and prop.fget is not None:
        return {'target_func': prop.fget, 'target_name': f"{type(obj).__name__}.{attr_name}"}
    return None

def _infer_default(var_name, instructions=None, crash_idx=None):
    if instructions is not None and crash_idx is not None:
        for i in range(crash_idx + 1, min(crash_idx + 11, len(instructions))):
            instr = instructions[i]
            if instr.opname in METHOD_LOAD_OPCODES:
                string_methods = {'upper', 'lower', 'strip', 'replace', 'split', 'join', 'format', 'startswith', 'endswith', 'capitalize', 'lstrip', 'rstrip', 'title', 'swapcase', 'center', 'ljust', 'rjust', 'zfill', 'encode'}
                list_dict_methods = {'append', 'extend', 'pop', 'remove', 'get', 'keys', 'values', 'items', 'update'}
                if instr.argval in string_methods:
                    return ""
                if instr.argval in list_dict_methods:
                    return []
            elif instr.opname in ARITHMETIC_OPCODES:
                return 0
            elif instr.opname in ALL_JUMP_OPCODES:
                return False
            elif instr.opname == 'IS_OP':
                return None
            elif instr.opname in FORMAT_OPCODES:
                return ""
            elif instr.opname in SUBSCRIPT_OPCODES:
                return {}
            elif instr.opname in ('LOAD_CONST', 'LOAD_FAST') or instr.opname in CALL_OPCODES:
                continue
            elif instr.opname in TERMINATOR_OPCODES:
                break

    name = var_name.lower()
    if any(kw in name for kw in ('count','num','age','len','size','index','price','amount','discount')):
        return 0
    if any(kw in name for kw in ('name','text','string','msg','email','path','filename')):
        return ""
    if any(kw in name for kw in ('list','items','keys','values','array')):
        return []
    return ""

def _infer_subscript_default(instructions=None, crash_idx=None):
    if instructions is not None and crash_idx is not None:
        i = crash_idx + 1
        while i < len(instructions):
            instr = instructions[i]
            if instr.opname in ('LOAD_CONST', 'LOAD_FAST'):
                i += 1
                continue
            if instr.opname in SUBSCRIPT_OPCODES:
                i += 1
                continue
            break
        for j in range(i, min(i + 4, len(instructions))):
            instr = instructions[j]
            if instr.opname in METHOD_LOAD_OPCODES:
                string_methods = {
                    'capitalize', 'casefold', 'center', 'encode', 'expandtabs',
                    'format', 'format_map', 'join', 'ljust', 'lower', 'lstrip',
                    'removeprefix', 'removesuffix', 'replace', 'rjust', 'strip',
                    'swapcase', 'title', 'translate', 'upper', 'zfill',
                    'split', 'startswith', 'endswith',
                }
                if instr.argval in string_methods:
                    return ""
            elif instr.opname in ARITHMETIC_OPCODES:
                return 0
            elif instr.opname in CALL_OPCODES:
                for k in range(max(0, j - 2), j):
                    if instructions[k].opname == 'LOAD_GLOBAL' and \
                       instructions[k].argval in ('int', 'float'):
                        return 0
            elif instr.opname in ('LIST_APPEND', 'STORE_SUBSCR'):
                return None
            elif instr.opname in TERMINATOR_OPCODES:
                break
            elif instr.opname in CALL_OPCODES:
                continue
            else:
                break
    return None

def _infer_attribute_default(attr_name, fallback_name, instructions=None, crash_idx=None):
    string_methods = {
        'capitalize', 'casefold', 'center', 'encode', 'expandtabs', 'format',
        'format_map', 'join', 'ljust', 'lower', 'lstrip', 'removeprefix',
        'removesuffix', 'replace', 'rjust', 'strip', 'swapcase', 'title',
        'translate', 'upper', 'zfill',
    }
    if attr_name in string_methods:
        return ""
    return _infer_default(fallback_name, instructions, crash_idx)

_ORIGINAL_INFER_DEFAULT = _infer_default

_KNOWN_CALLABLES = {
    '_infer_default': _ORIGINAL_INFER_DEFAULT,
}

def _callable_guard_spec(frame):

    instructions = list(dis.get_instructions(frame.f_code))
    tgt = _find_target_instr(instructions, frame.f_lasti)
    if tgt is None:
        return None
    idx = instructions.index(tgt)

    call_idx = None
    for i in range(max(0, idx - 1), min(idx + 3, len(instructions))):
        if instructions[i].opname in CALL_OPCODES:
            call_idx = i
            break
    if call_idx is None:
        call_idx = idx

    var_name = None
    for i in range(call_idx - 1, -1, -1):
        instr = instructions[i]
        if instr.opname == 'LOAD_GLOBAL':
            name = instr.argval

            val = frame.f_globals.get(name)
            if val is None and name not in frame.f_builtins:
                var_name = name
                break
        if instr.opname in ('LOAD_FAST', 'LOAD_DEREF'):
            name = frame.f_code.co_varnames[instr.arg]
            if frame.f_locals.get(name) is None:
                var_name = name
                break
    if var_name is None:
        return None

    default_callable = _KNOWN_CALLABLES.get(var_name)
    if default_callable is None:
        print(f"[CodeSuture] WARNING: Skipping callable_guard for unknown callable '{var_name}' \u2014 manual review required")
        return None
    return PatchSpec('callable_guard', var_name, default_value=default_callable)

def _null_guard_spec(frame):
    instructions = list(dis.get_instructions(frame.f_code))
    tgt = _find_target_instr(instructions, frame.f_lasti)
    if tgt is None:
        return None

    if tgt.opname not in (METHOD_LOAD_OPCODES | {'STORE_ATTR', 'DELETE_ATTR'}):
        return None

    idx = instructions.index(tgt)

    chain_instrs = []
    if tgt.opname in METHOD_LOAD_OPCODES:
        chain_instrs.append(tgt)
    curr = idx - 1
    while curr >= 0:
        instr = instructions[curr]
        if instr.opname in (METHOD_LOAD_OPCODES | {'LOAD_FAST', 'LOAD_GLOBAL', 'LOAD_DEREF'}):
            chain_instrs.insert(0, instr)
            if instr.opname in ('LOAD_FAST', 'LOAD_GLOBAL', 'LOAD_DEREF'):
                break
            curr -= 1
        else:
            break

    if not chain_instrs:

        for i in range(idx, -1, -1):
            if instructions[i].opname in ('LOAD_FAST', 'LOAD_DEREF'):
                var_name = frame.f_code.co_varnames[instructions[i].arg]
                attr_name = tgt.argval if tgt.opname in METHOD_LOAD_OPCODES else None
                return PatchSpec('null_guard', var_name, _infer_attribute_default(attr_name, var_name, instructions, idx))
        return None

    obj = None
    root_instr = chain_instrs[0]
    if root_instr.opname == 'LOAD_FAST':
        root_name = frame.f_code.co_varnames[root_instr.arg]
        obj = frame.f_locals.get(root_name)
        parent_local = root_name
    elif root_instr.opname == 'LOAD_GLOBAL':
        root_name = root_instr.argval
        obj = frame.f_globals.get(root_name, frame.f_builtins.get(root_name))
        parent_local = root_name
    elif root_instr.opname == 'LOAD_DEREF':
        names = frame.f_code.co_freevars + frame.f_code.co_cellvars
        root_name = names[root_instr.arg] if root_instr.arg < len(names) else f"unknown_{root_instr.arg}"
        obj = frame.f_locals.get(root_name)
        parent_local = root_name
    else:
        return None

    if obj is None:
        if len(chain_instrs) > 1:
            attr_chain = [chain_instrs[j].argval for j in range(1, len(chain_instrs))]
            attr_name = chain_instrs[-1].argval
            default = _infer_attribute_default(attr_name, parent_local, instructions, idx)
            return PatchSpec('null_guard', parent_local, default, key_name=tuple(attr_chain))
        else:
            attr_name = tgt.argval if tgt.opname in METHOD_LOAD_OPCODES else None
            return PatchSpec('null_guard', parent_local, _infer_attribute_default(attr_name, parent_local, instructions, idx), key_name=(attr_name,) if attr_name else None)

    for i in range(1, len(chain_instrs)):
        instr = chain_instrs[i]
        attr = instr.argval
        try:
            next_obj = getattr(obj, attr)
        except AttributeError:
            break

        if next_obj is None:

            prop = getattr(type(obj), attr, None)
            if isinstance(prop, property) and prop.fget is not None:
                attr_chain = [chain_instrs[j].argval for j in range(1, i + 1)]
                attr_name = chain_instrs[i+1].argval if i + 1 < len(chain_instrs) else (tgt.argval if tgt.opname in METHOD_LOAD_OPCODES else None)
                default = _infer_attribute_default(attr_name, attr, instructions, idx)
                return PatchSpec('return_guard', parent_local, default, key_name=tuple(attr_chain), target_func=prop.fget, target_name=f"{type(obj).__name__}.{attr}")

            attr_chain = [chain_instrs[j].argval for j in range(1, i + 1)]
            attr_name = chain_instrs[i+1].argval if i + 1 < len(chain_instrs) else (tgt.argval if tgt.opname in METHOD_LOAD_OPCODES else None)
            default = _infer_attribute_default(attr_name, attr, instructions, idx)
            return PatchSpec('null_guard', parent_local, default, key_name=tuple(attr_chain))

        obj = next_obj

    attr_name = tgt.argval if tgt.opname in METHOD_LOAD_OPCODES else None
    return PatchSpec('null_guard', parent_local, _infer_attribute_default(attr_name, parent_local, instructions, idx))

def _division_guard_spec(frame):
    instructions = list(dis.get_instructions(frame.f_code))
    tgt = _find_target_instr(instructions, frame.f_lasti)
    if tgt is None:
        return None
    idx = instructions.index(tgt)
    if tgt.opname == 'BINARY_OP':
        if idx > 0 and instructions[idx-1].opname in ('LOAD_FAST', 'LOAD_DEREF'):
            var_name = frame.f_code.co_varnames[instructions[idx-1].arg]
            return PatchSpec(strategy='division_guard', var_name=var_name, default_value=1)
    if tgt.opname in ('BINARY_TRUE_DIVIDE', 'BINARY_FLOOR_DIVIDE', 'BINARY_MODULO'):
        if idx > 0 and instructions[idx-1].opname in ('LOAD_FAST', 'LOAD_DEREF'):
            var_name = frame.f_code.co_varnames[instructions[idx-1].arg]
            return PatchSpec(strategy='division_guard', var_name=var_name, default_value=1)
    return None

def _none_subscript_spec(frame):
    instructions = list(dis.get_instructions(frame.f_code))
    tgt = _find_target_instr(instructions, frame.f_lasti)
    if tgt is None or tgt.opname not in ('BINARY_SUBSCR', 'STORE_SUBSCR'):
        return None
    idx = instructions.index(tgt)

    chain_spec = _try_chain_subscript(frame, instructions, idx)
    if chain_spec is not None:
        return chain_spec

    container_var = None
    key_const = None
    key_var = None

    for i in range(idx-1, -1, -1):
        instr = instructions[i]
        if instr.opname in ('LOAD_FAST', 'LOAD_DEREF'):
            if container_var is None:
                container_var = frame.f_code.co_varnames[instr.arg]
            elif key_var is None and key_const is None:
                key_var = frame.f_code.co_varnames[instr.arg]
                break
            else:
                break
        elif instr.opname == 'LOAD_CONST':
            if key_const is None and key_var is None:
                key_const = frame.f_code.co_consts[instr.arg]

    if container_var is None:
        return None
    final_key = key_const if key_const is not None else key_var
    if final_key is None:
        return None

    spec = PatchSpec('subscript_guard', var_name=container_var,
                     default_value=_infer_subscript_default(instructions, idx),
                     key_name=final_key)

    prop_origin = _check_property_origin(frame, instructions, idx)
    if prop_origin:

        return PatchSpec('return_guard', container_var, {}, key_name=spec.key_name, target_func=prop_origin['target_func'], target_name=prop_origin['target_name'])

    return spec

def _try_chain_subscript(frame, instructions, failing_idx):

    keys_back = []  

    key_idx = failing_idx - 1
    if key_idx < 0:
        return None
    key_instr = instructions[key_idx]
    if key_instr.opname == 'LOAD_CONST':
        keys_back.append(frame.f_code.co_consts[key_instr.arg])
    else:

        return None

    pos = key_idx - 1
    root_var = None
    while pos >= 0:
        instr = instructions[pos]
        if instr.opname in SUBSCRIPT_OPCODES:
            pos -= 1
            if pos < 0:
                return None
            k_instr = instructions[pos]
            if k_instr.opname == 'LOAD_CONST':
                keys_back.append(frame.f_code.co_consts[k_instr.arg])
            else:

                return None
            pos -= 1
        elif instr.opname in ('LOAD_FAST', 'LOAD_DEREF'):
            root_var = frame.f_code.co_varnames[instr.arg]
            break
        else:
            return None

    if root_var is None:
        return None

    keys_back.reverse()  

    keys_fwd = []
    fwd = failing_idx + 1
    while fwd + 1 < len(instructions):
        if (instructions[fwd].opname == 'LOAD_CONST' and
                instructions[fwd + 1].opname in SUBSCRIPT_OPCODES):
            keys_fwd.append(frame.f_code.co_consts[instructions[fwd].arg])
            fwd += 2
        else:
            break

    all_keys = keys_back + keys_fwd

    if len(all_keys) < 2:
        return None

    last_instr_idx = failing_idx + len(keys_fwd) * 2  
    final_key = all_keys[-1]
    default = _infer_subscript_default(instructions, last_instr_idx)

    return PatchSpec('chain_subscript_guard', var_name=root_var,
                     default_value=default, key_name=tuple(all_keys))

def _index_bound_spec(frame):
    instructions = list(dis.get_instructions(frame.f_code))
    tgt = _find_target_instr(instructions, frame.f_lasti)
    if tgt is None or tgt.opname not in SUBSCRIPT_OPCODES:
        return None
    idx_instr = instructions.index(tgt)
    if idx_instr > 0 and instructions[idx_instr - 1].opname == 'LOAD_CONST':
        chain = []
        pos = idx_instr
        while pos >= 2 and instructions[pos].opname in SUBSCRIPT_OPCODES:
            key_instr = instructions[pos - 1]
            if key_instr.opname not in ('LOAD_CONST', 'LOAD_FAST'):
                break
            chain.insert(0, key_instr.argval)
            pos -= 2
        root = instructions[pos] if 0 <= pos < len(instructions) else None
        if root is not None and root.opname in ('LOAD_FAST', 'LOAD_GLOBAL') and len(chain) >= 2:
            root_var = root.argval
            if isinstance(root_var, tuple):
                root_var = root_var[-1]
            return PatchSpec(
                'chain_subscript_guard',
                root_var,
                _infer_subscript_default(instructions, idx_instr),
                key_name=tuple(chain),
            )

    loads = []
    for i in range(idx_instr-1, -1, -1):
        if instructions[i].opname in ('LOAD_FAST', 'LOAD_DEREF'):
            loads.append(frame.f_code.co_varnames[instructions[i].arg])
            if len(loads) == 2:
                break
    if len(loads) < 2:
        chain = []
        pos = idx_instr
        while pos >= 2 and instructions[pos].opname in SUBSCRIPT_OPCODES:
            key_instr = instructions[pos - 1]
            if key_instr.opname not in ('LOAD_CONST', 'LOAD_FAST'):
                return None
            key = key_instr.argval
            chain.insert(0, key)
            pos -= 2
        root = instructions[pos] if 0 <= pos < len(instructions) else None
        if root is None or root.opname not in ('LOAD_FAST', 'LOAD_GLOBAL'):
            return None
        root_var = root.argval
        if isinstance(root_var, tuple):
            root_var = root_var[-1]
        if len(chain) < 2:
            return None
        return PatchSpec(
            'chain_subscript_guard',
            root_var,
            _infer_subscript_default(instructions, idx_instr),
            key_name=tuple(chain),
        )
    list_var, idx_var = loads[1], loads[0]
    inferred_default = _infer_default(idx_var, instructions, idx_instr)
    return PatchSpec('index_guard', idx_var, inferred_default, list_len_var=list_var)

def _dict_get_spec(frame, msg):
    match = re.search(r"KeyError\: '(\w+)'", msg)
    if not match:
        match = re.search(r"'(\w+)'", msg)
    key = match.group(1) if match else None
    instructions = list(dis.get_instructions(frame.f_code))
    tgt = _find_target_instr(instructions, frame.f_lasti)
    if tgt is None or tgt.opname not in SUBSCRIPT_OPCODES:
        return None
    idx = instructions.index(tgt)

    if key is None:
        if idx > 0 and instructions[idx - 1].opname == 'LOAD_CONST':
            key = instructions[idx - 1].argval
        else:
            return None

    chain_spec = _try_chain_subscript(frame, instructions, idx)
    if chain_spec is not None:
        return chain_spec

    for i in range(idx-1, -1, -1):
        if instructions[i].opname in ('LOAD_FAST', 'LOAD_DEREF'):
            dict_var = frame.f_code.co_varnames[instructions[i].arg]
            return PatchSpec('key_guard', dict_var, _infer_subscript_default(instructions, idx), key_name=key)
    return None

def _str_concat_spec(frame, msg):
    instructions = list(dis.get_instructions(frame.f_code))
    tgt = _find_target_instr(instructions, frame.f_lasti)
    if tgt and (tgt.opname == 'BINARY_ADD' or (tgt.opname == 'BINARY_OP' and tgt.arg == 0)):
        idx = instructions.index(tgt)
        if idx > 0 and instructions[idx-1].opname in ('LOAD_FAST', 'LOAD_DEREF'):
            var = frame.f_code.co_varnames[instructions[idx-1].arg]
            return PatchSpec('str_coerce_guard', var, "")
    return None

def _file_guard_spec(frame):
    instructions = list(dis.get_instructions(frame.f_code))
    tgt = _find_target_instr(instructions, frame.f_lasti)
    if tgt is None:
        return None
    idx = instructions.index(tgt)
    for i in range(idx-1, -1, -1):
        if instructions[i].opname in ('LOAD_FAST', 'LOAD_DEREF'):
            var = frame.f_code.co_varnames[instructions[i].arg]
            return PatchSpec('file_guard', var, "")
    return None

def _type_coercion_spec(frame, msg):

    instructions = list(dis.get_instructions(frame.f_code))
    tgt = _find_target_instr(instructions, frame.f_lasti)
    if tgt is None:
        return None
    idx = instructions.index(tgt)

    call_idx = None
    for i in range(max(0, idx - 1), min(idx + 3, len(instructions))):
        if instructions[i].opname in CALL_OPCODES:
            call_idx = i
            break
    if call_idx is None:
        call_idx = idx

    var_name = None
    for i in range(call_idx - 1, -1, -1):
        instr = instructions[i]
        if instr.opname in ('LOAD_FAST', 'LOAD_DEREF'):
            var_name = frame.f_code.co_varnames[instr.arg]
            break
        if instr.opname == 'LOAD_GLOBAL':
            continue
        if instr.opname in CALL_OPCODES or instr.opname == 'PUSH_NULL':
            continue
        break

    if var_name is None:
        return None

    default = 0
    for i in range(call_idx - 1, -1, -1):
        instr = instructions[i]
        if instr.opname == 'LOAD_GLOBAL':
            name = instr.argval
            if isinstance(name, tuple):
                name = name[1] if len(name) > 1 else name[0]
            if name == 'int':
                default = 0
            elif name == 'float':
                default = 0.0
            elif name == 'str':
                default = ""
            break

    return PatchSpec('type_coercion_guard', var_name, default)
