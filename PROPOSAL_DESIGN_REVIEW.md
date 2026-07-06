# MiniClaw2 Design Review & Refactor Proposal (2026-07)

A fresh review of the codebase against `PHILOSOPHY.md`, written without
reference to `PROPOSAL_REFACTOR.md`. Scope: backend domain / runner /
registry / contextspace / app, provider adapters, frontend canvas +
panels, and the documentation set.

Verdict up front: **the core architecture is sound and unusually
faithful to its philosophy.** The preview contract, the reap pipeline,
category-gated plan writes, reviews-as-agents, and the materialized
lane projection are all implemented essentially as §6–§9 describe, with
good invariant enforcement (strict-whitelist schemas, cycle checks,
atomic persistence, repair retries). The problems are second-order:
three doctrine/code conflicts that need an explicit decision, a handful
of structural god-modules on both sides of the wire, real fragility in
the PTY-driving provider path, and documentation drift that undermines
the repo's own "single source of truth" discipline.

---

## 1. Doctrine conflicts — decide, then write it down

These are places where the code and `PHILOSOPHY.md` disagree. Per the
philosophy's own preamble, the doc is the position to argue from — so
each of these needs either a code fix or a philosophy amendment. Right
now they are silent drift.

### 1.1 `verifier` kind vs. §6.1 and §9.2

`PHILOSOPHY.md` §6.1 defines exactly two kinds (`agent`, `op`) and §9.2
is explicit: *"Type-A questions never produce review virtuals; the
executing agent calls them inline."* The code has a third kind:

- `NodeKind.VERIFIER` (`domain.py:32`), `ReviewSubtype.PROGRAMMATIC_REVIEW`
  (`domain.py:55`), a full runner path (`runner.py:330-478`), and
  preview-schema support (`preview.py:47-51`).

A verifier is precisely a Type-A machine check reified as a
review-category node — the thing §9.2 says must not exist. Yet it is
useful (deterministic checks as first-class steps in a template DAG,
with normal previews and error states), template-only, and guarded
against agent authorship at reap (`reap.py:214-219`).

**Proposal:** keep verifiers, amend the philosophy. Add to §6.1 a third
kind with its constraint set (template-only provenance, no provider
session, brief required), and soften §9.2 to "Type-A checks are either
called inline by the executing agent or run as framework-owned verifier
nodes; they never become *review agent* virtuals." The alternative —
reclassifying verifier as an `op` with a brief — is a larger migration
for no behavioral gain.

### 1.2 `--dangerously-skip-permissions` vs. "the interesting events are human gates"

§1 stakes the product on human gates; §6.1 lists permission among the
inline gates. But the primary provider runs the native CLI with
`--dangerously-skip-permissions` and plan mode disabled
(`IMPLEMENTATION_STATUS.md` §1; `providers/claude_native/spawn.py`).
Consequences:

- Permission gates exist only on Codex. On Claude — the default —
  an agent node can run any command against the user's real repository
  with no gate ever opening.
- This is both a philosophy divergence and the single largest safety
  exposure in the product.

**Proposal:** restore permission gating on Claude using the mechanism
already built for `AskUserQuestion`: the `PreToolUse` hook bridge.
`claude_hook_bridge` → `POST /hook/ask` → `GateSubtype.PERMISSION` →
allow/deny/updated-input returned to the hook is exactly the shape a
permission gate needs; the plumbing (token auth, dispatcher registry,
gate futures in `NodeRunner._request_gate`) already exists. Ship it
behind the existing `permission_mode` project setting so trusted
workflows can keep the current bypass. Until then, PHILOSOPHY §6.1
should carry an honest caveat rather than describing gates that never
fire.

### 1.3 "Session" as the wire vocabulary

The domain model says `Project`; the entire REST/WS surface says
`session` (`app.py` throughout, `frontend/src/api.ts`). Meanwhile the
philosophy bans "provider session" from user surfaces — and the code
now has *two unrelated meanings* of "session" (project-as-session vs.
`provider_session_id` / `cli_session_id`), which is a standing source
of confusion in both codebases.

