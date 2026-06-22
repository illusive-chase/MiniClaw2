# MiniClaw2 Implementation Status

Companion to `PHILOSOPHY.md`. The philosophy doc describes the
destination; this doc is the single source of truth for what the code
actually does. Where the two disagree, the philosophy doc states the
position and this doc names the unresolved gap.

Trunk files are mentioned only as orientation; subordinate modules and
test files are left implicit. Items are bucketed by subsystem, then
split into **Landed** and **Pending**. Pending items are not
sequenced — dependencies are noted inline where they matter.


## 1. Backend domain model

Trunk: `backend/miniclaw2/domain.py`.

### Landed

- `NodeKind ∈ {agent, op, verifier}`.
- `NodeState ∈ {virtual, queued, running, waiting, awaiting_human_input, done, error, cancelled}`.
- `Category ∈ {planning, regular, review}` and
  `ReviewSubtype ∈ {agentic_review, human_interact_review, programmatic_review}`.
- `Node` fields covering ontology in `PHILOSOPHY.md` §6.1: `parent_node_id`,
  `planspace_id`, `context_sources`, `context_bundle_id`,
  `context_bundle_path`, `provider`, `provider_session_id`,
  `provider_turn_id`, `sdk_session_id` (legacy alias),
  `commit_before`, `commit_after`, `prompt`, `category`, `subtype`,
  `brief`, `prompt_draft`, `scheduled_deps`, `resume_from_node_id`,
  `verify_script_ref`, `proposed_by`, `obsolete_reason`, `summary`,
  `error`, `usage`, `system_context_snapshot`, `settings_snapshot`,
  `created_at`, `started_at`, `finished_at`.
- `Project` fields: `root_path`, `name`, `provider`, `head_commit`,
  `parent_project_id`, `parent_commit`, `project_context_binding_id`,
  `settings_override`, `temporary`, `template_id`,
  `layout_hints`, `layout_viewport`, `planspace_view`,
  `created_at`.
- `HumanGate` model is inline-only:
  `GateSubtype ∈ {permission, ask_user, plan_approval}`.
- Atomic JSON write (tmp + rename) via `store.py`; per-node JSONL
  event log; gates.jsonl audit trail.

### Pending

_None — the legacy output ontology has been removed._


## 2. Provider adapters

Trunk: `backend/miniclaw2/providers/` (`base.py`, `claude.py`, `codex.py`).

### Landed

- Claude adapter over `claude-agent-sdk`. Per-session `ClaudeSDKClient`
  with `system_prompt` preset = `claude_code`, `CONTEXT.md` appended
  via `system_prompt.append`. Plan-mode happy path returns
  `Allow(updated_permissions=[setMode acceptEdits])`. Interrupt wired
  to the SDK reader. Surfaces `ThinkingBlock` as a `thinking` event.
- Codex adapter over `codex app-server` JSON-RPC. Per-session thread.
  `CONTEXT.md` prepended to `turn/start` input on fresh threads
  (resumed threads keep the context they were started with). Maps
  Codex `requestUserInput`, command-execution, file-change, and
  permission approvals onto the same wire envelopes. Already streams
  per-delta.
- `NodeRunner` owns the state machine and persistence; providers do
  IO only. Cleanup resolves any open `HumanGate` as denied when the
  node ends.

### Pending

- Per-token streaming on Claude. Pinned `claude-agent-sdk` exposes no
  partial-message option; revisit when bumped.
- Cost estimate in the Usage strip (token counts are emitted; dollar
  conversion is not).


## 3. Node kinds

Trunk: `backend/miniclaw2/runner.py`, `backend/miniclaw2/registry.py`.

### Agent — landed

- Fresh provider session/thread on ordinary launch.
- Resume via `parent_node_id` when launched with `resume_from_node_id`;
  inherits `provider_session_id` / `sdk_session_id`. Resumed agent
  surfaces show `↻` continuation context.
- Inline gates (permission, ask-user, plan-approval) normalize to
  `waiting` substate; resolution returns the node to `running`.
- Launch instructions are composed from (in order): the category-aware
  block from `launch_prompt.build_category_launch_block` (planning /
  regular / agentic_review / human_interact_review), the planspace
  context bundle turn-text, the language preference hint, and the
  anti-self-poisoning guidance block appended last. Templates live in
  `backend/miniclaw2/prompts/category_*.md` and
  `prompts/anti_self_poisoning.md`; covered by `test_launch_prompt.py`.

### Review agents — landed

- Reviews are `agent` nodes with `category=review`.
- `agentic_review` launches like any other agent with the review brief
  in the category-aware launch block.
