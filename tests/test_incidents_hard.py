"""
Hard edge-case tests for codesuture.incidents
Focus: boundary conditions, adversarial inputs, concurrency, and
       exact semantic correctness that the happy-path tests skip.
"""

import json
import os
import threading
import time
import shutil
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from codesuture.incidents.incident import IncidentRecord, Severity, IncidentStatus
from codesuture.incidents.severity import classify_severity, _SENSITIVE_MODULES
from codesuture.incidents.incident_log import IncidentLogger
from codesuture.incidents.digest import DigestGenerator

class TestIncidentRecordEdgeCases:

    def test_incident_id_is_unique_per_instance(self):
        """Two IncidentRecords created back-to-back must have different IDs."""
        ids = {IncidentRecord().incident_id for _ in range(50)}
        assert len(ids) == 50, "Collision in incident_id generation"

    def test_incident_id_is_12_chars_hex(self):
        rec = IncidentRecord()
        assert len(rec.incident_id) == 12
        int(rec.incident_id, 16)

    def test_timestamp_is_utc_iso_format(self):
        rec = IncidentRecord()
        dt = datetime.fromisoformat(rec.timestamp)

        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_to_dict_does_not_mutate_enum_fields(self):
        """to_dict must serialize Enum fields as strings, not mutate them."""
        rec = IncidentRecord(severity=Severity.CRITICAL)
        d = rec.to_dict()
        d['severity'] = 'LOW'  # mutate the dict

        assert rec.severity == Severity.CRITICAL

    def test_to_dict_lists_are_shallow_references(self):
        """to_dict returns shallow list references — documents the current behavior.
        This is a known limitation: mutating the returned list also mutates the record.
        Engineers should copy lists if they need independence."""
        rec = IncidentRecord(related_incidents=['a', 'b'])
        d = rec.to_dict()

        assert d['related_incidents'] == ['a', 'b']

        d['related_incidents'].append('c')

        # This test ensures we know about this, not that it's safe
        assert d['related_incidents'] is rec.related_incidents

    def test_from_dict_all_severity_values(self):
        """Every Severity enum value survives the roundtrip."""
        for sev in Severity:
            rec = IncidentRecord(severity=sev)
            restored = IncidentRecord.from_dict(rec.to_dict())
            assert restored.severity == sev

    def test_from_dict_all_status_values(self):
        """Every IncidentStatus enum value survives the roundtrip."""
        for status in IncidentStatus:
            rec = IncidentRecord(status=status)
            restored = IncidentRecord.from_dict(rec.to_dict())
            assert restored.status == status

    def test_from_dict_invalid_severity_raises(self):
        """from_dict with an unknown severity string must raise ValueError."""
        data = IncidentRecord().to_dict()
        data['severity'] = 'CATASTROPHIC'
        with pytest.raises(ValueError):
            IncidentRecord.from_dict(data)

    def test_from_dict_invalid_status_raises(self):
        """from_dict with an unknown status string must raise ValueError."""
        data = IncidentRecord().to_dict()
        data['status'] = 'teleported'
        with pytest.raises(ValueError):
            IncidentRecord.from_dict(data)

    def test_to_dict_default_value_none_serializable(self):
        """None default_value must serialize to JSON null, not break."""
        rec = IncidentRecord(default_value=None)
        d = rec.to_dict()
        serialized = json.dumps(d)
        parsed = json.loads(serialized)
        assert parsed['default_value'] is None

    def test_to_dict_with_complex_default_value(self):
        """List/dict default_value must survive JSON roundtrip."""
        rec = IncidentRecord(default_value={'key': [1, 2, 3], 'nested': {'a': True}})
        serialized = json.dumps(rec.to_dict(), default=str)
        parsed = json.loads(serialized)
        assert parsed['default_value'] == {'key': [1, 2, 3], 'nested': {'a': True}}

    def test_python_version_format(self):
        """python_version field must be 'MAJOR.MINOR.MICRO' format."""
        import sys
        rec = IncidentRecord()
        parts = rec.python_version.split('.')
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)
        assert int(parts[0]) == sys.version_info.major

    def test_stack_trace_list_shallow_reference(self):
        """to_dict returns shallow list reference for stack_trace.
        Documents that to_dict does not deep-copy list fields."""
        rec = IncidentRecord(stack_trace=['frame1', 'frame2'])
        d = rec.to_dict()
        assert d['stack_trace'] == ['frame1', 'frame2']

        assert d['stack_trace'] is rec.stack_trace

    def test_related_incidents_roundtrip(self):
        """related_incidents list of IDs survives serialization."""
        ids = ['abc123', 'def456', 'ghi789']
        rec = IncidentRecord(related_incidents=ids)
        restored = IncidentRecord.from_dict(rec.to_dict())
        assert restored.related_incidents == ids