**Proposal:** rename the wire surface to `/projects/...` with a
deprecation alias for `/sessions/...`, and rename frontend types
(`SessionInfo` → `ProjectInfo`, `useSessionSocket` → `useProjectSocket`).
Mechanical, low-risk, best done before more endpoints accrete.

---

## 2. Backend structure

### 2.1 `runner.py` — one class, three node kinds, tangled finalization

`NodeRunner` (`runner.py:66`) dispatches on kind (`run()`,
`runner.py:137-143`) into three near-parallel lifecycles that share a
skeleton (commit_before → bundle snapshot → transition → `NodeStarted`
→ execute → finalize preview → transition → `TurnDone`) but duplicate
it by hand:

- `NodeStarted(...)` construction is copy-pasted three times
  (`runner.py:160-173`, `280-292`, `336-348`).
- `_run_agent` (`runner.py:145-267`) nests three `try` levels with
  `final_state` reassigned in six places and two distinct
  `CancelledError` paths. It is correct today, but every new
  requirement (e.g. a second op kind, mid-session graph reap) lands in
  this knot.
- Preview-stub rendering exists in three shapes with similar-but-
  different strings: `runner._write_stub_preview` (`runner.py:723`),
  the stale-node repair in `registry._repair_stale_nodes`
  (`registry.py:169-182`), and `materialize.render_node_preview` stubs
  (`materialize.py:45-58`).

**Proposal:** split into `AgentNodeRunner` / `OpNodeRunner` /
`VerifierNodeRunner` sharing a small base (`_start`, `_finalize`,
`_emit_node_started`, `_emit`), and centralize stub-preview rendering
in `preview.py` with a single `render_stub_preview(node, reason)`
entry. Target: `_run_agent`'s body becomes a linear sequence of named
phases (`await_human_prose`, `run_turn`, `reap_or_repair`, `finalize`).

### 2.2 Op/verifier nodes snapshot context bundles they never use

`_run_op` and `_run_verifier` both call `_snapshot_context_bundle()`
(`runner.py:277`, `333`), which composes the full context bundle and
**persists a snapshot JSON** to `contextspace/snapshots/` — for a
commit op that consumes no context. Combined with the fact that no GC
exists for `snapshots/`, every node of every kind adds a file forever.

**Proposal:** (a) skip bundle composition for `op` kind entirely
(verifiers arguably keep it — the settings snapshot is meaningful);
(b) add snapshot retention — simplest is prune-on-startup keeping the
last N per project, since `node.context_bundle_id` already records the
authoritative reference and stale audit files are the only casualty.

### 2.3 `registry.py` — orchestration, UI persistence, and validation in one class

`ProjectRegistry` (1,400 lines) currently owns five unrelated
responsibilities: project CRUD, **frontend layout persistence**
(`update_layout_hints`, `update_planspace_view` — pure UI state),
virtual CRUD + validation, promotion scheduling, and gate/interrupt
proxying. Specific pressure points:

- `create_virtual` (`registry.py:821-960`) and `update_virtual`
  (`registry.py:962-1100`) duplicate ~80 lines of category/subtype/
  brief normalization; the same rules exist a third time in the reap
  path (`preview.py` validators). One `virtual_rules.py` module should
  own normalization for both the API path and the reap path.
- Auto-promotion is triggered from five call sites
  (`_on_runner_done`, `update_planspace_mode`, `create_virtual`,
  `update_virtual`, `promote_next_virtual`) each re-implementing the
  guard dance (active lane? mode? busy?). Extract a
  `PromotionScheduler.maybe_promote(rt)` with the guards inside; call
  sites become one-liners.
- The `try: asyncio.get_running_loop().create_task(rt.broadcast(...))
  except RuntimeError: pass` boilerplate repeats three times
  (`registry.py:811-818`, `939-946`, `1080-1087`, `1146-1153`) —
  extract `_broadcast_soon(rt, event)`.
