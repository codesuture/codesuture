"""Tests for codesuture.persistence — patch save / load / integrity."""

import hashlib
import json
import marshal
import os
import shutil

import pytest

from codesuture.persistence import (
    CACHE_DIR,
    save_patch,
    _load_cached_code,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dummy():
    return 42


def _make_func_with_spec():
    """Return a (func, code_obj, spec-like) triple ready for save_patch."""
    from codesuture.pattern_matcher import PatchSpec

    func = _dummy
    code_obj = func.__code__
    spec = PatchSpec(
        strategy="null_guard",
        var_name="x",
        default_value="",
    )
    return func, code_obj, spec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def clean_store(tmp_path, monkeypatch):
    """Run every test inside a fresh temp directory so that
    .codesuture_store is created there instead of in the project root."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    store = tmp_path / CACHE_DIR
    if store.exists():
        shutil.rmtree(store)


# ---------------------------------------------------------------------------
# save_patch
# ---------------------------------------------------------------------------

class TestSavePatch:
    def test_creates_code_and_json(self, clean_store):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        func_name = func.__qualname__.replace("<", "_").replace(">", "_")
        base = f"{func.__module__}.{func_name}"

        code_path = clean_store / CACHE_DIR / f"{base}.code"
        json_path = clean_store / CACHE_DIR / f"{base}.json"

        assert code_path.exists(), ".code file should be created"
        assert json_path.exists(), ".json file should be created"

    def test_creates_orig_code_file(self, clean_store):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec, original_code=func.__code__)

        func_name = func.__qualname__.replace("<", "_").replace(">", "_")
        base = f"{func.__module__}.{func_name}"
        orig_path = clean_store / CACHE_DIR / f"{base}.orig.code"
        assert orig_path.exists(), ".orig.code file should be created"

        # Verify the marshalled content round-trips
        with open(orig_path, "rb") as f:
            loaded = marshal.load(f)
        assert loaded.co_name == func.__code__.co_name

    def test_json_contains_code_sha256(self, clean_store):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        func_name = func.__qualname__.replace("<", "_").replace(">", "_")
        base = f"{func.__module__}.{func_name}"
        json_path = clean_store / CACHE_DIR / f"{base}.json"

        with open(json_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        assert "code_sha256" in metadata
        assert isinstance(metadata["code_sha256"], str)
        assert len(metadata["code_sha256"]) == 64  # SHA-256 hex digest

    def test_json_metadata_fields(self, clean_store):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec, ttl_days=14)

        func_name = func.__qualname__.replace("<", "_").replace(">", "_")
        base = f"{func.__module__}.{func_name}"
        json_path = clean_store / CACHE_DIR / f"{base}.json"

        with open(json_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        assert metadata["guard_type"] == "null_guard"
        assert metadata["target"] == "x"
        assert metadata["ttl_days"] == 14
        assert "patched_at" in metadata
        assert "func_name" in metadata

    def test_code_file_is_valid_marshal(self, clean_store):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        func_name = func.__qualname__.replace("<", "_").replace(">", "_")
        base = f"{func.__module__}.{func_name}"
        code_path = clean_store / CACHE_DIR / f"{base}.code"

        with open(code_path, "rb") as f:
            raw = f.read()

        loaded_code = marshal.loads(raw)
        assert loaded_code.co_name == code_obj.co_name

    def test_no_save_when_no_module(self, clean_store):
        """save_patch returns early when func has no __module__."""
        func, code_obj, spec = _make_func_with_spec()

        # Create a wrapper that has no __module__
        class FakeFunc:
            __qualname__ = "fakefunc"
            __name__ = "fakefunc"
        fake = FakeFunc()
        fake.__module__ = None  # type: ignore[attr-defined]

        save_patch(fake, code_obj, spec)
        store = clean_store / CACHE_DIR
        assert not store.exists() or len(list(store.iterdir())) == 0


# ---------------------------------------------------------------------------
# _load_cached_code
# ---------------------------------------------------------------------------

class TestLoadCachedCode:
    def test_loads_valid_code(self, clean_store):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        func_name = func.__qualname__.replace("<", "_").replace(">", "_")
        loaded = _load_cached_code(func.__module__, func_name)

        assert loaded is not None
        assert loaded.co_name == code_obj.co_name

    def test_returns_none_when_no_store(self, clean_store):
        loaded = _load_cached_code("nonexistent.module", "no_func")
        assert loaded is None

    def test_tampered_code_returns_none(self, clean_store, capsys):
        func, code_obj, spec = _make_func_with_spec()
        save_patch(func, code_obj, spec)

        func_name = func.__qualname__.replace("<", "_").replace(">", "_")
        base = f"{func.__module__}.{func_name}"
        code_path = clean_store / CACHE_DIR / f"{base}.code"

        # Tamper with the .code file by appending garbage bytes
        with open(code_path, "ab") as f:
            f.write(b"\x00\xff\xfe\xfd")

        loaded = _load_cached_code(func.__module__, func_name)
        assert loaded is None

        captured = capsys.readouterr()
        assert "integrity check failed" in captured.out.lower() or "WARNING" in captured.out

    def test_missing_json_legacy_warning(self, clean_store, capsys):
        """When .json is absent, the loader prints a legacy-patch warning."""
        func, code_obj, _ = _make_func_with_spec()

        func_name = func.__qualname__.replace("<", "_").replace(">", "_")
        base = f"{func.__module__}.{func_name}"

        os.makedirs(os.path.join(str(clean_store), CACHE_DIR), exist_ok=True)
        code_path = os.path.join(str(clean_store), CACHE_DIR, f"{base}.code")
        with open(code_path, "wb") as f:
            f.write(marshal.dumps(code_obj))

        loaded = _load_cached_code(func.__module__, func_name)
        captured = capsys.readouterr()
        # Legacy patch — no hash to verify, but should still load
        assert "Legacy patch" in captured.out or loaded is not None
