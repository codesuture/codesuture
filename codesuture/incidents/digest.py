import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from collections import Counter
from codesuture.incidents.incident import IncidentRecord, Severity
from codesuture.incidents.incident_log import IncidentLogger


class DigestGenerator:
    def __init__(self, logger: IncidentLogger):
        self.logger = logger

    def generate_daily(self, date: datetime = None) -> str:
        """Generate daily incident digest as markdown."""
        if date is None:
            date = datetime.now(timezone.utc)

        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        incidents = self.logger.get_incidents(since=start)
        # Filter to only this day
        incidents = [i for i in incidents if i.timestamp < end.isoformat()]

        return self._build_digest(incidents, f'Daily Incident Report — {start.strftime("%Y-%m-%d")}', 'daily')

    def generate_weekly(self, end_date: datetime = None) -> str:
        """Generate weekly incident digest."""
        if end_date is None:
            end_date = datetime.now(timezone.utc)
        start = end_date - timedelta(days=7)
        incidents = self.logger.get_incidents(since=start)
        week_num = end_date.strftime('%Y-W%W')
        return self._build_digest(incidents, f'Weekly Incident Report — {week_num}', 'weekly')

    def _build_digest(self, incidents: List[IncidentRecord], title: str, period: str) -> str:
        """Build the markdown digest content."""
        lines = [f'# CodeSuture {title}', '']

        if not incidents:
            lines.append('No incidents recorded during this period.')
            lines.append('')
            return '\n'.join(lines)

        # Summary
        severity_counts = Counter(i.severity.value for i in incidents)
        status_counts = Counter(i.status.value for i in incidents)
        unique_functions = len(set(i.function for i in incidents if i.function))
        unique_patterns = len(set(i.fingerprint for i in incidents if i.fingerprint))
        replayed = sum(1 for i in incidents if i.status.value == 'replayed')

        lines.append('## Summary')
        lines.append(f'- **Total incidents:** {len(incidents)}')
        lines.append(f'- **CRITICAL:** {severity_counts.get("CRITICAL", 0)} | '
                     f'**HIGH:** {severity_counts.get("HIGH", 0)} | '
                     f'**MEDIUM:** {severity_counts.get("MEDIUM", 0)} | '
                     f'**LOW:** {severity_counts.get("LOW", 0)}')
        lines.append(f'- **Unique crash patterns:** {unique_patterns}')
        lines.append(f'- **Functions patched:** {unique_functions}')
        if replayed:
            lines.append(f'- **HTTP transactions replayed:** {replayed}')
        lines.append('')

        # CRITICAL and HIGH priority details
        critical_high = [i for i in incidents if i.severity in (Severity.CRITICAL, Severity.HIGH)]
        if critical_high:
            lines.append('## CRITICAL & HIGH Priority')
            lines.append('')
            # Group by function
            func_groups = {}
            for inc in critical_high:
                key = inc.function or 'unknown'
                func_groups.setdefault(key, []).append(inc)

            for func_name, func_incidents in func_groups.items():
                rep = func_incidents[0]
                count = len(func_incidents)
                sev = rep.severity.value
                marker = '🔴' if sev == 'CRITICAL' else '🟡'
                lines.append(f'### {marker} {sev}: {rep.exception_type} in {func_name}() (×{count})')
                lines.append(f'- **Guard:** {rep.guard_type} on `{rep.target_variable}` → default `{repr(rep.default_value)}`')
                if rep.default_rationale:
                    lines.append(f'- **Why:** {rep.default_rationale}')
                if rep.suggested_fix:
                    lines.append(f'- **Suggested fix:**')
                    lines.append(f'  ```python')
                    lines.append(f'  {rep.suggested_fix}')
                    lines.append(f'  ```')
                lines.append(f'- **Review:** `codesuture explain {func_name}`')
                lines.append('')

        # All incidents table
        lines.append('## All Incidents')
        lines.append('')
        lines.append('| Time | Severity | Function | Guard | Target | Status |')
        lines.append('|------|----------|----------|-------|--------|--------|')
        for inc in incidents:
            time_str = inc.timestamp[11:19] if len(inc.timestamp) > 19 else inc.timestamp
            lines.append(f'| {time_str} | {inc.severity.value} | {inc.function}() | '
                         f'{inc.guard_type} | {inc.target_variable} | {inc.status.value} |')
        lines.append('')

        # Recommended actions
        func_counts = Counter(i.function for i in incidents if i.function)
        repeat_offenders = [(f, c) for f, c in func_counts.most_common() if c >= 2]
        if repeat_offenders:
            lines.append('## Recommended Actions')
            lines.append('')
            for idx, (func, count) in enumerate(repeat_offenders, 1):
                lines.append(f'{idx}. 🔴 Review `{func}` — patched {count}× during this period, fix at source')
            lines.append('')

        return '\n'.join(lines)

    def save_digest(self, content: str, filename: str) -> str:
        """Save digest to file, returns path."""
        path = os.path.join(self.logger.log_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def generate_and_save_daily(self, date: datetime = None) -> str:
        """Generate and save daily digest. Returns file path."""
        if date is None:
            date = datetime.now(timezone.utc)
        content = self.generate_daily(date)
        filename = f'digest_daily_{date.strftime("%Y-%m-%d")}.md'
        return self.save_digest(content, filename)

    def generate_and_save_weekly(self, end_date: datetime = None) -> str:
        """Generate and save weekly digest. Returns file path."""
        if end_date is None:
            end_date = datetime.now(timezone.utc)
        content = self.generate_weekly(end_date)
        week_num = end_date.strftime('%Y-W%W')
        filename = f'digest_weekly_{week_num}.md'
        return self.save_digest(content, filename)
