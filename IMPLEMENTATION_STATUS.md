# MiniClaw2 Implementation Status

Companion to `PHILOSOPHY.md`. The philosophy doc describes the
destination; this doc tracks how far the code has actually travelled
toward it. Where the two disagree, the philosophy doc states the
position and this doc names the unresolved gap.

Trunk files are mentioned only as orientation; subordinate modules and
test files are left implicit. Items are bucketed by subsystem, then
split into **Landed** and **Pending**. Pending items are not
sequenced — dependencies are noted inline where they matter.


## 1. Backend domain model

Trunk: `backend/miniclaw2/domain.py`.

### Landed

- `NodeKind ∈ {agent, gate, op}`.
- `NodeState ∈ {queued, running, waiting, awaiting_review, done, error, cancelled}`.
- `AcceptanceState ∈ {not_applicable, unreviewed, accepted, rejected, blocked}`.
- `VerdictSource ∈ {none, human, deterministic, cross_provider, same_provider_advisory}`.
- `Node` fields covering ontology in `PHILOSOPHY.md` §5.1: `parent_node_id`,
  `planspace_id`, `context_sources`, `context_bundle_id`,
  `context_bundle_path`, `provider`, `provider_session_id`,
  `provider_turn_id`, `sdk_session_id` (legacy alias),
  `commit_before`, `commit_after`, `output_kind`, `output_path`,
  `output_contract_snapshot`, `prompt`, `contract`, `summary`,
  `error`, `usage`, `system_context_snapshot`, `settings_snapshot`,
  `scenario_step_id`, `review_outcome`, `acceptance_state`,
  `verdict_source`, `verdict_artifact_path`, `verdict_thread_id`,
  `accepted_at`, `rejected_at`, `created_at`, `started_at`,
  `finished_at`.
- `Project` fields: `root_path`, `name`, `provider`, `head_commit`,
  `parent_project_id`, `parent_commit`, `project_context_binding_id`,
  `settings_override`, `temporary`, `scenario_name`,
  `scenario_step_history`, `layout_hints`, `created_at`.
- `HumanGate` model with `GateKind ∈ {inline, checkpoint}`,
  `GateSubtype ∈ {permission, ask_user, plan_approval, checkpoint_review}`,
  `GateState ∈ {pending, resolved}`.
- Atomic JSON write (tmp + rename) via `store.py`; per-node JSONL
  event log; gates.jsonl audit trail.

### Pending

- `NodeOutputKind` enum (`freeform / summary / interface / review_brief`)
  is still in use. `PHILOSOPHY.md` §7 calls for collapsing per-node
  output kinds into "all outputs are planspace state updates"; this
  requires deprecating `output_kind`, `output_path`,
  `output_contract_snapshot` and the helper functions that produce
  default output paths and contract text.


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
- Output contracts are injected at launch as long as `output_kind ≠
  freeform`. Contracts and default artifact paths are still
  per-node (see §1 Pending).

### Gate (passive checkpoint) — landed

- `NodeRunner._run_passive_gate` skips the provider entirely and goes
  straight to `awaiting_review`.
- `InteractionRequest.interaction_type = "checkpoint_review"` carries
  the brief as `tool_input.contract`.
- Write-json response validates project-relative paths (rejects
  absolute, parent-traversal); loops on write errors so the user can
  retry without restarting the node. No-op response marks done.
- No client-facing `start_gate_node` envelope. `registry.start_gate_node`
  is an internal API used by the scenario expander and the
  user-launched-gate auto-spawner.
- Auto-spawn from upstream agent: when an agent with
  `output_kind: review_brief` reaches `done`,
  `registry._advance_user_gate` reads the brief and spawns a passive
  gate using the brief as the contract. Routes through the commit op's
  `parent_node_id` when `auto_commit` is on. Double-spawn is guarded
  by checking for an existing `GATE` child.
