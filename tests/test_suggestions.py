"""Tests for codesuture.suggest — fix suggestion engine."""

import os
import tempfile
import pytest
from unittest.mock import MagicMock

from codesuture.suggest import (
    generate_suggestion, format_suggestion, FixSuggestion,
    _build_diff, _build_key_guard_fix, _build_str_coerce_fix,
    _extract_owner, _extract_container, _extract_func,
)

class TestFixSuggestion:
    def test_defaults(self):
        s = FixSuggestion()
        assert s.confidence == "LIKELY"
        assert s.guard_type == ""
        assert s.diff == ""

class TestGenerateSuggestion:
    def _make_incident(self, **kwargs):
        """Create a mock incident with reasonable defaults."""
        defaults = {
            'guard_type': 'null_guard',
            'function': 'get_bio',
            'file_path': '',
            'line_number': 0,
            'target_variable': 'profile',
            'default_value': '',
            'exception_type': 'AttributeError',
            'exception_message': "'NoneType' has no attribute 'bio'",
            'shadow_verified': False,
        }
        defaults.update(kwargs)
        inc = MagicMock()
        for k, v in defaults.items():
            setattr(inc, k, v)
        return inc

    def test_null_guard_suggestion(self):
        inc = self._make_incident(guard_type='null_guard', target_variable='profile')
        s = generate_suggestion(inc)
        assert s is not None
        assert s.guard_type == 'null_guard'
        assert 'is not None' in s.suggested_line or 'None' in s.explanation
        assert s.confidence == 'LIKELY'

    def test_key_guard_suggestion(self):
        inc = self._make_incident(guard_type='key_guard', target_variable='timeout',
                                  default_value=30)
        s = generate_suggestion(inc)
        assert s is not None
        assert s.guard_type == 'key_guard'
        assert '.get(' in s.explanation or 'missing' in s.explanation

    def test_division_guard_suggestion(self):
        inc = self._make_incident(guard_type='division_guard', target_variable='count',
                                  default_value=0)
        s = generate_suggestion(inc)
        assert s is not None
        assert '!= 0' in s.suggested_line or 'zero' in s.explanation

    def test_callable_guard_is_experimental(self):
        inc = self._make_incident(guard_type='callable_guard', target_variable='handler')
        s = generate_suggestion(inc)
        assert s is not None
        assert s.confidence == 'EXPERIMENTAL'

    def test_type_coercion_guard(self):
        inc = self._make_incident(guard_type='type_coercion_guard', target_variable='value',
                                  default_value=0)
        s = generate_suggestion(inc)
        assert s is not None
        assert 'isinstance' in s.suggested_line or 'type' in s.explanation
        assert s.confidence == 'EXPERIMENTAL'

    def test_unknown_guard_returns_none(self):
        inc = self._make_incident(guard_type='unicorn_guard')
        s = generate_suggestion(inc)
        assert s is None

    def test_empty_guard_returns_none(self):
        inc = self._make_incident(guard_type='')
        s = generate_suggestion(inc)
        assert s is None

    def test_with_real_source_file(self, tmp_path):
        """When a real source file exists, reads the actual line."""
        src = tmp_path / "test_script.py"
        src.write_text("def foo():\n    bio = user.profile.bio.strip()\n    return bio\n")

        inc = self._make_incident(
            file_path=str(src),
            line_number=2,
            guard_type='null_guard',
            target_variable='user.profile',
        )
        s = generate_suggestion(inc)
        assert s is not None
        assert 'bio' in s.original_line
        assert s.line_number == 2
        assert s.file_path == str(src)

    def test_diff_format(self, tmp_path):
        """Generates a unified diff."""
        src = tmp_path / "handler.py"
        src.write_text("def get():\n    return data['key']\n")

        inc = self._make_incident(
            file_path=str(src),
            line_number=2,
            guard_type='key_guard',
            target_variable='key',
            default_value=None,
        )
        s = generate_suggestion(inc)
        assert s is not None
        assert s.diff.startswith('--- a/')
        assert '+++ b/' in s.diff
        assert '-' in s.diff
        assert '+' in s.diff

    def test_subscript_guard(self):
        inc = self._make_incident(guard_type='subscript_guard', target_variable='data',
                                  default_value=None)
        s = generate_suggestion(inc)
        assert s is not None
        assert 'None' in s.explanation

    def test_index_guard(self):
        inc = self._make_incident(guard_type='index_guard', target_variable='items',
                                  default_value=None)
        s = generate_suggestion(inc)
        assert s is not None
        assert 'bounds' in s.explanation

    def test_file_guard(self):
        inc = self._make_incident(guard_type='file_guard', target_variable='/tmp/x.txt',
                                  default_value=None)
        s = generate_suggestion(inc)
        assert s is not None
        assert 'exist' in s.explanation

    def test_str_coerce_guard(self):
        inc = self._make_incident(guard_type='str_coerce_guard', target_variable='count')
        s = generate_suggestion(inc)
        assert s is not None
        assert 'str()' in s.explanation

    def test_all_guard_types_produce_suggestions(self):
        """Every template in _FIX_TEMPLATES should produce a suggestion."""
        from codesuture.suggest import _FIX_TEMPLATES
        for guard_type in _FIX_TEMPLATES:
            inc = self._make_incident(guard_type=guard_type, target_variable='x',
                                      default_value=0)
            s = generate_suggestion(inc)
            assert s is not None, f"{guard_type} produced None"
            assert s.guard_type == guard_type

