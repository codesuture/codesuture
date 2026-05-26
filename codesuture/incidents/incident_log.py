import json
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from codesuture.incidents.incident import IncidentRecord, Severity, IncidentStatus


class IncidentLogger:
    def __init__(self, log_dir='.codesuture_incidents'):
        self.log_dir = log_dir
        self._lock = threading.Lock()
        os.makedirs(self.log_dir, exist_ok=True)

    def _daily_log_path(self, dt: datetime = None) -> str:
        if dt is None:
            dt = datetime.now(timezone.utc)
        date_str = dt.strftime('%Y-%m-%d')
        return os.path.join(self.log_dir, f'incidents_{date_str}.jsonl')

    def log_incident(self, incident: IncidentRecord) -> str:
        """Append incident to daily JSONL file. Returns incident_id."""
        with self._lock:
            path = self._daily_log_path()
            with open(path, 'a', encoding='utf-8') as f:
                json.dump(incident.to_dict(), f, default=str)
                f.write('\n')
        return incident.incident_id

    def get_incidents(self, since: datetime = None,
                      severity: Severity = None,
                      status: IncidentStatus = None,
                      function: str = None) -> List[IncidentRecord]:
        """Query incidents with optional filters."""
        results = []
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(days=1)

        # Iterate over log files from 'since' date to today
        current = since.date()
        today = datetime.now(timezone.utc).date()
        while current <= today:
            dt = datetime(current.year, current.month, current.day, tzinfo=timezone.utc)
            path = self._daily_log_path(dt)
            if os.path.isfile(path):
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            record = IncidentRecord.from_dict(data)
                            # Apply filters
                            if severity and record.severity != severity:
                                continue
                            if status and record.status != status:
                                continue
                            if function and function.lower() not in record.function.lower():
                                continue
                            results.append(record)
                        except (json.JSONDecodeError, Exception):
                            continue
            current += timedelta(days=1)
        return results

    def get_today_incidents(self) -> List[IncidentRecord]:
        """Shortcut for today's incidents."""
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.get_incidents(since=today)

    def get_incident_count(self, since: datetime = None) -> dict:
        """Get incident counts by severity."""
        incidents = self.get_incidents(since=since)
        counts = {s.value: 0 for s in Severity}
        for inc in incidents:
            counts[inc.severity.value] += 1
        counts['total'] = len(incidents)
        return counts