class TestSeverityBoundaries:

    def test_callable_guard_beats_all_other_rules(self):
        """callable_guard is CRITICAL regardless of http_method or hit_count."""
        assert classify_severity('callable_guard', http_method='GET', hit_count=100) == Severity.CRITICAL
        assert classify_severity('callable_guard', module='boring.utils', hit_count=99) == Severity.CRITICAL

    def test_sensitive_module_with_null_guard_is_high_not_critical(self):
        """null_guard in sensitive module → HIGH (not CRITICAL like callable/division)."""
        assert classify_severity('null_guard', module='auth.service') == Severity.HIGH

    def test_sensitive_module_with_division_guard_is_critical(self):
        assert classify_severity('division_guard', module='payment.processor') == Severity.CRITICAL

    def test_sensitive_module_with_type_coercion_is_critical(self):
        assert classify_severity('type_coercion_guard', module='security.validator') == Severity.CRITICAL

    def test_http_get_does_not_escalate(self):
        """GET requests should NOT be elevated to HIGH by http_method rule."""
        result = classify_severity('null_guard', http_method='GET', hit_count=1)

        assert result == Severity.MEDIUM

    def test_hit_count_boundary_exactly_zero(self):
        """hit_count=0 means first occurrence → HIGH."""
        assert classify_severity('null_guard', hit_count=0) == Severity.HIGH

    def test_hit_count_boundary_exactly_one_is_medium(self):
        """hit_count=1 is NOT first occurrence → MEDIUM for standard guard."""
        assert classify_severity('null_guard', hit_count=1) == Severity.MEDIUM

    def test_hit_count_boundary_exactly_three_is_low(self):
        """hit_count=3 triggers the >= 3 LOW threshold for standard guards."""
        assert classify_severity('null_guard', hit_count=3) == Severity.LOW
        assert classify_severity('key_guard', hit_count=3) == Severity.LOW

    def test_hit_count_two_is_still_medium(self):
        """hit_count=2 is below the LOW threshold."""
        assert classify_severity('null_guard', hit_count=2) == Severity.MEDIUM

    def test_unknown_guard_type_falls_to_medium(self):
        """An unrecognized guard_type with hit_count=1 should return MEDIUM."""
        result = classify_severity('future_unknown_guard', hit_count=1)
        assert result == Severity.MEDIUM

    def test_all_sensitive_module_keywords_trigger(self):
        """Every keyword in _SENSITIVE_MODULES elevates to at least HIGH."""
        for keyword in _SENSITIVE_MODULES:
            result = classify_severity('null_guard', module=f'myapp.{keyword}.handler')
            assert result == Severity.HIGH, f"Keyword '{keyword}' did not escalate to HIGH"

    def test_sensitive_keyword_in_function_name_also_escalates(self):
        """The sensitive keyword check covers both module AND function name."""
        result = classify_severity('null_guard', module='myapp.users', function='login_user')
        assert result == Severity.HIGH

    def test_str_coerce_guard_is_low_for_known_patterns(self):
        """str_coerce_guard is LOW when it is not the first occurrence.
        Note: hit_count=0 (first occurrence) triggers the HIGH rule BEFORE the
        str_coerce LOW rule — rule ordering means first-occurrence always wins.
        """

        assert classify_severity('str_coerce_guard', hit_count=0) == Severity.HIGH

        assert classify_severity('str_coerce_guard', hit_count=1) == Severity.LOW
        assert classify_severity('str_coerce_guard', hit_count=10) == Severity.LOW

    def test_chain_subscript_beats_repeat_rule(self):
        """chain_subscript_guard is HIGH even at hit_count=10 (no LOW override)."""

        result = classify_severity('chain_subscript_guard', hit_count=10)
        assert result == Severity.HIGH

