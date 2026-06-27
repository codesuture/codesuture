"""Tests for codesuture.lifecycle — patch lifecycle state machine."""

import os
import json
import pytest
from datetime import datetime, timezone, timedelta

from codesuture.lifecycle import PatchState, PatchLifecycle, LifecycleManager

class TestPatchState:
    def test_all_states_exist(self):
        expected = ['detected', 'patched', 'replayed', 'persisted',
                    'suggested', 'verified', 'fixed', 'expired', 'rolled_back']
        for s in expected:
            assert PatchState(s).value == s

    def test_state_count(self):
        assert len(PatchState) == 9

class TestPatchLifecycle:
    def test_defaults(self):
        p = PatchLifecycle()
        assert p.current_state == PatchState.DETECTED
        assert p.transitions == []
        assert p.ttl_days == 7

    def test_transition_records_history(self):
        p = PatchLifecycle(function_name='get_bio', guard_type='null_guard')
        p.transition_to(PatchState.PATCHED, "AttributeError caught")
        p.transition_to(PatchState.PERSISTED, "Saved to disk")

        assert p.current_state == PatchState.PERSISTED
        assert len(p.transitions) == 2
        assert p.transitions[0]['from'] == 'detected'
        assert p.transitions[0]['to'] == 'patched'
        assert p.transitions[1]['from'] == 'patched'
        assert p.transitions[1]['to'] == 'persisted'

    def test_to_dict_roundtrip(self):
        p = PatchLifecycle(
            patch_id='mod:fn:guard',
            function_name='get_bio',
            module='myapp',
            guard_type='null_guard',
        )
        p.transition_to(PatchState.PATCHED)
        d = p.to_dict()
        assert d['current_state'] == 'patched'

        restored = PatchLifecycle.from_dict(d)
        assert restored.current_state == PatchState.PATCHED
        assert restored.function_name == 'get_bio'

    def test_is_expired_false_when_fresh(self):
        p = PatchLifecycle(ttl_days=7)
        assert p.is_expired() is False

    def test_is_expired_true_when_old(self):
        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        p = PatchLifecycle(created_at=old_time, ttl_days=7)
        assert p.is_expired() is True

    def test_is_expired_exact_boundary(self):
        """Patch exactly ttl_days old should be expired (>= not >)."""
        exact_time = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        p = PatchLifecycle(created_at=exact_time, ttl_days=7)
        assert p.is_expired() is True

    def test_age_days(self):
        old_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        p = PatchLifecycle(created_at=old_time)
        assert p.age_days() == 5

class TestLifecycleManager:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.store_dir = str(tmp_path / "incidents")
        self.mgr = LifecycleManager(store_dir=self.store_dir)

    def test_track_creates_patch(self):
        p = self.mgr.track('myapp', 'get_bio', 'null_guard')
        assert p.current_state == PatchState.PATCHED
        assert p.function_name == 'get_bio'

    def test_track_advances_state(self):
        self.mgr.track('myapp', 'get_bio', 'null_guard', PatchState.PATCHED)
        self.mgr.track('myapp', 'get_bio', 'null_guard', PatchState.PERSISTED)
        p = self.mgr.get(self.mgr._make_id('myapp', 'get_bio', 'null_guard'))
        assert p.current_state == PatchState.PERSISTED
        assert len(p.transitions) == 2

    def test_track_same_state_no_duplicate(self):
        self.mgr.track('myapp', 'fn', 'null_guard', PatchState.PATCHED)
        self.mgr.track('myapp', 'fn', 'null_guard', PatchState.PATCHED)
        p = self.mgr.get(self.mgr._make_id('myapp', 'fn', 'null_guard'))
        assert len(p.transitions) == 1

    def test_persistence_across_reloads(self):
        self.mgr.track('myapp', 'get_bio', 'null_guard')

        mgr2 = LifecycleManager(store_dir=self.store_dir)
        p = mgr2.get(mgr2._make_id('myapp', 'get_bio', 'null_guard'))
        assert p is not None
        assert p.function_name == 'get_bio'

    def test_get_by_state(self):
        self.mgr.track('myapp', 'fn1', 'null_guard', PatchState.PATCHED)
        self.mgr.track('myapp', 'fn2', 'key_guard', PatchState.FIXED)
        self.mgr.track('myapp', 'fn3', 'null_guard', PatchState.PATCHED)

        patched = self.mgr.get_by_state(PatchState.PATCHED)
        assert len(patched) == 2
        fixed = self.mgr.get_by_state(PatchState.FIXED)
        assert len(fixed) == 1

    def test_get_by_function(self):
        self.mgr.track('myapp', 'get_bio', 'null_guard')
        self.mgr.track('myapp', 'get_settings', 'key_guard')
        results = self.mgr.get_by_function('bio')
        assert len(results) == 1

    def test_get_stale(self):
        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.mgr.track('myapp', 'stale_fn', 'null_guard', PatchState.PERSISTED)
        pid = self.mgr._make_id('myapp', 'stale_fn', 'null_guard')
        self.mgr._patches[pid].created_at = old_time
        self.mgr._save()
        stale = self.mgr.get_stale(days=5)
        assert len(stale) == 1

    def test_get_expired(self):
        old_time = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        self.mgr.track('myapp', 'expired_fn', 'null_guard')
        pid = self.mgr._make_id('myapp', 'expired_fn', 'null_guard')
        self.mgr._patches[pid].created_at = old_time
        self.mgr._patches[pid].ttl_days = 7
        expired = self.mgr.get_expired()
        assert len(expired) == 1

    def test_mark_fixed(self):
        self.mgr.track('myapp', 'broken_fn', 'null_guard')
        found = self.mgr.mark_fixed('broken_fn')
        assert found is True
        pid = self.mgr._make_id('myapp', 'broken_fn', 'null_guard')
        assert self.mgr.get(pid).current_state == PatchState.FIXED

    def test_mark_rolled_back(self):
        self.mgr.track('myapp', 'risky_fn', 'callable_guard')
        found = self.mgr.mark_rolled_back('risky_fn')
        assert found is True
        pid = self.mgr._make_id('myapp', 'risky_fn', 'callable_guard')
        assert self.mgr.get(pid).current_state == PatchState.ROLLED_BACK

    def test_summary(self):
        self.mgr.track('myapp', 'fn1', 'null_guard', PatchState.PATCHED)
        self.mgr.track('myapp', 'fn2', 'key_guard', PatchState.PATCHED)
        self.mgr.track('myapp', 'fn3', 'null_guard', PatchState.FIXED)

        s = self.mgr.summary()
        assert s['total'] == 3
        assert s['patched'] == 2
        assert s['fixed'] == 1

    def test_get_all(self):
        self.mgr.track('a', 'fn1', 'null_guard')
        self.mgr.track('b', 'fn2', 'key_guard')
        assert len(self.mgr.get_all()) == 2
