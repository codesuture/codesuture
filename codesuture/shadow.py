"""Shadow execution engine.

After patching, optionally runs the ORIGINAL code in a sandboxed call
and compares the result with the patched version to verify the patch
was necessary.
"""

import threading
import types
import logging
import concurrent.futures
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from enum import Enum

_log = logging.getLogger(__name__)


class ShadowVerdict(Enum):
    PATCH_JUSTIFIED = "patch_justified"      # Original crashed, patch saved it
    PATCH_UNNECESSARY = "patch_unnecessary"  # Original succeeded, patch may not be needed
    PATCH_DIVERGENT = "patch_divergent"      # Original returned different value
    SHADOW_ERROR = "shadow_error"            # Could not run shadow execution
    NOT_RUN = "not_run"                      # Shadow was not executed


@dataclass
class ShadowResult:
    """Result of shadow execution comparison."""
    verdict: ShadowVerdict = ShadowVerdict.NOT_RUN
    original_crashed: bool = False
    original_exception: str = ""
    original_result: Any = None
    patched_result: Any = None
    results_match: bool = False
    function_name: str = ""
    guard_type: str = ""

    def to_dict(self) -> dict:
        return {
            'verdict': self.verdict.value,
            'original_crashed': self.original_crashed,
            'original_exception': self.original_exception,
            'original_result': repr(self.original_result),
            'patched_result': repr(self.patched_result),
            'results_match': self.results_match,
            'function_name': self.function_name,
            'guard_type': self.guard_type,
        }


# ──────────────────────────────────────────────────────────────────────
# Sentinel detection (backward compat)
# ──────────────────────────────────────────────────────────────────────

SENTINEL_VALUES = {"", 0, 0.0, None, False, (), frozenset()}

def is_sentinel(value) -> bool:
    """Check if a value looks like a CodeSuture default sentinel."""
    try:
        # Check unhashable sentinels first
        if value == [] or value == {}:
            return True
        if value in SENTINEL_VALUES:
            return True
    except Exception:
        pass
    return False


def shadow_check(func_name: str, return_value, guard_type: str):
    """Legacy shadow check — warns on sentinel return values."""
    if is_sentinel(return_value):
        print(
            f"[CodeSuture SHADOW] ⚠ {func_name}() returned sentinel "
            f"value {return_value!r} after {guard_type} patch. "
            f"Verify this default is safe for downstream consumers."
        )


# ──────────────────────────────────────────────────────────────────────
# Real shadow executor
# ──────────────────────────────────────────────────────────────────────

