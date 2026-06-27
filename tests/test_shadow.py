"""Tests for codesuture.shadow — shadow execution engine."""

import types
import pytest

from codesuture.shadow import (
    ShadowExecutor, ShadowVerdict, ShadowResult,
    is_sentinel, shadow_check, SENTINEL_VALUES,
)

# Backward compatibility

class TestSentinelDetection:
    """Backward-compat: is_sentinel and SENTINEL_VALUES still work."""

    def test_sentinel_values_exist(self):
        assert isinstance(SENTINEL_VALUES, set)

    def test_is_sentinel_true(self):
        assert is_sentinel("") is True
        assert is_sentinel(0) is True
        assert is_sentinel(None) is True
        assert is_sentinel(False) is True
        assert is_sentinel(()) is True
        assert is_sentinel([]) is True
        assert is_sentinel({}) is True

    def test_is_sentinel_false(self):
        assert is_sentinel("hello") is False
        assert is_sentinel(42) is False
        assert is_sentinel([1, 2, 3]) is False
        assert is_sentinel({"key": "val"}) is False

    def test_shadow_check_prints_on_sentinel(self, capsys):
        shadow_check("test_fn", None, "null_guard")
        captured = capsys.readouterr()
        assert "SHADOW" in captured.out
        assert "test_fn" in captured.out

    def test_shadow_check_silent_on_real_value(self, capsys):
        shadow_check("test_fn", "real_data", "null_guard")
        captured = capsys.readouterr()
        assert captured.out == ""

class TestShadowDataModels:
    def test_verdict_enum_values(self):
        assert ShadowVerdict.PATCH_JUSTIFIED.value == "patch_justified"
        assert ShadowVerdict.PATCH_UNNECESSARY.value == "patch_unnecessary"
        assert ShadowVerdict.PATCH_DIVERGENT.value == "patch_divergent"
        assert ShadowVerdict.SHADOW_ERROR.value == "shadow_error"
        assert ShadowVerdict.NOT_RUN.value == "not_run"

    def test_shadow_result_defaults(self):
        r = ShadowResult()
        assert r.verdict == ShadowVerdict.NOT_RUN
        assert r.original_crashed is False
        assert r.results_match is False

    def test_shadow_result_to_dict(self):
        r = ShadowResult(
            verdict=ShadowVerdict.PATCH_JUSTIFIED,
            original_crashed=True,
            original_exception="AttributeError: x",
            function_name="get_bio",
            guard_type="null_guard",
        )
        d = r.to_dict()
        assert d['verdict'] == 'patch_justified'
        assert d['original_crashed'] is True
        assert d['function_name'] == 'get_bio'

class TestShadowExecutor:

    def _make_function(self, body_code):
        """Create a real function with given body."""
        ns = {}
        exec(body_code, ns)
        return ns[list(ns.keys())[-1]]

    def test_register_and_retrieve(self):
        executor = ShadowExecutor()
        def dummy(): return 42
        executor.register_original("test:dummy", dummy.__code__)
        assert executor.has_original("test:dummy")
        assert executor.get_original("test:dummy") is dummy.__code__

    def test_register_doesnt_overwrite(self):
        executor = ShadowExecutor()
        def v1(): return 1
        def v2(): return 2
        executor.register_original("test:fn", v1.__code__)
        executor.register_original("test:fn", v2.__code__)
        assert executor.get_original("test:fn") is v1.__code__

    def test_shadow_execute_justified(self):
        """Original crashes, patched returns default → PATCH_JUSTIFIED."""
        executor = ShadowExecutor()

        def original():
            raise AttributeError("boom")

        def patched():
            return ""

        executor.register_original("test:fn", original.__code__)

        result = executor.shadow_execute(
            func_key="test:fn",
            patched_func=original,
            patched_result="",
            guard_type="null_guard",
        )

        assert result.verdict == ShadowVerdict.PATCH_JUSTIFIED
        assert result.original_crashed is True
        assert "AttributeError" in result.original_exception

    def test_shadow_execute_unnecessary(self):
        """Original succeeds with same result → PATCH_UNNECESSARY."""
        executor = ShadowExecutor()

        def fn():
            return 42

        original_code = fn.__code__
        executor.register_original("test:fn", original_code)

        result = executor.shadow_execute(
            func_key="test:fn",
            patched_func=fn,
            patched_result=42,
            guard_type="null_guard",
        )
        assert result.verdict == ShadowVerdict.PATCH_UNNECESSARY
        assert result.results_match is True
        assert result.original_crashed is False

    def test_shadow_execute_divergent(self):
        """Original returns different value → PATCH_DIVERGENT."""
        executor = ShadowExecutor()

        def fn():
            return 42

        original_code = fn.__code__
        executor.register_original("test:fn", original_code)

        result = executor.shadow_execute(
            func_key="test:fn",
            patched_func=fn,
            patched_result="",
            guard_type="null_guard",
        )
        assert result.verdict == ShadowVerdict.PATCH_DIVERGENT
        assert result.results_match is False

    def test_shadow_error_no_original(self):
        """No original code stored → SHADOW_ERROR."""
        executor = ShadowExecutor()

        def fn():
            return 1

        result = executor.shadow_execute(
            func_key="test:nonexistent",
            patched_func=fn,
            patched_result=1,
        )
        assert result.verdict == ShadowVerdict.SHADOW_ERROR

    def test_code_restored_after_shadow(self):
        """After shadow execution, the function retains its patched code."""
        executor = ShadowExecutor()

        def original():
            return "original"

        def patched():
            return "patched"

        executor.register_original("test:fn", original.__code__)

        fn = patched
        patched_code = fn.__code__

        executor.shadow_execute(
            func_key="test:fn",
            patched_func=fn,
            patched_result="patched",
        )

        assert fn.__code__ is patched_code
        assert fn() == "patched"

    def test_get_result(self):
        executor = ShadowExecutor()
        def fn(): return 1
        executor.register_original("test:fn", fn.__code__)
        executor.shadow_execute("test:fn", fn, 1)
        result = executor.get_result("test:fn")
        assert result is not None

    def test_get_all_results(self):
        executor = ShadowExecutor()

        def fn1(): return 1
        def fn2(): return 2

        executor.register_original("a", fn1.__code__)
        executor.register_original("b", fn2.__code__)
        executor.shadow_execute("a", fn1, 1)
        executor.shadow_execute("b", fn2, 2)

        all_results = executor.get_all_results()
        assert len(all_results) == 2
        assert "a" in all_results
        assert "b" in all_results

    def test_clear(self):
        executor = ShadowExecutor()
        def fn(): return 1
        executor.register_original("test:fn", fn.__code__)
        executor.shadow_execute("test:fn", fn, 1)
        executor.clear()
        assert not executor.has_original("test:fn")
        assert executor.get_result("test:fn") is None

    def test_concurrent_shadow_same_function(self):
        """Two threads shadow-executing the same function don't corrupt __code__."""
        import threading

        executor = ShadowExecutor()

        def original():
            return "original"

        def patched():
            return "patched"

        original_code = original.__code__
        patched_code = patched.__code__

        fn = patched
        executor.register_original("test:concurrent", original_code)

        errors = []
        results = []

        def worker():
            try:
                r = executor.shadow_execute(
                    func_key="test:concurrent",
                    patched_func=fn,
                    patched_result="patched",
                )
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

        assert fn() == "patched"
        assert fn.__code__ is patched_code
