"""Source-level fix suggestion engine.

Reverse-maps bytecode guards applied by CodeSuture to human-readable
source-level fixes with diffs, explanations, and confidence levels.
"""

import inspect
import os
import linecache
import textwrap
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class FixSuggestion:
    """A source-level fix suggestion for a patched function."""
    function_name: str = ""
    file_path: str = ""
    line_number: int = 0
    original_line: str = ""            # The source line that crashed
    suggested_line: str = ""            # The fix
    explanation: str = ""               # Human-readable explanation
    confidence: str = "LIKELY"          # "VERIFIED" / "LIKELY" / "EXPERIMENTAL"
    guard_type: str = ""                # What bytecode guard was applied
    target_variable: str = ""           # The variable that was None/missing/etc.
    diff: str = ""                      # Unified diff format


# ──────────────────────────────────────────────────────────────────────
# Guard-type → source-fix mapping rules
# ──────────────────────────────────────────────────────────────────────

_FIX_TEMPLATES = {
    'null_guard': {
        'pattern': '{target}',
        'fix_attr': '{line_stripped} if {owner} is not None else {default}',
        'fix_simple': '{line_stripped} if {target} is not None else {default}',
        'explanation': '`{target}` can be None. Add a None check with fallback to `{default}`.',
    },
    'key_guard': {
        'fix': '{container}.get({key}, {default})',
        'explanation': 'Key `{key}` may be missing. Use `.get()` with a default instead of `[]`.',
    },
    'subscript_guard': {
        'fix': '{line_stripped} if {target} is not None else {default}',
        'explanation': '`{target}` can be None before subscripting. Add a None check.',
    },
    'chain_subscript_guard': {
        'fix': '{line_stripped} if {target} is not None else {default}',
        'explanation': 'Nested subscript on `{target}` which can be None. Add a None check at the chain root.',
    },
    'division_guard': {
        'fix': '{line_stripped} if {target} != 0 else {default}',
        'explanation': '`{target}` can be zero, causing ZeroDivisionError. Add a zero check.',
    },
    'index_guard': {
        'fix': '{line_stripped} if len({target}) > {index} else {default}',
        'explanation': 'Index may be out of bounds for `{target}`. Add a bounds check.',
    },
    'list_bound_guard': {
        'fix': '{line_stripped} if len({target}) > {index} else {default}',
        'explanation': 'Constant index `{index}` may exceed the length of `{target}`. Add a bounds check.',
    },
    'type_coercion_guard': {
        'fix': '{func}({target}) if isinstance({target}, (int, float, str)) else {default}',
        'explanation': '`{target}` may not be convertible. Add a type check before conversion.',
    },
    'file_guard': {
        'fix': '{line_stripped} if os.path.exists({target}) else {default}',
        'explanation': 'File `{target}` may not exist. Add an existence check.',
    },
    'str_coerce_guard': {
        'fix': '{left} + str({right})',
        'explanation': 'String concatenation with non-string. Use explicit `str()` conversion.',
    },
    'callable_guard': {
        'fix': '{target}({args}) if {target} is not None and callable({target}) else {default}',
        'explanation': '`{target}` may be None or not callable. Add a guard before calling.',
    },
    'return_guard': {
        'fix': 'result = {call}; result if result is not None else {default}',
        'explanation': 'Function may return None. Add a None check on the return value.',
    },
}


def generate_suggestion(incident) -> Optional[FixSuggestion]:
    """Generate a source-level fix suggestion from an incident record.

    Args:
        incident: An IncidentRecord (or any object with guard_type, function,
                  file_path, line_number, target_variable, default_value,
                  exception_type, exception_message attributes).

    Returns:
        A FixSuggestion if a fix could be generated, None otherwise.
    """
    guard_type = getattr(incident, 'guard_type', '')
    if not guard_type or guard_type not in _FIX_TEMPLATES:
        return None

    file_path = getattr(incident, 'file_path', '')
    line_number = getattr(incident, 'line_number', 0)
    target = getattr(incident, 'target_variable', '') or 'var'
    default_value = getattr(incident, 'default_value', None)
    function_name = getattr(incident, 'function', '') or 'unknown'
    exc_msg = getattr(incident, 'exception_message', '')
    shadow_verified = getattr(incident, 'shadow_verified', False)

    # Read the original source line
    original_line = ''
    if file_path and line_number > 0 and os.path.isfile(file_path):
        original_line = linecache.getline(file_path, line_number).rstrip()
    
    if not original_line:
        original_line = f'# (source line {line_number} not available)'

    # Build the fix
    line_stripped = original_line.strip()
    indent = original_line[:len(original_line) - len(original_line.lstrip())]
    default_repr = repr(default_value)
    template = _FIX_TEMPLATES[guard_type]

    # Format variables for templates
    fmt_vars = {
        'target': target,
        'default': default_repr,
        'line_stripped': line_stripped,
        'owner': _extract_owner(target, exc_msg),
        'container': _extract_container(line_stripped, target),
        'key': repr(target),
        'index': '0',
        'func': _extract_func(line_stripped),
        'left': _extract_left_operand(line_stripped),
        'right': _extract_right_operand(line_stripped, target),
        'call': line_stripped,
        'args': '',
    }

    # Generate the suggested line
    if guard_type == 'null_guard' and '.' in target:
        suggested_stripped = template['fix_attr'].format(**fmt_vars)
    elif guard_type == 'null_guard':
        suggested_stripped = template.get('fix_simple', template.get('fix_attr', line_stripped)).format(**fmt_vars)
    elif guard_type == 'key_guard':
        suggested_stripped = _build_key_guard_fix(line_stripped, target, default_repr)
    elif guard_type == 'str_coerce_guard':
        suggested_stripped = _build_str_coerce_fix(line_stripped, target)
    else:
        fix_template = template.get('fix', '{line_stripped}')
        suggested_stripped = fix_template.format(**fmt_vars)

    suggested_line = indent + suggested_stripped
    explanation = template['explanation'].format(**fmt_vars)

    # Build unified diff
    diff = _build_diff(file_path, line_number, original_line, suggested_line)

    # Determine confidence
    if shadow_verified:
        # Shadow execution confirmed the patch is justified and fix works
        confidence = 'VERIFIED'
    elif guard_type in ('null_guard', 'key_guard', 'division_guard'):
        confidence = 'LIKELY'
    elif guard_type in ('callable_guard', 'type_coercion_guard'):
        confidence = 'EXPERIMENTAL'
    else:
        confidence = 'LIKELY'

    return FixSuggestion(
        function_name=function_name,
        file_path=file_path,
        line_number=line_number,
        original_line=original_line,
        suggested_line=suggested_line,
        explanation=explanation,
        confidence=confidence,
        guard_type=guard_type,
        target_variable=target,
        diff=diff,
    )


