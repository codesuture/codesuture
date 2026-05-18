# CodeSuture v1.0 Roadmap

CodeSuture v0.6.5 proved the core idea: the runtime can intercept structural
crashes, patch live CPython bytecode, and preserve active web transactions.

CodeSuture v1.0 turns that engine into a production platform.

The v1.0 goal is simple:

1. Run with negligible healthy-path overhead.
2. Save live requests without leaking tracebacks.
3. Convert runtime patches into verified source-level pull requests.
4. Establish the foundation for a future polyglot control plane.

This document is planning only. It describes the engineering sequence,
decision gates, and success criteria for the v1.0 program.

---

## North Star

CodeSuture should become the operating layer for self-healing software:

- Runtime protection keeps production traffic alive.
- Patch intelligence explains what changed and why.
- Source regeneration turns temporary runtime scaffolding into durable fixes.
- Fleet controls make every automatic action auditable, reversible, and safe.

The product posture for v1.0 is disciplined autonomy. CodeSuture may heal a
live transaction, but source changes must pass tests, preserve developer
review, and leave a full audit trail.

---

## Release Themes

### 1. Zero-Overhead Aegis Shield

Problem:

`sys.settrace` is not acceptable as the default production engine. It observes
too much, runs too often, and creates a performance objection before platform
teams can trust the tool.

v1.0 direction:

- Use Python 3.12+ `sys.monitoring` as the primary production engine.
- Keep `sys.settrace` as a Python 3.11 compatibility path.
- Normalize both engines behind one telemetry interface.
- Wake only on exception events during healthy execution.
- Measure and publish request-path overhead.

Success criteria:

- Python 3.12+ healthy-path overhead is below 1 percent in benchmarked API
  workloads.
- The runtime captures exception location, frame, code object, exception type,
  and instruction offset without line-by-line tracing.
- Python 3.11 remains supported, but clearly labeled as compatibility mode.
- No production-facing feature depends on undocumented behavior unless it has
  an explicit fallback and test coverage.

Decision gate:

Before adding a custom C extension, prove whether `sys.monitoring` plus safe
transaction replay is sufficient for v1.0. C-level frame mutation should remain
behind an experimental flag until it is benchmarked and failure-isolated.

---

### 2. Transaction-First Recovery

Problem:

Raw frame rewinding is powerful, but fragile across CPython versions, exception
tables, active handlers, and web server request loops. A production server cares
about the client contract first: no empty response, no traceback leak, and a
structured result.

v1.0 direction:

- Treat request replay as the default web recovery strategy.
- Use frame rewind only where the runtime can prove it is safe.
- Detect supported transaction boundaries:
  - `http.server` and `socketserver`
  - WSGI middleware
  - ASGI middleware
  - worker-thread request handlers
- Return structured JSON or framework-native responses when replay cannot
  complete cleanly.
- Mark every replay with metadata for audit and observability.

Success criteria:

- First failing request returns a structured response.
- Server logs remain free of unhandled traceback cascades.
- Supported web integrations have regression harnesses.
- Every fallback response includes enough metadata to trace the runtime patch.

Decision gate:

If native frame rewind and transaction replay disagree, v1.0 chooses transaction
integrity over VM cleverness. The client experience is the release contract.

---

### 3. Self-Reflective Source Repair

Problem:

In-memory bytecode patches are scaffolding. They protect the process, but the
source tree remains broken. If the process restarts after TTL expiry, the defect
can return.

v1.0 direction:

- Capture crash evidence after a runtime heal:
  - exception type and message
  - stack and source location
  - bytecode fingerprint
  - synthesized guard type
  - semantic diff summary
  - sanitized runtime values
- Build a background repair loop:
  - generate a source-level patch using a local or private LLM
  - run tests in an isolated sandbox
  - produce an explanation and risk rating
  - open a pull request when verification passes
- Keep human review in the loop.

PR convention:

`[CodeSuture Auto-Heal] Fix production crash in <module>:<line>`

