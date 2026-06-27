"""Tests for codesuture.sandbox — subprocess fix verification."""

import os
import sys
import tempfile
import pytest

from codesuture.sandbox import verify_fix

class TestSandboxTestFix:
    """Real subprocess sandbox tests."""

    def _write_script(self, tmp_path, filename, content):
        """Write a script file and return its path."""
        path = tmp_path / filename
        path.write_text(content, encoding='utf-8')
        return str(path)

    def test_fix_passes_when_fix_is_correct(self, tmp_path):
        """A correct fix should return True."""

        script = self._write_script(tmp_path, "buggy.py", """
def divide(a, b):
    return a / b

result = divide(10, 0)
print(result)
""")

        fixed_source = """
def divide(a, b):
    if b == 0:
        return 0
    return a / b
"""
        result = verify_fix(
            original_script_path=script,
            module_name='__main__',
            func_name='divide',
            new_source=fixed_source,
            exc_type_name='ZeroDivisionError',
        )
        assert result is True

    def test_fix_fails_when_same_exception_occurs(self, tmp_path):
        """A 'fix' that doesn't actually fix should return False."""
        script = self._write_script(tmp_path, "buggy.py", """
def divide(a, b):
    return a / b

result = divide(10, 0)
print(result)
""")

        bad_fix = """
def divide(a, b):
    return a / b
"""
        result = verify_fix(
            original_script_path=script,
            module_name='__main__',
            func_name='divide',
            new_source=bad_fix,
            exc_type_name='ZeroDivisionError',
        )
        assert result is False

    def test_fix_fails_when_new_exception_introduced(self, tmp_path):
        """A 'fix' that introduces a different exception returns False."""
        script = self._write_script(tmp_path, "buggy.py", """
def get_data():
    data = None
    return data.strip()

result = get_data()
""")

        bad_fix = """
def get_data():
    return undefined_variable
"""
        result = verify_fix(
            original_script_path=script,
            module_name='__main__',
            func_name='get_data',
            new_source=bad_fix,
            exc_type_name='AttributeError',
        )
        assert result is False

    def test_fix_timeout_returns_false(self, tmp_path):
        """A fix that causes an infinite loop times out and returns False."""
        script = self._write_script(tmp_path, "buggy.py", """
def compute():
    return 1 / 0

result = compute()
""")

        loop_fix = """
def compute():
    while True:
        pass
"""
        result = verify_fix(
            original_script_path=script,
            module_name='__main__',
            func_name='compute',
            new_source=loop_fix,
            exc_type_name='ZeroDivisionError',
        )
        assert result is False

    def test_fix_with_correct_none_guard(self, tmp_path):
        """Fix that adds a None check should pass."""
        script = self._write_script(tmp_path, "buggy.py", """
def get_name(user):
    return user.strip()

result = get_name(None)
print(repr(result))
""")
        fixed_source = """
def get_name(user):
    if user is None:
        return ""
    return user.strip()
"""
        result = verify_fix(
            original_script_path=script,
            module_name='__main__',
            func_name='get_name',
            new_source=fixed_source,
            exc_type_name='AttributeError',
        )
        assert result is True

    def test_cleanup_removes_temp_files(self, tmp_path):
        """Temp files should be cleaned up after verify_fix runs."""
        script = self._write_script(tmp_path, "buggy.py", """
def fn():
    return 1 / 0
result = fn()
""")
        fixed = """
def fn():
    return 0
"""

        temp_dir = tempfile.gettempdir()
        before = set(os.listdir(temp_dir))

        verify_fix(
            original_script_path=script,
            module_name='__main__',
            func_name='fn',
            new_source=fixed,
            exc_type_name='ZeroDivisionError',
        )

        after = set(os.listdir(temp_dir))

        leaked = after - before
        py_leaked = [f for f in leaked if f.endswith('.py')]
        assert len(py_leaked) == 0, f"Leaked temp files: {py_leaked}"
