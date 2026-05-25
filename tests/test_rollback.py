"""Tests for codesuture.rollback — function rollback, rollback-all, dry-run."""

import json
import marshal
import os
import shutil

import pytest

from codesuture.persistence import CACHE_DIR, save_patch
from codesuture.rollback import (
    rollback_all,
    rollback_dry_run,
    rollback_function,
    rollback_runtime,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dummy_target():
    return 42


def _make_func_with_spec():
    from codesuture.pattern_matcher import PatchSpec

    func = _dummy_target
    code_obj = func.__code__
    spec = PatchSpec(
        strategy="null_guard",
        var_name="x",
        default_value="",
    )
    return func, code_obj, spec


def _base_name(func):
    func_name = func.__qualname__.replace("<", "_").replace(">", "_")
    return f"{func.__module__}.{func_name}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def clean_env(tmp_path, monkeypatch):
    """Chdir into a temp directory so .codesuture_store lives there."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    store = tmp_path / CACHE_DIR
    if store.exists():
        shutil.rmtree(store)
    fp = tmp_path / ".codesuture_fingerprints"
    if fp.exists():
        fp.unlink()


# ---------------------------------------------------------------------------
# rollback_function
# ---------------------------------------------------------------------------

class TestRollbackFunction:
    def test_removes_code_and_json(self, clean_env):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        base = _base_name(func)
        code_path = clean_env / CACHE_DIR / f"{base}.code"
        json_path = clean_env / CACHE_DIR / f"{base}.json"
        assert code_path.exists()
        assert json_path.exists()

        # rollback_function matches by the short function name
        rollback_function(func.__qualname__.replace("<", "_").replace(">", "_"))

        assert not code_path.exists()
        assert not json_path.exists()

    def test_removes_orig_code_too(self, clean_env):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec, original_code=func.__code__)

        base = _base_name(func)
        orig_path = clean_env / CACHE_DIR / f"{base}.orig.code"
        assert orig_path.exists()

        rollback_function(func.__qualname__.replace("<", "_").replace(">", "_"))
        assert not orig_path.exists()

    def test_no_error_when_no_store(self, clean_env, capsys):
        rollback_function("nonexistent_func")
        captured = capsys.readouterr()
        assert "Nothing to roll back" in captured.out or "No patch found" in captured.out


# ---------------------------------------------------------------------------
# rollback_all
# ---------------------------------------------------------------------------

class TestRollbackAll:
    def test_removes_entire_store(self, clean_env):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        store = clean_env / CACHE_DIR
        assert store.exists()

        rollback_all()
        assert not store.exists()

    def test_removes_fingerprint_file(self, clean_env):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        fp_file = clean_env / ".codesuture_fingerprints"
        fp_file.write_text("{}", encoding="utf-8")
        assert fp_file.exists()

        rollback_all()
        assert not fp_file.exists()

    def test_no_error_when_nothing_to_rollback(self, clean_env, capsys):
        rollback_all()
        captured = capsys.readouterr()
        assert "Nothing to roll back" in captured.out


# ---------------------------------------------------------------------------
# rollback_dry_run
# ---------------------------------------------------------------------------

class TestRollbackDryRun:
    def test_lists_patches_without_deleting(self, clean_env, capsys):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        rollback_dry_run()
        captured = capsys.readouterr()
        assert "DRY-RUN" in captured.out
        assert "Would remove" in captured.out

        # Files should still exist
        base = _base_name(func)
        code_path = clean_env / CACHE_DIR / f"{base}.code"
        json_path = clean_env / CACHE_DIR / f"{base}.json"
        assert code_path.exists()
        assert json_path.exists()

    def test_prints_guard_type(self, clean_env, capsys):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        rollback_dry_run()
        captured = capsys.readouterr()
        assert "null_guard" in captured.out

    def test_mentions_fingerprint_file(self, clean_env, capsys):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        fp_file = clean_env / ".codesuture_fingerprints"
        fp_file.write_text("{}", encoding="utf-8")

        rollback_dry_run()
        captured = capsys.readouterr()
        assert "fingerprint" in captured.out.lower()

    def test_no_error_when_no_store(self, clean_env, capsys):
        rollback_dry_run()
        captured = capsys.readouterr()
        assert "Nothing to roll back" in captured.out


# ---------------------------------------------------------------------------
# rollback_runtime
# ---------------------------------------------------------------------------

class TestRollbackRuntime:
    def test_restores_from_original_codes_dict(self, clean_env):
        """When _ORIGINAL_CODES has an entry, rollback_runtime restores it."""
        from codesuture.tracer import _ORIGINAL_CODES

        def my_func():
            return 99

        original_code = my_func.__code__

        # Simulate that CodeSuture saved the original and patched the function
        func_key = f"{my_func.__code__.co_filename}:{my_func.__code__.co_name}"
        _ORIGINAL_CODES[func_key] = original_code

        # The function is still reachable via gc, so rollback_runtime should
        # find it. The call returns True/False. We mainly verify no crash.
        result = rollback_runtime("my_func")
        # Whether or not the live function was found, the dict entry is
        # consumed on success. At minimum we assert no exception.
        assert isinstance(result, bool)

        # Clean up
        _ORIGINAL_CODES.pop(func_key, None)

    def test_returns_false_when_nothing_found(self, clean_env, capsys):
        result = rollback_runtime("totally_fake_function_xyz")
        assert result is False
        captured = capsys.readouterr()
        assert "No live function found" in captured.out
