import pytest
import types
from codesuture.pattern_matcher import PatchSpec
from codesuture.guard_synthesizer import synthesize_guarded_code

def test_key_guard_synthesis():

    def original(d):
        return d['missing']

    spec = PatchSpec('key_guard', 'd', 'default_val', key_name='missing')
    new_bc = synthesize_guarded_code(original.__code__, spec)
    new_code = new_bc.to_code()
    new_func = types.FunctionType(new_code, {})

    assert new_func({'missing': 'found'}) == 'found'
    assert new_func({}) == 'default_val'

def test_index_guard_synthesis():

    def original(lst, i):
        return lst[i]

    spec = PatchSpec('index_guard', 'i', 0, list_len_var='lst')
    new_bc = synthesize_guarded_code(original.__code__, spec)
    new_code = new_bc.to_code()
    new_func = types.FunctionType(new_code, {'len': len})

    assert new_func([10, 20], 1) == 20
    assert new_func([10, 20], 5) == 0  # returns default, NOT items[0]

def test_type_coercion_guard_synthesis():

    def original(val):
        return int(val) * 2

    spec = PatchSpec('type_coercion_guard', 'val', 0)
    new_bc = synthesize_guarded_code(original.__code__, spec)
    new_code = new_bc.to_code()
    new_func = types.FunctionType(new_code, {'int': int, 'isinstance': isinstance, 'str': str})

    assert new_func("123") == 246
    assert new_func("not_a_number") == 0

def test_key_guard_targets_matching_nested_key():
    def original(state):
        return state["config"]["retry_limit"]

    spec = PatchSpec('key_guard', 'state', None, key_name='retry_limit')
    new_bc = synthesize_guarded_code(original.__code__, spec)
    new_code = new_bc.to_code()
    new_func = types.FunctionType(new_code, {})

    assert new_func({"config": {"retry_limit": 3}}) == 3
    assert new_func({"config": {}}) is None

def test_type_coercion_guard_after_branch_local_assignment():
    def original(path):
        if path == "pay":
            user_input = "not_a_number"
            return int(user_input)
        return "ok"

    spec = PatchSpec('type_coercion_guard', 'user_input', 0)
    new_bc = synthesize_guarded_code(original.__code__, spec)
    new_code = new_bc.to_code()
    new_func = types.FunctionType(new_code, {'int': int, 'isinstance': isinstance, 'str': str})

    assert new_func("other") == "ok"
    assert new_func("pay") == 0

def test_chain_subscript_guard_handles_global_list_index():
    state = {"active_users": ["admin"]}

    def original():
        return STATE["active_users"][10]

    spec = PatchSpec('chain_subscript_guard', 'STATE', None, key_name=('active_users', 10))
    new_bc = synthesize_guarded_code(original.__code__, spec)
    new_code = new_bc.to_code()
    new_func = types.FunctionType(new_code, {'STATE': state, 'len': len})

    assert new_func() is None

def test_file_not_found_guard_synthesis():
    import os
    def original(path):
        with open(path) as f:
            return f.read()

    spec = PatchSpec('file_guard', 'path', "file_content_default")
    new_bc = synthesize_guarded_code(original.__code__, spec)
    new_code = new_bc.to_code()
    new_func = types.FunctionType(new_code, {'os': os, 'open': open})

    assert new_func("non_existent_file_xyz.txt") == "file_content_default"

def test_str_concat_guard_synthesis():
    def original(s, n):
        return s + n

    spec = PatchSpec('str_coerce_guard', 'n', "")
    new_bc = synthesize_guarded_code(original.__code__, spec)
    new_code = new_bc.to_code()
    new_func = types.FunctionType(new_code, {'isinstance': isinstance, 'str': str})

    assert new_func("age: ", "25") == "age: 25"
    assert new_func("age: ", 25) == "age: 25"
