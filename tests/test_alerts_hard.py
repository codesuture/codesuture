"""
Hard edge-case tests for codesuture.alerts
Focus: routing logic, escalation boundaries, webhook format correctness,
       file channel atomicity, config parsing edge cases, and thread safety.
"""

import json
import os
import threading
import time
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone, timedelta

from codesuture.alerts.config import (
    AlertConfig, WebhookConfig, FileAlertConfig,
    EscalationConfig, load_config,
)
from codesuture.alerts.router import AlertRouter
from codesuture.alerts.channels.file_channel import FileAlertChannel
from codesuture.alerts.channels.webhook_channel import WebhookChannel
from codesuture.incidents.incident import IncidentRecord, Severity, IncidentStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _incident(severity=Severity.HIGH, function='test_fn', guard='null_guard',
              exc='AttributeError', status=IncidentStatus.PATCHED,
              suggested_fix=None, stack_trace=None):
    return IncidentRecord(
        exception_type=exc,
        exception_message="boom",
        function=function,
        file_path='app.py',
        line_number=42,
        severity=severity,
        status=status,
        guard_type=guard,
        target_variable='x',
        default_value='',
        # Do NOT hardcode incident_id — IncidentRecord generates a unique UUID hex
        # so concurrent callers each get a distinct filename.
        suggested_fix=suggested_fix,
        stack_trace=stack_trace or [],
    )


def _router(tmp_path, *, file_enabled=True, webhook_enabled=False,
            webhook_url='', webhook_format='raw',
            escalation_threshold=5, escalation_window=24,
            routing=None):
    cfg = AlertConfig(
        enabled=True,
        file=FileAlertConfig(enabled=file_enabled, directory=str(tmp_path / 'alerts')),
        webhook=WebhookConfig(enabled=webhook_enabled, url=webhook_url, format=webhook_format),
        escalation=EscalationConfig(
            repeat_threshold=escalation_threshold,
            repeat_window_hours=escalation_window,
            escalate_to='CRITICAL',
        ),
        routing=routing or {
            'CRITICAL': ['file', 'webhook'],
            'HIGH': ['file'],
            'MEDIUM': ['digest'],
            'LOW': ['digest'],
        },
    )
    return AlertRouter(cfg)


# ---------------------------------------------------------------------------
# AlertConfig — TOML parsing edge cases
# ---------------------------------------------------------------------------

class TestAlertConfigEdgeCases:

    def test_malformed_toml_returns_defaults(self, tmp_path):
        """Malformed TOML must return default config, not raise."""
        bad = tmp_path / '.codesuture.toml'
        bad.write_text("this is not valid toml @@@ !!!")
        cfg = load_config(str(bad))
        assert cfg.enabled is True  # defaults
        assert cfg.webhook.enabled is False

    def test_empty_toml_returns_defaults(self, tmp_path):
        """Completely empty TOML file must return defaults."""
        empty = tmp_path / '.codesuture.toml'
        empty.write_text('')
        cfg = load_config(str(empty))
        assert cfg.enabled is True

    def test_toml_routing_keys_normalized_to_uppercase(self, tmp_path):
        """Routing keys from TOML must be uppercased."""
        toml = tmp_path / '.codesuture.toml'
        toml.write_text("""
[alerts.routing]
critical = ["file"]
high = ["file"]
medium = ["digest"]
low = ["digest"]
""")
        cfg = load_config(str(toml))
        assert 'CRITICAL' in cfg.routing
        assert 'HIGH' in cfg.routing
        assert 'critical' not in cfg.routing  # must NOT be lowercase key

    def test_default_routing_has_all_four_severities(self):
        """Default config must route every severity level."""
        cfg = AlertConfig()
        for sev in ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW'):
            assert sev in cfg.routing, f"Severity '{sev}' missing from default routing"

    def test_escalation_defaults_are_sensible(self):
        """Default escalation config: threshold=5, window=24h, target=CRITICAL."""
        cfg = AlertConfig()
        assert cfg.escalation.repeat_threshold == 5
        assert cfg.escalation.repeat_window_hours == 24
        assert cfg.escalation.escalate_to == 'CRITICAL'

    def test_webhook_disabled_by_default(self):
        """Webhook must be off by default — never accidentally POST."""
        cfg = AlertConfig()
        assert cfg.webhook.enabled is False
        assert cfg.webhook.url == ''


