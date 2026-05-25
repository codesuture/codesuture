"""
CPython version-aware opcode abstraction layer.

Provides correct opcode names, instruction builders, and opcode name sets
for bytecode generation and pattern matching across Python 3.11, 3.12, and 3.13+.

Python 3.11 → 3.12 breaking changes handled:
  - PRECALL removed (folded into CALL)
  - POP_JUMP_FORWARD_IF_FALSE/TRUE → POP_JUMP_IF_FALSE/TRUE
  - LOAD_METHOD merged into LOAD_ATTR
  - BINARY_ADD/SUBTRACT/etc. unified under BINARY_OP (already in 3.11)

Python 3.13 notes:
  - Follows the 3.12 opcode layout for the operations we use
  - BINARY_SUBSCR still exists; BINARY_SLICE added for slice operations
"""

import sys

from bytecode import Instr

PY_VERSION = sys.version_info[:2]

# ---------------------------------------------------------------------------
# Jump opcodes
# ---------------------------------------------------------------------------
if PY_VERSION >= (3, 12):
    JUMP_IF_FALSE = "POP_JUMP_IF_FALSE"
    JUMP_IF_TRUE = "POP_JUMP_IF_TRUE"
else:
    JUMP_IF_FALSE = "POP_JUMP_FORWARD_IF_FALSE"
    JUMP_IF_TRUE = "POP_JUMP_FORWARD_IF_TRUE"

# ---------------------------------------------------------------------------
# Call protocol
# ---------------------------------------------------------------------------
HAS_PRECALL = PY_VERSION < (3, 12)

# ---------------------------------------------------------------------------
# Method / attribute loading
# ---------------------------------------------------------------------------
if PY_VERSION >= (3, 12):
    LOAD_METHOD_OP = "LOAD_ATTR"
else:
    LOAD_METHOD_OP = "LOAD_METHOD"

# ---------------------------------------------------------------------------
# Python 3.13 notes
# ---------------------------------------------------------------------------
# Python 3.13 follows the 3.12 opcode layout for the operations we use:
#   - POP_JUMP_IF_FALSE/TRUE (same as 3.12)
#   - LOAD_ATTR for method loading (same as 3.12)
#   - No PRECALL (same as 3.12)
#   - LOAD_GLOBAL with (push_null, name) tuple (same as 3.11+)
# No additional branching needed for 3.13.

# ---------------------------------------------------------------------------
# LOAD_GLOBAL format
# ---------------------------------------------------------------------------

def make_load_global(name: str, *, push_null: bool = False):
    """Build the argument for LOAD_GLOBAL.

    On 3.11+ LOAD_GLOBAL takes ``(push_null, name)`` tuple.
    On older versions it takes just the name string.
    """
    if PY_VERSION >= (3, 11):
        return (push_null, name)
    return name


# ---------------------------------------------------------------------------
# Instruction builders
# ---------------------------------------------------------------------------

def emit_call(nargs: int) -> list:
    """Emit the call sequence: PRECALL + CALL (3.11) or just CALL (3.12+)."""
    instrs: list = []
    if HAS_PRECALL:
        instrs.append(Instr("PRECALL", nargs))
    instrs.append(Instr("CALL", nargs))
    return instrs


def emit_load_method(name: str) -> Instr:
    """Emit LOAD_METHOD (3.11) or LOAD_ATTR (3.12+)."""
    return Instr(LOAD_METHOD_OP, name)


def emit_jump_if_false(label) -> Instr:
    """Emit the correct conditional-false jump for the running Python."""
    return Instr(JUMP_IF_FALSE, label)


def emit_jump_if_true(label) -> Instr:
    """Emit the correct conditional-true jump for the running Python."""
    return Instr(JUMP_IF_TRUE, label)


def emit_load_global(name: str, *, push_null: bool = False) -> Instr:
    """Emit LOAD_GLOBAL with version-correct argument format."""
    return Instr("LOAD_GLOBAL", make_load_global(name, push_null=push_null))


# ---------------------------------------------------------------------------
# Opcode name sets — for matching in dis.get_instructions() output
# ---------------------------------------------------------------------------

# All opcode names that represent a "call" instruction
CALL_OPCODES: frozenset = frozenset(
    {"CALL", "PRECALL"} if HAS_PRECALL else {"CALL"}
)

# Conditional jumps (both 3.11 and 3.12 names included for resilience)
JUMP_FALSE_OPCODES: frozenset = frozenset(
    {"POP_JUMP_FORWARD_IF_FALSE", "POP_JUMP_IF_FALSE"}
)
JUMP_TRUE_OPCODES: frozenset = frozenset(
    {"POP_JUMP_FORWARD_IF_TRUE", "POP_JUMP_IF_TRUE"}
)
ALL_JUMP_OPCODES: frozenset = JUMP_FALSE_OPCODES | JUMP_TRUE_OPCODES

# Method/attribute loading (match both old and new names in disassembly)
METHOD_LOAD_OPCODES: frozenset = frozenset({"LOAD_METHOD", "LOAD_ATTR"})

# Subscript operation (BINARY_SUBSCR for indexing, BINARY_SLICE added in 3.13
# for slice ops but BINARY_SUBSCR still present for regular indexing)
SUBSCRIPT_OPCODES: frozenset = frozenset({"BINARY_SUBSCR", "BINARY_SLICE"})

# Arithmetic operations (3.10 individual names + 3.11+ unified BINARY_OP)
ARITHMETIC_OPCODES: frozenset = frozenset({
    "BINARY_OP",
    "BINARY_ADD",
    "BINARY_SUBTRACT",
    "BINARY_MULTIPLY",
    "BINARY_TRUE_DIVIDE",
    "BINARY_FLOOR_DIVIDE",
    "BINARY_MODULO",
    "BINARY_POWER",
})

# String formatting
FORMAT_OPCODES: frozenset = frozenset({"FORMAT_VALUE", "BUILD_STRING"})

# Return / store (terminator opcodes for forward scan)
TERMINATOR_OPCODES: frozenset = frozenset({"RETURN_VALUE", "STORE_FAST"})
