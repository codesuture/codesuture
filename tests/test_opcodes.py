"""Tests for codesuture.opcodes — version-aware opcode abstraction layer."""

import sys
import pytest
from bytecode import Instr, Label

from codesuture.opcodes import (
    JUMP_IF_FALSE,
    JUMP_IF_TRUE,
    HAS_PRECALL,
    LOAD_METHOD_OP,
    make_load_global,
    emit_call,
    emit_load_method,
    emit_jump_if_false,
    emit_jump_if_true,
    emit_load_global,
    CALL_OPCODES,
    JUMP_FALSE_OPCODES,
    JUMP_TRUE_OPCODES,
    ALL_JUMP_OPCODES,
    METHOD_LOAD_OPCODES,
    SUBSCRIPT_OPCODES,
    ARITHMETIC_OPCODES,
    FORMAT_OPCODES,
    TERMINATOR_OPCODES,
    PY_VERSION,
)

class TestConstants:
    def test_jump_if_false_is_string(self):
        assert isinstance(JUMP_IF_FALSE, str)
        assert len(JUMP_IF_FALSE) > 0

    def test_jump_if_true_is_string(self):
        assert isinstance(JUMP_IF_TRUE, str)
        assert len(JUMP_IF_TRUE) > 0

    def test_has_precall_is_bool(self):
        assert isinstance(HAS_PRECALL, bool)

    def test_has_precall_matches_version(self):
        if PY_VERSION >= (3, 12):
            assert HAS_PRECALL is False
        else:
            assert HAS_PRECALL is True

    def test_load_method_op_is_string(self):
        assert isinstance(LOAD_METHOD_OP, str)
        assert LOAD_METHOD_OP in ("LOAD_METHOD", "LOAD_ATTR")

    def test_jump_opcodes_match_version(self):
        if PY_VERSION >= (3, 12):
            assert JUMP_IF_FALSE == "POP_JUMP_IF_FALSE"
            assert JUMP_IF_TRUE == "POP_JUMP_IF_TRUE"
        else:
            assert JUMP_IF_FALSE == "POP_JUMP_FORWARD_IF_FALSE"
            assert JUMP_IF_TRUE == "POP_JUMP_FORWARD_IF_TRUE"

class TestMakeLoadGlobal:
    def test_returns_correct_format(self):
        result = make_load_global("foo")
        if PY_VERSION >= (3, 11):
            assert isinstance(result, tuple)
            assert result == (False, "foo")
        else:
            assert result == "foo"

    def test_push_null_true(self):
        result = make_load_global("bar", push_null=True)
        if PY_VERSION >= (3, 11):
            assert result == (True, "bar")
        else:
            assert result == "bar"

    def test_push_null_false(self):
        result = make_load_global("baz", push_null=False)
        if PY_VERSION >= (3, 11):
            assert result == (False, "baz")
        else:
            assert result == "baz"

class TestEmitCall:
    def test_returns_list(self):
        result = emit_call(0)
        assert isinstance(result, list)

    def test_all_elements_are_instr(self):
        result = emit_call(2)
        for item in result:
            assert isinstance(item, Instr)

    def test_includes_call(self):
        result = emit_call(1)
        opnames = [i.name for i in result]
        assert "CALL" in opnames

    def test_precall_presence_matches_version(self):
        result = emit_call(3)
        opnames = [i.name for i in result]
        if HAS_PRECALL:
            assert "PRECALL" in opnames
            assert len(result) == 2
        else:
            assert "PRECALL" not in opnames
            assert len(result) == 1

class TestEmitLoadMethod:
    def test_returns_instr(self):
        result = emit_load_method("my_method")
        assert isinstance(result, Instr)

    def test_correct_opname(self):
        result = emit_load_method("my_method")
        assert result.name == LOAD_METHOD_OP

    def test_arg_is_name(self):
        result = emit_load_method("some_attr")
        assert result.arg == "some_attr"

class TestEmitJumpIfFalse:
    def test_returns_instr(self):
        label = Label()
        result = emit_jump_if_false(label)
        assert isinstance(result, Instr)

    def test_correct_opname(self):
        label = Label()
        result = emit_jump_if_false(label)
        assert result.name == JUMP_IF_FALSE

class TestEmitJumpIfTrue:
    def test_returns_instr(self):
        label = Label()
        result = emit_jump_if_true(label)
        assert isinstance(result, Instr)

    def test_correct_opname(self):
        label = Label()
        result = emit_jump_if_true(label)
        assert result.name == JUMP_IF_TRUE

class TestEmitLoadGlobal:
    def test_returns_instr(self):
        result = emit_load_global("print")
        assert isinstance(result, Instr)

    def test_opname_is_load_global(self):
        result = emit_load_global("len")
        assert result.name == "LOAD_GLOBAL"

    def test_push_null_kwarg(self):
        result = emit_load_global("open", push_null=True)
        assert isinstance(result, Instr)
        assert result.name == "LOAD_GLOBAL"

class TestOpcodeSets:
    ALL_SETS = [
        ("CALL_OPCODES", CALL_OPCODES),
        ("JUMP_FALSE_OPCODES", JUMP_FALSE_OPCODES),
        ("JUMP_TRUE_OPCODES", JUMP_TRUE_OPCODES),
        ("ALL_JUMP_OPCODES", ALL_JUMP_OPCODES),
        ("METHOD_LOAD_OPCODES", METHOD_LOAD_OPCODES),
        ("SUBSCRIPT_OPCODES", SUBSCRIPT_OPCODES),
        ("ARITHMETIC_OPCODES", ARITHMETIC_OPCODES),
        ("FORMAT_OPCODES", FORMAT_OPCODES),
        ("TERMINATOR_OPCODES", TERMINATOR_OPCODES),
    ]

    @pytest.mark.parametrize("name,opset", ALL_SETS, ids=[n for n, _ in ALL_SETS])
    def test_is_frozenset(self, name, opset):
        assert isinstance(opset, frozenset), f"{name} should be a frozenset"

    @pytest.mark.parametrize("name,opset", ALL_SETS, ids=[n for n, _ in ALL_SETS])
    def test_has_at_least_one_element(self, name, opset):
        assert len(opset) >= 1, f"{name} should have at least 1 element"

    @pytest.mark.parametrize("name,opset", ALL_SETS, ids=[n for n, _ in ALL_SETS])
    def test_elements_are_strings(self, name, opset):
        for item in opset:
            assert isinstance(item, str), f"All elements of {name} should be strings"

    def test_all_jump_is_union(self):
        assert ALL_JUMP_OPCODES == JUMP_FALSE_OPCODES | JUMP_TRUE_OPCODES

    def test_call_opcodes_contains_call(self):
        assert "CALL" in CALL_OPCODES

    def test_method_load_opcodes_contents(self):
        assert "LOAD_METHOD" in METHOD_LOAD_OPCODES
        assert "LOAD_ATTR" in METHOD_LOAD_OPCODES

    def test_subscript_opcodes_contents(self):
        assert "BINARY_SUBSCR" in SUBSCRIPT_OPCODES

    def test_terminator_opcodes_contents(self):
        assert "RETURN_VALUE" in TERMINATOR_OPCODES
        assert "STORE_FAST" in TERMINATOR_OPCODES