# ---------------------------------------------------------------------------
# FileAlertChannel — exact file content and atomicity
# ---------------------------------------------------------------------------

class TestFileAlertChannelHard:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.alert_dir = str(tmp_path / 'alerts')
        self.channel = FileAlertChannel(directory=self.alert_dir)

    def test_filename_contains_severity(self):
        for sev in Severity:
            inc = _incident(severity=sev, function=f'fn_{sev.value}')
            fname = self.channel.send(inc)
            assert f'_{sev.value}_' in fname, f"Severity '{sev.value}' not in filename '{fname}'"

    def test_filename_starts_with_ALERT(self):
        fname = self.channel.send(_incident())
        assert fname.startswith('ALERT_')

    def test_alert_file_contains_incident_id(self):
        inc = _incident()
        fname = self.channel.send(inc)
        fpath = os.path.join(self.alert_dir, fname)
        with open(fpath, encoding='utf-8') as f:
            content = f.read()
        assert inc.incident_id in content

    def test_alert_file_contains_function_name(self):
        inc = _incident(function='my_special_handler')
        fname = self.channel.send(inc)
        fpath = os.path.join(self.alert_dir, fname)
        content = open(fpath, encoding='utf-8').read()
        assert 'my_special_handler' in content

    def test_alert_file_contains_exception_type(self):
        inc = _incident(exc='ZeroDivisionError')
        fname = self.channel.send(inc)
        content = open(os.path.join(self.alert_dir, fname), encoding='utf-8').read()
        assert 'ZeroDivisionError' in content

    def test_critical_alert_contains_action_required_section(self):
        inc = _incident(severity=Severity.CRITICAL)
        fname = self.channel.send(inc)
        content = open(os.path.join(self.alert_dir, fname), encoding='utf-8').read()
        assert 'Engineer Action Required' in content

    def test_non_critical_alert_has_no_action_required(self):
        for sev in (Severity.HIGH, Severity.MEDIUM, Severity.LOW):
            inc = _incident(severity=sev, function=f'fn_{sev.value}')
            fname = self.channel.send(inc)
            content = open(os.path.join(self.alert_dir, fname), encoding='utf-8').read()
            assert 'Engineer Action Required' not in content, \
                f"'Engineer Action Required' found in {sev.value} alert"

    def test_suggested_fix_rendered_in_code_block(self):
        inc = _incident(suggested_fix='x = x or default_value')
        fname = self.channel.send(inc)
        content = open(os.path.join(self.alert_dir, fname), encoding='utf-8').read()
        assert '```python' in content
        assert 'x = x or default_value' in content

    def test_no_suggested_fix_section_when_none(self):
        inc = _incident(suggested_fix=None)
        fname = self.channel.send(inc)
        content = open(os.path.join(self.alert_dir, fname), encoding='utf-8').read()
        assert '```python' not in content

    def test_stack_trace_rendered_when_present(self):
        inc = _incident(stack_trace=['  File "app.py", line 42', '    raise KeyError("x")'])
        fname = self.channel.send(inc)
        content = open(os.path.join(self.alert_dir, fname), encoding='utf-8').read()
        assert 'Stack Trace' in content
        assert 'raise KeyError' in content

    def test_no_stack_trace_section_when_empty(self):
        inc = _incident(stack_trace=[])
        fname = self.channel.send(inc)
        content = open(os.path.join(self.alert_dir, fname), encoding='utf-8').read()
        assert 'Stack Trace' not in content

    def test_dismiss_returns_false_for_nonexistent_id(self):
        result = self.channel.dismiss('nonexistent_incident_id')
        assert result is False

    def test_dismiss_only_removes_matching_file(self):
        inc1 = _incident(function='fn1')
        inc2 = _incident(function='fn2')
        self.channel.send(inc1)
        time.sleep(0.01)  # ensure distinct filenames
        self.channel.send(inc2)

        result = self.channel.dismiss(inc1.incident_id)
        assert result is True

        remaining = [f for f in os.listdir(self.alert_dir)
                     if f.startswith('ALERT_')]
        assert len(remaining) == 1
        with open(os.path.join(self.alert_dir, remaining[0]), encoding='utf-8') as f:
            content = f.read()
        assert inc2.incident_id in content

    def test_unread_md_contains_emoji_for_each_severity(self):
        """unread.md must use emoji severity markers."""
        emoji_map = {'CRITICAL': '🔴', 'HIGH': '🟡', 'MEDIUM': '🔵', 'LOW': '⚪'}
        for sev in Severity:
            self.channel.send(_incident(severity=sev, function=f'fn_{sev.value}'))
        unread = self.channel.get_unread()
        for sev, emoji in emoji_map.items():
            assert emoji in unread, f"Emoji for {sev} missing from unread.md"

    def test_get_unread_with_no_file_returns_message(self):
        """get_unread must return a safe string when unread.md doesn't exist."""
        result = self.channel.get_unread()
        assert 'No unread' in result or isinstance(result, str)

    def test_dismiss_all_removes_unread_md_too(self):
        self.channel.send(_incident())
        self.channel.dismiss_all()
        assert not os.path.exists(os.path.join(self.alert_dir, 'unread.md'))

    def test_concurrent_send_no_corruption(self):
        """10 threads sending simultaneously must produce valid, distinct alert files."""
        errors = []

        def worker(n):
            try:
                self.channel.send(_incident(function=f'concurrent_fn_{n}'))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent send failed: {errors}"
        alert_files = [f for f in os.listdir(self.alert_dir) if f.startswith('ALERT_')]
        assert len(alert_files) == 10


