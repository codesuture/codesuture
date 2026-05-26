"""Metrics export in Prometheus / OpenMetrics format.

No external dependencies. Reads from incident logs and lifecycle state
to compute metrics on demand.
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from collections import Counter


class MetricsCollector:
    """Collects and exports CodeSuture metrics."""

    def __init__(self, incident_dir: str = '.codesuture_incidents',
                 patch_store: str = None):
        self.incident_dir = incident_dir
        self.patch_store = patch_store

    def collect(self) -> Dict[str, any]:
        """Collect all metrics as a dict."""
        metrics = {}

        # Incident metrics
        incidents = self._load_incidents()
        severity_counts = Counter(i.get('severity', 'UNKNOWN') for i in incidents)
        guard_counts = Counter(i.get('guard_type', 'unknown') for i in incidents)
        status_counts = Counter(i.get('status', 'unknown') for i in incidents)

        metrics['codesuture_incidents_total'] = len(incidents)
        for sev, cnt in severity_counts.items():
            metrics[f'codesuture_incidents_total{{severity="{sev}"}}'] = cnt
        for guard, cnt in guard_counts.items():
            metrics[f'codesuture_patches_total{{guard_type="{guard}"}}'] = cnt

        # Status metrics
        metrics['codesuture_replay_success_total'] = status_counts.get('replayed', 0)
        metrics['codesuture_patches_applied'] = status_counts.get('patched', 0)

        # Lifecycle metrics
        lifecycle_data = self._load_lifecycle()
        state_counts = Counter(p.get('current_state', 'unknown') for p in lifecycle_data)
        metrics['codesuture_patches_active'] = sum(
            1 for p in lifecycle_data
            if p.get('current_state') in ('patched', 'persisted', 'suggested')
        )
        for state, cnt in state_counts.items():
            metrics[f'codesuture_lifecycle_total{{state="{state}"}}'] = cnt

        # TTL metrics
        expiring_soon = 0
        for p in lifecycle_data:
            ttl = p.get('ttl_days', 7)
            created = p.get('created_at', '')
            if created:
                try:
                    dt = datetime.fromisoformat(created)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - dt).days
                    if age >= ttl - 1 and p.get('current_state') not in ('fixed', 'rolled_back', 'expired'):
                        expiring_soon += 1
                except Exception:
                    pass
        metrics['codesuture_ttl_expiring_soon'] = expiring_soon

        # Suggestion metrics
        suggestions_total = sum(1 for i in incidents if i.get('suggested_fix'))
        metrics['codesuture_suggestions_generated'] = suggestions_total
        metrics['codesuture_suggestions_verified'] = sum(
            1 for i in incidents if i.get('fix_confidence') == 'VERIFIED'
        )

        return metrics

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        metrics = self.collect()
        lines = [
            '# HELP codesuture_incidents_total Total incidents recorded',
            '# TYPE codesuture_incidents_total counter',
        ]
        for key, value in sorted(metrics.items()):
            lines.append(f'{key} {value}')
        return '\n'.join(lines) + '\n'

    def export_json(self) -> str:
        """Export metrics as JSON."""
        return json.dumps(self.collect(), indent=2, default=str)

    def _load_incidents(self) -> List[dict]:
        """Load all incident records from JSONL files."""
        incidents = []
        if not os.path.isdir(self.incident_dir):
            return incidents
        for fname in sorted(os.listdir(self.incident_dir)):
            if fname.startswith('incidents_') and fname.endswith('.jsonl'):
                fpath = os.path.join(self.incident_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    incidents.append(json.loads(line))
                                except json.JSONDecodeError:
                                    continue
                except Exception:
                    continue
        return incidents

    def _load_lifecycle(self) -> List[dict]:
        """Load lifecycle state."""
        lf_path = os.path.join(self.incident_dir, 'lifecycle.json')
        if not os.path.isfile(lf_path):
            return []
        try:
            with open(lf_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return list(data.values()) if isinstance(data, dict) else []
        except Exception:
            return []
