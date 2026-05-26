<p align="center">
  <img src="assets/hero.png" alt="CodeSuture" width="100%">
</p>

<h1 align="center">CodeSuture</h1>

<p align="center">
  <strong>Self-healing runtime for Python. Catches crashes, patches live bytecode, keeps your server alive.</strong>
</p>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/version-1.0.0-0d6efd?style=for-the-badge" alt="Version"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.11%2B-10b981?style=for-the-badge" alt="Python"></a>
  <a href="#"><img src="https://img.shields.io/badge/tests-416%20passing-10b981?style=for-the-badge" alt="Tests"></a>
  <a href="#"><img src="https://img.shields.io/badge/license-MIT-6366f1?style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <code>pip install codesuture</code>
</p>

> ⚠️ **CodeSuture modifies live bytecode at runtime.** Use in production at your own risk. Always verify patches with `codesuture audit` and `codesuture explain` before relying on them.

---

## What is CodeSuture?

When a Python program crashes — `AttributeError`, `KeyError`, `ZeroDivisionError`, `IndexError`, `TypeError` — CodeSuture intercepts the exception **at the exact bytecode instruction**, analyzes the crash pattern, injects a deterministic guard into the function's code object **in memory**, and retries execution.

No source files are modified. No decorator required. No restart needed.

```bash
codesuture run your_app.py
```

```
[CodeSuture] Caught AttributeError: 'NoneType' object has no attribute 'bio'
[CodeSuture] Applying null_guard on 'profile' ...
[CodeSuture] Patch applied to get_bio().
[CodeSuture] Active Shield: Native frame rewound for get_bio() successfully.

Session summary:
  Patches applied: 1
```

Run it again — the patch loads from disk before the first call:

```
[CodeSuture] Already healed: loaded persistent patch for get_bio
```

---

## How It Works

<p align="center">
  <img src="assets/architecture.png" alt="CodeSuture Architecture" width="90%">
</p>

CodeSuture operates in five stages:

| Stage | What happens |
|-------|-------------|
| **① Catch** | `sys.settrace()` intercepts exceptions at the exact frame and bytecode offset |
| **② Analyze** | The pattern matcher disassembles the failing instruction chain and identifies the crashing variable, operation, and crash type |
| **③ Patch** | The guard synthesizer injects new bytecode into the function's code object. A semantic diff gate rejects patches that modify too much logic |
| **④ Rewind** | The execution frame is rewound to re-enter the patched function. On HTTP servers, the full transaction is replayed — the client gets a `200` |
| **⑤ Persist** | The patched code object is serialized to `.codesuture_store/` with SHA-256 integrity checks and TTL metadata |

---

## Guard Types

<p align="center">
  <img src="assets/guards.png" alt="Guard Types" width="80%">
</p>

CodeSuture ships with **11 deterministic guard types** — each one targets a specific crash pattern and injects the minimal bytecode fix:

| Guard | Crash Type | Example | What It Does |
|-------|-----------|---------|--------------|
| `null_guard` | `AttributeError` on `None` | `user.profile.bio` | Inserts `if x is None: x = default` before attribute access |
| `key_guard` | `KeyError` | `config["timeout"]` | Wraps dict access with `.get(key, default)` |
| `subscript_guard` | `TypeError` subscripting `None` | `data["key"]` when `data` is `None` | Null-checks the container before subscript |
| `chain_subscript_guard` | Nested subscript failures | `resp["user"]["name"]["first"]` | Guards the entire chain from the root |
| `index_guard` | `IndexError` (variable index) | `items[i]` when `i >= len(items)` | Bounds-checks `i` against `len(items)` |
| `list_bound_guard` | `IndexError` (constant index) | `parts[3]` when `len(parts) < 4` | Checks `len(parts) > 3` before access |
| `division_guard` | `ZeroDivisionError` | `total / count` when `count` is `0` | Substitutes a safe divisor when variable denominator is zero |
| `str_coerce_guard` | `TypeError` on string concat | `"age: " + age` when `age` is `int` | Wraps non-str variable with `str()` after assignment |
| `type_coercion_guard` | `TypeError` on conversion | `int("not_a_number")` | Adds type validation before coercion |
| `file_guard` | `FileNotFoundError` | `open(path)` | Checks `os.path.exists()` before open |
| `callable_guard` | `TypeError` calling `None` | `callback()` when `callback` is `None` | Returns `None` for unknown callables |

---

## CLI Reference

### Core Commands

