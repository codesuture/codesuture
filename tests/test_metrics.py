"""Tests for codesuture.metrics — Prometheus/JSON metrics export."""

import os
import json
import pytest
from datetime import datetime, timezone, timedelta

from codesuture.metrics import MetricsCollector


class TestMetricsCollector:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.incident_dir = str(tmp_path / "incidents")
        os.makedirs(self.incident_dir, exist_ok=True)
        self.collector = MetricsCollector(incident_dir=self.incident_dir)

    def _write_incidents(self, incidents):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(self.incident_dir, f"incidents_{today}.jsonl")
        with open(path, 'w', encoding='utf-8') as f:
            for inc in incidents:
                json.dump(inc, f, default=str)
                f.write('\n')

    def _write_lifecycle(self, patches):
        path = os.path.join(self.incident_dir, 'lifecycle.json')
        data = {}
        for i, p in enumerate(patches):
            data[f"patch_{i}"] = p
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    def test_empty_metrics(self):
        metrics = self.collector.collect()
        assert metrics['codesuture_incidents_total'] == 0

    def test_incident_counts(self):
        self._write_incidents([
            {'severity': 'CRITICAL', 'guard_type': 'null_guard', 'status': 'patched'},
            {'severity': 'HIGH', 'guard_type': 'null_guard', 'status': 'patched'},
            {'severity': 'HIGH', 'guard_type': 'key_guard', 'status': 'replayed'},
            {'severity': 'MEDIUM', 'guard_type': 'division_guard', 'status': 'patched'},
        ])
        metrics = self.collector.collect()
        assert metrics['codesuture_incidents_total'] == 4
        assert metrics['codesuture_incidents_total{severity="CRITICAL"}'] == 1
        assert metrics['codesuture_incidents_total{severity="HIGH"}'] == 2
        assert metrics['codesuture_replay_success_total'] == 1

    def test_guard_type_counts(self):
        self._write_incidents([
            {'guard_type': 'null_guard', 'severity': 'MEDIUM', 'status': 'patched'},
            {'guard_type': 'null_guard', 'severity': 'HIGH', 'status': 'patched'},
            {'guard_type': 'key_guard', 'severity': 'MEDIUM', 'status': 'patched'},
        ])
        metrics = self.collector.collect()
        assert metrics['codesuture_patches_total{guard_type="null_guard"}'] == 2
        assert metrics['codesuture_patches_total{guard_type="key_guard"}'] == 1

    def test_lifecycle_metrics(self):
        self._write_lifecycle([
            {'current_state': 'patched', 'created_at': datetime.now(timezone.utc).isoformat(), 'ttl_days': 7},
            {'current_state': 'persisted', 'created_at': datetime.now(timezone.utc).isoformat(), 'ttl_days': 7},
            {'current_state': 'fixed', 'created_at': datetime.now(timezone.utc).isoformat(), 'ttl_days': 7},
        ])
        metrics = self.collector.collect()
        assert metrics['codesuture_patches_active'] == 2  # patched + persisted

    def test_ttl_expiring_soon(self):
        old_time = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
        self._write_lifecycle([
            {'current_state': 'patched', 'created_at': old_time, 'ttl_days': 7},  # Expiring soon
            {'current_state': 'fixed', 'created_at': old_time, 'ttl_days': 7},     # Fixed, skip
        ])
        metrics = self.collector.collect()
        assert metrics['codesuture_ttl_expiring_soon'] == 1

    def test_suggestion_metrics(self):
        self._write_incidents([
            {'severity': 'HIGH', 'guard_type': 'null_guard', 'status': 'patched',
             'suggested_fix': '--- a/test.py\n+++ b/test.py\n', 'fix_confidence': 'LIKELY'},
            {'severity': 'MEDIUM', 'guard_type': 'key_guard', 'status': 'patched',
             'suggested_fix': '--- a/test.py\n', 'fix_confidence': 'VERIFIED'},
            {'severity': 'LOW', 'guard_type': 'null_guard', 'status': 'patched'},
        ])
        metrics = self.collector.collect()
        assert metrics['codesuture_suggestions_generated'] == 2
        assert metrics['codesuture_suggestions_verified'] == 1

    def test_prometheus_export_format(self):
        self._write_incidents([
            {'severity': 'HIGH', 'guard_type': 'null_guard', 'status': 'patched'},
        ])
        output = self.collector.export_prometheus()
        assert '# HELP' in output
        assert '# TYPE' in output
        assert 'codesuture_incidents_total 1' in output

    def test_json_export_format(self):
        self._write_incidents([
            {'severity': 'HIGH', 'guard_type': 'null_guard', 'status': 'patched'},
        ])
        output = self.collector.export_json()
        data = json.loads(output)
        assert data['codesuture_incidents_total'] == 1

    def test_corrupted_incident_file_handled(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(self.incident_dir, f"incidents_{today}.jsonl")
        with open(path, 'w') as f:
            f.write('invalid json\n')
            f.write('{"severity": "HIGH", "guard_type": "null_guard", "status": "patched"}\n')
        metrics = self.collector.collect()
        assert metrics['codesuture_incidents_total'] == 1  # Skips corrupted line
