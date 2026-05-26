import dis
import pytest
from codesuture.pattern_matcher import analyze_exception, PatchSpec, _infer_default

def make_frame(func, lasti):
    class FakeFrame:
        f_code = func.__code__
        f_lasti = lasti
        f_locals = {func.__name__: func}
        f_globals = {}
        f_builtins = {}
    return FakeFrame()

def test_infer_default():
    assert _infer_default("count") == 0
    assert _infer_default("name") == ""
    assert _infer_default("user_items") == []
    assert _infer_default("x") == ""

def test_analyze_attribute_error():
    def buggy():
        user = None
        return user.get_name()
    instrs = list(dis.get_instructions(buggy.__code__))
    load_fast_user = None
    for i, instr in enumerate(instrs):
        if instr.opname == 'LOAD_FAST' and buggy.__code__.co_varnames[instr.arg] == 'user':
            load_fast_user = instr
            break
    assert load_fast_user is not None
    load_attr = instrs[i+1]
    assert load_attr.opname in ('LOAD_ATTR', 'LOAD_METHOD')
    frame = make_frame(buggy, lasti=load_attr.offset)
    spec = analyze_exception(frame, AttributeError, AttributeError("'NoneType' object has no attribute 'get_name'"), None)
    assert spec is not None
    assert spec.strategy == 'null_guard'
    assert spec.var_name == 'user'
    assert spec.default_value == ""

def test_analyze_string_method_attribute_error_defaults_to_empty_string():
    def buggy():
        user = None
        return user.strip()

    instrs = list(dis.get_instructions(buggy.__code__))
    load_method = next(instr for instr in instrs if instr.opname in ('LOAD_METHOD', 'LOAD_ATTR'))
    frame = make_frame(buggy, lasti=load_method.offset)

    spec = analyze_exception(
        frame,
        AttributeError,
        AttributeError("'NoneType' object has no attribute 'strip'"),
        None,
    )

    assert spec is not None
    assert spec.strategy == 'null_guard'
    assert spec.var_name == 'user'
    assert spec.default_value == ''

def test_analyze_zero_division():
    def div_bug():
        price = 100
        discount = 0
        return price / discount
    instrs = list(dis.get_instructions(div_bug.__code__))
    candidates = [i for i in instrs if i.opname == 'BINARY_OP']
    assert candidates, "No BINARY_OP found at all – check Python version"
    bin_op = candidates[0]
    idx = instrs.index(bin_op)
    load_denom = instrs[idx - 1]
    assert load_denom.opname == 'LOAD_FAST'
    assert div_bug.__code__.co_varnames[load_denom.arg] == 'discount'
    frame = make_frame(div_bug, lasti=bin_op.offset)
    spec = analyze_exception(frame, ZeroDivisionError, ZeroDivisionError("division by zero"), None)
    assert spec is not None
    assert spec.strategy == 'division_guard'
    assert spec.var_name == 'discount'
    assert spec.default_value == 1

def test_index_error_const_index_returns_list_bound_guard():
    """x[5] (constant integer index) -> list_bound_guard with list var and const key."""
    def simple():
        x = [1, 2]
        return x[5]
    instrs = list(dis.get_instructions(simple.__code__))
    subscr = next(i for i in instrs if i.opname == 'BINARY_SUBSCR')
    frame = make_frame(simple, lasti=subscr.offset)
    spec = analyze_exception(frame, IndexError, IndexError("list index out of range"), None)
    assert spec is not None, "IndexError on x[5] should return a spec"
    assert spec.strategy == 'list_bound_guard', (
        f"Expected list_bound_guard for constant-index subscript, got {spec.strategy}"
    )
    assert spec.var_name == 'x', f"Expected var_name='x', got {spec.var_name!r}"
    assert spec.key_name == (5,), f"Expected key_name=(5,), got {spec.key_name!r}"


def test_index_error_var_index_returns_index_guard():
    """list[i] (variable index) -> index_guard with idx_var and list_len_var."""
    def search(items, i):
        return items[i]
    instrs = list(dis.get_instructions(search.__code__))
    subscr = next(i for i in instrs if i.opname == 'BINARY_SUBSCR')
    frame = make_frame(search, lasti=subscr.offset)
    spec = analyze_exception(frame, IndexError, IndexError("list index out of range"), None)
    assert spec is not None, "IndexError on items[i] should return a spec"
    assert spec.strategy == 'index_guard', (
        f"Expected index_guard for variable-index subscript, got {spec.strategy}"
    )
    assert spec.var_name == 'i', f"Expected var_name='i', got {spec.var_name!r}"
    assert spec.list_len_var == 'items', f"Expected list_len_var='items', got {spec.list_len_var!r}"

def test_truly_unknown_exception():
    """Exceptions we don't handle should return None."""
    def simple():
        import math
        return math.sqrt(-1)
    instrs = list(dis.get_instructions(simple.__code__))
    frame = make_frame(simple, lasti=0)
    spec = analyze_exception(frame, ValueError, ValueError("math domain error"), None)
    assert spec is None