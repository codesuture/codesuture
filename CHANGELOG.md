# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.1] - 2026-05-25

### Fixed — Safety Hardening
- **Index guard no longer returns wrong item:** `index_guard` now returns a
  type-appropriate default value instead of clamping the index to 0 (which
  silently returned `list[0]` — a data corruption bug)
- **Callable guard no longer replaces unknown functions with `lambda: 0`:**
  Unknown callables are skipped with a warning instead of being silently
  replaced with a stub that could bypass validation logic
- **Exception tracebacks shown by default:** `_codesuture_excepthook` now
  prints a structured summary of self-healed exceptions instead of
  suppressing all output; use `--silent` to restore old behavior
- **Rollback restores runtime code:** `codesuture rollback` now restores
  original bytecode in memory (not just deletes files); original code
  objects are backed up before patching and saved as `.orig.code` files
- **Marshal integrity checks:** Persisted `.code` files now include SHA-256
  checksums in metadata; tampered files are refused on load
- **Thread safety:** Added locking on `HEALED_FUNCTIONS`,
  `ANNOUNCED_HEALED_FUNCTIONS`, `_retried_exc_types`, fingerprint registry,
  and `CodeSutureMetaFinder` recursion guard (now uses `threading.local()`)
- **`_INLINE_STRATEGIES` set populated:** Try-block detection for inline
  strategies (`subscript_guard`, `key_guard`, `division_guard`,
  `chain_subscript_guard`) is now active — previously disabled by empty set
- **Safe tuple propagation:** Replaced dangerous `ctypes` memory write into
  tuple `ob_item` array with safe `code.replace(co_consts=...)` approach

### Added — CPython 3.12+ Compatibility
- **`codesuture/opcodes.py`:** Version-aware opcode abstraction layer
  providing correct opcode names, instruction builders, and opcode name
  sets for Python 3.11, 3.12, and 3.13+
- All guard builders now use `opcodes.py` instead of hardcoded 3.11-only
  opcode names (`PRECALL`, `POP_JUMP_FORWARD_IF_FALSE`, `LOAD_METHOD`)
- Pattern matcher uses opcode sets for resilient bytecode analysis across
  Python versions
- Frame rewind offset detection is now runtime-detected instead of
  hardcoded to CPython 3.11 struct layout (`id(frame) + 40`)
- CLI: added `--silent` flag to `codesuture run`

### Changed
- Removed old test files, v4test/ verification suite, and heavily-mocked
  tracer test; replaced with comprehensive new test suite

### Fixed — Audit Round (Post-Release)
- **`_eval_fix.py`:** Fixed `ImportError` — was importing `apply_fix_with_info`
  which doesn't exist; changed to `apply_fix`
- **`_build_subscript_guarded_code`:** No longer calls `.get()` on lists/tuples;
  now checks `isinstance(container, dict)` before using `.get()`, falls back to
  direct subscript for non-dict containers
- **`rollback_runtime`:** Removed dead `gc.get_referrers` loop that had only `pass`
  in its body
- **`middleware.py`:** Retry tracking now uses `(exc_type, filename, func_name)`
  tuple instead of bare exception type — prevents second crash of same type in
  different function from being silently ignored
- **`persistence.py`:** Thread name now uses `threading.current_thread().name`
  instead of hardcoded `"MainThread"`; `datetime.utcnow()` replaced with
  `datetime.now(timezone.utc)` (Python 3.12 deprecation fix);
  `_iter_cached_function_names` now excludes `.orig.code` files
- **`audit.py`:** Unicode box-drawing characters now actually used when terminal
  supports them (was identical ASCII `|` on both branches);
  `datetime.utcnow()` deprecation fixed
- **`rollback.py`:** `datetime.utcnow()` replaced with timezone-aware datetime
  (was causing `TypeError` when comparing with timezone-aware `patched_at` values)
- **`tracer.py`:** `--silent` flag now gates ALL 20+ informational print statements
  (fingerprint hits, patch applied, already healed, etc.); errors/warnings still
  always print
- **`opcodes.py`:** Added Python 3.13 compatibility documentation; expanded
  `SUBSCRIPT_OPCODES` to include `BINARY_SLICE` for 3.13 slice operations

