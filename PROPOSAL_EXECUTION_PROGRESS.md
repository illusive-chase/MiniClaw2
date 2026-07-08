# MiniClaw2 Refactor Execution Progress

Status as of 2026-07-08, after Step 2 working-tree implementation.

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

## Recommended Next Step

Continue the remaining P0 safety/robustness work before moving into
contract renames or large structural refactors.

### Step 3 — Claude Transcript Retarget/Fingerprint Hardening

Goal: prevent stale transcript replay and cross-project session adoption.

Likely files:

- `backend/miniclaw2/providers/claude_native/__init__.py`
- `backend/miniclaw2/providers/claude_native/input.py`
- `backend/miniclaw2/providers/claude_native/transcript.py`
- `backend/tests/test_claude_provider.py`

Expected work:

- On session retarget, seed the JSONL offset from the fresh user marker or
  seek to EOF; do not reset to 0.
- Harden submit-confirmation fallback so it fingerprints the actual node
  prompt rather than common template header text.
- Scope fallback scanning to the expected project hash.

### Step 4 — Stale Launch Settings Robustness

Goal: a stale stored setting should not silently strip the preview
contract or wedge every launch.

Likely files:

- `backend/miniclaw2/contextspace.py`
- `backend/miniclaw2/language.py`
- `backend/miniclaw2/runner.py`
- `backend/tests/test_language_preference.py`
- `backend/tests/test_planspace_creation.py` or related launch tests

Expected work:

- If persisted `active_planspace_id` no longer resolves, either refuse
  launch with a visible framework error or fall back to the first valid
  bound planspace with logging. Prefer visible failure unless the existing
  product behavior strongly favors fallback.
- Make persisted `preferred_language` lenient on read-back; strict
  validation should remain at the API/write boundary.

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

### Structure

- Extract a single lane/virtual graph mutation service used by reap,
  REST create/update/delete, and templates.
- Deduplicate runner finalization and `NodeStarted` construction before
  considering a full per-kind runner split.
- Add an in-process `Store.list_nodes` cache before considering SQLite.
- Move dead code removal into a small mechanical PR.

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