# ---------------------------------------------------------------------------
# WebhookChannel — format correctness
# ---------------------------------------------------------------------------

class TestWebhookChannelFormats:

    def _inc(self, **kwargs):
        defaults = dict(
            exception_type='TypeError',
            exception_message='bad',
            function='process_payment',
            file_path='billing.py',
            line_number=55,
            severity=Severity.CRITICAL,
            status=IncidentStatus.PATCHED,
            guard_type='division_guard',
            target_variable='amount',
            default_value=1,
        )
        defaults.update(kwargs)
        return IncidentRecord(**defaults)

    def test_slack_blocks_structure(self):
        ch = WebhookChannel(url='http://x', format='slack')
        payload = ch._format_payload(self._inc())
        assert isinstance(payload['blocks'], list)
        assert len(payload['blocks']) >= 2
        # First block is section with severity text
        assert payload['blocks'][0]['type'] == 'section'
        assert 'CRITICAL' in payload['text']

    def test_slack_includes_guard_and_target_in_fields(self):
        ch = WebhookChannel(url='http://x', format='slack')
        payload = ch._format_payload(self._inc())
        fields_text = json.dumps(payload['blocks'][1]['fields'])
        assert 'division_guard' in fields_text
        assert 'amount' in fields_text

    def test_slack_suggested_fix_adds_third_block(self):
        ch = WebhookChannel(url='http://x', format='slack')
        inc = self._inc(suggested_fix='amount = amount or 1')
        payload = ch._format_payload(inc)
        assert len(payload['blocks']) == 3
        assert 'amount = amount or 1' in json.dumps(payload['blocks'][2])

    def test_discord_color_per_severity(self):
        expected = {
            Severity.CRITICAL: 0xFF0000,
            Severity.HIGH: 0xFFA500,
            Severity.MEDIUM: 0x0099FF,
            Severity.LOW: 0x808080,
        }
        ch = WebhookChannel(url='http://x', format='discord')
        for sev, color in expected.items():
            inc = self._inc(severity=sev)
            payload = ch._format_payload(inc)
            assert payload['embeds'][0]['color'] == color, \
                f"Wrong color for {sev.value}"

    def test_discord_has_timestamp(self):
        ch = WebhookChannel(url='http://x', format='discord')
        inc = self._inc()
        payload = ch._format_payload(inc)
        assert 'timestamp' in payload['embeds'][0]
        assert payload['embeds'][0]['timestamp'] == inc.timestamp

    def test_discord_suggested_fix_added_as_field(self):
        ch = WebhookChannel(url='http://x', format='discord')
        inc = self._inc(suggested_fix='x = x or 0')
        payload = ch._format_payload(inc)
        field_names = [f['name'] for f in payload['embeds'][0]['fields']]
        assert 'Suggested Fix' in field_names

    def test_teams_has_required_keys(self):
        ch = WebhookChannel(url='http://x', format='teams')
        payload = ch._format_payload(self._inc())
        assert payload['@type'] == 'MessageCard'
        assert payload['@context'] == 'http://schema.org/extensions'
        assert 'themeColor' in payload
        assert 'sections' in payload

    def test_teams_color_per_severity(self):
        expected = {
            Severity.CRITICAL: 'FF0000',
            Severity.HIGH: 'FFA500',
            Severity.MEDIUM: '0099FF',
            Severity.LOW: '808080',
        }
        ch = WebhookChannel(url='http://x', format='teams')
        for sev, color in expected.items():
            inc = self._inc(severity=sev)
            payload = ch._format_payload(inc)
            assert payload['themeColor'] == color

    def test_raw_format_is_full_to_dict(self):
        ch = WebhookChannel(url='http://x', format='raw')
        inc = self._inc()
        payload = ch._format_payload(inc)
        # raw must be to_dict() — every field present
        d = inc.to_dict()
        for key in d:
            assert key in payload, f"Key '{key}' missing from raw payload"

    def test_unknown_format_falls_back_to_raw(self):
        ch = WebhookChannel(url='http://x', format='telegram')
        inc = self._inc()
        payload = ch._format_payload(inc)
        # Should fall back to raw (to_dict)
        assert 'severity' in payload
        assert 'guard_type' in payload

    def test_send_handles_http_error_gracefully(self):
        """send() must return False on HTTP error, not raise."""
        import urllib.error
        ch = WebhookChannel(url='http://nonexistent.invalid/hook', format='raw')
        result = ch.send(self._inc())
        assert result is False

    def test_send_returns_true_on_200(self):
        """send() must return True when server responds 2xx."""
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with patch('urllib.request.urlopen', return_value=mock_resp):
            ch = WebhookChannel(url='http://example.com/hook', format='raw')
            result = ch.send(self._inc())
        assert result is True

    def test_send_returns_false_on_4xx(self):
        """send() must return False when server responds 4xx."""
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 403

        with patch('urllib.request.urlopen', return_value=mock_resp):
            ch = WebhookChannel(url='http://example.com/hook', format='raw')
            result = ch.send(self._inc())
        assert result is False

    def test_payload_is_valid_json_for_all_formats(self):
        """Every format adapter must produce JSON-serializable output."""
        for fmt in ('slack', 'discord', 'teams', 'raw'):
            ch = WebhookChannel(url='http://x', format=fmt)
            payload = ch._format_payload(self._inc())
            try:
                json.dumps(payload)
            except (TypeError, ValueError) as e:
                pytest.fail(f"Format '{fmt}' produced non-serializable payload: {e}")

    def test_custom_headers_are_included(self):
        """Custom headers must be merged into the request."""
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured['headers'] = dict(req.headers)
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.status = 200
            return m

        ch = WebhookChannel(
            url='http://example.com/hook',
            headers={'X-Api-Key': 'secret123'},
            format='raw',
        )
        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            ch.send(self._inc())

        # Python's Request capitalizes header keys
        header_values = {k.lower(): v for k, v in captured.get('headers', {}).items()}
        assert 'x-api-key' in header_values
        assert header_values['x-api-key'] == 'secret123'


