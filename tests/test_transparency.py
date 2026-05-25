"""Tests for transparency features — CodeSutureTracer options and _ORIGINAL_CODES dict."""

import pytest

from codesuture.tracer import CodeSutureTracer, _ORIGINAL_CODES


# ---------------------------------------------------------------------------
# CodeSutureTracer construction
# ---------------------------------------------------------------------------

class TestCodeSutureTracerConstruction:
    def test_default_silent_is_false(self):
        tracer = CodeSutureTracer()
        assert tracer.silent is False

    def test_silent_true(self):
        tracer = CodeSutureTracer(silent=True)
        assert tracer.silent is True

    def test_default_dry_run_is_false(self):
        tracer = CodeSutureTracer()
        assert tracer.dry_run is False

    def test_default_verbose_is_false(self):
        tracer = CodeSutureTracer()
        assert tracer.verbose is False

    def test_default_shadow_is_false(self):
        tracer = CodeSutureTracer()
        assert tracer.shadow_mode is False

    def test_default_autonomous_is_false(self):
        tracer = CodeSutureTracer()
        assert tracer.autonomous is False

    def test_default_max_retries(self):
        tracer = CodeSutureTracer()
        assert tracer.max_retries == 3

    def test_custom_max_retries(self):
        tracer = CodeSutureTracer(max_retries=10)
        assert tracer.max_retries == 10

    def test_default_ttl(self):
        tracer = CodeSutureTracer()
        assert tracer.ttl == 7

    def test_custom_ttl(self):
        tracer = CodeSutureTracer(ttl=30)
        assert tracer.ttl == 30

    def test_log_file_default_none(self):
        tracer = CodeSutureTracer()
        assert tracer.log_file is None

    def test_log_file_custom(self):
        tracer = CodeSutureTracer(log_file="my.log")
        assert tracer.log_file == "my.log"

    def test_script_path_default_none(self):
        tracer = CodeSutureTracer()
        assert tracer.script_path is None

    def test_all_kwargs(self):
        tracer = CodeSutureTracer(
            dry_run=True,
            log_file="test.log",
            max_retries=5,
            autonomous=True,
            script_path="script.py",
            verbose=True,
            shadow=True,
            ttl=14,
            silent=True,
        )
        assert tracer.dry_run is True
        assert tracer.log_file == "test.log"
        assert tracer.max_retries == 5
        assert tracer.autonomous is True
        assert tracer.script_path == "script.py"
        assert tracer.verbose is True
        assert tracer.shadow_mode is True
        assert tracer.ttl == 14
        assert tracer.silent is True


# ---------------------------------------------------------------------------
# Tracer initial state
# ---------------------------------------------------------------------------

class TestTracerInitialState:
    def test_stats_initialized(self):
        tracer = CodeSutureTracer()
        assert tracer.stats["patched"] == 0
        assert tracer.stats["dry_run_suggestions"] == 0
        assert tracer.stats["self_healed"] == 0

    def test_patched_signatures_empty(self):
        tracer = CodeSutureTracer()
        assert tracer.patched_signatures == {}

    def test_patched_codes_empty(self):
        tracer = CodeSutureTracer()
        assert tracer._patched_codes == {}

    def test_attempts_empty(self):
        tracer = CodeSutureTracer()
        assert tracer.attempts == {}


# ---------------------------------------------------------------------------
# _ORIGINAL_CODES
# ---------------------------------------------------------------------------

class TestOriginalCodesDict:
    def test_exists_and_is_dict(self):
        assert isinstance(_ORIGINAL_CODES, dict)

    def test_is_module_level(self):
        import codesuture.tracer as tracer_mod
        assert hasattr(tracer_mod, "_ORIGINAL_CODES")
        assert tracer_mod._ORIGINAL_CODES is _ORIGINAL_CODES

    def test_can_store_and_retrieve(self):
        """Verify the dict is usable — store a code object and get it back."""
        def sample():
            return 1

        key = "test:sample"
        _ORIGINAL_CODES[key] = sample.__code__
        try:
            assert _ORIGINAL_CODES[key] is sample.__code__
        finally:
            _ORIGINAL_CODES.pop(key, None)
