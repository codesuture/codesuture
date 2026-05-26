import os
import threading
from datetime import datetime, timezone
from codesuture.incidents.incident import IncidentRecord, Severity


class FileAlertChannel:
    def __init__(self, directory='.codesuture_incidents/alerts'):
        self.directory = directory
        self._lock = threading.Lock()
        os.makedirs(self.directory, exist_ok=True)

    def send(self, incident: IncidentRecord) -> str:
        """Write alert file and update unread.md. Returns filename."""
        timestamp = datetime.now(timezone.utc)
        ts_str = timestamp.strftime('%Y-%m-%d_%H%M%S')
        # Include incident_id to prevent filename collisions when multiple
        # incidents arrive within the same second (concurrent requests, tests).
        filename = f'ALERT_{ts_str}_{incident.severity.value}_{incident.incident_id}.md'
        filepath = os.path.join(self.directory, filename)

        content = self._format_alert(incident, timestamp)
        with self._lock:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            self._update_unread(incident, filename)
        return filename

    def _format_alert(self, inc: IncidentRecord, ts: datetime) -> str:
        severity_emoji = {'CRITICAL': '🔴', 'HIGH': '🟡', 'MEDIUM': '🔵', 'LOW': '⚪'}
        emoji = severity_emoji.get(inc.severity.value, '⚪')

        lines = [
            f'# {emoji} {inc.severity.value} ALERT — CodeSuture Incident',
            '',
            f'**Time:** {ts.strftime("%Y-%m-%d %H:%M:%S UTC")}',
            f'**Incident ID:** {inc.incident_id}',
            f'**Function:** {inc.function}() in {inc.file_path}:{inc.line_number}',
            f'**Exception:** {inc.exception_type}: {inc.exception_message}',
            '',
            '## What Happened',
            '',
            f'The function `{inc.function}()` crashed with `{inc.exception_type}`.',
        ]

        if inc.guard_type:
            lines.append(f'CodeSuture applied a `{inc.guard_type}` guard with default value `{repr(inc.default_value)}`.')
        if inc.default_rationale:
            lines.append(f'**Rationale:** {inc.default_rationale}')

        lines.extend([
            '',
            '## What CodeSuture Did',
            '',
            f'- **Guard:** {inc.guard_type}',
            f'- **Target:** {inc.target_variable}',
            f'- **Default:** `{repr(inc.default_value)}`',
            f'- **Status:** {inc.status.value}',
        ])

        if inc.severity == Severity.CRITICAL:
            lines.extend([
                '',
                '## ⚠️ Engineer Action Required',
                '',
                'This is a **CRITICAL** patch that may affect application logic.',
                'Review immediately and apply a permanent fix in source.',
            ])

        if inc.suggested_fix:
            lines.extend([
                '',
                '## Suggested Fix',
                '',
                '```python',
                inc.suggested_fix,
                '```',
            ])

        lines.extend([
            '',
            '## Commands',
            '',
            f'- Review: `codesuture explain {inc.function}`',
            f'- Rollback: `codesuture rollback {inc.function}`',
            f'- Dismiss: `codesuture alerts dismiss {inc.incident_id}`',
        ])

        if inc.stack_trace:
            lines.extend([
                '',
                '## Stack Trace',
                '',
                '```',
                *inc.stack_trace,
                '```',
            ])

        return '\n'.join(lines) + '\n'

    def _update_unread(self, inc: IncidentRecord, filename: str):
        """Append to unread.md aggregator."""
        unread_path = os.path.join(self.directory, 'unread.md')
        ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
        severity_emoji = {'CRITICAL': '🔴', 'HIGH': '🟡', 'MEDIUM': '🔵', 'LOW': '⚪'}
        emoji = severity_emoji.get(inc.severity.value, '⚪')
        entry = f'- {emoji} [{ts}] **{inc.severity.value}** — {inc.exception_type} in {inc.function}() → {inc.guard_type} | [Details]({filename})\n'
        with open(unread_path, 'a', encoding='utf-8') as f:
            f.write(entry)

    def get_unread(self) -> str:
        """Read unread.md contents."""
        unread_path = os.path.join(self.directory, 'unread.md')
        if not os.path.isfile(unread_path):
            return 'No unread alerts.'
        with open(unread_path, 'r', encoding='utf-8') as f:
            return f.read()

    def dismiss(self, alert_id: str) -> bool:
        """Remove an alert file by incident_id."""
        for fname in os.listdir(self.directory):
            if fname.endswith('.md') and fname != 'unread.md':
                fpath = os.path.join(self.directory, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    if alert_id in content:
                        os.remove(fpath)
                        return True
                except Exception:
                    continue
        return False

    def dismiss_all(self):
        """Clear unread.md and remove all alert files."""
        for fname in os.listdir(self.directory):
            fpath = os.path.join(self.directory, fname)
            if os.path.isfile(fpath):
                os.remove(fpath)
