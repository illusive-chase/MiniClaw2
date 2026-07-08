# MiniClaw2 Refactor Execution Progress

Status as of 2026-07-08, after Step 5 working-tree implementation.

Source proposals:

- `PROPOSAL_REFACTOR.md`
- `PROPOSAL_DESIGN_REVIEW.md`

This file is a handoff tracker for follow-on sessions. Keep it short and
update it after each committed slice.

## Completed

### Step 1 — Initial Stop-The-Bleeding Slice

Committed in `a2e5949 update`.

What changed:

- Refreshed root `CONTEXT.md` so future agent launches no longer receive
  stale SDK/scenario/unittest/passive-review-gate guidance.
- Prevented framework-generated `.miniclaw2/` files from being swept into
  user auto-commit diffs:
  - `commit_all()` now stages non-framework paths only.
  - `.miniclaw2/` is explicitly unstaged before commit.
  - commit/no-op detection now checks the staged diff, not the whole
    working tree.
  - project creation and temporary workspace creation best-effort append
    `.miniclaw2/` to `.git/info/exclude`.
- Hardened the Claude ask-hook timeout/port chain:
  - `/hook/ask` has a supervised 120s server-side timeout.
  - hook runtime port is set from env at startup and from HTTP/WS scope
    when requests arrive.
  - installed Claude hook entries now include explicit timeout values:
    AskUserQuestion 700s, SessionStart 15s.

Tests added:

- `backend/tests/test_git_state.py`
- `backend/tests/test_hook_routes.py`

Verification run before commit:

```bash
python -m py_compile backend/miniclaw2/git_state.py backend/miniclaw2/registry.py backend/miniclaw2/workspace.py backend/miniclaw2/app.py backend/miniclaw2/providers/claude_native/hook_installer.py
python -m pytest backend/tests
```

Result at that point: `255 passed`.

### Step 2 — Claude Provider Failure Semantics

Implemented in working tree after `a2e5949 update`.

What changed:

- Documented the provider stream contract: providers must emit an explicit
  terminal event before `run()` exhausts.
- Claude native stream now emits explicit terminal provider events:
  - `done(final_state="done")` for result/summary completion.
  - `done(final_state="cancelled")` after requested interruption.
  - `error` for child death, error result records, or stream idle without an
    end-of-turn marker.
- Claude native stream checks PTY child liveness via `pty.isalive()` and
  surfaces early child exit as a provider error instead of falling through.
- `ClaudeProvider` converts bare native stream exhaustion into an error.
- `NodeRunner` treats any provider `run()` exhaustion without `done` or
  `error` as a provider error instead of implicit `DONE`.
- Context init/refresh now applies the same terminal-event requirement.

Tests added/updated:

- `backend/tests/test_claude_provider.py`
  - normal Claude result emits explicit `done`.
  - PTY child death emits `error`.
  - requested interrupt emits cancelled terminal event.
  - bare native stream exhaustion becomes a Claude provider error.
- `backend/tests/test_runner_preview_repair.py`
  - bare provider exhaustion moves the node to `error` without preview repair.

Verification run:

```bash
python -m py_compile backend/miniclaw2/providers/base.py backend/miniclaw2/providers/claude_native/__init__.py backend/miniclaw2/providers/claude.py backend/miniclaw2/context_refresh.py backend/miniclaw2/runner.py backend/tests/test_claude_provider.py backend/tests/test_runner_preview_repair.py
python -m pytest backend/tests/test_claude_provider.py backend/tests/test_runner_preview_repair.py
python -m pytest backend/tests
```

Result after Step 2: `261 passed`.

### Step 3 — Claude Transcript Retarget/Fingerprint Hardening

Implemented in working tree after Step 2.

What changed:

- Claude submit confirmation now carries the JSONL offset of the fresh
  user marker or fingerprint match.
- Session retarget now seeds transcript draining from that marker offset
  when known, otherwise from the target JSONL EOF; it no longer resets to
  `0` and replays copied session history.
- `ClaudeProvider` passes the actual node prompt as the submit-confirmation
  fingerprint source while still sending the composed turn text to Claude.
