<h1 align="center">CodeSuture</h1>

<p align="center">
  <strong>Your Python app crashed at 3 AM. CodeSuture already patched it — and left you the one-line fix for the morning.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/codesuture/"><img src="https://img.shields.io/pypi/v/codesuture?style=for-the-badge&color=0d6efd" alt="PyPI"></a>
  <a href="https://pypi.org/project/codesuture/"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-10b981?style=for-the-badge" alt="Python"></a>
  <a href="LINK_TO_CI"><img src="https://img.shields.io/badge/tests-436%20passing-10b981?style=for-the-badge" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-6366f1?style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <code>pip install codesuture</code>
</p>

---

## 30 seconds, zero code changes

```bash
codesuture run your_server.py
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

And in the morning, it hands you the permanent fix:

```
$ codesuture suggest

  Function: get_bio
  Guard: null_guard on 'profile'    Confidence: LIKELY

  -    bio = user.profile.bio
  +    bio = user.profile.bio if user.profile is not None else ''
```

**No decorators. No source file changes. No restart.**

<p align="center">
  <img src="assets/codesuture.webp" alt="CodeSuture healing a live crash" width="100%">
</p>

---

## Why this exists

Sentry tells you your server crashed. Your logs tell you why. **Nothing keeps it alive while you sleep.**

When a Python program hits an `AttributeError`, `KeyError`, `ZeroDivisionError`, `IndexError`, or `TypeError`, CodeSuture intercepts the exception at the exact bytecode instruction, injects a minimal deterministic guard into the function's code object in memory, and retries execution.

Every patch is temporary by design: it carries a TTL, generates a source-level fix suggestion, and nags you until the root cause is fixed. CodeSuture is a safety net — built to make ignoring real bugs impossible.

---

## How It Works

CodeSuture operates in five stages:

| Stage | What happens |
|-------|-------------|
| **① Catch** | On Python 3.12+, `sys.monitoring` (PEP 669) fires **only when an exception is raised** — near-zero overhead on healthy code. On 3.11, a `sys.settrace` fallback is used |
| **② Analyze** | The pattern matcher disassembles the failing instruction chain and identifies the crashing variable, operation, and crash type |
| **③ Patch** | The guard synthesizer injects minimal bytecode. A semantic diff gate rejects patches that modify too much logic |
| **④ Rewind** | The frame is rewound and the patched function re-enters. On HTTP servers, **safe requests (GET/HEAD) are replayed in-place** — the client gets a real response instead of a dropped socket |
| **⑤ Persist** | The patch is serialized to `.codesuture_store/`, **HMAC-signed with a server-generated 256-bit key**, checksummed, and stamped with a TTL |

---

## Performance

On Python 3.12+ instrumentation activates **only at the moment an exception is raised**. Your happy path runs at native speed.

| Runtime | Engine | Healthy-path overhead |
|---------|--------|----------------------|
| Python 3.12 / 3.13 | `sys.monitoring` (PEP 669) | ~0% — exception-only callbacks |
| Python 3.11 | `sys.settrace` fallback | measurable — recommended for dev/staging |

Verify it yourself: `python benchmarks/overhead.py`

---

## The 11 Guards

Every guard is **deterministic and rule-based** — no AI deciding what your code does at runtime.

| Guard | Crash Type | Example | What It Does |
|-------|-----------|---------|--------------|
| `null_guard` | `AttributeError` on `None` | `user.profile.bio` | Inserts `if x is None: x = default` before attribute access |
| `key_guard` | `KeyError` | `config["timeout"]` | Wraps dict access with `.get(key, default)` |
| `subscript_guard` | `TypeError` subscripting `None` | `data["key"]` when `data` is `None` | Null-checks the container before subscript |
| `chain_subscript_guard` | Nested subscript failures | `resp["user"]["name"]["first"]` | Guards the entire chain from the root |
| `index_guard` | `IndexError` (variable index) | `items[i]` when `i >= len(items)` | Bounds-checks `i` against `len(items)` |
| `list_bound_guard` | `IndexError` (constant index) | `parts[3]` when `len(parts) < 4` | Checks `len(parts) > 3` before access |
| `division_guard` | `ZeroDivisionError` | `total / count` when `count` is `0` | Substitutes a safe divisor when denominator is zero |
| `str_coerce_guard` | `TypeError` on string concat | `"age: " + age` when `age` is `int` | Wraps non-str variable with `str()` |
| `type_coercion_guard` | `TypeError` on conversion | `int("not_a_number")` | Adds type validation before coercion |
| `file_guard` | `FileNotFoundError` | `open(path)` | Checks `os.path.exists()` before open |
| `callable_guard` | `TypeError` calling `None` | `callback()` when `callback` is `None` | Returns `None` for unknown callables |

---

## HTTP Recovery — Honest by Design

When a request handler crashes, CodeSuture patches the function mid-request. The client gets a response instead of a socket close — and is **never lied to** about it:

```http
HTTP/1.0 200 OK
Content-type: application/json
X-CodeSuture: patched=1; guard=null_guard; target=get_profile