- Passive gate write-json response stamps acceptance fields
  (`acceptance_state`, `verdict_source`, `verdict_artifact_path`,
  `accepted_at` / `rejected_at`) on the **upstream source node**, not
  the gate node.

### Op — landed

- `commit` op only. Auto-appended after any `agent` / `gate` node that
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

Trunk: `backend/miniclaw2/contextspace.py`, `backend/miniclaw2/context.py`,
`backend/miniclaw2/planspace_state.py`.

### Landed

- ContextSpace root resolution: `$MINICLAW_CONTEXT_HOME` or
  `$MINICLAW_HOME/contextspace`. No silent bootstrap on first run.
- On-disk layout matching `PHILOSOPHY.md` §6:
  ```
  contextspace/
    contextspace.yaml
    bindings/projects/<binding-id>.yaml
    plugs/global/{manifest.yaml, CONTEXT.md}
    plugs/skills/<id>/{manifest.yaml, CONTEXT.md, assets/}
    plugs/planspaces/<id>/{manifest.yaml, STATUS.md, PLAN.md,
                           SKILLS.md, events.jsonl,
                           inbox/<node-id>.memory-delta.json}
    snapshots/<bundle-id>.json
  ```
- Plug loaders for project-root `CONTEXT.md`, planspace STATUS/PLAN,
  and skill `CONTEXT.md`.
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
- Planspace `STATUS.md` auto-writer runs only when an explicit
  planspace binding exists.
- Planspace state schema (`planspace_state.py`):
  - YAML frontmatter slots — `goal`, `current_state`, `open_questions`,
    `decisions`, `out_of_scope`. `unknown` is a first-class slot value.
  - Append-only `Q1 / Q2 / …`, `D1 / D2 / …` IDs.
  - `derive_plan_markdown()` regenerates PLAN.md from STATUS on every
    refresh; PLAN.md is never edited directly.
- Memory delta inbox:
  - Agents write a project-local artifact at
    `.miniclaw2/outputs/<node-id>/memory-delta.json`.
  - On terminal transition the runner validates the file and copies
    it into the snapshotted planspace's
    `inbox/<node-id>.memory-delta.json`.
  - Only `STATUS.md`-targeted updates are auto-applied.
  - Failed / cancelled / rejected-review nodes do not promote
    durable updates.
- Passive gate write-json response stamps acceptance fields on the
  upstream source node (see §3 Gate).

### Memory delta JSON shape (wire-level)

```json
{
  "version": 1,
  "node_id": "<node id>",
  "project_id": "<project id>",
  "binding_id": "<binding id>",
  "created_at": 1234567890,
  "terminal_state": "done",
  "acceptance_state": "unreviewed",
  "updates": [
    {
      "target": "STATUS.md",
      "operation": "append_observation",
      "policy": "auto",
      "confidence": "observed",
      "evidence": {
        "artifact": ".miniclaw2/outputs/<node-id>/result.md",
        "event_seq": 42
      },
      "text": "Implemented X; tests Y passed; blocker Z remains."
    },
    {
      "target": "PLAN.md",
      "operation": "propose_patch",
      "policy": "proposed",
      "reason": "The next milestone changed after implementation.",
      "patch": "..."
    }
  ]
}
```

Supported `STATUS.md` operations (auto-applied):

- `append_observation` / `append_body` / `append_note` — append text to
  the body.
- `rewrite_current_state` — replace the `current_state` slot.
- `add_open_question` — append a new `Q*` entry.
- `add_decision` — append a new `D*` entry.
- `add_out_of_scope` — append an out-of-scope note.

`PLAN.md` updates are recorded as proposed and not applied — PLAN is
derived from STATUS. Skill and protocol updates remain proposed.

### Pending

- Anti-self-poisoning pre-commit filter (`PHILOSOPHY.md` §7.4). The
  runner currently does not inject a fixed pre-commit prompt template
  before STATUS writes; transient errors and negative tool claims can
  reach durable state if the agent does not filter them itself.