```bash
# Run a script with live patching
codesuture run app.py

# Run with full diagnostics
codesuture run app.py --verbose --shadow --retries 5

# Preview patches without applying
codesuture run app.py --dry-run

# Watch mode — auto-restart after patches
codesuture watch server.py --max-restarts 10
```

### Inspection & Governance

```bash
# Show all active patches
codesuture audit

# Plain-language explanation of patches
codesuture explain
codesuture explain get_user_profile

# View incident log
codesuture incidents
codesuture incidents --since 2d

# Generate markdown incident report
codesuture digest

# View fix suggestions
codesuture suggest

# Export Prometheus metrics
codesuture metrics

# View patch lifecycle states
codesuture lifecycle show
```

### Rollback & Cleanup

```bash
# Roll back a specific patch
codesuture rollback get_user_profile

# Preview what would be removed
codesuture rollback --dry-run

# Remove everything — patches, fingerprints, incidents
codesuture rollback --all
```

### Alerts

```bash
# View unread alerts
codesuture alerts

# Dismiss alerts for a resolved incident
codesuture alerts dismiss <incident_id>
```

---

## HTTP Recovery

CodeSuture patches exceptions inside HTTP request handlers. When a handler crashes and a guard is synthesized, CodeSuture patches the function **mid-request** and replays the transaction in-place. The client receives a response instead of a socket close.

```
[CodeSuture] Caught AttributeError: 'NoneType' object has no attribute 'get_profile'
[CodeSuture] Applying null_guard on 'get_profile' ...
[CodeSuture] Patch applied to do_GET().
[CodeSuture] Transaction replay armed for do_GET().
[CodeSuture] Transaction replay: retrying patched HTTP handler in-place.
127.0.0.1 - - "GET /user-data HTTP/1.1" 200 -
```

Every patched response carries a transparency header:

```http
HTTP/1.0 200 OK
Content-type: application/json
X-CodeSuture: patched=1; guard=null_guard; target=get_profile

{"result": null}
```

### Framework Middleware

```python
# WSGI (Flask, Django, Bottle, etc.)
from codesuture.middleware import CodeSutureMiddleware
app = CodeSutureMiddleware(your_wsgi_app)

# ASGI (FastAPI, Starlette, etc.)
from codesuture.middleware_asgi import CodeSutureASGIMiddleware
app = CodeSutureASGIMiddleware(your_asgi_app)
```

---

## Incident Intelligence

Every crash CodeSuture intercepts is logged as a structured incident with automatic severity classification:

| Severity | When |
|----------|------|
| **CRITICAL** | Callable replacement, sensitive modules (auth, payment, billing) |
| **HIGH** | First occurrence, HTTP mutating methods (POST/PUT/DELETE), chain subscripts |
| **MEDIUM** | Standard guards (null, key, division, index) after first occurrence |
| **LOW** | Repeat patterns, file guards, string coercion |

```bash
$ codesuture incidents

  Time                 Severity   Function                  Guard                Target          Status
  ──────────────────── ────────── ───────────────────────── ──────────────────── ─────────────── ──────────
  2026-05-26T19:05:55  MEDIUM     render_user_card          null_guard           user            patched
  2026-05-26T19:05:55  HIGH       format_weather_report     chain_subscript_guard data            patched
  2026-05-26T19:05:56  MEDIUM     compute_metrics           division_guard       success         patched
```

```bash
$ codesuture digest

# CodeSuture Daily Incident Report — 2026-05-26

## Summary
- **Total incidents:** 3
- **CRITICAL:** 0 | **HIGH:** 1 | **MEDIUM:** 2 | **LOW:** 0
- **Unique crash patterns:** 3
- **Functions patched:** 3
```

---

## Alert System

CodeSuture routes incidents to alert channels based on severity:

- **File alerts** — Markdown files written to `.codesuture_alerts/`
- **Webhook alerts** — HTTP POST to your alerting endpoint (Slack, PagerDuty, etc.)
- **Escalation** — Functions patched 5+ times in 24 hours are auto-escalated

```bash
$ codesuture alerts

  CodeSuture — Unread Alerts

  [HIGH] format_weather_report crashed with KeyError, patched with chain_subscript_guard
  Escalating get_user_display from HIGH to CRITICAL (patched 5 times in 24h)
```

---

## Shadow Execution

With `--shadow`, CodeSuture runs the original (unpatched) function alongside the patched version and compares results:

```bash
codesuture run app.py --shadow
```

| Verdict | Meaning |
|---------|---------|
| **JUSTIFIED** | Original crashes, patched succeeds — the patch is necessary |
| **UNNECESSARY** | Both produce the same result — consider removing the patch |
| **DIVERGENT** | Results differ — the patch changes behavior, review recommended |