{"_degraded": true, "result": null, "patched": true}
```

Three hard rules:

1. **Mutating requests are never replayed.** POST, PUT, PATCH, and DELETE handlers are patched for *future* requests, but the failing transaction is never re-executed. No double charges. No duplicate writes. Ever.
2. **Degraded responses say so.** Every patched response carries the `X-CodeSuture` header and an explicit `"_degraded": true` body flag.
3. **Replay applies to safe methods only.** GET and HEAD are replayed in-place after patching.

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

## Built Paranoid

A tool that touches live bytecode has to earn trust. Every layer assumes something will go wrong:

| Defense | What it prevents |
|---------|-----------------|
| **HMAC-signed patches** | A random 256-bit key is generated per server; every persisted patch is signed. A malicious file dropped into `.codesuture_store/` is rejected on load — it can't carry a valid signature |
| **Semantic diff gate** | Patches that modify too many instructions are rejected. The engine never rewrites complex logic to fix a simple crash |
| **SHA-256 integrity** | Corrupted or tampered `.code` files are refused on load |
| **Bytecode validation** | Synthesized patches referencing variables not in `co_varnames` are rejected before injection |
| **Original code backup** | Pre-patch code objects are kept in memory — rollback restores them in the *running* process, no restart |
| **Patch TTL** | Every patch expires. An expired patch means you should have fixed the root cause by now — and CodeSuture will tell you so |
| **Mutating-method lockout** | POST/PUT/PATCH/DELETE transactions are never replayed |
| **Thread safety** | All shared state is lock-protected. Safe under free-threaded Python 3.13+ (no-GIL) |
| **Caller-aware propagation** | After patching, `gc.get_referrers` updates closures, bound methods, and partials |
| **CPython portability** | Version-aware opcode sets handle 3.11, 3.12, and 3.13+ instruction differences |

Audit everything at any time:

```bash
codesuture audit      # every active patch
codesuture explain    # plain-language description of what each patch does
```

---

## Incident Intelligence

Every crash is logged as a structured incident with automatic severity classification:

| Severity | When |
|----------|------|
| **CRITICAL** | Callable replacement, sensitive modules (auth, payment, billing) |
| **HIGH** | First occurrence, crashes in mutating HTTP handlers, chain subscripts |
| **MEDIUM** | Standard guards (null, key, division, index) after first occurrence |
| **LOW** | Repeat patterns, file guards, string coercion |

```bash
$ codesuture incidents

  Time                 Severity   Function                  Guard                  Status
  ──────────────────── ────────── ───────────────────────── ────────────────────── ─────────
  2026-05-26T19:05:55  MEDIUM     render_user_card          null_guard             patched
  2026-05-26T19:05:55  HIGH       format_weather_report     chain_subscript_guard  patched
  2026-05-26T19:05:56  MEDIUM     compute_metrics           division_guard         patched
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

