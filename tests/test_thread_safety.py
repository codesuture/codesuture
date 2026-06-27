"""Tests for thread safety in fingerprint.record() and persistence.save_patch()."""

import os
import shutil
import threading

import pytest

from codesuture.fingerprint import FINGERPRINT_FILE, record
from codesuture.persistence import CACHE_DIR, save_patch

def _make_dummy_func(n):
    """Create a unique dummy function (different code objects)."""

    ns = {}
    exec(f"def _dummy_{n}():\n    return {n}", ns)
    func = ns[f"_dummy_{n}"]
    func.__module__ = "test_thread_safety"
    return func

@pytest.fixture()
def clean_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    store = tmp_path / CACHE_DIR
    if store.exists():
        shutil.rmtree(store)
    fp = tmp_path / FINGERPRINT_FILE
    if fp.exists():
        fp.unlink()

NUM_WORKERS = 8
RECORDS_PER_WORKER = 5

class TestConcurrentRecord:
    def test_concurrent_record_no_corruption(self, clean_env):
        """Multiple threads calling record() concurrently must not corrupt the
        fingerprint registry file."""
        errors = []

        def worker(thread_id):
            try:
                for i in range(RECORDS_PER_WORKER):
                    fp = f"fp_{thread_id:03d}_{i:03d}_{'a' * 16}"[:16]
                    record(
                        fingerprint=fp,
                        guard_type="null_guard",
                        target="x",
                        func_name=f"func_{thread_id}_{i}",
                        error_type="AttributeError",
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(NUM_WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Threads raised exceptions: {errors}"

        fp_path = clean_env / FINGERPRINT_FILE
        if fp_path.exists():
            import json
            with open(fp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert isinstance(data, dict)

            assert len(data) > 0

class TestConcurrentSavePatch:
    def test_concurrent_save_patch_no_corruption(self, clean_env):
        """Multiple threads calling save_patch() for different functions
        must not crash or corrupt each other's files."""
        from codesuture.pattern_matcher import PatchSpec

        errors = []
        funcs = [_make_dummy_func(i) for i in range(NUM_WORKERS)]

        def worker(thread_id):
            try:
                func = funcs[thread_id]
                spec = PatchSpec(
                    strategy="null_guard",
                    var_name=f"var_{thread_id}",
                    default_value="",
                )
                for _ in range(RECORDS_PER_WORKER):
                    save_patch(func, func.__code__, spec)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(NUM_WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Threads raised exceptions: {errors}"

        store = clean_env / CACHE_DIR
        assert store.exists()

        code_files = list(store.glob("*.code"))
        json_files = list(store.glob("*.json"))
        assert len(code_files) == NUM_WORKERS
        assert len(json_files) == NUM_WORKERS

        import json as _json
        for jf in json_files:
            with open(jf, "r", encoding="utf-8") as f:
                data = _json.load(f)
            assert "code_sha256" in data