def format_suggestion(suggestion: FixSuggestion) -> str:
    """Format a FixSuggestion as a human-readable string."""
    lines = [
        f'# Fix suggestion for {suggestion.function_name}()',
        f'# File: {suggestion.file_path}, Line: {suggestion.line_number}',
        f'# Guard: {suggestion.guard_type} on \'{suggestion.target_variable}\'',
        f'# Confidence: {suggestion.confidence}',
        '',
        f'# Original:',
        suggestion.original_line,
        '',
        f'# Suggested fix:',
        suggestion.suggested_line,
        '',
        f'# Explanation:',
        f'# {suggestion.explanation}',
    ]
    if suggestion.diff:
        lines.extend(['', '# Diff:', suggestion.diff])
    return '\n'.join(lines)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _extract_owner(target: str, exc_msg: str) -> str:
    """Extract the owner object from a dotted attribute target.

    For 'user.profile.bio' returns 'user.profile' (the owner of .bio).
    For non-dotted targets like 'data', returns 'data' unchanged —
    there is no owner to extract when the target itself is the object.
    """
    if '.' in target:
        return target.rsplit('.', 1)[0]
    return target


def _extract_container(line: str, target: str) -> str:
    """Extract the container variable from a subscript expression."""
    import re
    m = re.search(r'(\w+)\s*\[', line)
    if m:
        return m.group(1)
    return target


def _extract_func(line: str) -> str:
    """Extract the function name from a call expression."""
    import re
    m = re.search(r'(\w+)\s*\(', line)
    if m:
        return m.group(1)
    return 'func'


def _extract_left_operand(line: str) -> str:
    """Extract left operand of a + expression."""
    if '+' in line:
        return line.split('+')[0].strip().split('=')[-1].strip()
    return '""'


def _extract_right_operand(line: str, target: str) -> str:
    """Extract right operand of a + expression."""
    if '+' in line:
        parts = line.split('+')
        if len(parts) >= 2:
            return parts[-1].strip()
    return target


def _build_key_guard_fix(line: str, key: str, default: str) -> str:
    """Build a .get() fix for KeyError."""
    import re
    # Match patterns like data["key"] or cfg['key'] or obj[key]
    pattern = r'(\w+)\s*\[\s*[\'\"]?' + re.escape(key) + r'[\'\"]?\s*\]'
    m = re.search(pattern, line)
    if m:
        container = m.group(1)
        return re.sub(pattern, f'{container}.get({repr(key)}, {default})', line)
    # Fallback: simple substitution
    return line.replace(f'["{key}"]', f'.get("{key}", {default})').replace(f"['{key}']", f".get('{key}', {default})")


def _build_str_coerce_fix(line: str, target: str) -> str:
    """Build str() wrapping fix for TypeError on string concat.

    Wraps only the specific target variable with str(), preserving
    chained expressions and string literals. 'label + count + suffix'
    with target='count' produces 'label + str(count) + suffix'.
    """
    import re
    # Match target as a standalone identifier NOT inside quotes.
    # Strategy: split the line into quoted and unquoted segments,
    # only substitute in unquoted segments.
    parts = re.split(r'''(["'][^"']*["'])''', line)
    found = False
    pattern = rf'\b{re.escape(target)}\b'
    for i, part in enumerate(parts):
        # Odd indices are quoted strings — skip them
        if i % 2 == 0 and re.search(pattern, part):
            parts[i] = re.sub(pattern, f'str({target})', part, count=1)
            found = True
            break
    if found:
        return ''.join(parts)
    return f'str({line.strip()})'


def _build_diff(file_path: str, line_number: int, original: str, suggested: str) -> str:
    """Build a unified diff snippet."""
    basename = os.path.basename(file_path) if file_path else 'source.py'
    return (
        f'--- a/{basename}\n'
        f'+++ b/{basename}\n'
        f'@@ -{line_number},1 +{line_number},1 @@\n'
        f'-{original}\n'
        f'+{suggested}'
    )
