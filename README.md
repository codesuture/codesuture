# CodeSuture

**Runtime guard synthesis for CPython. Catches structural crashes, patches live bytecode, keeps your server running.**

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

1. **Catch** — `sys.settrace` intercepts exceptions at the exact frame and bytecode offset.
2. **Analyze** — The pattern matcher walks the instruction chain and identifies the failing variable or operation.
3. **Patch** — The guard synthesizer injects new bytecode into the function's code object. A semantic diff gate rejects patches that change too much logic.
4. **Rewind** — Execution restarts from the patched function. The guard prevents recurrence.
5. **Persist** — The patched code object is serialized to `.codesuture_store/` with JSON metadata. Subsequent runs load it before the first call.

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

---

## CLI reference

| Command | Flags | What it does |
|---|---|---|
| `codesuture run <script>` | | Run with live patching |
| `codesuture run <script>` | `--verbose` | Show patch diffs and instruction deltas |
| `codesuture run <script>` | `--shadow` | Warn when patched functions return sentinel values |
| `codesuture run <script>` | `--dry-run` | Preview patches without applying |
| `codesuture run <script>` | `--ttl DAYS` | Set patch expiry (default: 7 days) |
| `codesuture run <script>` | `--retries N` | Max re-execution attempts (default: 3) |
| `codesuture watch <script>` | `--max-restarts N` | Run continuously, restart after each patch |
| `codesuture audit` | | Show all active patches in a table |
| `codesuture explain` | | Plain-language breakdown of every patch |
| `codesuture explain <name>` | | Explain one function's patch |
| `codesuture rollback <name>` | | Remove one persisted patch |
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

Four crash types, one server, all returning 200:

```
"GET /user-data HTTP/1.1"       200  ← null_guard on None object
"GET /config HTTP/1.1"          200  ← key_guard on missing key
"GET /process-payment HTTP/1.1" 200  ← type_coercion_guard on bad input
"GET /latest-user HTTP/1.1"     200  ← chain_subscript_guard on out-of-bounds
```

### WSGI middleware

```python
from codesuture.middleware import CodeSutureMiddleware

app = CodeSutureMiddleware(wsgi_app)
```

---

## Runtime Intelligence

**Semantic diff gate** — Patches that modify too many instructions for the guard type are automatically rejected. The engine never corrupts a complex function to patch a simple crash.

**Caller-aware propagation** — After patching, CodeSuture uses `gc.get_referrers` to update every live reference to the original code object: closures, bound methods, partials. No stale copy survives.

**Shadow execution mode** — `--shadow` monitors return values of patched functions. If a sentinel default leaks into downstream logic, a warning fires before it causes a second failure.

**Patch expiry** — Every persisted patch carries a TTL. When it ages past the limit, CodeSuture logs a reminder to fix the root cause in source. Patches are scaffolding, not permanent fixes.

**Bytecode fingerprint registry** — Crash sites are hashed by their surrounding instruction window. Repeat patterns get instant cached guard application without re-analysis.

**Audit trail** — `codesuture audit` shows every active patch: function, guard type, target, default value, age. `codesuture explain` gives a plain-language breakdown of what changed and whether the default is safe downstream.

---

## What CodeSuture is not

**Not a logger.** It does not record exceptions and move on. It patches the function and retries.

**Not a static analyzer.** It operates at runtime on live bytecode, not on source.

**Not autonomous.** Patches should be reviewed via `codesuture audit` and `codesuture explain`. The goal is to keep your program running while you fix the root cause — not to replace the fix.

---

## Limitations

**Python 3.11+ only.** CodeSuture depends on CPython 3.11 bytecode structures.

**Comprehensions are not patchable.** List, dict, set, and generator comprehensions are anonymous nested code objects. CodeSuture logs a warning and skips them. Refactor into a named function to enable patching.

**Semantic bugs are not patchable.** CodeSuture fixes structural crashes — null access, missing keys, type mismatches, bounds errors. Logic errors that produce wrong results without crashing are out of scope.

**Single-process scope.** Patches apply per-process. Multi-process applications need one instance per worker. `.codesuture_store/` is shared on disk, so patches load correctly on restart.

**Async support is experimental.** Standard `async def` functions are patched. Async generators and deeply nested `await` chains may not be handled correctly in all cases.

**HTTP recovery covers simple server paths.** Validated against `http.server` and `socketserver`. Full ASGI framework support is in progress.

---

## Roadmap

Tracked in `ROADMAP.md`. v1.0 themes:

- `sys.monitoring` as the default engine on Python 3.12+ (zero line-tracing overhead on hot paths)
- Stronger transaction recovery boundaries across web frameworks
- Verified source-level repair proposals via local LLM
- Fleet governance, audit lifecycle, and incident export
- Language-neutral incident protocol for future polyglot adapters

---

## License

MIT. See `LICENSE` for details.