- Fingerprint fallback scans only the expected Claude project hash instead
  of all recently modified project hashes.

Tests added/updated:

- `backend/tests/test_claude_provider.py`
  - retarget without a marker offset seeks to EOF.
  - retarget with a marker offset does not replay stale transcript history.
  - fingerprint fallback uses node prompt text instead of composed launch
    headers.
  - fingerprint fallback does not adopt sessions from another project hash.
  - `ClaudeProvider` passes node prompt text into native submit confirmation.

Verification run:

```bash
python -m py_compile backend/miniclaw2/providers/claude_native/input.py backend/miniclaw2/providers/claude_native/__init__.py backend/miniclaw2/providers/claude.py backend/tests/test_claude_provider.py
python -m pytest backend/tests/test_claude_provider.py
python -m pytest backend/tests
```

Result after Step 3: `267 passed`.

### Step 4 — Stale Launch Settings Robustness

Implemented in working tree after Step 3.

What changed:

- Persisted `preferred_language` is now lenient on read-back:
  - API/write validation still rejects unsupported labels.
  - launch/session read paths ignore invalid stored values with logging.
  - invalid stored values no longer block runner startup or language
    instruction composition.
- Persisted `active_planspace_id` is now checked before agent provider
  launch:
  - missing active planspace settings remain allowed for older/free-form
    projects.
  - present but unresolvable active planspace settings fail visibly with a
    framework `error` node and stub preview.
  - the provider is not started when the preview-contract lane would be
    silently stripped.

Tests added/updated:

- `backend/tests/test_language_preference.py`
  - invalid persisted project/settings language values are ignored on read.
  - runner startup proceeds without injecting language instructions for an
    invalid stored language.
- `backend/tests/test_runner_preview_repair.py`
  - stale `active_planspace_id` errors before provider launch and writes a
    visible error preview.

Verification run:

```bash
python -m py_compile backend/miniclaw2/contextspace.py backend/miniclaw2/language.py backend/miniclaw2/runner.py backend/miniclaw2/app.py backend/tests/test_language_preference.py backend/tests/test_runner_preview_repair.py
python -m pytest backend/tests/test_language_preference.py backend/tests/test_runner_preview_repair.py
python -m pytest backend/tests
```

Result after Step 4: `272 passed`.

### Step 5 — Ask-Gate Timeout Chain Reconciliation

Implemented in working tree after Step 4, following a design review of
Steps 1–4 against both proposals.

What changed:

- Reconciled the contradiction between the Step 1 timeout fixes: the
  120s `/hook/ask` dispatcher timeout was undercutting the 700s/600s
  hook/bridge timeouts, capping the human answer window for
  AskUserQuestion at 120s. The chain is now strictly ordered and each
  layer gives up before the layer beneath it kills the transport:
  - runner-side ask-gate supervision: 570s
    (`providers/claude._ASK_GATE_TIMEOUT_SECONDS`)
  - `/hook/ask` dispatcher wait: 590s (`app._HOOK_ASK_TIMEOUT_SECONDS`)
  - hook bridge HTTP timeout: 600s (unchanged)
  - installed hook entry timeout: 700s (unchanged)
- `GateRequest` gained an optional `timeout_seconds`; when set, the
  runner supervises the gate and on expiry emits an honest error event,
  records it on the node, deliberately interrupts the session, and
  raises `GateTimeoutError` (previously the turn died via the 2s
  idle-detection heuristic with a misleading message). Gates on
  deadline-free transports (Codex permission gates, human review prose)
  remain unbounded.
- Session retarget without a known marker/record offset now falls back
  to EOF universally; the mid-stream retarget path could previously
  fall back to offset 0 and replay copied session history when the
  observed rotation record was not found in the new JSONL.
- Extracted `NodeRunner._emit_node_started()` and replaced five inline
  `NodeStarted(...)` copies (agent start, stale-settings handler,
  generic pre-start error handler, op, verifier).

Tests added/updated:

- `backend/tests/test_runner_gate_timeout.py`
  - supervised gate timeout interrupts the session, records an honest
    error, and finalizes the node as cancelled.
- `backend/tests/test_hook_routes.py`
  - the four ask-timeout constants are strictly ordered.
- `backend/tests/test_claude_provider.py`
  - mid-stream retarget without a matching record seeks to EOF instead
    of replaying the copied file head.

Verification run:

```bash
python -m pytest backend/tests
```

Result after Step 5: `275 passed`.

## Recommended Next Step

Begin the Contract Hardening phase with a narrow typed `GateResponse`
slice before large structural refactors.

## Later Phases

Do these after the P0 robustness slices unless the user explicitly
reprioritizes.

### Contract Hardening

- Typed `GateResponse` instead of untyped response dicts through
  WS/registry/runner/provider layers.
- Slim `node_updated` wire event so snapshots are not repeatedly sent and
  persisted.
- Add `node_id` to streaming envelopes such as text deltas, thinking,
  activity, usage, and interaction requests.
- Clean up provider abstraction leaks and remove legacy
  `checkpoint_review` where still present.

### Remaining Phase-0 Robustness (from PROPOSAL_DESIGN_REVIEW)

Cheap items that were part of the design review's Phase 0/1 and have
not landed yet:

- Skip context-bundle snapshots for `op` nodes; add snapshot retention
  (prune-on-startup keeping last N per project)
  (PROPOSAL_DESIGN_REVIEW §2.2).
- Provider lifetime `try/finally` hardening plus lifespan-shutdown
  interruption of in-flight runners; startup sweep for orphaned
  `claude` PTYs / `codex app-server` processes
  (PROPOSAL_DESIGN_REVIEW §4.4, PROPOSAL_REFACTOR §2.6).
- Surface `context_refresh` task errors (`last_error`) in the status
  payload (PROPOSAL_REFACTOR §2.6).
- Make the verifier timeout (60s hardcoded at the `runner.py`
  subprocess wait) configurable (PROPOSAL_REFACTOR §2.6).

### Docs And Dead Code (from both proposals' Phase 0)

- Refresh `README.md`: remove `PhantomNode`, the retired
  output-contract enum, `scenarios/`, planspace inbox files, and the
  removed bootstrap endpoint; trim to run-instructions + pointers to
  PHILOSOPHY/IMPLEMENTATION_STATUS.
- Amend `PHILOSOPHY.md`: add the `verifier` kind to §6.1 with its
  constraint set and soften §9.2 (PROPOSAL_DESIGN_REVIEW §1.1); add an
  honest caveat to §6.1 that permission gates do not currently fire on
  Claude (PROPOSAL_DESIGN_REVIEW §1.2).
- Archive or rewrite `TEST.md` / `TESTING.zh.md` (retired scenario
  engine) and move `TEMP_EXAMPLE.md` to `docs/archive/`.
- Delete verified-dead code: `domain.ContextBundle`, `context.py`
  (`load_project_context`), root `paths.py`
  (`validate_project_relative_path`), `GateKind` one-member enum,
  `SpawnArgs`, `_write_passthrough`, and the other §4.5 items in
  PROPOSAL_REFACTOR.

### Structure

- Extract a single lane/virtual graph mutation service used by reap,
  REST create/update/delete, and templates.
- Deduplicate the remaining runner finalization sequences (stub preview
  → transition → `node_updated` → `turn_done`) before considering a
  full per-kind runner split (`_emit_node_started` is done).
- Add an in-process `Store.list_nodes` cache before considering SQLite.

### Naming And UI Vocabulary

- `/sessions` to `/projects` route/type rename with compatibility aliases.
- Decide code vocabulary for lane/planspace/direction/plug and apply
  consistently.
- Add frontend `vocab.ts` or equivalent to keep schema words off primary
  UI surfaces.
- Frontend hook/component extractions should be opportunistic, not ahead
  of the backend safety work.

### Product-Level Safety

- Claude permission gating via `PreToolUse` hook bridge remains the
  larger safety item. Treat it as its own design/implementation slice
  after basic provider failure semantics are correct.