# ---------------------------------------------------------------------------
# AlertRouter — routing logic and escalation
# ---------------------------------------------------------------------------

class TestAlertRouterHard:

    def test_disabled_router_sends_nothing(self, tmp_path):
        """When config.enabled=False, no file alerts must be created."""
        cfg = AlertConfig(
            enabled=False,
            file=FileAlertConfig(enabled=True, directory=str(tmp_path / 'alerts')),
        )
        router = AlertRouter(cfg)
        router.route(_incident(severity=Severity.CRITICAL))
        alert_dir = str(tmp_path / 'alerts')
        files = os.listdir(alert_dir) if os.path.isdir(alert_dir) else []
        assert len(files) == 0

    def test_critical_creates_file_alert(self, tmp_path):
        router = _router(tmp_path)
        router.route(_incident(severity=Severity.CRITICAL))
        files = [f for f in os.listdir(str(tmp_path / 'alerts')) if f.startswith('ALERT_')]
        assert len(files) == 1

    def test_high_creates_file_alert(self, tmp_path):
        router = _router(tmp_path)
        router.route(_incident(severity=Severity.HIGH))
        files = [f for f in os.listdir(str(tmp_path / 'alerts')) if f.startswith('ALERT_')]
        assert len(files) == 1

    def test_medium_does_not_create_file(self, tmp_path):
        router = _router(tmp_path)
        router.route(_incident(severity=Severity.MEDIUM))
        alert_dir = str(tmp_path / 'alerts')
        files = [f for f in os.listdir(alert_dir) if f.startswith('ALERT_')] \
            if os.path.isdir(alert_dir) else []
        assert files == []

    def test_low_does_not_create_file(self, tmp_path):
        router = _router(tmp_path)
        router.route(_incident(severity=Severity.LOW))
        alert_dir = str(tmp_path / 'alerts')
        files = [f for f in os.listdir(alert_dir) if f.startswith('ALERT_')] \
            if os.path.isdir(alert_dir) else []
        assert files == []

    def test_escalation_triggers_at_exact_threshold(self, tmp_path):
        """Escalation must trigger exactly at repeat_threshold, not before."""
        router = _router(tmp_path, escalation_threshold=3)
        fn = 'escalation_target_fn'

        # First 2 routes: should NOT escalate (threshold is 3)
        for i in range(2):
            inc = _incident(severity=Severity.MEDIUM, function=fn)
            router.route(inc)
            assert inc.severity == Severity.MEDIUM, f"Premature escalation at hit {i+1}"

        # 3rd route: SHOULD escalate
        inc = _incident(severity=Severity.MEDIUM, function=fn)
        router.route(inc)
        assert inc.severity == Severity.CRITICAL

    def test_escalation_sets_review_priority_urgent(self, tmp_path):
        """Escalated incidents must have review_priority set to URGENT."""
        router = _router(tmp_path, escalation_threshold=2)
        fn = 'priority_test_fn'
        for _ in range(2):
            inc = _incident(severity=Severity.MEDIUM, function=fn)
            router.route(inc)
        assert inc.review_priority == 'URGENT'

    def test_escalation_prunes_old_timestamps(self, tmp_path):
        """Escalation tracker must not count timestamps older than the window."""
        router = _router(tmp_path, escalation_threshold=3, escalation_window=1)
        fn = 'pruning_test_fn'

        # Inject 2 "old" timestamps directly
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        with router._lock:
            router._escalation_tracker[fn].extend([old_time, old_time])

        # Now route once — total in window = 1 (old ones pruned), no escalation
        inc = _incident(severity=Severity.MEDIUM, function=fn)
        router.route(inc)
        assert inc.severity == Severity.MEDIUM, "Old timestamps should have been pruned"

    def test_escalation_only_affects_same_function(self, tmp_path):
        """Escalation for function A must not affect function B."""
        router = _router(tmp_path, escalation_threshold=2)

        for _ in range(3):
            router.route(_incident(severity=Severity.MEDIUM, function='fn_a'))

        inc_b = _incident(severity=Severity.MEDIUM, function='fn_b')
        router.route(inc_b)
        assert inc_b.severity == Severity.MEDIUM  # B not escalated

    def test_webhook_called_for_critical(self, tmp_path):
        """Webhook channel's send() must be called for CRITICAL incidents."""
        cfg = AlertConfig(
            enabled=True,
            file=FileAlertConfig(enabled=False, directory=str(tmp_path / 'alerts')),
            webhook=WebhookConfig(enabled=True, url='http://webhook.example.com'),
            routing={'CRITICAL': ['file', 'webhook'], 'HIGH': ['file'],
                     'MEDIUM': ['digest'], 'LOW': ['digest']},
        )
        router = AlertRouter(cfg)

        mock_channel = MagicMock()
        router._webhook_channel = mock_channel

        inc = _incident(severity=Severity.CRITICAL)
        router.route(inc)
        mock_channel.send.assert_called_once_with(inc)

    def test_webhook_not_called_for_medium(self, tmp_path):
        """Webhook must NOT be called for MEDIUM incidents (default routing)."""
        cfg = AlertConfig(
            enabled=True,
            file=FileAlertConfig(enabled=True, directory=str(tmp_path / 'alerts')),
            webhook=WebhookConfig(enabled=True, url='http://webhook.example.com'),
        )
        router = AlertRouter(cfg)
        mock_channel = MagicMock()
        router._webhook_channel = mock_channel

        router.route(_incident(severity=Severity.MEDIUM))
        mock_channel.send.assert_not_called()

    def test_flush_sends_batched_high_incidents(self, tmp_path):
        """flush() must deliver any batched HIGH incidents to file channel."""
        router = _router(tmp_path, routing={
            'CRITICAL': ['file'],
            'HIGH': [],        # HIGH goes to batch only
            'MEDIUM': ['digest'],
            'LOW': ['digest'],
        })

        # Manually add to batch buffer instead of relying on timer
        inc = _incident(severity=Severity.HIGH)
        with router._lock:
            router._batch_buffer.append(inc)

        router.flush()

        alert_dir = str(tmp_path / 'alerts')
        files = [f for f in os.listdir(alert_dir) if f.startswith('ALERT_')] \
            if os.path.isdir(alert_dir) else []
        assert len(files) == 1

    def test_get_unread_alerts_returns_string(self, tmp_path):
        """get_unread_alerts must always return a string."""
        router = _router(tmp_path)
        result = router.get_unread_alerts()
        assert isinstance(result, str)

    def test_dismiss_all_delegates_to_file_channel(self, tmp_path):
        """dismiss_all must clear all alerts from the file channel."""
        router = _router(tmp_path)
        router.route(_incident(severity=Severity.CRITICAL, function='fn1'))
        router.route(_incident(severity=Severity.CRITICAL, function='fn2'))
        router.dismiss_all()
        alert_dir = str(tmp_path / 'alerts')
        files = os.listdir(alert_dir) if os.path.isdir(alert_dir) else []
        assert files == []

    def test_file_channel_error_does_not_crash_router(self, tmp_path):
        """A failing file channel must not crash the router — errors are logged."""
        router = _router(tmp_path)
        mock_channel = MagicMock()
        mock_channel.send.side_effect = OSError("disk full")
        router._file_channel = mock_channel

        # Should not raise
        router.route(_incident(severity=Severity.CRITICAL))

    def test_concurrent_routing_no_corruption(self, tmp_path):
        """20 threads routing simultaneously must produce 20 alert files."""
        router = _router(tmp_path)
        errors = []

        def worker(n):
            try:
                router.route(_incident(
                    severity=Severity.CRITICAL,
                    function=f'concurrent_fn_{n}'
                ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Threads raised: {errors}"
        alert_dir = str(tmp_path / 'alerts')
        files = [f for f in os.listdir(alert_dir) if f.startswith('ALERT_')]
        assert len(files) == 20