**Alerts** route by severity — markdown files in `.codesuture_alerts/`, or webhooks (Slack, PagerDuty). Functions patched 5+ times in 24 hours are auto-escalated to CRITICAL.

---

## Shadow Execution — Prove the Patch Is Right

```bash
codesuture run app.py --shadow
```

Runs the original (unpatched) function alongside the patched version and compares results:

| Verdict | Meaning |
|---------|---------|
| **JUSTIFIED** | Original crashes, patched succeeds — the patch is necessary |
| **UNNECESSARY** | Identical results — consider removing the patch |
| **DIVERGENT** | Results differ — review before trusting |

Shadow-verified patches get upgraded to **VERIFIED** confidence in fix suggestions.

---

## Fix Suggestions

CodeSuture generates concrete source-code fixes for every active patch:

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

- **VERIFIED** — Shadow execution confirmed the fix works
- **LIKELY** — Deterministic guard with high confidence
- **EXPERIMENTAL** — Complex guard, review recommended

---

## Lifecycle Management

Every patch transitions through a state machine — stale patches get flagged:

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

## CLI Reference

```bash
# Core
codesuture run app.py                     # run with live patching
codesuture run app.py --dry-run           # preview patches without applying
codesuture run app.py --verbose --shadow --retries 5
codesuture watch server.py --max-restarts 10

# Inspection & governance
codesuture audit                          # all active patches
codesuture explain [func]                 # plain-language explanations
codesuture incidents [--since 2d]         # crash log
codesuture digest                         # markdown incident report
codesuture suggest                        # source-level fix suggestions
codesuture metrics                        # Prometheus export
codesuture lifecycle show                 # patch state machine
codesuture alerts                         # unread alerts
codesuture alerts dismiss <incident_id>   # dismiss resolved alerts

# Rollback
codesuture rollback <func>                # disk files AND live process restore
codesuture rollback --dry-run             # preview removal
codesuture rollback --all                 # remove everything
```

---

## Prometheus Metrics

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

## Known Limitations

We'd rather you read these here than discover them in production:

| Limitation | Detail |
|-----------|--------|
| **Python 3.11+ only** | Depends on CPython bytecode structures introduced in 3.11 |
| **First crash propagates** | The initial exception reaches the caller. The patch prevents recurrence on subsequent calls |
| **Comprehensions skipped** | List/dict/set/generator comprehensions are anonymous nested code objects — logged and skipped |
| **Crashes only, not logic bugs** | Wrong results that don't raise an exception cannot be detected |
| **Per-process patching** | Patches apply per-process; `.codesuture_store/` is shared on disk for cross-restart persistence |
| **Async is experimental** | Standard `async def` works. Async generators and deep `await` chains may not be handled correctly |

---

## What CodeSuture Is Not

**Not a logger.** It doesn't record exceptions and move on. It patches the function and retries.

**Not a static analyzer.** It operates at runtime on live bytecode, not on source files.

**Not autonomous by default.** All patches are deterministic rule-based guards. An opt-in `--autonomous` flag exists for experimental LLM-powered suggestions — they are never auto-applied.

**Not a replacement for fixing bugs.** The `suggest` command tells you exactly what source code to change. The `lifecycle` system tracks patch age. Expired patches mean you should have fixed the root cause by now.

---

## Installation

```bash
pip install codesuture
```

Requires **Python 3.11+** and the [`bytecode`](https://pypi.org/project/bytecode/) library (installed automatically).

```bash
pip install "codesuture[autonomous]"   # optional: experimental LLM suggestions
```

---

## License

MIT. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <sub>Built with obsession, not sleep. If CodeSuture saved your server at 3 AM, consider giving it a ⭐.</sub>
</p>