- The review-node "seed a fake virtual preview" block in `start_node`
  (`registry.py:528-546`) fabricates a VIRTUAL-state copy of a QUEUED
  node so materialization has metadata. It works, but the honest fix is
  to make directly-launched reviews go through the create-virtual →
  promote path so they are never a special case.

**Proposal module split:** `registry.py` (runtime + lifecycle),
`virtuals.py` (create/update/delete/normalize, shared with reap),
`promotion.py` (scheduler), and fold layout/planspace-view persistence
into a thin `project_prefs.py` or directly into `app.py`-level calls to
`Store` — it needs no orchestration.

### 2.4 `Store.list_nodes` is O(all-nodes disk reads) in hot paths

`store.py:125-135` re-reads and re-validates every `node.json` on each
call. Callers include the auto-promotion candidate scan (after **every**
node completion), reap cycle detection, lane materialization (before
every launch), `delete_virtual` blocker scans, and `turn_count` — which
runs per project on every `GET /sessions`. Single-user scale hides
this; a 200-node project on a landing page with 20 projects does
4,000 file reads + Pydantic validations per poll.

**Proposal:** the store is single-writer by construction (its own
docstring guarantees it), so an in-process `dict[str, dict[str, Node]]`
cache invalidated in `update_node`/`create_node`/`delete_node` is
trivial and safe. This also removes the main motivation for the
deferred SQLite migration.

### 2.5 `contextspace.py` — four modules wearing one trench coat

1,249 lines mixing: root resolution, bundle composition, binding CRUD,
planspace CRUD, skill listing/deletion, UI-facing describe summaries,
and YAML IO helpers. Symptoms of strain: the local
`from .context_refresh import context_refresh_status` inside
`describe_project_contextspace` (`contextspace.py:409`) to dodge an
import cycle, and `describe_*` returning untyped `dict[str, Any]` blobs
that `app.py` exposes verbatim and the frontend mirrors loosely.

**Proposal:** split into `contextspace/{bundle,bindings,planspaces,
skills,describe}.py`; give the describe payloads Pydantic response
models so the wire contract is checkable against
`frontend/src/types.ts`.

### 2.6 Smaller backend items

- **`app.py` guard duplication:** the
  `is_running` / `_context_task_running` 409-check triple appears in
  ~8 endpoints — replace with a FastAPI dependency
  (`require_idle_project`).
- **`context_refresh.py` module-global `_TASKS`** breaks the otherwise
  clean "runtime state lives on `ProjectRuntime`" ownership; move the
  task record onto the runtime object.
- **`events.py:42`** still admits `"checkpoint_review"` in
  `InteractionRequest` — documented as legacy; give it a removal date
  or a `# legacy-replay-only` comment tied to a migration note.
- **`GateKind`** (`domain.py:87-88`) is a one-member enum; either
  delete the axis or leave a comment saying why it is reserved.

---

## 3. Frontend structure

(Findings from a full-source review; the philosophy compliance story is
good — schema vocabulary is correctly quarantined in the Inspect
drawer, gates render inline rather than in modals, and the composer is
genuinely a virtual node on the canvas.)

### 3.1 One banned word on a primary surface

`App.tsx:1695` renders the awaiting-response banner as
`` `Node ${active.nodeId.slice(0, 8)} is awaiting your ${labelKind}.` `` —
"node" plus a raw hex id, both banned by §4. Should read from the
tile's human label, e.g. *"A step is awaiting your answer"* with a
click-through.

### 3.2 God components at the inflection point

- **`App.tsx` (1,727 lines):** 30+ `useState`, 6 refs mirroring state,
  a single `handleEvent()` switch, context-bundle prefetch logic,
  layout-save promise chaining, and gate/review routing all in one
  component. Extract hooks: `useNodeState(sessionId)`,
  `useContextSpace(sessionId)`, `useBundlePrefetch`, `useSelection`.
- **`AgentPanel.tsx` (1,383 lines):** virtual-draft editing, executed
  transcript rendering, review forms, and inspect drawer in one file.
  Extract `VirtualDraftEditor` (form state + validation) — this also
  unblocks reusing the editor in canvas inline expansion later.