Required PR contents:

- Crash summary
- Runtime patch summary
- Source patch summary
- Test evidence
- Data redaction note
- Rollback instructions

Success criteria:

- CodeSuture can open a verified pull request for deterministic crash classes.
- No sensitive production data is sent to a remote model by default.
- Failed source repairs remain local artifacts and never become PRs.
- Every generated PR links back to a runtime incident fingerprint.

Decision gate:

Auto-merge is out of scope for v1.0. The product may propose and verify, but
humans approve source changes.

---

### 4. Production Governance

Problem:

Self-healing systems need control, not magic. Platform teams need to know what
was patched, when, why, how often, and how to roll it back.

v1.0 direction:

- Promote the patch registry into a first-class operational surface.
- Add clear lifecycle states:
  - detected
  - patched
  - replayed
  - persisted
  - expired
  - source-fix-proposed
  - source-fix-verified
  - rolled-back
- Build fleet-ready reporting:
  - patch count by service
  - crash recurrence by fingerprint
  - guard type distribution
  - fallback response count
  - source PR status
- Require explanations for every applied patch.

Success criteria:

- Operators can answer "what did CodeSuture change?" in one command.
- Every patch is reversible.
- Every persisted patch has TTL and provenance.
- Every source-repair proposal has a test record.

Decision gate:

No new autonomous behavior ships without a matching audit and rollback story.

---

### 5. Polyglot Foundation

Problem:

Enterprise systems are not Python-only. Long-term adoption requires a control
plane that can reason about Python, Node.js, and JVM services.

v1.0 direction:

Do not attempt full polyglot runtime patching in v1.0. Instead, define the
shared protocol that future adapters will use.

Planning commitments:

- Define a language-neutral incident schema:
  - runtime
  - service
  - version
  - exception class
  - source location
  - stack fingerprint
  - recovery action
  - confidence
  - verification status
- Design a Rust core boundary for:
  - fingerprinting
  - event normalization
  - policy evaluation
  - telemetry export
- Treat Python as the reference implementation.
- Track future adapters:
  - Node.js and V8
  - Java and Kotlin on the JVM
  - WebAssembly sidecars for shared analysis

Success criteria:

- v1.0 has a stable incident protocol.
- Python events can be exported in the protocol format.
- Future runtimes do not require redesigning the controller model.

Decision gate:

Polyglot runtime patching begins after Python v1.0 is stable, benchmarked, and
operationally governed.

---

## Milestone Plan

### Milestone 1: Runtime Core Hardening

Objective:

Make the Python engine predictable under supported crash classes.

Workstreams:

- Formalize patch confidence scoring.
- Expand regression harnesses for HTTP transaction replay.
- Separate frame rewind, transaction replay, and fallback response strategies.
- Add stricter semantic diff rejection rules.
- Document unsupported bytecode shapes.

Exit criteria:

- Supported guard types have deterministic tests.
- HTTP request recovery has first-request tests.
- Traceback-free recovery is validated in harness logs.

---

### Milestone 2: Python 3.12 Monitoring Engine

Objective:

Make `sys.monitoring` the production default on modern Python.

Workstreams:

- Build a telemetry engine abstraction.
- Normalize trace and monitoring event payloads.
- Register exception-only callbacks on Python 3.12+.
- Add cleanup and tool-id lifecycle management.
- Benchmark against the legacy trace engine.

Exit criteria:

- Python 3.12+ runs without line tracing.
- Python 3.11 fallback remains compatible.
- Benchmarks show negligible healthy-path overhead.

---

### Milestone 3: Auto-Heal Source Repair

Objective:

Convert runtime patches into source-level pull requests.

Workstreams:

- Define crash evidence bundles.
- Add secret and payload redaction policy.
- Build local sandbox verification flow.
- Support local/private LLM repair generation.
- Generate PR descriptions with test evidence.

Exit criteria:

- Deterministic source repairs can be proposed for known guard classes.
- Failed repairs do not leave persistent source changes.
- Verified repairs can open review-ready pull requests.

---

### Milestone 4: Governance And Operations

Objective:

Make CodeSuture trustworthy in real platform teams.

Workstreams:

- Expand `audit` and `explain`.
- Add incident export.
- Add patch lifecycle status.
- Add rollback drills.
- Add policy settings for autonomous behavior.

Exit criteria:

- Every runtime action has provenance.
- Every patch can be explained and rolled back.
- Teams can run CodeSuture in "observe", "patch", and "auto-heal PR" modes.

---

### Milestone 5: Protocol And Controller Foundation

Objective:

Prepare for distributed and polyglot adoption.

Workstreams:

- Define incident schema.
- Define controller API boundaries.
- Sketch Rust core module responsibilities.
- Add protocol export from Python runtime.
- Publish adapter requirements for future Node.js and JVM work.

Exit criteria:

- Python emits language-neutral incident records.
- Controller design does not assume CPython internals.
- Polyglot work has a clear post-v1.0 starting line.

---

## Team Tracks

### Runtime Team

Owns:

- `sys.monitoring` engine
- trace fallback
- frame/replay strategy selection
- bytecode safety gates
- performance benchmarks

Primary metric:

Healthy-path overhead and recovery reliability.

### Repair Intelligence Team

Owns:

- crash evidence bundles
- LLM repair prompts
- sandbox verification
- PR generation
- redaction policy

Primary metric:

Verified source fixes generated from runtime incidents.

### Platform Team

Owns:

- audit surfaces
- patch registry lifecycle
- incident export
- rollback controls
- deployment modes

Primary metric:

Operator trust and reversibility.

### Future Runtime Team

Owns:

- protocol design
- Rust core planning
- Node.js and JVM adapter research
- controller architecture

Primary metric:

A stable path to polyglot support without destabilizing Python v1.0.

---

## Risk Register

### Risk: Unsafe Frame Mutation

Frame rewinding can behave differently across Python releases and active
exception states.

Mitigation:

Make transaction replay the web default. Keep frame mutation behind confidence
checks and explicit tests.

### Risk: Patch Corruption

Bytecode edits can alter control flow or exception tables incorrectly.

Mitigation:

Strengthen semantic diff gates, add per-guard regression fixtures, and reject
ambiguous bytecode.

### Risk: Sensitive Data Leakage

Crash evidence may contain production payloads or secrets.

Mitigation:

Redact by default, prefer local/private models, and require explicit opt-in for
remote repair generation.

### Risk: Autonomy Without Trust

Teams may reject a tool that changes runtime behavior without clear governance.

Mitigation:

Provide modes, audit logs, rollback commands, TTL, and human-reviewed PRs.

### Risk: Premature Polyglot Expansion

Trying to patch Python, Node.js, and JVM runtimes at once could fracture the
product.

Mitigation:

Ship Python v1.0 first. Define the shared protocol now, but defer runtime
adapter implementation.

---

## v1.0 Release Criteria

CodeSuture v1.0 is ready when:

- Python 3.12+ uses `sys.monitoring` as the default engine.
- Python 3.11 fallback remains tested and documented.
- Supported web transactions return structured responses on first failure.
- Runtime logs stay traceback-free for supported recovery paths.
- Patch confidence and rejection rules are documented.
- Runtime patches are auditable, explainable, reversible, and TTL-bound.
- Background source repair can generate verified pull requests.
- Sensitive data redaction is enforced by default.
- The incident schema is stable enough for future controller and polyglot work.

---

## v1.0 Challenge Statement

v0.6.5 proved that CodeSuture can manipulate the CPython runtime to save live
web transactions. v1.0 turns that power into a disciplined production platform:
zero-overhead exception telemetry, safe transaction recovery, verified source
repair, and fleet-grade governance.

The engine exists. The next release makes it trustworthy enough to run
everywhere.