class ShadowExecutor:
    """Runs original code alongside patched code to verify patches.

    After a function is patched, the executor stores the original code.
    When shadow mode is active, it runs the original code in a try/except
    with the same arguments and compares outcomes.
    """

    def __init__(self):
        self._original_codes: Dict[str, types.CodeType] = {}
        self._shadow_results: Dict[str, ShadowResult] = {}
        self._lock = threading.Lock()
        self._func_locks: Dict[str, threading.Lock] = {}  # per-function lock for __code__ swap

    def register_original(self, func_key: str, original_code: types.CodeType):
        """Store original code before patching."""
        with self._lock:
            if func_key not in self._original_codes:
                self._original_codes[func_key] = original_code

    def has_original(self, func_key: str) -> bool:
        """Check if we have the original code for a function."""
        return func_key in self._original_codes

    def get_original(self, func_key: str) -> Optional[types.CodeType]:
        """Retrieve stored original code."""
        return self._original_codes.get(func_key)

    def shadow_execute(self, func_key: str, patched_func, patched_result,
                       args: tuple = (), kwargs: dict = None,
                       guard_type: str = '') -> ShadowResult:
        """Run original code with same args and compare.

        Args:
            func_key: Key identifying the function (e.g. "file.py:func_name")
            patched_func: The function object (currently has patched code)
            patched_result: The result from running the patched version
            args: Positional arguments the function was called with
            kwargs: Keyword arguments
            guard_type: The guard type that was applied

        Returns:
            ShadowResult with comparison verdict.
        """
        kwargs = kwargs or {}
        result = ShadowResult(
            function_name=func_key,
            guard_type=guard_type,
            patched_result=patched_result,
        )

        original_code = self._original_codes.get(func_key)
        if original_code is None:
            result.verdict = ShadowVerdict.SHADOW_ERROR
            _log.debug('No original code for %s, skipping shadow', func_key)
            return result

        # Acquire per-function lock to prevent TOCTOU race on __code__ swap
        with self._lock:
            if func_key not in self._func_locks:
                self._func_locks[func_key] = threading.Lock()
            func_lock = self._func_locks[func_key]

        print(f"[CodeSuture] Shadow warning: shallow copy used — nested mutations may affect verdict for {func_key}")

        # Temporarily swap to original code and run (serialized per function)
        with func_lock:
            try:
                current_code = patched_func.__code__
                patched_func.__code__ = original_code
                try:
                    def _shadow_wrapper():
                        import threading
                        threading.current_thread()._is_codesuture_shadow = True
                        try:
                            return patched_func(*args, **kwargs)
                        finally:
                            threading.current_thread()._is_codesuture_shadow = False

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_shadow_wrapper)
                        original_result = future.result(timeout=5.0)
                    
                    result.original_result = original_result
                    result.original_crashed = False

                    # Compare results
                    try:
                        results_match = original_result == patched_result
                    except Exception:
                        results_match = original_result is patched_result

                    result.results_match = results_match

                    if results_match:
                        result.verdict = ShadowVerdict.PATCH_UNNECESSARY
                        _log.info(
                            '[Shadow] %s: original succeeded with same result — '
                            'patch may be unnecessary (transient crash?)', func_key
                        )
                    else:
                        result.verdict = ShadowVerdict.PATCH_DIVERGENT
                        _log.info(
                            '[Shadow] %s: original returned %r, patched returned %r',
                            func_key, original_result, patched_result
                        )

                except concurrent.futures.TimeoutError:
                    result.verdict = ShadowVerdict.SHADOW_ERROR
                    _log.error('[Shadow] Timeout executing original code for %s', func_key)
                except Exception as e:
                    # If it's a TimeoutError from another source or just the actual crash
                    real_e = e.args[0] if isinstance(e, getattr(concurrent.futures, 'TimeoutError', type(None))) else e
                    # Original crashed — this confirms the patch was needed
                    result.original_crashed = True
                    result.original_exception = f"{type(e).__name__}: {e}"
                    result.verdict = ShadowVerdict.PATCH_JUSTIFIED
                    _log.debug('[Shadow] %s: original crashed (%s) — patch justified',
                              func_key, result.original_exception)

                finally:
                    # ALWAYS restore patched code
                    patched_func.__code__ = current_code

            except Exception as e:
                result.verdict = ShadowVerdict.SHADOW_ERROR
                _log.error('[Shadow] Error during shadow execution of %s: %s',
                          func_key, e)

        # Store result
        with self._lock:
            self._shadow_results[func_key] = result

        # Print warning for interesting cases
        if result.verdict == ShadowVerdict.PATCH_UNNECESSARY:
            print(
                f"[CodeSuture SHADOW] ⚠ {func_key}: original code succeeded "
                f"with same result — patch may be unnecessary. "
                f"The crash might have been transient."
            )
        elif result.verdict == ShadowVerdict.PATCH_DIVERGENT:
            print(
                f"[CodeSuture SHADOW] ⚠ {func_key}: original returned "
                f"{result.original_result!r}, patched returned "
                f"{result.patched_result!r}. Results diverge."
            )

        return result

    def get_result(self, func_key: str) -> Optional[ShadowResult]:
        """Get the shadow execution result for a function."""
        return self._shadow_results.get(func_key)

    def get_all_results(self) -> Dict[str, ShadowResult]:
        """Get all shadow execution results."""
        with self._lock:
            return dict(self._shadow_results)

    def clear(self):
        """Clear all stored originals and results."""
        with self._lock:
            self._original_codes.clear()
            self._shadow_results.clear()
            self._func_locks.clear()