class TestIncidentLoggerEdgeCases:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.log_dir = str(tmp_path / 'incidents')
        self.logger = IncidentLogger(log_dir=self.log_dir)

    def test_log_and_retrieve_preserves_all_fields(self):
        """Every field set on log must come back identical after retrieval."""
        original = IncidentRecord(
            exception_type='KeyError',
            exception_message="'user_id'",
            module='myapp.views',
            function='get_profile',
            line_number=99,
            file_path='views.py',
            severity=Severity.HIGH,
            status=IncidentStatus.REWOUND,
            guard_type='key_guard',
            target_variable='user_id',
            default_value='anonymous',
            default_rationale='Safe fallback for missing user',
            suggested_fix='Use .get() with default',
            fix_confidence='HIGH',
            review_priority='URGENT',
            ttl_days=14,
            hit_count=3,
            thread_name='worker-1',
            related_incidents=['abc123'],
            stack_trace=['File "views.py", line 99, in get_profile'],
        )
        self.logger.log_incident(original)
        results = self.logger.get_today_incidents()
        assert len(results) == 1
        r = results[0]
        assert r.exception_type == 'KeyError'
        assert r.exception_message == "'user_id'"
        assert r.module == 'myapp.views'
        assert r.function == 'get_profile'
        assert r.line_number == 99
        assert r.severity == Severity.HIGH
        assert r.status == IncidentStatus.REWOUND
        assert r.guard_type == 'key_guard'
        assert r.default_value == 'anonymous'
        assert r.default_rationale == 'Safe fallback for missing user'
        assert r.suggested_fix == 'Use .get() with default'
        assert r.ttl_days == 14
        assert r.hit_count == 3
        assert r.thread_name == 'worker-1'
        assert r.related_incidents == ['abc123']
        assert 'views.py' in r.stack_trace[0]

    def test_corrupted_jsonl_line_is_skipped(self):
        """A malformed JSON line in the log file must be silently skipped."""
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        path = os.path.join(self.log_dir, f'incidents_{today}.jsonl')
        os.makedirs(self.log_dir, exist_ok=True)

        good_rec = IncidentRecord(exception_type='RealError', function='real_fn')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('{"this": is not valid json!!!\n')
            json.dump(good_rec.to_dict(), f, default=str)
            f.write('\n')
            f.write('')

        results = self.logger.get_today_incidents()

        assert len(results) == 1
        assert results[0].exception_type == 'RealError'

    def test_empty_log_file_returns_no_incidents(self):
        """An empty JSONL file should not crash and return []."""
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        path = os.path.join(self.log_dir, f'incidents_{today}.jsonl')
        os.makedirs(self.log_dir, exist_ok=True)
        open(path, 'w').close()

        results = self.logger.get_today_incidents()
        assert results == []

    def test_query_by_status(self):
        """Filtering by IncidentStatus returns only matching records."""
        self.logger.log_incident(IncidentRecord(status=IncidentStatus.REWOUND, function='a'))
        self.logger.log_incident(IncidentRecord(status=IncidentStatus.PATCHED, function='b'))
        self.logger.log_incident(IncidentRecord(status=IncidentStatus.REWOUND, function='c'))

        rewound = self.logger.get_incidents(status=IncidentStatus.REWOUND)
        assert len(rewound) == 2
        assert all(r.status == IncidentStatus.REWOUND for r in rewound)

    def test_query_function_is_case_insensitive(self):
        """Function filter should match regardless of case."""
        self.logger.log_incident(IncidentRecord(function='GetUserProfile'))
        self.logger.log_incident(IncidentRecord(function='other_fn'))

        results = self.logger.get_incidents(function='getuserprofile')
        assert len(results) == 1

    def test_get_incident_count_empty(self):
        """get_incident_count on empty log returns zeros."""
        counts = self.logger.get_incident_count()
        assert counts['total'] == 0
        assert counts['CRITICAL'] == 0
        assert counts['HIGH'] == 0
        assert counts['MEDIUM'] == 0
        assert counts['LOW'] == 0

    def test_get_incident_count_all_severities(self):
        """Each severity contributes correctly to the count dict."""
        for sev in Severity:
            self.logger.log_incident(IncidentRecord(severity=sev))

        counts = self.logger.get_incident_count()
        assert counts['total'] == 4
        for sev in Severity:
            assert counts[sev.value] == 1

    def test_log_returns_incident_id(self):
        """log_incident must return the incident_id string."""
        rec = IncidentRecord()
        returned_id = self.logger.log_incident(rec)
        assert returned_id == rec.incident_id

    def test_jsonl_file_has_one_line_per_incident(self):
        """Each logged incident must occupy exactly one line in the JSONL file."""
        for i in range(10):
            self.logger.log_incident(IncidentRecord(function=f'fn{i}'))

        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        path = os.path.join(self.log_dir, f'incidents_{today}.jsonl')
        with open(path, 'r', encoding='utf-8') as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 10

    def test_since_filter_excludes_old_incidents(self):
        """Incidents from before 'since' must not be returned."""

        yesterday = datetime.now(timezone.utc) - timedelta(days=2)
        yesterday_str = yesterday.strftime('%Y-%m-%d')
        old_path = os.path.join(self.log_dir, f'incidents_{yesterday_str}.jsonl')
        old_rec = IncidentRecord(function='old_fn')
        old_rec.timestamp = yesterday.isoformat()
        with open(old_path, 'w', encoding='utf-8') as f:
            json.dump(old_rec.to_dict(), f, default=str)
            f.write('\n')

        self.logger.log_incident(IncidentRecord(function='new_fn'))

        since = datetime.now(timezone.utc) - timedelta(days=3)
        all_results = self.logger.get_incidents(since=since)
        assert len(all_results) == 2

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_results = self.logger.get_incidents(since=today_start)
        assert len(today_results) == 1
        assert today_results[0].function == 'new_fn'