- Vendor-specific on-disk context loading: `CLAUDE.md` walk
  (project + user), `.claude/settings.json` + `settings.local.json`
  → `permissions / env / hooks / mcpServers / allowedTools /
  disallowedTools`, `.claude/agents/*.md`, `.mcp.json`.
- Drag-and-drop plug UX; skill authoring UI; manifest editor for
  injection-mode and `max_chars`.
- Automatic ContextSpace git commits (v1 keeps changes visible).
- `query_pack` compressed STATUS view for context-budget pressure.
- Cross-provider reviewer nodes (the ontology supports the
  `cross_provider` verdict source; no UI to invoke it).
- Fork merge semantics (importing fork memory deltas into parent
  planspace inbox).
- Verifier op nodes that own deterministic acceptance and write
  acceptance back to the source node.


## 5. Frontend canvas

Trunk: `frontend/src/canvas/Canvas.tsx`, `frontend/src/canvas/layout.ts`,
`frontend/src/canvas/nodes/`, `frontend/src/canvas/edges/`,
`frontend/src/panel/`.

### Landed

- React Flow canvas; one canvas per project; pan/zoom per-project.
- Node kinds rendered: `AgentNode`, `GateNode`, `OpNode`,
  `ArtifactNode`, `ContextNode`, `ErrorTerminalNode`,
  `PhantomNode`, `PlanspaceLaneNode`, `ProjectRootNode`.
- Polymorphic side panel: `AgentPanel`, `GatePanel`, `ArtifactPanel`,
  `ContextNodePanel`, `OpPanel`, `ProjectPanel` switched by selection
  (`SidePanel.tsx`).
- Phantom composer replaces the old launch modal. Resume source is
  implicit in spawn site. Currently still exposes an intent-chip row
  (`Explore` / `Build & summarize` / `Hand off for review` / `⋯
  Interface`); see §6 Pending.
- Edges: timeline spine, resume (`↻` mid-glyph), produces (artifact
  authorship), reviews (gate ← brief), loads (dashed, auto-hidden
  unless endpoint hovered/selected).
- Op as edge chevron when the op has a downstream child; trailing
  ops without a child fall back to a tile.
- Error terminal nodes downstream of failed runs carry the `error`
  text in red.
- Memory-delta `+Δ` arrow from an agent into the context node it
  updated (badge shows applied / proposed counts).
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
- Projects landing page (`ProjectsLanding`) with rename/delete; Tests
  modal.

### Pending

- Inline gate **tile-expansion** on the canvas itself. Currently
  pending requests render in `AgentPanel`; `UI_REDESIGN` direction
  calls for expanding the agent tile in place so the question lives
  where the user is already looking.
- Phantom future scenario steps (dashed phantoms ahead of the cursor,
  alternate paths for `on_state` branches).
- Reviewer-written JSON files are not always materialized as artifact
  nodes (only briefs and `result.md` consistently appear).
- Schema-aware review forms (PRD §8.7). Cancelled — user judgment is
  free-form by design; left here as a documented non-goal.


## 6. Planspace / output redesign

This is the largest in-flight ontology shift. Some pieces have landed;
the front-of-house changes have not.

### Landed

- STATUS.md frontmatter + body schema (see §4 above).
- PLAN.md as derived view.
- `merge_reviewed_update_markdown()` performs the template-driven
  merge of interim delta + free-form user judgment when a passive
  gate resolves.
- `Node.acceptance_state` and `verdict_*` fields decouple "done" from
  "accepted."
- Planspace lane rendering (`PlanspaceLaneNode`).
- Memory delta inbox driving STATUS updates (see §4).

### Pending

