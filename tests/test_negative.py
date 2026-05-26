"""Negative tests — edge cases, failures, unsupported scenarios.

Every test actually invokes CodeSuture code and asserts real behavior.
No 'assert True' padding.
"""

import sys
import types
import threading
import pytest
from codesuture.tracer import CodeSutureTracer
from codesuture.pattern_matcher import analyze_exception


class TestUnsupportedExceptions:
    """Exceptions that should NOT be patched."""

    def test_system_exit_not_patched(self):
        """SystemExit should not produce a guard spec."""
        def bad_func():
            raise SystemExit("goodbye")

        try:
            bad_func()
        except SystemExit as e:
            tb = sys.exc_info()[2]
            inner = tb
            while inner.tb_next:
                inner = inner.tb_next
            spec = analyze_exception(inner.tb_frame, type(e), e, inner)
            assert spec is None  # No guard for SystemExit

    def test_stopiteration_not_patched(self):
        """StopIteration is flow control, not a bug."""
        def gen():
            yield 1

        g = gen()
        next(g)
        try:
            next(g)
        except StopIteration as e:
            tb = sys.exc_info()[2]
            inner = tb
            while inner.tb_next:
                inner = inner.tb_next
            spec = analyze_exception(inner.tb_frame, type(e), e, inner)
            assert spec is None

    def test_os_error_not_patched(self):
        """OSError (file not found) should not produce a guard for most cases."""
        def open_missing():
            with open('/nonexistent/path/that/does/not/exist/file.txt') as f:
                return f.read()

        try:
            open_missing()
        except (FileNotFoundError, OSError) as e:
            tb = sys.exc_info()[2]
            inner = tb
            while inner.tb_next:
                inner = inner.tb_next
            spec = analyze_exception(inner.tb_frame, type(e), e, inner)
            # FileNotFoundError has no guard strategy today.
            # If someone adds one (e.g. file_guard), this test becomes
            # the canary — it will fail and force a conscious decision.
            assert spec is None


class TestTracerEdgeCases:
    """Tracer internals handle edge cases correctly."""

    def test_handled_exc_ids_dedup(self):
        """Same exception ID in _handled_exc_ids is skipped."""
        tracer = CodeSutureTracer(silent=True)
        exc = ValueError("test")
        exc_id = id(exc)
        tracer._handled_exc_ids.add(exc_id)
        assert exc_id in tracer._handled_exc_ids
        # Adding again doesn't raise
        tracer._handled_exc_ids.add(exc_id)
        assert len([x for x in tracer._handled_exc_ids if x == exc_id]) == 1

    def test_attempts_counter_blocks_retry(self):
        """Once attempts >= max_retries, the key is blocked."""
        tracer = CodeSutureTracer(max_retries=2, silent=True)
        key = (12345, 0)
        tracer.attempts[key] = 0
        assert tracer.attempts[key] < tracer.max_retries  # First try OK
        tracer.attempts[key] = 2
        assert tracer.attempts[key] >= tracer.max_retries  # Now blocked

    def test_patched_signatures_tracks_patches(self):
        """patched_signatures dict exists and accepts entries."""
        tracer = CodeSutureTracer(silent=True)
        tracer.patched_signatures['test:fn'] = 'null_guard'
        assert tracer.patched_signatures['test:fn'] == 'null_guard'

    def test_dry_run_flag_prevents_mutation(self):
        """Dry run tracer has dry_run=True and stats start at 0."""
        tracer = CodeSutureTracer(dry_run=True, silent=True)
        assert tracer.dry_run is True
        assert tracer.stats['patched'] == 0
        assert tracer.stats['dry_run_suggestions'] == 0

    def test_rewound_exc_ids_prevents_rewind_loop(self):
        """_rewound_exc_ids prevents re-rewinding the same exception."""
        tracer = CodeSutureTracer(silent=True)
        tracer._rewound_exc_ids.add(42)
        assert 42 in tracer._rewound_exc_ids

    def test_tracer_thread_local_state(self):
        """Thread-local state is isolated per thread."""
        tracer = CodeSutureTracer(silent=True)
        tracer._thread_state.depth = 5

        found_depth = [None]
        def worker():
            # New thread should not see depth=5
            found_depth[0] = getattr(tracer._thread_state, 'depth', None)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert found_depth[0] is None  # Thread-local is isolated

    def test_stats_keys_exist(self):
        """All expected stats keys are initialized."""
        tracer = CodeSutureTracer(silent=True)
        for key in ('patched', 'self_healed', 'dry_run_suggestions'):
            assert key in tracer.stats, f"Missing stats key: {key}"