class TestIncidentLoggerConcurrency:

    def test_concurrent_log_no_corruption(self, tmp_path):
        """50 threads logging simultaneously must produce 50 valid JSONL lines."""
        log_dir = str(tmp_path / 'concurrent_incidents')
        logger = IncidentLogger(log_dir=log_dir)
        errors = []

        def worker(n):
            try:
                rec = IncidentRecord(function=f'fn_{n}', exception_type='Error')
                logger.log_incident(rec)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Threads raised: {errors}"

        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        path = os.path.join(log_dir, f'incidents_{today}.jsonl')
        with open(path, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if l.strip()]

        assert len(lines) == 50

        for line in lines:
            obj = json.loads(line)
            assert 'incident_id' in obj
            assert 'function' in obj

    def test_concurrent_log_unique_incident_ids(self, tmp_path):
        """All 50 logged incidents must have unique IDs even under contention."""
        log_dir = str(tmp_path / 'unique_ids')
        logger = IncidentLogger(log_dir=log_dir)
        ids = []
        lock = threading.Lock()

        def worker():
            rec = IncidentRecord()
            logger.log_incident(rec)
            with lock:
                ids.append(rec.incident_id)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(ids) == len(set(ids)), "Duplicate incident IDs generated"

class TestDigestGeneratorHard:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.log_dir = str(tmp_path / 'incidents')
        self.logger = IncidentLogger(log_dir=self.log_dir)
        self.gen = DigestGenerator(self.logger)

    def test_severity_counts_exact(self):
        """Summary line must show exact counts for each severity."""
        self.logger.log_incident(IncidentRecord(severity=Severity.CRITICAL, function='a'))
        self.logger.log_incident(IncidentRecord(severity=Severity.CRITICAL, function='b'))
        self.logger.log_incident(IncidentRecord(severity=Severity.HIGH, function='c'))
        self.logger.log_incident(IncidentRecord(severity=Severity.MEDIUM, function='d'))

        content = self.gen.generate_daily()
        assert 'Total incidents:** 4' in content
        assert 'CRITICAL:** 2' in content
        assert 'HIGH:** 1' in content
        assert 'MEDIUM:** 1' in content
        assert 'LOW:** 0' in content

    def test_incident_table_contains_all_incidents(self):
        """The All Incidents table must have a row for every logged incident."""
        funcs = ['alpha', 'beta', 'gamma', 'delta']
        for fn in funcs:
            self.logger.log_incident(IncidentRecord(function=fn, guard_type='null_guard'))

        content = self.gen.generate_daily()
        for fn in funcs:
            assert fn in content, f"Function '{fn}' missing from digest table"

    def test_critical_high_section_only_shows_high_and_critical(self):
        """The CRITICAL & HIGH section must NOT contain MEDIUM or LOW incidents."""
        self.logger.log_incident(IncidentRecord(
            function='high_fn', severity=Severity.HIGH, guard_type='chain_subscript_guard'))
        self.logger.log_incident(IncidentRecord(
            function='low_fn', severity=Severity.LOW, guard_type='file_guard'))

        content = self.gen.generate_daily()
        assert 'high_fn' in content

        lines = content.split('\n')
        in_section = False
        for line in lines:
            if '## CRITICAL' in line:
                in_section = True
            if in_section and line.startswith('## ') and 'CRITICAL' not in line:
                in_section = False
            if in_section and 'low_fn' in line and line.startswith('###'):
                pytest.fail("low_fn appeared as a header in CRITICAL & HIGH section")

    def test_repeat_offender_requires_at_least_two_occurrences(self):
        """A function patched only once must NOT appear in Recommended Actions."""
        self.logger.log_incident(IncidentRecord(function='once_only'))
        self.logger.log_incident(IncidentRecord(function='twice_fn'))
        self.logger.log_incident(IncidentRecord(function='twice_fn'))

        content = self.gen.generate_daily()
        assert 'twice_fn' in content

        if 'Recommended Actions' in content:
            actions_section = content.split('## Recommended Actions')[1]
            assert 'once_only' not in actions_section

    def test_suggested_fix_appears_in_digest(self):
        """Incidents with suggested_fix must show the fix block in CRITICAL/HIGH section."""
        self.logger.log_incident(IncidentRecord(
            function='pay_handler',
            severity=Severity.CRITICAL,
            guard_type='callable_guard',
            suggested_fix='if callable(fn): fn() else: return default',
        ))
        content = self.gen.generate_daily()
        assert 'Suggested fix:' in content
        assert 'if callable(fn)' in content

    def test_no_replayed_line_when_none_replayed(self):
        """'HTTP transactions replayed' line must not appear if none were replayed."""
        self.logger.log_incident(IncidentRecord(status=IncidentStatus.PATCHED))
        content = self.gen.generate_daily()
        assert 'HTTP transactions replayed' not in content

    def test_replayed_line_when_some_replayed(self):
        """'HTTP transactions replayed' line must appear when replayed count > 0."""
        self.logger.log_incident(IncidentRecord(status=IncidentStatus.REPLAYED))
        self.logger.log_incident(IncidentRecord(status=IncidentStatus.PATCHED))
        content = self.gen.generate_daily()
        assert 'HTTP transactions replayed:** 1' in content

    def test_weekly_digest_spans_7_days(self):
        """Weekly digest must aggregate incidents from the last 7 days."""

        five_ago = datetime.now(timezone.utc) - timedelta(days=5)
        date_str = five_ago.strftime('%Y-%m-%d')
        path = os.path.join(self.log_dir, f'incidents_{date_str}.jsonl')
        old_rec = IncidentRecord(function='old_weekly_fn')
        old_rec.timestamp = five_ago.isoformat()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(old_rec.to_dict(), f, default=str)
            f.write('\n')

        self.logger.log_incident(IncidentRecord(function='today_fn'))
        content = self.gen.generate_weekly()
        assert 'old_weekly_fn' in content
        assert 'today_fn' in content

    def test_save_and_load_digest_is_idempotent(self):
        """Saving a digest file and reading it back produces identical content."""
        self.logger.log_incident(IncidentRecord(function='save_test'))
        content = self.gen.generate_daily()
        path = self.gen.save_digest(content, 'idempotent_test.md')
        with open(path, encoding='utf-8') as f:
            loaded = f.read()
        assert loaded == content

    def test_generate_and_save_daily_returns_real_path(self):
        """generate_and_save_daily must return a path that exists on disk."""
        path = self.gen.generate_and_save_daily()
        assert os.path.isfile(path), f"Expected file at {path}"

    def test_generate_and_save_weekly_filename_contains_week(self):
        """Weekly digest filename must include the ISO week number."""
        path = self.gen.generate_and_save_weekly()
        filename = os.path.basename(path)
        assert 'digest_weekly_' in filename
        assert os.path.isfile(path)

    def test_digest_is_valid_markdown(self):
        """Digest must start with # (h1) and contain at least one ## section."""
        self.logger.log_incident(IncidentRecord(function='md_test'))
        content = self.gen.generate_daily()
        lines = content.strip().split('\n')
        assert lines[0].startswith('# '), "Digest must begin with h1"
        assert any(line.startswith('## ') for line in lines), "Digest must have at least one h2"

    def test_digest_unique_patterns_count(self):
        """Unique crash patterns = unique fingerprints, not unique incidents."""
        for _ in range(5):
            rec = IncidentRecord(function='repeat_fn')
            rec.fingerprint = 'same_fp_abc123'
            self.logger.log_incident(rec)
        rec2 = IncidentRecord(function='other_fn')
        rec2.fingerprint = 'different_fp'
        self.logger.log_incident(rec2)

        content = self.gen.generate_daily()
        assert 'Unique crash patterns:** 2' in content

    def test_empty_fingerprint_not_counted_as_pattern(self):
        """Incidents with empty fingerprint must not count as a unique pattern."""
        self.logger.log_incident(IncidentRecord(function='no_fp', fingerprint=''))
        self.logger.log_incident(IncidentRecord(function='has_fp', fingerprint='abc123'))
        content = self.gen.generate_daily()

        assert 'Unique crash patterns:** 1' in content