Shadow-verified patches get upgraded to **VERIFIED** confidence in fix suggestions.

---

## Fix Suggestions

CodeSuture generates concrete source-code fix suggestions for every active patch:

```bash
$ codesuture suggest

  Function: render_user_card
  Guard: null_guard on 'profile'
  Confidence: LIKELY

  --- a/app.py
  +++ b/app.py
  @@ -15,1 +15,1 @@
  -    bio = user.profile.bio
  +    bio = user.profile.bio if user.profile is not None else ''
```

Confidence levels:
- **VERIFIED** — Shadow execution confirmed the fix works
- **LIKELY** — Deterministic guard with high confidence
- **EXPERIMENTAL** — Complex guard, review recommended

---

## Lifecycle Management

Every patch transitions through a state machine:

```
DETECTED → PATCHED → PERSISTED → SUGGESTED → VERIFIED → FIXED
               ↓                                  ↓
           REPLAYED                            EXPIRED
                                                  ↓
                                            ROLLED_BACK
```

```bash
$ codesuture lifecycle show

  Function         State       Age    TTL
  get_bio          PERSISTED   2d     7d
  compute_ratio    VERIFIED    1d     7d
  parse_config     EXPIRED     8d     7d  ← needs attention
```

---

## Prometheus Metrics

Export patch metrics in Prometheus text format:

```bash
$ codesuture metrics

# HELP codesuture_incidents_total Total incidents recorded
# TYPE codesuture_incidents_total counter
codesuture_incidents_total 20
codesuture_patches_total{guard_type="null_guard"} 12
codesuture_patches_total{guard_type="key_guard"} 5
codesuture_patches_total{guard_type="division_guard"} 3
```

---

## Safety & Security

| Feature | What it prevents |
|---------|-----------------|
| **Semantic diff gate** | Rejects patches that modify too many instructions. The engine never corrupts a complex function to fix a simple crash |
| **SHA-256 integrity** | Persisted `.code` files are checksummed. Tampered files are refused on load |
| **Patch validation** | Synthesized bytecode is checked for `LOAD_FAST` references to variables not in `co_varnames`. Invalid patches are rejected |
| **Original code backup** | Pre-patch code objects are stored in `_ORIGINAL_CODES` for runtime rollback |
| **Patch TTL** | Every patch carries a time-to-live. Expired patches warn to fix the root cause |
| **Thread safety** | All shared state protected by locks. Safe under free-threaded Python 3.13+ (no-GIL) |
| **Caller-aware propagation** | After patching, `gc.get_referrers` updates closures, bound methods, and partials |
| **Runtime rollback** | `codesuture rollback` removes disk files AND restores original code in the running process |
| **CPython portability** | Version-aware opcode sets handle 3.11, 3.12, and 3.13+ instruction differences |

---

## Known Limitations

| Limitation | Detail |
|-----------|--------|
| **Python 3.11+ only** | Depends on CPython bytecode structures introduced in 3.11 |
| **First crash leaks** | The initial exception propagates to the caller. The patch prevents recurrence on subsequent calls |
| **Comprehensions** | List/dict/set/generator comprehensions are anonymous nested code objects — CodeSuture logs a warning and skips them |
| **Semantic bugs** | CodeSuture fixes structural crashes (null access, missing keys, type mismatches). Logic errors that produce wrong results without crashing cannot be detected |
| **Single-process** | Patches apply per-process. `.codesuture_store/` is shared on disk for cross-restart persistence |
| **Async (experimental)** | Standard `async def` functions are patched. Async generators and deep `await` chains may not be handled correctly |

---

## What CodeSuture Is Not

**Not a logger.** It doesn't record exceptions and move on. It patches the function and retries.

**Not a static analyzer.** It operates at runtime on live bytecode, not on source files.

**Not autonomous by default.** All patches are deterministic rule-based guards. An opt-in `--autonomous` flag exists for experimental LLM-powered suggestions, but it never auto-applies.

**Not a replacement for fixing bugs.** CodeSuture is a runtime safety net. The `suggest` command tells you exactly what source code to change. The `lifecycle` system tracks patch age. Expired patches mean you should have fixed the root cause by now.

---

## Installation

```bash
pip install codesuture
```

Requires **Python 3.11+** and the [`bytecode`](https://pypi.org/project/bytecode/) library (installed automatically).

For experimental LLM-powered autonomous mode:

```bash
pip install "codesuture[autonomous]"
```

---

## License

MIT. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <sub>Built with obsession, not sleep. If CodeSuture saved your server at 3 AM, consider giving it a ⭐.</sub>
</p>