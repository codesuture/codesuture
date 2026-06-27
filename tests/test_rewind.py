"""Tests for the Rewind crash forensics module."""
import time
import threading
import json
import os
import tempfile
import pytest
from codesuture.rewind.buffer import RewindBuffer, FrameSnapshot
from codesuture.rewind.formatter import format_rewind_timeline
from codesuture.rewind.persistence import save_rewind_dump, load_latest_rewind, list_rewind_dumps

class TestRewindBuffer:
    def test_basic_record_and_dump(self):
        buf = RewindBuffer(max_frames=10, max_age_seconds=60.0)
        snap = FrameSnapshot(
            timestamp=time.monotonic(),
            event='call',
            function='my_func',
            module='my_module',
            lineno=42,
            args={'x': '1'},
            locals_snapshot={},
        )
        buf.record(snap)
        result = buf.dump()
        assert len(result) == 1
        assert result[0].function == 'my_func'
        assert result[0].event == 'call'

    def test_maxlen_eviction(self):
        buf = RewindBuffer(max_frames=5, max_age_seconds=60.0)
        for i in range(10):
            buf.record(FrameSnapshot(
                timestamp=time.monotonic(),
                event='call',
                function=f'func_{i}',
                module='mod',
                lineno=i,
                args={},
                locals_snapshot={},
            ))
        assert len(buf) == 5
        result = buf.dump()

        assert result[0].function == 'func_5'
        assert result[-1].function == 'func_9'

    def test_age_eviction(self):
        buf = RewindBuffer(max_frames=100, max_age_seconds=0.1)
        buf.record(FrameSnapshot(
            timestamp=time.monotonic() - 1.0,
            event='call',
            function='old_func',
            module='mod',
            lineno=1,
            args={},
            locals_snapshot={},
        ))
        buf.record(FrameSnapshot(
            timestamp=time.monotonic(),
            event='call',
            function='new_func',
            module='mod',
            lineno=2,
            args={},
            locals_snapshot={},
        ))
        result = buf.dump()
        assert len(result) == 1
        assert result[0].function == 'new_func'

    def test_dump_for_function(self):
        buf = RewindBuffer(max_frames=100)
        for name in ['alpha', 'beta', 'alpha', 'gamma', 'alpha']:
            buf.record(FrameSnapshot(
                timestamp=time.monotonic(),
                event='call',
                function=name,
                module='mod',
                lineno=1,
                args={},
                locals_snapshot={},
            ))
        result = buf.dump_for_function('alpha')
        assert len(result) == 3
        assert all(s.function == 'alpha' for s in result)

    def test_disabled_buffer(self):
        buf = RewindBuffer(max_frames=10)
        buf.enabled = False
        buf.record(FrameSnapshot(
            timestamp=time.monotonic(),
            event='call',
            function='skipped',
            module='mod',
            lineno=1,
            args={},
            locals_snapshot={},
        ))
        assert len(buf) == 0

    def test_thread_safety(self):
        buf = RewindBuffer(max_frames=1000)
        errors = []

        def writer(thread_id):
            try:
                for i in range(100):
                    buf.record(FrameSnapshot(
                        timestamp=time.monotonic(),
                        event='call',
                        function=f'thread_{thread_id}_func_{i}',
                        module='mod',
                        lineno=i,
                        args={},
                        locals_snapshot={},
                    ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety errors: {errors}"
        result = buf.dump(last_n=1000)
        assert len(result) == 500

    def test_clear(self):
        buf = RewindBuffer(max_frames=10)
        for i in range(5):
            buf.record(FrameSnapshot(
                timestamp=time.monotonic(),
                event='call',
                function='f',
                module='m',
                lineno=i,
                args={},
                locals_snapshot={},
            ))
        assert len(buf) == 5
        buf.clear()
        assert len(buf) == 0

    def test_to_dict(self):
        snap = FrameSnapshot(
            timestamp=123.456,
            event='exception',
            function='crash_here',
            module='myapp',
            lineno=99,
            args={'user_id': '42'},
            locals_snapshot={'x': 'None'},
            exception='AttributeError: NoneType has no attribute bio',
        )
        d = snap.to_dict()
        assert d['event'] == 'exception'
        assert d['function'] == 'crash_here'
        assert d['exception'] == 'AttributeError: NoneType has no attribute bio'
        assert d['args'] == {'user_id': '42'}

class TestRewindFormatter:
    def test_empty_snapshots(self):
        result = format_rewind_timeline([])
        assert 'No recorded events' in result

    def test_basic_timeline(self):
        now = time.monotonic()
        snapshots = [
            FrameSnapshot(
                timestamp=now - 0.5,
                event='call',
                function='get_user',
                module='app.db',
                lineno=10,
                args={'user_id': '42'},
                locals_snapshot={},
            ),
            FrameSnapshot(
                timestamp=now - 0.3,
                event='return',
                function='get_user',
                module='app.db',
                lineno=15,
                args={},
                locals_snapshot={},
                return_value='<User id=42>',
            ),
            FrameSnapshot(
                timestamp=now,
                event='exception',
                function='get_bio',
                module='app.routes',
                lineno=42,
                args={},
                locals_snapshot={'profile': 'None'},
                exception="AttributeError: 'NoneType' has no attribute 'bio'",
            ),
        ]
        result = format_rewind_timeline(snapshots, crash_time=now)
        assert 'CALL' in result
        assert 'RETURN' in result
        assert 'EXCEPTION' in result
        assert 'get_user' in result
        assert 'get_bio' in result

    def test_timeline_relative_times(self):
        now = time.monotonic()
        snapshots = [
            FrameSnapshot(
                timestamp=now - 1.0,
                event='call',
                function='early',
                module='m',
                lineno=1,
                args={},
                locals_snapshot={},
            ),
            FrameSnapshot(
                timestamp=now,
                event='exception',
                function='crash',
                module='m',
                lineno=2,
                args={},
                locals_snapshot={},
                exception='Error',
            ),
        ]
        result = format_rewind_timeline(snapshots, crash_time=now)
        assert '-1.000s' in result

class TestRewindPersistence:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        import codesuture.rewind.persistence as rp
        monkeypatch.setattr(rp, 'REWIND_DIR', str(tmp_path / '.codesuture_rewind'))

        snapshots = [
            FrameSnapshot(
                timestamp=123.456,
                event='call',
                function='my_func',
                module='my_mod',
                lineno=10,
                args={'x': '1'},
                locals_snapshot={},
            ),
            FrameSnapshot(
                timestamp=123.789,
                event='exception',
                function='my_func',
                module='my_mod',
                lineno=15,
                args={},
                locals_snapshot={'y': 'None'},
                exception='TypeError: bad',
            ),
        ]
        filepath = save_rewind_dump('my_func', snapshots, crash_info={'exception_type': 'TypeError'})
        assert os.path.exists(filepath)

        data = load_latest_rewind('my_func')
        assert data is not None
        assert data['function'] == 'my_func'
        assert len(data['timeline']) == 2
        assert data['crash_info']['exception_type'] == 'TypeError'

    def test_list_dumps(self, tmp_path, monkeypatch):
        import codesuture.rewind.persistence as rp
        monkeypatch.setattr(rp, 'REWIND_DIR', str(tmp_path / '.codesuture_rewind'))

        snap = FrameSnapshot(
            timestamp=100.0, event='call', function='f1', module='m',
            lineno=1, args={}, locals_snapshot={},
        )
        save_rewind_dump('f1', [snap])
        save_rewind_dump('f2', [snap])

        dumps = list_rewind_dumps()
        assert len(dumps) == 2

    def test_load_no_dir(self, tmp_path, monkeypatch):
        import codesuture.rewind.persistence as rp
        monkeypatch.setattr(rp, 'REWIND_DIR', str(tmp_path / 'nonexistent'))
        result = load_latest_rewind()
        assert result is None

    def test_list_no_dir(self, tmp_path, monkeypatch):
        import codesuture.rewind.persistence as rp
        monkeypatch.setattr(rp, 'REWIND_DIR', str(tmp_path / 'nonexistent'))
        result = list_rewind_dumps()
        assert result == []

class TestRewindTracer:
    def test_safe_repr_normal(self):
        from codesuture.rewind.tracer import _safe_repr
        assert _safe_repr(42) == '42'
        assert _safe_repr('hello') == "'hello'"
        assert _safe_repr(None) == 'None'

    def test_safe_repr_truncation(self):
        from codesuture.rewind.tracer import _safe_repr
        long_string = 'x' * 500
        result = _safe_repr(long_string, max_len=50)
        assert len(result) <= 50
        assert result.endswith('...')

    def test_safe_repr_unprintable(self):
        from codesuture.rewind.tracer import _safe_repr

        class BadRepr:
            def __repr__(self):
                raise RuntimeError("cannot repr")

        result = _safe_repr(BadRepr())
        assert 'BadRepr' in result or 'unprintable' in result

    def test_enable_disable(self):
        from codesuture.rewind.tracer import enable_rewind, disable_rewind, get_buffer
        buf = enable_rewind(max_frames=10)
        assert get_buffer() is buf
        assert buf.enabled
        disable_rewind()
        assert get_buffer() is None

    def test_should_skip_frame(self):
        from codesuture.rewind.tracer import _should_skip_frame
        import types

        class FakeFrame:
            def __init__(self, module_name):
                self.f_globals = {'__name__': module_name}

        assert _should_skip_frame(FakeFrame('codesuture.tracer')) is True
        assert _should_skip_frame(FakeFrame('importlib')) is True
        assert _should_skip_frame(FakeFrame('threading')) is True
        assert _should_skip_frame(FakeFrame('myapp.routes')) is False
        assert _should_skip_frame(FakeFrame('')) is True