- `human_interact_review` first transitions to
  `awaiting_human_input`, emits
  `interaction_request {interaction_type: "human_review_prose"}`, writes
  the user's prose to durable and materialized `human-review.md`, then
  launches the reviewer agent.
- The reviewer's verdict is represented by its preview and any virtual
  graph mutations reaped from `.miniclaw2/graph/`.

### Verifier — landed

- Verifiers are `kind=verifier`, `category=review`,
  `subtype=programmatic_review` nodes.
- A verifier runs `bash <verify_script_ref>` in the project root with
  `CI=1`, `MINICLAW_HOME`, and `MINICLAW_PROJECT_ID`; it does not start
  a provider session.
- Exit 0 writes an executed preview with `summary="verify passed"` and
  transitions to `done`. Non-zero exit or timeout writes stderr/stdout
  tail into the preview, stores `node.error`, and transitions to
  `error`. Cancellation transitions to `cancelled`.
- Verifier virtuals are template-only. Agent-authored verifier virtuals
  are rejected by reap because they cannot safely supply
  `verify_script_ref`.

### Op — landed

- `commit` op only. Auto-appended after any `agent` node that
  reaches `done` when `project.settings_override.auto_commit` is
  truthy. Runs `git add -A && git commit -m miniclaw:node:<id>`.
- On success the preceding node's `commit_after` is rewritten to the
  new commit hash; a `node_updated` event broadcasts the change so
  the frontend can refresh diffs.
- `NodeStarted.kind` distinguishes op-node events so the frontend does
  not jump the selection to the op tile.

### Op — pending

- `fork-project` op (`git worktree add` → new `Project` row pointing
  at the new path, context bundle copied from source project).
- Other ops listed in older sketches (`checkout`, `reset`,
  `import-context`) — no concrete need yet.


## 4. ContextSpace

Trunk: `backend/miniclaw2/contextspace.py`, `backend/miniclaw2/context.py`.

### Landed

- ContextSpace root resolution: `$MINICLAW_CONTEXT_HOME` or
  `$MINICLAW_HOME/contextspace`. No silent bootstrap on first run.
- On-disk layout matching `PHILOSOPHY.md` §7:
  ```
  contextspace/
    contextspace.yaml
    bindings/projects/<binding-id>.yaml
    plugs/global/{manifest.yaml, CONTEXT.md}
    plugs/skills/<id>/{manifest.yaml, CONTEXT.md, assets/}
    plugs/planspaces/<id>/{manifest.yaml, events.jsonl}
    snapshots/<bundle-id>.json
  ```
- Plug loaders for project-root `CONTEXT.md`, global `CONTEXT.md`, and
  skill `CONTEXT.md`. Planspace plugs are manifest-only; lane state is
  read through `.miniclaw2/graph/lanes/<lane>/`.
- `ProjectBinding`, `PlugRef`, `ComposedContextBundle` carry binding
  resolution; `Project.project_context_binding_id` references the
  current binding; active planspace lives in
  `Project.settings_override["active_planspace_id"]`.
- Provider-neutral `<project_root>/CONTEXT.md` loading at every node
  launch. Claude receives it via `system_prompt.append`; Codex
  prepends it to fresh-thread `turn/start` input. Resolved text is
  snapshotted on the node as `system_context_snapshot`.
- Context bundle snapshot persisted to
  `snapshots/<bundle-id>.json` with source paths, sha256 hashes,
  plug ids, char counts, and injection modes (`system` / `turn`).
- ContextSpace bootstrap split into idempotent helpers:
  `ensure_contextspace_root`, `ensure_project_binding`, and
  `add_planspace_to_binding`.
- `POST /sessions/{sid}/planspaces` creates a new bound direction,
  activates it, and launches the concierge bootstrap agent node from a
  preset markdown prompt.
- Per-planspace mode lives in the planspace manifest and is read /
  written through `read_planspace_mode` / `set_planspace_mode`.
- `GET /sessions/{sid}/contextspace` returns binding and plug summaries
  including planspace mode, active lane, and hidden-lane state.
- Whitelisted file reads via `GET /sessions/{sid}/files?role=context`
  expose project-root `CONTEXT.md` for the UI.
- Per-project lane visibility is persisted in
  `Project.planspace_view` and updated via
  `PATCH /sessions/{sid}/planspace-view`.