- **`layout.ts` (1,019 lines):** hand-rolled geometry with two
  coordinate regimes (absolute vs. lane-relative), cursor-tracking
  helpers, and O(n²) descendant scans. Separate "resolve lane
  membership/order" (data) from "compute positions" (geometry) from
  "make React Flow nodes" (presentation).

### 3.3 Duplicated interaction paths

Gate/review resolution renders in two places (canvas inline expansion
under the tile, and the side panel form) with independently maintained
payload marshaling. Unify into one `PendingInteractionHandler` with a
`variant` prop; both mount points share submission logic. Same story
for virtual promotion buttons (tile action stack + panel header).

### 3.4 No fetch layer discipline

`api.ts` is a thin fetch wrapper; deduplication and caching are
hand-rolled at call sites (`inflightBundleFetchRef`, prefetch cap of 6,
manual `.catch()` swallowing). A ~100-line query-cache hook (or
TanStack Query) keyed on `(sessionId, nodeId, finished_at)` removes a
whole class of races and re-fetches.

### 3.5 Prop drilling

`SidePanel` inner receives 27 props; `AgentPanel` 17. Introduce a
`PanelActionsContext` (resolve gate, promote, update virtual, open
inspect) so subpanels stop threading callbacks three levels deep.

---

## 4. Provider layer robustness

The PTY-driven native-CLI approach is a deliberate bet (everything the
CLI ships works for free) and the adapter code is careful, but the
review found real fragility:

1. **No server-side timeout on `/hook/ask`** (`app.py:236-252`): if a
   gate dispatcher hangs, the hook bridge's POST hangs for its full
   600 s timeout and Claude never falls back. Wrap the dispatcher call
   in `asyncio.wait_for(..., 120)` and return a structured failure so
   the bridge exits passthrough. *(Highest-value single fix in this
   section.)*
2. **Submit-confirmation fingerprint fallback can misattribute**
   (`claude_native/input.py:157-181`): the fallback scans recent JSONL
   files for the prompt text within an mtime window; two concurrent
   projects submitting similar prompts can cross-match. Prefer
   lengthening the marker-confirmation window and draining the session
   JSONL directly before falling back to the fingerprint scan.
3. **Session retarget replays history** (`claude_native/__init__.py:283-288`):
   after `/clear`-style session rotation, `_jsonl_offset` resets to 0
   and prior turns re-emit as current-node events. On retarget, seek to
   EOF instead.
4. **Orphaned PTY on early exceptions:** if spawn succeeds but an
   exception fires before the event iterator is consumed, `close()` is
   never called. Wrap session lifetime in `try/finally` at the provider
   boundary; also add lifespan-shutdown interruption of in-flight
   runners (today SIGTERM leaves PTYs running).
5. **Duplicated normalization between adapters:** diff detection,
   result truncation, and token-usage extraction exist independently in
   `claude_native/transcript.py` and `codex.py` and will drift. Extract
   `providers/translation.py`.
6. **Base-interface leaks:** `AgentProviderContext.tool_allowlist` is
   Claude-only (Codex silently ignores it) and `minimal_mode` is
   reinterpreted per adapter. Document per-provider semantics on the
   field, or move them to provider-specific options.
7. **No CLI version guard:** spawn args (`--append-system-prompt`,
   `--disallowed-tools`, `--dangerously-skip-permissions`) are assumed
   stable. Record `claude --version` at spawn into
   `settings_snapshot` and log a warning when it changes between
   launches — cheap drift detection.

---

## 5. Dead code & documentation drift

Verified dead (production-unreferenced):

| Item | Location | Note |
|---|---|---|
| `ContextBundle` model | `domain.py:259-268` | Docstring claims `compose_context_bundle` uses it; that function returns `ComposedContextBundle`. Delete. |
| `context.py` (`load_project_context`) | `backend/miniclaw2/context.py` | Only referenced by its own test; `contextspace._read_context_source` superseded it. Delete with test. |
| `paths.py` (`validate_project_relative_path`) | `backend/miniclaw2/paths.py` | No callers (the used `paths.py` is `providers/claude_native/paths.py`). Delete. |

