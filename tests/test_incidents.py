"""Tests for codesuture.incidents — incident model, severity, logging, digest."""

import json
import os
import shutil
import tempfile
import pytest
from datetime import datetime, timezone, timedelta

from codesuture.incidents.incident import IncidentRecord, Severity, IncidentStatus
from codesuture.incidents.severity import classify_severity
from codesuture.incidents.incident_log import IncidentLogger
from codesuture.incidents.digest import DigestGenerator

class TestIncidentRecord:
    def test_create_defaults(self):
        """IncidentRecord creates with sensible defaults."""
        rec = IncidentRecord()
        assert rec.incident_id
        assert rec.timestamp
        assert rec.severity == Severity.MEDIUM
        assert rec.status == IncidentStatus.PATCHED
        assert rec.python_version

    def test_to_dict_roundtrip(self):
        """to_dict → from_dict preserves all fields."""
        rec = IncidentRecord(
            exception_type="AttributeError",
            exception_message="'NoneType' has no attribute 'bio'",
            module="myapp.handlers",
            function="get_user_profile",
            line_number=42,
            file_path="handlers.py",
            severity=Severity.HIGH,
            status=IncidentStatus.REWOUND,
            guard_type="null_guard",
            target_variable="profile",
            default_value="",
            ttl_days=14,
        )
        d = rec.to_dict()
        assert d["severity"] == "HIGH"
        assert d["status"] == "rewound"
        assert d["exception_type"] == "AttributeError"

        restored = IncidentRecord.from_dict(d)
        assert restored.severity == Severity.HIGH
        assert restored.status == IncidentStatus.REWOUND
        assert restored.function == "get_user_profile"
        assert restored.ttl_days == 14

    def test_to_dict_json_serializable(self):
        """to_dict output can be dumped to JSON without errors."""
        rec = IncidentRecord(
            guard_type="key_guard",
            default_value={"nested": True},
            stack_trace=["  File ...", "  line 5"],
        )
        serialized = json.dumps(rec.to_dict(), default=str)
        assert "key_guard" in serialized

    def test_from_dict_ignores_unknown_fields(self):
        """from_dict silently ignores keys not in the dataclass."""
        data = {
            "exception_type": "KeyError",
            "function": "get_config",
            "severity": "LOW",
            "status": "patched",
            "unknown_future_field": 12345,
        }
        rec = IncidentRecord.from_dict(data)
        assert rec.exception_type == "KeyError"
        assert rec.severity == Severity.LOW

class TestSeverityClassification:
    def test_callable_guard_always_critical(self):
        assert classify_severity("callable_guard") == Severity.CRITICAL

    def test_sensitive_module_escalates(self):
        assert classify_severity("null_guard", module="myapp.auth.login") == Severity.HIGH
        assert classify_severity("division_guard", module="billing.service") == Severity.CRITICAL

    def test_http_post_is_high(self):
        assert classify_severity("null_guard", http_method="POST") == Severity.HIGH
        assert classify_severity("null_guard", http_method="DELETE") == Severity.HIGH

    def test_first_occurrence_is_high(self):
        assert classify_severity("null_guard", hit_count=0) == Severity.HIGH

    def test_repeat_offender_drops_to_low(self):
        assert classify_severity("null_guard", hit_count=5) == Severity.LOW
        assert classify_severity("key_guard", hit_count=3) == Severity.LOW

    def test_file_guard_is_low(self):
        assert classify_severity("file_guard", hit_count=1) == Severity.LOW

    def test_chain_subscript_is_high(self):
        assert classify_severity("chain_subscript_guard", hit_count=1) == Severity.HIGH

    def test_standard_guard_medium(self):
        assert classify_severity("null_guard", hit_count=1) == Severity.MEDIUM
        assert classify_severity("key_guard", hit_count=2) == Severity.MEDIUM