- Out-of-band `CONTEXT.md` init / refresh tasks live in
  `backend/miniclaw2/context_refresh.py`. They run an agent against the
  project's provider using framework-held preset prompts
  (`prompts/context_init.md`, `prompts/context_refresh.md`) and a tight
  tool allowlist (`Read`, `Glob`, `Grep`, `Write`). The agent writes
  `CONTEXT.md` itself via `Write`; the framework only books
  `.miniclaw2/context.meta.json` on success. In-flight state surfaces on
  `GET /sessions/{sid}/contextspace` and can be cancelled via
  `POST /sessions/{sid}/context/cancel`. The task deliberately does not
  create nodes or append node event streams.
- Provider adapters support a `minimal_mode` flag on
  `AgentProviderContext` that skips `CONTEXT.md` self-injection, drives a
  tool whitelist (`PermissionResultAllow` / `Deny`) on Claude, and forces
  `approvalPolicy: "never"` on Codex. Used by the out-of-band
  `context_refresh` agent; unused by `NodeRunner`.

### Pending

- Vendor-specific on-disk context loading: `CLAUDE.md` walk
  (project + user), `.claude/settings.json` + `settings.local.json`
  → `permissions / env / hooks / mcpServers / allowedTools /
  disallowedTools`, `.claude/agents/*.md`, `.mcp.json`.
- Drag-and-drop plug UX; skill authoring UI; manifest editor for
  injection-mode and `max_chars`.
- Automatic ContextSpace git commits (v1 keeps changes visible).
- Cross-provider reviewer nodes (the ontology supports review agents;
  no UI to configure provider override yet).
- Fork merge semantics.
- Codex per-tool allowlist for out-of-band agent mode. Claude's
  `can_use_tool` callback enforces the whitelist directly; Codex has no
  per-tool callback, so the current `context_refresh` agent relies on
  `approvalPolicy: "never"` plus the preset prompt's self-constraint to
  scope tool use.


## 5. Frontend canvas

Trunk: `frontend/src/canvas/Canvas.tsx`, `frontend/src/canvas/layout.ts`,
`frontend/src/canvas/nodes/`, `frontend/src/canvas/edges/`,
`frontend/src/panel/`.

### Landed

- React Flow canvas; one canvas per project; pan/zoom per-project.
- Node kinds rendered: `AgentNode`, `OpNode`, `ContextNode`,
  `ErrorTerminalNode`, `PhantomNode`, `PlanspaceLaneNode`,
  `ProjectRootNode`. Passive `GateNode` and `ArtifactNode` have been
  removed.
- Polymorphic side panel: `AgentPanel`, `ContextNodePanel`, `OpPanel`,
  lane panel, `ProjectPanel` switched by selection (`SidePanel.tsx`).
- Phantom composer replaces the old launch modal. Resume source is
  implicit in spawn site; it now launches regular agent runs only.
- Virtual agent tiles render as dashed plan tiles with category badges,
  draft prompt text, ready/obsolete/dependency footer state, and a
  hover promote affordance.
- Virtual agent nodes can be edited from the side panel
  (`prompt_draft`, category/subtype/brief, motivation, dependencies,
  and obsoletion); verifier virtuals render as read-only
  programmatic-review steps.
- Agent tiles show category badges for planning / regular / review /
  human-interact review nodes; verifier tiles use the review tone and
  a programmatic label.
- Edges: timeline spine, resume (`↻` mid-glyph), review-agent edges,
  loads (dashed, auto-hidden unless endpoint hovered/selected).
- Op as edge chevron when the op has a downstream child; trailing
  ops without a child fall back to a tile.
- Error terminal nodes downstream of failed runs carry the `error`
  text in red.
- Planspace lanes render with persisted manifest color overrides
  (falls back to creation-order palette).
- Planspace lane visibility is per-project and persisted; hidden lanes,
  and their nodes are filtered from the canvas, while Project →
  Directions keeps a recovery row.
- The active direction is highlighted at lane level, and fresh
  phantoms drop on the active lane's trailing slot.
- Project `CONTEXT.md` sits in its own neutral top stripe above the
  planspace-colored "loaded context" lane.
- `AgentPanel` shows virtual node draft/provenance/dependencies/brief
  for virtuals; executed nodes show agent input, persisted
  `preview.json`, activity, thinking, and inspect drawer.
- Human-interact review prose form renders inside `AgentPanel` while
  the review node is in `awaiting_human_input`.
- Clicking a lane header opens a lane panel with manual/auto mode
  controls; Project → Directions also shows mode and hide/show.
- `PlanspaceFilePanel.tsx` now handles project-root `CONTEXT.md` only.
- `ProjectPanel.tsx` has Project actions (`+ New direction`,
  initialize/refresh project notes) and a Directions section with
  active badges, mode labels, and hide/show controls.
- Persisted `layout_hints` round-tripped through `project.json` via
  `PATCH /sessions/{sid}/layout-hints`.