## [0.7.0] - 2026-05-17

### Added
- Active Shield: after patching, the engine re-invokes the function with
  original arguments to save the current transaction (eliminates
  ERR_EMPTY_RESPONSE on first request to patched server endpoints)
- Python 3.12+ sys.monitoring dual engine: zero baseline overhead,
  callback fires only on RAISE events instead of line-by-line tracing
- Transaction fallback: graceful JSON 500 response for network handlers
  when re-invocation fails, preventing hanging socket connections

## [0.6.0] - 2026-05-12

### Fixed
- PEP 659: Force de-specialization after `__code__` swap via
  `ctypes.pythonapi.PyFunction_SetCode` — prevents CPython 3.11+
  adaptive bytecode cache from ignoring injected patches
- Thread blindness: Install trace hook on all threads via
  `threading.settrace` at startup, with `_install_trace_on_all_threads`
  helper covering existing and future threads; added `threading.Lock`
  for thread-safe patch store writes
- Exception table corruption: Guard injection now detects try/except
  scope via `TryBegin`/`TryEnd` markers and redirects to function
  entry-point injection to avoid corrupting `co_exceptiontable` offsets
  in CPython 3.11+

## [0.5.1] - 2026-05-11

### Fixed
- propagate_patch: skip list/dict/set/generator comprehensions
  instead of crashing with AttributeError on __code__
- key_guard, subscript_guard, chain_subscript_guard: infer
  correct default type from downstream bytecode usage
  (string methods -> "" default, numeric ops -> 0 default)
- KeyError on chained subscripts (e.g. request["headers"]["auth"].strip())
  now produces a chain_subscript_guard instead of a simple key_guard,
  preventing secondary TypeError from None subscript access

## [0.5.0] - 2026-05-08

### Added
- Async/await support (CO_COROUTINE frame detection) — automatic `RESUME 0` preservation for coroutine bytecode patching.
- Watch mode: `codesuture watch --max-restarts N` — subprocess loop with automatic crash-patch-restart cycle.
- Explain command: `codesuture explain [func_name]` — detailed table of active patches with safety assessment (LIKELY/RISKY/UNKNOWN).
- WSGI middleware: `CodeSutureMiddleware` — intercepts request handler exceptions, patches, and retries with `X-CodeSuture` response header.

## [0.4.0] - 2026-05-07

### Added
- `codesuture rollback` command to selectively remove persisted patches (`codesuture rollback <func>`, `--all`, and `--dry-run`).
- Three new guard types:
  - `type_coercion_guard` for `TypeError` and `ValueError` during type conversions.
  - `index_guard` for `IndexError` bounds checking.
  - `key_guard` for safe dictionary `KeyError` fallbacks.
- Enhanced `--dry-run` mode with confidence levels (HIGH/MEDIUM/LOW) based on fingerprint registry hits.
- Full PyPI packaging structure (`pyproject.toml`, complete `README.md`, `CHANGELOG.md`).

### Changed
- Migrated legacy guards `list_bound_guard` to `index_guard` and `dict_get_guard` to `key_guard` for consistency.
- Standardized CLI output format and improved error reporting.

## [0.3.0] - 2026-05-06

### Added
-  Upgrade D1: Semantic diff safety gate to prevent runaway bytecode corruption.
-  Upgrade D2: Caller-aware patch propagation to automatically fix closures and bound methods in-memory.
-  Upgrade D3: Shadow execution mode (`--shadow`) to monitor and warn when sentinel defaults leak downstream.
-  Upgrade D4: Patch expiry TTL warnings to nudge developers toward source-level fixes.
-  Upgrade D5: Bytecode fingerprint registry for instant cache hits on known crash patterns.
-  Upgrade D6: `codesuture audit` command for viewing all active patches in a formatted table.

### Fixed
- Addressed Windows `UnicodeDecodeError` and `cp1252` terminal limitations by enforcing `utf-8` encoding.
- Resolved a race condition where patch persistence was executing after the code object swap, preventing correct caller identification.
- Fixed namespace pollution during nested patching.