class TestIncidentLogger:
    @pytest.fixture(autouse=True)
    def setup_tmpdir(self, tmp_path):
        self.log_dir = str(tmp_path / "incidents")
        self.logger = IncidentLogger(log_dir=self.log_dir)

    def test_log_creates_daily_file(self):
        rec = IncidentRecord(exception_type="TestError", function="test_fn")
        self.logger.log_incident(rec)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        expected_file = os.path.join(self.log_dir, f"incidents_{today}.jsonl")
        assert os.path.isfile(expected_file)

        with open(expected_file, "r") as f:
            lines = f.readlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["exception_type"] == "TestError"

    def test_log_multiple_appends(self):
        for i in range(5):
            rec = IncidentRecord(exception_type=f"Error{i}", function=f"fn{i}")
            self.logger.log_incident(rec)

        incidents = self.logger.get_today_incidents()
        assert len(incidents) == 5

    def test_query_by_severity(self):
        self.logger.log_incident(IncidentRecord(severity=Severity.HIGH, function="high_fn"))
        self.logger.log_incident(IncidentRecord(severity=Severity.LOW, function="low_fn"))
        self.logger.log_incident(IncidentRecord(severity=Severity.HIGH, function="high_fn2"))

        high = self.logger.get_incidents(severity=Severity.HIGH)
        assert len(high) == 2
        assert all(i.severity == Severity.HIGH for i in high)

    def test_query_by_function(self):
        self.logger.log_incident(IncidentRecord(function="get_profile"))
        self.logger.log_incident(IncidentRecord(function="get_settings"))
        self.logger.log_incident(IncidentRecord(function="update_profile"))

        results = self.logger.get_incidents(function="profile")
        assert len(results) == 2

    def test_get_incident_count(self):
        self.logger.log_incident(IncidentRecord(severity=Severity.CRITICAL))
        self.logger.log_incident(IncidentRecord(severity=Severity.HIGH))
        self.logger.log_incident(IncidentRecord(severity=Severity.HIGH))
        self.logger.log_incident(IncidentRecord(severity=Severity.MEDIUM))

        counts = self.logger.get_incident_count()
        assert counts["total"] == 4
        assert counts["CRITICAL"] == 1
        assert counts["HIGH"] == 2
        assert counts["MEDIUM"] == 1
        assert counts["LOW"] == 0

class TestDigestGenerator:
    @pytest.fixture(autouse=True)
    def setup_tmpdir(self, tmp_path):
        self.log_dir = str(tmp_path / "incidents")
        self.logger = IncidentLogger(log_dir=self.log_dir)
        self.generator = DigestGenerator(self.logger)

    def test_empty_digest(self):
        content = self.generator.generate_daily()
        assert "No incidents" in content

    def test_daily_digest_with_incidents(self):
        self.logger.log_incident(IncidentRecord(
            exception_type="AttributeError",
            function="get_bio",
            severity=Severity.HIGH,
            guard_type="null_guard",
            target_variable="profile",
            default_value="",
        ))
        self.logger.log_incident(IncidentRecord(
            exception_type="KeyError",
            function="get_config",
            severity=Severity.MEDIUM,
            guard_type="key_guard",
            target_variable="timeout",
            default_value=None,
        ))

        content = self.generator.generate_daily()
        assert "Daily Incident Report" in content
        assert "Total incidents" in content
        assert "get_bio" in content
        assert "null_guard" in content
        assert "CRITICAL & HIGH Priority" in content

    def test_weekly_digest(self):
        self.logger.log_incident(IncidentRecord(
            exception_type="ZeroDivisionError",
            function="compute_ratio",
            severity=Severity.MEDIUM,
            guard_type="division_guard",
        ))
        content = self.generator.generate_weekly()
        assert "Weekly Incident Report" in content
        assert "compute_ratio" in content

    def test_save_digest(self):
        self.logger.log_incident(IncidentRecord(function="test"))
        content = self.generator.generate_daily()
        path = self.generator.save_digest(content, "test_digest.md")
        assert os.path.isfile(path)
        with open(path, encoding='utf-8') as f:
            assert f.read() == content

    def test_repeat_offenders_section(self):
        """Functions patched 2+ times appear in Recommended Actions."""
        for _ in range(3):
            self.logger.log_incident(IncidentRecord(
                function="broken_handler",
                severity=Severity.MEDIUM,
                guard_type="null_guard",
            ))
        content = self.generator.generate_daily()
        assert "Recommended Actions" in content
        assert "broken_handler" in content
