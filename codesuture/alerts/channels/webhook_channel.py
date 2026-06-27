import json
import urllib.request
import urllib.error
import logging
from typing import Dict, Optional
from codesuture.incidents.incident import IncidentRecord, Severity

_log = logging.getLogger(__name__)

class WebhookChannel:
    def __init__(self, url: str, headers: Dict[str, str] = None, format: str = 'raw'):
        self.url = url
        self.headers = headers or {}
        self.format = format

    def send(self, incident: IncidentRecord) -> bool:
        """Send incident to webhook. Returns True on success."""
        if not self.url:
            _log.warning('Webhook URL not configured')
            return False

        payload = self._format_payload(incident)
        try:
            data = json.dumps(payload).encode('utf-8')
            headers = {'Content-Type': 'application/json'}
            headers.update(self.headers)
            req = urllib.request.Request(self.url, data=data, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 400
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            _log.error('Webhook delivery failed: %s', e)
            return False

    def _format_payload(self, inc: IncidentRecord) -> dict:
        if self.format == 'slack':
            return self._format_slack(inc)
        elif self.format == 'discord':
            return self._format_discord(inc)
        elif self.format == 'teams':
            return self._format_teams(inc)
        return self._format_raw(inc)

    def _format_raw(self, inc: IncidentRecord) -> dict:
        return inc.to_dict()

    def _format_slack(self, inc: IncidentRecord) -> dict:
        emoji = {'CRITICAL': ':red_circle:', 'HIGH': ':large_yellow_circle:', 'MEDIUM': ':large_blue_circle:', 'LOW': ':white_circle:'}
        e = emoji.get(inc.severity.value, ':white_circle:')
        text = f'{e} *{inc.severity.value}* — `{inc.exception_type}` in `{inc.function}()`'
        blocks = [
            {'type': 'section', 'text': {'type': 'mrkdwn', 'text': text}},
            {'type': 'section', 'fields': [
                {'type': 'mrkdwn', 'text': f'*Guard:* {inc.guard_type}'},
                {'type': 'mrkdwn', 'text': f'*Target:* {inc.target_variable}'},
                {'type': 'mrkdwn', 'text': f'*Default:* `{repr(inc.default_value)}`'},
                {'type': 'mrkdwn', 'text': f'*Status:* {inc.status.value}'},
            ]},
        ]
        if inc.suggested_fix:
            blocks.append({'type': 'section', 'text': {'type': 'mrkdwn', 'text': f'*Suggested fix:*\n```{inc.suggested_fix}```'}})
        return {'text': text, 'blocks': blocks}

    def _format_discord(self, inc: IncidentRecord) -> dict:
        colors = {'CRITICAL': 0xFF0000, 'HIGH': 0xFFA500, 'MEDIUM': 0x0099FF, 'LOW': 0x808080}
        embed = {
            'title': f'{inc.severity.value}: {inc.exception_type} in {inc.function}()',
            'color': colors.get(inc.severity.value, 0x808080),
            'fields': [
                {'name': 'Guard', 'value': inc.guard_type, 'inline': True},
                {'name': 'Target', 'value': inc.target_variable, 'inline': True},
                {'name': 'Status', 'value': inc.status.value, 'inline': True},
                {'name': 'File', 'value': f'{inc.file_path}:{inc.line_number}', 'inline': False},
            ],
            'timestamp': inc.timestamp,
        }
        if inc.suggested_fix:
            embed['fields'].append({'name': 'Suggested Fix', 'value': f'```python\n{inc.suggested_fix}\n```', 'inline': False})
        return {'embeds': [embed]}

    def _format_teams(self, inc: IncidentRecord) -> dict:
        colors = {'CRITICAL': 'FF0000', 'HIGH': 'FFA500', 'MEDIUM': '0099FF', 'LOW': '808080'}
        return {
            '@type': 'MessageCard',
            '@context': 'http://schema.org/extensions',
            'themeColor': colors.get(inc.severity.value, '808080'),
            'summary': f'CodeSuture {inc.severity.value}: {inc.exception_type} in {inc.function}()',
            'sections': [{
                'activityTitle': f'{inc.severity.value} — {inc.exception_type}',
                'activitySubtitle': f'in {inc.function}() at {inc.file_path}:{inc.line_number}',
                'facts': [
                    {'name': 'Guard', 'value': inc.guard_type},
                    {'name': 'Target', 'value': inc.target_variable},
                    {'name': 'Default', 'value': repr(inc.default_value)},
                    {'name': 'Status', 'value': inc.status.value},
                ],
            }],
        }