Documentation drift — the repo's contract is "PHILOSOPHY = destination,
IMPLEMENTATION_STATUS = ledger", but two other documents contradict
both:

- **`README.md`** still describes the retired output-contract enum
  (`freeform` / `summary` / `interface` / `review_brief`), the removed
  `PhantomNode` / `GatePanel` / `ArtifactPanel`, a nonexistent
  `artifacts.py`, `scenarios/` (now `templates/`), planspace
  `STATUS.md` / `PLAN.md` / `SKILLS.md` / memory-delta inbox files, and
  a `POST /sessions/{sid}/contextspace/bootstrap` endpoint that no
  longer exists.
- **`CONTEXT.md`** references `scenarios/bundled/`, `GatePanel`,
  "Claude Code SDK" (now native CLI), and "passive review gates" —
  all retired. Since CONTEXT.md is injected into **every agent
  launch**, this is not cosmetic: it actively misleads every agent the
  framework runs. This is the same self-poisoning failure mode §8.4
  warns about, at project scope.

**Proposal:** run the framework's own `context/refresh` flow on this
repo, then trim README to run-instructions + pointers (architecture
belongs to PHILOSOPHY/IMPLEMENTATION_STATUS; duplicated prose is what
rotted). Consider a CI-ish verifier that greps README/CONTEXT for
retired terms (`PhantomNode`, `scenario`, `output_kind`, `STATUS.md`).

---

## 6. Prioritized plan

Ordered by leverage-per-risk; each phase is independently landable.

**Phase 0 — truth restoration (hours, no code risk)**
1. Fix `CONTEXT.md` and `README.md` drift (§5). CONTEXT.md first — it
   poisons every launch.
2. Delete the three dead modules/models (§5).
3. Philosophy amendments for verifier (§1.1) and the permission-gate
   caveat (§1.2), so doc and code stop silently disagreeing.

**Phase 1 — safety & robustness (days)**
4. `/hook/ask` server-side timeout (§4.1).
5. Session-retarget EOF seek + fingerprint-fallback hardening
   (§4.2–4.3).
6. Provider lifetime `try/finally` + lifespan shutdown interruption
   (§4.4).
7. Skip bundle snapshots for ops; snapshot retention (§2.2).

**Phase 2 — backend refactors (1–2 weeks, mechanical, test-backed)**
8. Runner split into per-kind runners + single stub-preview renderer
   (§2.1).
9. Registry split: `virtuals.py` + `PromotionScheduler` +
   `_broadcast_soon`; move layout/planspace-view persistence out
   (§2.3).
10. Store node cache (§2.4).
11. `contextspace/` package split + typed describe models (§2.5).
12. `providers/translation.py` shared normalization (§4.5).

**Phase 3 — frontend refactors (1–2 weeks)**
13. Extract `useNodeState` / `useContextSpace` / `useBundlePrefetch`
    hooks from `App.tsx`; `VirtualDraftEditor` from `AgentPanel`
    (§3.2).
14. Unified `PendingInteractionHandler` (§3.3).
15. Query-cache layer in `api.ts` (§3.4); `PanelActionsContext` (§3.5).
16. Banner wording fix (§3.1) — can land any time, one line.

**Phase 4 — product-level (needs its own design pass)**
17. Permission gating on Claude via the PreToolUse hook bridge (§1.2).
18. `/sessions` → `/projects` wire rename with alias (§1.3).
19. `layout.ts` geometry/data separation (§3.2) — largest pure-frontend
    refactor; schedule before the next visual-grammar change, not
    after.

Non-goals reaffirmed by this review: SQLite (the store cache removes
the pressure), schema-aware review forms (already a documented
non-goal), live mid-session reap (deferred is right — the terminal-time
walk-diff is what makes the category-rights enforcement atomic).