class TestFormatSuggestion:
    def test_format_output(self):
        s = FixSuggestion(
            function_name='get_bio',
            file_path='handlers.py',
            line_number=42,
            original_line='    bio = user.profile.bio',
            suggested_line='    bio = user.profile.bio if user.profile is not None else ""',
            explanation='profile can be None',
            confidence='LIKELY',
            guard_type='null_guard',
            target_variable='profile',
            diff='--- a/handlers.py\n+++ b/handlers.py\n@@ -42,1 +42,1 @@\n-    bio = user.profile.bio\n+    bio = user.profile.bio if user.profile is not None else ""',
        )
        output = format_suggestion(s)
        assert 'get_bio' in output
        assert 'handlers.py' in output
        assert 'LIKELY' in output
        assert 'Original:' in output
        assert 'Suggested fix:' in output

class TestHelpers:
    def test_extract_owner_dotted(self):
        assert _extract_owner('user.profile', '') == 'user'

    def test_extract_owner_deeply_dotted(self):
        assert _extract_owner('request.user.profile', '') == 'request.user'

    def test_extract_owner_simple_with_exc_msg(self):
        result = _extract_owner('data', "'NoneType' has no attribute 'bio'")
        assert result == 'data'

    def test_extract_owner_simple_no_match(self):
        result = _extract_owner('data', 'some random error')
        assert result == 'data'

    def test_extract_container(self):
        assert _extract_container('config["timeout"]', 'timeout') == 'config'

    def test_extract_func(self):
        assert _extract_func('int(value)') == 'int'

    def test_build_diff(self):
        diff = _build_diff('test.py', 10, '    x = None', '    x = 0')
        assert '--- a/test.py' in diff
        assert '+++ b/test.py' in diff
        assert '@@ -10,1 +10,1 @@' in diff

    def test_build_key_guard_fix_bracket(self):
        result = _build_key_guard_fix('config["timeout"]', 'timeout', 'None')
        assert '.get(' in result

    def test_build_str_coerce_fix(self):
        result = _build_str_coerce_fix('"count: " + count', 'count')
        assert result == '"count: " + str(count)'

    def test_build_str_coerce_fix_chained(self):
        """Regression: chained + must wrap only the target, not the tail."""
        result = _build_str_coerce_fix('label + count + suffix', 'count')
        assert result == 'label + str(count) + suffix', (
            f"Chained + corrupted: got {result!r}"
        )

    def test_build_str_coerce_fix_no_match(self):
        """When target isn't found, wrap the whole expression."""
        result = _build_str_coerce_fix('x + y', 'z')
        assert result == 'str(x + y)'

class TestConfidenceLevels:
    """Tests for confidence level assignment including VERIFIED."""

    def _make_incident(self, **kwargs):
        defaults = {
            'guard_type': 'null_guard',
            'function': 'fn',
            'file_path': '',
            'line_number': 0,
            'target_variable': 'x',
            'default_value': None,
            'exception_type': 'AttributeError',
            'exception_message': '',
            'shadow_verified': False,
        }
        defaults.update(kwargs)
        from unittest.mock import MagicMock
        inc = MagicMock()
        for k, v in defaults.items():
            setattr(inc, k, v)
        return inc

    def test_verified_when_shadow_confirmed(self):
        """VERIFIED confidence is assigned when shadow_verified=True."""
        inc = self._make_incident(shadow_verified=True, guard_type='null_guard')
        s = generate_suggestion(inc)
        assert s is not None
        assert s.confidence == 'VERIFIED'

    def test_likely_for_deterministic_guards(self):
        for guard in ('null_guard', 'key_guard', 'division_guard'):
            inc = self._make_incident(guard_type=guard, shadow_verified=False)
            s = generate_suggestion(inc)
            assert s.confidence == 'LIKELY', f"{guard} should be LIKELY"

    def test_experimental_for_complex_guards(self):
        for guard in ('callable_guard', 'type_coercion_guard'):
            inc = self._make_incident(guard_type=guard, shadow_verified=False)
            s = generate_suggestion(inc)
            assert s.confidence == 'EXPERIMENTAL', f"{guard} should be EXPERIMENTAL"

    def test_verified_overrides_experimental(self):
        """Even EXPERIMENTAL guards become VERIFIED when shadow confirms."""
        inc = self._make_incident(guard_type='callable_guard', shadow_verified=True)
        s = generate_suggestion(inc)
        assert s.confidence == 'VERIFIED'