- Tool I/O rendering: `Activity.result` (≤4 KB) + `result_kind ∈
  {stdout, diff, text, json}`. Collapsible `<details>` with diff
  coloring; failed-default-open.
- Markdown rendering for assistant text (`react-markdown` + GFM +
  `highlight.js` with github-dark).
- Reconnect replay: `node_started` server event + `replay_request
  {node_id, since_seq}` client envelope; replay consumes
  `events.jsonl` then attaches to live tail. WS 4xxx close codes
  suppress the auto-reconnect loop.
- Inline pending requests (permission / ask-user / plan-approval)
  render at the top of `AgentPanel`. An amber canvas banner surfaces
  "Node X is awaiting your response" when the user is inspecting a
  different node.
- Inline pending requests (permission / ask-user / plan-approval)
  also expand directly under the waiting agent tile on the canvas,
  using the same response mapping as `AgentPanel`.
- Projects landing page (`ProjectsLanding`) with rename/delete; Tests
  modal for bundled templates.

### Pending

- User-editable verifier virtual controls. Agent virtual editing is in,
  but verifier virtuals remain read-only because script selection is a
  template-owned concern.
- Schema-aware review forms (PRD §8.7). Cancelled — user judgment is
  free-form by design; left here as a documented non-goal.


## 6. Preview / virtual-node output model

Executed and virtual nodes both carry a `preview.json` (strict-whitelist
schema). The materialized active-lane subtree is the agent's read/write
surface; the durable node store is the source of truth.

### Landed

- Every executed node is expected to write its own
  `.miniclaw2/graph/lanes/<lane>/nodes/<nid>/preview.json`; the runner
  reaps it into `projects/<pid>/nodes/<nid>/preview.json`.
- Planning and review agents may create or mutate virtual previews in
  the materialized graph; regular agents are rejected if they write
  virtuals.
- Virtual preview writes are validated, canonicalized from slugs to
  node ids, cycle-checked, persisted atomically, and surfaced on the
  canvas after reap.
- Missing/malformed own preview or invalid graph writes drive up to
  three inline repair turns in the same provider session; only after
  the retry bound is exhausted does the runner end the node as `error`
  with a framework-written "preview contract abandoned" stub preview.
- Cancelled/error runs skip virtual reap and get framework stub
  previews.
- Anti-self-poisoning prompt is appended last in launch instruction
  composition and applies to preview content as guidance.

### Deferred

- Live mid-session graph-write canvas updates; v1 remains reap-only.


## 7. CLI-parity gaps

The wrapper matches the native `claude` CLI on the items below.
Vendor-specific config loading is the largest remaining drift.

### Landed

- Provider-neutral `<project_root>/CONTEXT.md` (the textual substitute
  for vendor-specific project context).
- Plan-mode approve returns the correct permission update (no longer
  treated as a Deny).
- Interrupt wired (Stop button → `cancelled`).
- `ThinkingBlock` surfaced as a `thinking` event.
- Tool I/O result rendering for both providers.
- Markdown rendering for assistant text.
- Reconnect replay over the JSONL log.
- Codex `requestUserInput`, command/file/permission approvals mapped
  onto the same gate envelope; session-scoped allow via
  `acceptForSession`.

### Pending

- `CLAUDE.md` hierarchical walk (project + user) merged into Claude's
  preset.
- `.claude/settings.json` + `settings.local.json` →
  `ClaudeAgentOptions.permissions / env / hooks / mcpServers /
  allowedTools / disallowedTools`.
- `.claude/agents/*.md` → `agents=` SDK option.
- `.mcp.json` → `mcp_servers=` SDK option.
- `@file` / `!cmd` / image-paste input affordances.
- Permission dialog: `updated_input` editing, allow-always project
  scoping, `suggestions` rendering.
- Settings UI: model picker, permission-mode dropdown, cwd selector,
  tool allowlist.
- Queue user messages while a turn is in-flight (currently rejects).
- `ClaudeSDKClient` lifetime = session lifetime, not turn (would
  preserve MCP connections, permission state, skill caches across
  turns).
- Slash commands (`/clear`, `/compact`, `/model`, `/cwd`,
  `/permissions`) as frontend interceptors.
- Hooks lifecycle (`PreToolUse`, `PostToolUse`, `Stop`,
  `UserPromptSubmit`).
- Cost estimate (per-model rates × token counts).


## 8. Templates

A bundled recipe layer for canned multi-step launches. A template stamps
a complete lane of virtual nodes into a fresh temporary project; normal
virtual-node promotion drives the run from there. Parameterized
templates, slot interpolation, branching DSL, and loops remain deferred.

### Landed