- **Backend.** Collapse `output_kind` / `output_path` /
  `output_contract_snapshot` into a single planspace-commit
  mechanism. Today every node still picks a per-node output contract
  and writes to `.miniclaw2/outputs/<node-id>/`; the redesign treats
  every node output as a planspace state update first, with per-node
  files demoted to artifacts of the agent's work.
- **Backend.** Anti-self-poisoning pre-commit filter prompt template
  injected before every planspace state write (§4 Pending).
- **Frontend.** Replace the intent-chip row in `PhantomNode.tsx`
  (`Explore` / `Build & summarize` / `Hand off for review` /
  `⋯ Interface`) with a single "needs review" toggle. The intent of
  the run lives in the prompt text.
- **Frontend.** Remove the brief artifact node and the review-response
  artifact node from `layout.ts`. The brief and the user's response
  are gate-internal transient packets, not durable artifacts.
- **Frontend.** Gate hexagon inline expansion: the brief and free-form
  textarea live inside the hexagon, not in a sibling artifact.
- **Frontend.** `AgentPanel` "Result" rewrite — show the planspace
  delta this node proposed / committed, not `output_kind`-keyed
  branching.
- **Frontend.** STATUS.md viewer + slot-aware editor in the planspace
  side panel (selected by clicking the lane header).
- **Frontend.** Cross-lane `loaded from:` chip on tiles whose
  `loads` edges cross planspaces.
- **Frontend.** Neutral project-CONTEXT top stripe distinct from the
  planspace palette.
- **Frontend.** Planspace palette persistence (`color: indigo` etc.
  recorded in the planspace manifest, drawn from in creation order
  when manifest is empty).
- **Vocabulary.** Drop `output kind`, `output path`, `output contract`,
  `memory delta`, `review brief`, `review response`, `verdict`,
  `acceptance` from primary surfaces — keep them in Inspect.
- **Deferred.** Compressed `query_pack` view (defer until context
  budget pressure is measurable); micro-agent merge for paragraph-
  scale user verdicts (defer until template-merge quality degrades).


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


## 8. Templates (deferred)

A declarative recipe layer for canned multi-step launches ("build →
review → fix → snapshot"). The earlier design sketched YAML templates
with slot interpolation, `on_state` branching, and `next:` loops, plus
a registry-level `TemplateExpander` invoked on `runner_done`.

### Status: not started

- No `backend/miniclaw2/templates/` package, no `TemplateDefinition`,
  `TemplateInstance`, or `TemplateExpander` classes.
- `Node.created_by` and `Node.template_step_id` fields are not
  present (would be added when the engine ships).
- No composer UI template picker; no ghost-step rendering.

`backend/miniclaw2/scenarios/` (loader + launcher + verify) provides
adjacent machinery for multi-step demo flows used by the Tests modal.
The scenario expander (`_advance_scenario_step`) overlaps with what a
template engine would do but is intentionally narrower (single hand-
written YAML per demo, no slot interpolation, no `on_state`
branching).


## 9. Multi-project / forks

`PHILOSOPHY.md` §5.2 names forks as the concurrency model. None of the
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
  all nodes in `awaiting_review` across the workspace") actually
  become hot.


## 11. Wire envelopes (current set)

Quick reference; the on-disk shape is authoritative.

- Server → client: `node_started {node_id, parent_node_id, kind, prompt}`,
  `node_updated`, `interaction_request {interaction_type, ...}` with
  `interaction_type = "checkpoint_review"` carrying brief as
  `tool_input.contract`, `text_delta`, `thinking`, `tool_use`,
  `tool_result`, `activity`, `usage`, `state_change`, `error`.
- Client → server: user prompt, `interrupt`, gate response,
  `replay_request {node_id, since_seq}`.
- REST: project CRUD, node and event introspection, artifact fetch,
  `PATCH /sessions/{sid}/layout-hints`, `POST /sessions
  {auto_commit, ...}`.

No client-facing `start_gate_node` envelope; gates are auto-spawned
server-side via the upstream agent's `output_kind: review_brief`.
