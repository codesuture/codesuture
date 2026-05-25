# CodeSuture

![CodeSuture Banner](assets/banner.png)

**Runtime guard synthesis for CPython 3.11+. Catches structural crashes, patches live bytecode, keeps your server running.**

[![Version](https://img.shields.io/badge/version-0.7.1-blue)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-brightgreen)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/status-beta-orange)]()

```
pip install codesuture
```

---

## What it does

When a Python program crashes, CodeSuture intercepts the exception at the exact bytecode instruction, disassembles the failing function, injects a deterministic guard into its code object in memory, and retries — without touching a single source file.

The patch persists. Next run, it loads before the first function call.

On a live HTTP server, CodeSuture patches the handler mid-request and replays the transaction. The client receives a 200. No restart. No traceback leak.

---

## Quick start — script

```bash
codesuture run your_script.py
```

```
[CodeSuture] Caught AttributeError: 'NoneType' object has no attribute 'bio'
[CodeSuture] Applying null_guard on 'profile' ...
[CodeSuture] Patch applied to get_bio().
[CodeSuture] Re-executing after 1 patch(es)...

Session summary:
  Patches applied: 1
```

Run it again:

```
[CodeSuture] Already healed, skipping: loaded persistent patch for get_bio
Session summary:
  Patches applied: 0
```

---

## Quick start — live server

```bash
codesuture run server.py --verbose --retries 3
```

```
[CodeSuture] Caught AttributeError: 'NoneType' object has no attribute 'get_profile'
[CodeSuture] Applying null_guard on 'get_profile' ...
[CodeSuture DEBUG] Diff: +12 -9 instructions (allowed <= 55)
[CodeSuture] Patch applied to do_GET().
[CodeSuture] Transaction replay armed for do_GET().
[CodeSuture] Transaction replay: retrying patched HTTP handler in-place.
127.0.0.1 - - "GET /user-data HTTP/1.1" 200 -
```

The client sees:

```http
HTTP/1.0 200 OK
Content-type: application/json
X-CodeSuture: patched=1; guard=null_guard; target=get_profile

{"result": null}
```

No 500. No traceback. Server process intact.

---

## How it works

1. **Catch** — `sys.settrace` (3.11) or `sys.monitoring` (3.12+) intercepts exceptions at the exact frame and bytecode offset.
2. **Analyze** — The pattern matcher walks the instruction chain and identifies the failing variable or operation.
3. **Patch** — The guard synthesizer injects new bytecode into the function's code object. A semantic diff gate rejects patches that change too much logic.
4. **Rewind** — Execution restarts from the patched function via `f_lineno` setter. The guard prevents recurrence.
5. **Persist** — The patched code object is serialized to `.codesuture_store/` with SHA-256 integrity checks and JSON metadata. Subsequent runs load it before the first call.

No source files are modified.

---

## Supported guard types

| Guard type | Triggers on | Example |
|---|---|---|
| `null_guard` | `AttributeError` on `None` | `user.profile.bio` when `profile` is `None` |
| `key_guard` | `KeyError` | `cfg["timeout"]` when key is missing |
| `subscript_guard` | `TypeError` subscripting `None` | `data["key"]` when `data` is `None` |
| `chain_subscript_guard` | Nested subscript on `None` | `data["user"]["name"]` |
| `index_guard` | `IndexError` | `items[10]` when `len(items) == 2` |
| `type_coercion_guard` | `TypeError` on conversion | `int("not_a_number")` |
| `division_guard` | `ZeroDivisionError` | `x / count` when `count == 0` |
| `str_coerce_guard` | `TypeError` on string concat | `"age: " + 25` |
| `file_guard` | `FileNotFoundError` | `open(path)` when file is missing |
| `callable_guard` | `TypeError` calling `None` | `func()` when `func` is `None` |
| `return_guard` | `TypeError` on `None` return | Downstream use of a `None` return value |

---

## CLI reference

| Command | Flags | What it does |
|---|---|---|
| `codesuture run <script>` | | Run with live patching |
| | `--verbose` | Show patch diffs and instruction deltas |
| | `--shadow` | Warn when patched functions return sentinel values |
| | `--dry-run` | Preview patches without applying |
| | `--silent` | Suppress all informational output |
| | `--ttl DAYS` | Set patch expiry (default: 7 days) |
| | `--retries N` | Max re-execution attempts (default: 3) |
| `codesuture watch <script>` | `--max-restarts N` | Run continuously, restart after each patch |
| `codesuture audit` | | Show all active patches in a table |
| `codesuture explain` | | Plain-language breakdown of every patch |
| `codesuture explain <name>` | | Explain one function's patch |
| `codesuture rollback <name>` | | Remove one persisted patch and restore runtime code |
| `codesuture rollback` | `--all` | Remove all patches and fingerprint registry |
| `codesuture rollback` | `--dry-run` | Preview what would be removed |

---

## HTTP recovery

CodeSuture patches exceptions inside `http.server` and `socketserver` request handlers. The handler runs in its own thread — CodeSuture installs `threading.settrace` to intercept crashes there.

When a handler crashes and a guard is available, CodeSuture patches the function mid-request and replays the transaction in-place. The client receives a response instead of a socket close.

Every patched response carries:

```
X-CodeSuture: patched=1; guard=<type>; target=<variable>
```

### WSGI middleware

```python
from codesuture.middleware import CodeSutureMiddleware

app = CodeSutureMiddleware(wsgi_app)
```

---

## Safety features

- **Semantic diff gate** — Patches that modify too many instructions are rejected. The engine never corrupts a complex function to fix a simple crash.
- **SHA-256 integrity** — Persisted patches are checksummed. Tampered `.code` files are refused on load.
- **Caller-aware propagation** — After patching, `gc.get_referrers` updates every live reference: closures, bound methods, partials. No stale copy survives.
- **Patch validation** — Synthesized bytecode is checked for `LOAD_FAST` references to variables not in `co_varnames`. Invalid patches are rejected before application.
- **Patch expiry (TTL)** — Every patch carries a time-to-live. Aged patches trigger a warning to fix the root cause in source.
- **Thread safety** — All shared state (fingerprint registry, persistence store, healed function sets) is protected by locks.
- **Rollback** — `codesuture rollback` removes persisted files AND restores original code in the running process.

---

## What CodeSuture is not

**Not a logger.** It does not record exceptions and move on. It patches the function and retries.

**Not a static analyzer.** It operates at runtime on live bytecode, not on source.

**Not autonomous by default.** All patches are deterministic rule-based guards. An opt-in `--autonomous` flag exists for experimental LLM-powered suggestions via local models, but it is off by default and never auto-applies fixes.

---

## Known limitations

- **Python 3.11+ only.** Depends on CPython bytecode structures introduced in 3.11.
- **3.12+ frame rewind.** The Python-level `f_lineno` setter is used on 3.12+. The ctypes fallback is disabled on 3.12+ to prevent memory corruption from struct layout changes.
- **Comprehensions are not patchable.** List/dict/set/generator comprehensions are anonymous nested code objects. CodeSuture logs a warning and skips them.
- **Semantic bugs are out of scope.** CodeSuture fixes structural crashes — null access, missing keys, type mismatches, bounds errors. Logic errors that produce wrong results without crashing cannot be detected.
- **Single-process scope.** Patches apply per-process. `.codesuture_store/` is shared on disk, so patches persist across restarts.
- **Async support is experimental.** Standard `async def` functions are patched. Async generators and deeply nested `await` chains may not be handled correctly.
- **HTTP recovery is validated against `http.server`.** Full ASGI framework support is not yet implemented.

---

## License

MIT. See [LICENSE](LICENSE) for details.

For a detailed history of changes, see the [Changelog](CHANGELOG.md).