- `backend/miniclaw2/templates/` provides `loader.py`, `launcher.py`,
  and `bundled/` template definitions with `template.yaml`,
  `lane.yaml`, `prompts/`, `scripts/`, and optional `seed/`.
- REST exposes `GET /templates`, `GET /templates/{name}`, and
  `POST /templates/{name}/run`. The old `/scenarios` and
  `/sessions/{sid}/verify` endpoints have been removed.
- `Project.template_id` records provenance for projects launched from a
  template. The old `scenario_name`, `scenario_step_history`, and
  `scenario_step_id` fields are gone.
- Template launch creates a temporary git workspace, applies
  provider/permission/auto-commit settings, creates a planspace, writes
  every lane entry as a virtual node, persists each virtual preview, and
  performs one auto-promotion pass.
- `lane.yaml` supports `agent` and `verifier` steps,
  `scheduled_deps`, review briefs, and `resume_from` for agent
  continuations. `promote_virtual` copies provider session/thread ids
  from the done resume parent.
- The Tests modal now lists bundled templates and opens the resulting
  project. There is no template-specific verify card or scenario-future
  node renderer; templates use the normal canvas, side panel, virtual
  editing, human-review form, and interrupt controls.
- Bundled templates: `hello-text`, `bash-uname`, `write-readme`,
  `permission-approve`, `plan-mode-approval`, `interrupt-midstream`,
  `context-md-respected`, `resume-fix-after-reject`, and
  `gui-calculator`. `reconnect-replay` was intentionally dropped
  because it required a test-only UI hook.

### Pending

- User-authored templates from the UI.
- Template parameters / slot interpolation / branching DSL.
- Template versioning beyond the instantiated node snapshots.


## 9. Multi-project / forks

`PHILOSOPHY.md` §6.2 names forks as the concurrency model. None of the
multi-project surface has been built.

### Status: not started

- Workspace UI for stacked project lanes.
- `fork-project` op (`git worktree add`).
- Context edges crossing project boundaries.
- Fork visualization, fork merge semantics.


## 10. Persistence

Trunk: `backend/miniclaw2/store.py`.

### Landed

- JSON + JSONL on disk under `$MINICLAW_HOME` (default
  `~/.miniclaw2/`). Single-user assumption.
- Layout:
  ```
  projects/<pid>/project.json
  projects/<pid>/nodes/<nid>/node.json
  projects/<pid>/nodes/<nid>/events.jsonl
  projects/<pid>/nodes/<nid>/gates.jsonl
  contextspace/...                # see §4
  ```
- Atomic JSON writes (tmp + rename). Single-writer per node is
  guaranteed by sequential intra-project execution.
- Reconnect replay reads `events.jsonl` from `since_seq` then attaches
  to the live tail; project-level WebSocket observers continue to
  receive live events after the JSONL gap is replayed.

### Pending

- SQLite migration. Deferred until cross-project queries (e.g. "list
  all nodes in `awaiting_human_input` across the workspace") actually
  become hot.


## 11. Wire envelopes (current set)

Quick reference; the on-disk shape is authoritative.

- Server → client:
  `node_started {node_id, parent_node_id, kind, category, subtype, prompt}`,
  `node_updated`, `interaction_request {interaction_type, ...}` with
  `interaction_type ∈ {"permission", "ask_user", "plan_approval",
  "human_review_prose"}` (`checkpoint_review` remains accepted in
  legacy replay/client types only), `text_delta`, `thinking`,
  `activity`, `usage`, `turn_done`, `error`.
- Client → server: user prompt with optional `resume_from_node_id`,
  `interrupt`, interaction response,
  `replay_request {node_id, since_seq}`.
- REST: project CRUD, node and event introspection,
  `PATCH /sessions/{sid}/layout-hints`,
  `PATCH /sessions/{sid}/planspace-view`,
  `PATCH /sessions/{sid}/planspaces/{planspace_id}/mode`,
  `POST /sessions/{sid}/planspaces {title, seed, mode?}` (creates a
  new planspace + activates it + launches the concierge planning
  agent),
  `POST /sessions/{sid}/virtuals/{vid}/promote` (manual promotion,
  returns and broadcasts the promoted node),
  `POST /sessions/{sid}/context/init`,
  `POST /sessions/{sid}/context/refresh`,
  `POST /sessions/{sid}/context/cancel`,
  `GET /sessions/{sid}/files`,
  `GET /sessions/{sid}/nodes/{nid}/preview` (durable preview text),
  `POST /sessions {auto_commit, ...}`.

No client-facing `start_gate_node` envelope remains; reviews are
ordinary agent nodes with `category=review`.
