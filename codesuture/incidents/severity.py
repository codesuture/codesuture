from codesuture.incidents.incident import Severity

# Module path keywords that escalate severity
_SENSITIVE_MODULES = {'auth', 'payment', 'security', 'admin', 'login', 'credential', 'token', 'secret', 'encrypt', 'decrypt', 'billing', 'transaction'}


def classify_severity(guard_type: str, module: str = '', function: str = '',
                      http_method: str = '', hit_count: int = 0,
                      default_value=None) -> Severity:
    """Classify incident severity based on context."""

    module_lower = module.lower()
    func_lower = function.lower()

    # CRITICAL: callable replacement, sensitive modules
    if guard_type == 'callable_guard':
        return Severity.CRITICAL
    if any(kw in module_lower or kw in func_lower for kw in _SENSITIVE_MODULES):
        if guard_type in ('callable_guard', 'division_guard', 'type_coercion_guard'):
            return Severity.CRITICAL
        return Severity.HIGH

    # HIGH: HTTP replay, complex guards, first occurrence
    if http_method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        return Severity.HIGH
    if guard_type == 'chain_subscript_guard':
        return Severity.HIGH
    if guard_type == 'type_coercion_guard':
        return Severity.HIGH
    if hit_count == 0:  # First occurrence
        return Severity.HIGH

    # MEDIUM: standard guards
    if guard_type in ('null_guard', 'key_guard', 'subscript_guard', 'division_guard', 'index_guard', 'list_bound_guard'):
        if hit_count >= 3:
            return Severity.LOW
        return Severity.MEDIUM

    # LOW: well-known patterns, file guard, string coercion, repeat offenders
    if guard_type in ('file_guard', 'str_coerce_guard'):
        return Severity.LOW
    if hit_count >= 3:
        return Severity.LOW

    return Severity.MEDIUM
