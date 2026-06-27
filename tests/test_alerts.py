"""Tests for codesuture.alerts — config, routing, file channel, webhook."""

import json
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from codesuture.alerts.config import AlertConfig, load_config, WebhookConfig, FileAlertConfig
from codesuture.alerts.router import AlertRouter
from codesuture.alerts.channels.file_channel import FileAlertChannel
from codesuture.alerts.channels.webhook_channel import WebhookChannel
from codesuture.incidents.incident import IncidentRecord, Severity, IncidentStatus

class TestAlertConfig:
    def test_default_config(self):
        cfg = AlertConfig()
        assert cfg.enabled is True
        assert cfg.file.enabled is True
        assert cfg.webhook.enabled is False
        assert "CRITICAL" in cfg.routing
        assert "file" in cfg.routing["CRITICAL"]

    def test_load_config_no_file(self, tmp_path):
        """load_config returns defaults when no .codesuture.toml exists."""
        cfg = load_config(str(tmp_path / "nonexistent.toml"))
        assert cfg.enabled is True
        assert cfg.webhook.enabled is False

    def test_load_config_with_toml(self, tmp_path):
        """load_config parses a real .codesuture.toml."""
        toml_path = tmp_path / ".codesuture.toml"
        toml_path.write_text("""
[alerts]
enabled = true

[alerts.file]
enabled = true
directory = "custom/alerts"

[alerts.webhook]
enabled = true
url = "https://hooks.example.com/test"
format = "slack"

[alerts.routing]
CRITICAL = ["file", "webhook"]
HIGH = ["file"]
MEDIUM = ["digest"]
LOW = ["digest"]

[alerts.escalation]
repeat_threshold = 3
repeat_window_hours = 12
escalate_to = "CRITICAL"
""")
        cfg = load_config(str(toml_path))
        assert cfg.enabled is True
        assert cfg.file.directory == "custom/alerts"
        assert cfg.webhook.enabled is True
        assert cfg.webhook.url == "https://hooks.example.com/test"
        assert cfg.webhook.format == "slack"
        assert cfg.escalation.repeat_threshold == 3
        assert cfg.escalation.repeat_window_hours == 12

class TestFileAlertChannel:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.alert_dir = str(tmp_path / "alerts")
        self.channel = FileAlertChannel(directory=self.alert_dir)

    def _make_incident(self, severity=Severity.HIGH, function="test_func"):
        return IncidentRecord(
            exception_type="AttributeError",
            exception_message="'NoneType' has no attribute 'x'",
            function=function,
            file_path="test.py",
            line_number=10,
            severity=severity,
            status=IncidentStatus.PATCHED,
            guard_type="null_guard",
            target_variable="x",
            default_value="",
        )

    def test_send_creates_alert_file(self):
        inc = self._make_incident()
        filename = self.channel.send(inc)
        assert filename.startswith("ALERT_")
        assert "_HIGH_" in filename
        assert filename.endswith(".md")
        filepath = os.path.join(self.alert_dir, filename)
        assert os.path.isfile(filepath)
        with open(filepath) as f:
            content = f.read()
        assert "HIGH ALERT" in content
        assert "test_func" in content
        assert "null_guard" in content

    def test_critical_alert_has_action_required(self):
        inc = self._make_incident(severity=Severity.CRITICAL)
        filename = self.channel.send(inc)
        filepath = os.path.join(self.alert_dir, filename)
        with open(filepath, encoding='utf-8') as f:
            content = f.read()
        assert "CRITICAL ALERT" in content
        assert "Engineer Action Required" in content

    def test_unread_md_updated(self):
        inc = self._make_incident()
        self.channel.send(inc)
        unread = self.channel.get_unread()
        assert "HIGH" in unread
        assert "test_func" in unread

    def test_dismiss_removes_alert(self):
        inc = self._make_incident()
        self.channel.send(inc)
        result = self.channel.dismiss(inc.incident_id)
        assert result is True

        md_files = [f for f in os.listdir(self.alert_dir)
                    if f.endswith('.md') and f != 'unread.md']
        assert len(md_files) == 0

    def test_dismiss_all(self):
        self.channel.send(self._make_incident(function="fn1"))
        import time; time.sleep(0.01)
        self.channel.send(self._make_incident(function="fn2"))
        file_count = len(os.listdir(self.alert_dir))
        assert file_count >= 2
        self.channel.dismiss_all()
        assert len(os.listdir(self.alert_dir)) == 0

class TestWebhookChannel:
    def _make_incident(self):
        return IncidentRecord(
            exception_type="KeyError",
            exception_message="'timeout'",
            function="get_config",
            severity=Severity.HIGH,
            status=IncidentStatus.PATCHED,
            guard_type="key_guard",
            target_variable="timeout",
        )

    def test_slack_format(self):
        ch = WebhookChannel(url="https://example.com", format="slack")
        payload = ch._format_payload(self._make_incident())
        assert "blocks" in payload
        assert "text" in payload
        assert "key_guard" in json.dumps(payload)

    def test_discord_format(self):
        ch = WebhookChannel(url="https://example.com", format="discord")
        payload = ch._format_payload(self._make_incident())
        assert "embeds" in payload
        embed = payload["embeds"][0]
        assert "fields" in embed
        assert embed["color"] == 0xFFA500

    def test_teams_format(self):
        ch = WebhookChannel(url="https://example.com", format="teams")
        payload = ch._format_payload(self._make_incident())
        assert payload["@type"] == "MessageCard"
        assert "sections" in payload

    def test_raw_format(self):
        ch = WebhookChannel(url="https://example.com", format="raw")
        payload = ch._format_payload(self._make_incident())
        assert payload["guard_type"] == "key_guard"
        assert payload["severity"] == "HIGH"

    def test_send_without_url_returns_false(self):
        ch = WebhookChannel(url="")
        result = ch.send(self._make_incident())
        assert result is False

class TestAlertRouter:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.alert_dir = str(tmp_path / "alerts")
        self.config = AlertConfig(
            file=FileAlertConfig(enabled=True, directory=self.alert_dir),
            webhook=WebhookConfig(enabled=False),
        )
        self.router = AlertRouter(self.config)

    def _make_incident(self, severity=Severity.CRITICAL, function="fn"):
        return IncidentRecord(
            exception_type="TestError",
            function=function,
            severity=severity,
            status=IncidentStatus.PATCHED,
            guard_type="null_guard",
        )

    def test_critical_creates_file_alert(self):
        inc = self._make_incident(severity=Severity.CRITICAL)
        self.router.route(inc)
        files = [f for f in os.listdir(self.alert_dir)
                 if f.startswith("ALERT_") and f.endswith(".md")]
        assert len(files) >= 1

    def test_medium_does_not_create_file(self):
        inc = self._make_incident(severity=Severity.MEDIUM)
        self.router.route(inc)
        files = [f for f in os.listdir(self.alert_dir)
                 if f.startswith("ALERT_")]
        assert len(files) == 0

    def test_escalation_on_repeat(self):
        """Patching same function N times escalates severity."""
        self.config.escalation.repeat_threshold = 3
        self.config.escalation.repeat_window_hours = 24
        router = AlertRouter(self.config)

        for i in range(3):
            inc = self._make_incident(severity=Severity.MEDIUM, function="broken_fn")
            router.route(inc)

        assert inc.severity == Severity.CRITICAL

    def test_dismiss_all(self):
        self.router.route(self._make_incident(severity=Severity.CRITICAL, function="fn1"))
        self.router.route(self._make_incident(severity=Severity.CRITICAL, function="fn2"))
        self.router.dismiss_all()
        assert len(os.listdir(self.alert_dir)) == 0
