# MiniClaw2 long-term design

This document supersedes `PROPOSAL.md` (which remains as a punch list of
CLI-parity gaps in the current wrapper). It captures the architecture we
are converging toward over the next several phases.

> **Status (Phase 0 spine + Phase 1 graph shell + Phase 2 op/gate landed).** The spine is in.
> Domain model (`Project`, `Node`, `HumanGate`) persists to disk as
> JSON + JSONL under `$MINICLAW_HOME` (default `~/.miniclaw2`); SQLite
> from §9 is **deferred** in favor of JSON/JSONL while the schema is
> still exploratory. The legacy `/sessions` + `/ws/{sid}` wire
> protocol is kept as a 1:1 session-to-project compat layer so the
> existing UI works unchanged. Three CLI-parity items from
> `PROPOSAL.md` rode along with Phase 0: plan-mode happy path,
> interrupt button, `ThinkingBlock` surface. `NodeRunner` now
> delegates provider-native IO to adapters: Claude via
> `claude-agent-sdk`, Codex via `codex app-server` JSON-RPC. The
> chat-surface polish from `PROPOSAL.md` Phase 1 then landed as a
> follow-up pass: tool I/O rendering (`Activity.result` +
> `result_kind` carrying Bash stdout / Edit diffs / Read content from
> both providers), markdown rendering for assistant text
> (`react-markdown` + GFM + `highlight.js`), and WebSocket reconnect
> replay (`node_started` server event + `replay_request` client
> envelope, consuming the per-node JSONL log and re-attaching the new
> socket to live project events). The first graph-shell pass is also in:
> a single-project horizontal timeline, selected-node side panel, and
> read-only node/event/diff REST APIs, with explicit `node_updated`
> events for live tile/panel state. Per-token streaming remains
> deferred until the pinned `claude-agent-sdk` exposes partial messages.
>
> **Phase 2 first slice — provider-neutral `CONTEXT.md`.** A
> `<project_root>/CONTEXT.md` file is loaded at each node launch and
> injected uniformly across providers: Claude receives it via
> `system_prompt.append` on the `claude_code` preset; Codex gets it
> prepended to `turn/start` input on fresh threads (resumed threads
> keep the context they were started with). The resolved text is
> snapshotted onto the `Node` as `system_context_snapshot` for audit
> and surfaced in the side-panel summary. Vendor-specific
> CLAUDE.md/AGENTS.md/`.claude/`/`.mcp.json` loading is **intentionally
> deferred** — the simpler one-file protocol covers the project-context
> need without provider-format negotiation.
>
> **Phase 2 centerpieces — `op` node + `gate` node.** A `commit` op
> node is now auto-appended after an `agent`/`gate` node reaches `done`
> when the project's `auto_commit` setting is on (`POST /sessions
> {auto_commit:true}`). The op runs `git add -A && git commit -m
> miniclaw:node:<id>`; on success it rewrites the preceding node's
> `commit_after` to the new commit hash so the per-node diff is a real
> two-commit diff. Op tiles render narrower in the timeline; the
> selection does not jump to op nodes (`node_started.kind` distinguishes
> them). `gate` nodes carry a markdown contract; the agent runs to
> completion, the node enters `awaiting_review`, and the `NodeDetail`
> side panel grows a `Review` tab with the contract + a
> write-json / no-op response form. Write-json rejects absolute paths
> and parent traversal, loops on write errors so the user can retry
> without restarting the node. Vendor-specific on-disk context remains
> deferred.
>
> **Phase 1/2 polish sweep.** Three follow-up items landed together:
> (1) inline gates moved off the chat composer into a unified `gate`
> tab on `NodeDetail` (subsumes the old `Review` tab; auto-switches
> when a request arrives; an amber banner surfaces requests for nodes
> that aren't currently selected). (2) A read-only `Settings` tab on
> `NodeDetail` backed by a new `Node.settings_snapshot` field that
> `NodeRunner` populates at start with `project.settings_override +
> cwd + provider`. (3) Resume-edge connectors: `ProjectTimeline`
> overlays an SVG bezier from each parent agent/gate tile to its
> resume child, with a `↻ {id}` badge on resumed tiles for when the
> parent is off-screen. Op-parent edges (auto-append commit) are
> skipped so the line on the timeline always means "conversation
> continuation."
>
> **Gate redesign — passive checkpoint (Phase 2 follow-up).** The
> `gate` node kind is now a **pure human checkpoint with no agent
> run**. The runner skips the provider entirely
> (`NodeRunner._run_passive_gate`) and goes straight to
> `awaiting_review`. The previous agent step is responsible for the
> brief: a new `NodeOutputKind.REVIEW_BRIEF` injects an output
> contract telling the agent to write `.miniclaw2/outputs/<id>/brief.md`
> with `# How to run`, `# What to verify`, and `# Response schema`
> sections. The scenario loader auto-promotes the source step's
> `output_kind` to `review_brief` whenever a downstream gate has
> `brief_from: <step>` set. The user-launched `+ Gate` modal is also
> passive — it asks for a brief markdown blob directly, no prompt
> field. Wire surface change: `StartGateNode` is now
> `{type, brief}` (was `{type, prompt, contract}`).
>
> **Multi-step scenario expander.** `ProjectRegistry` grew an
> `_advance_scenario_step` method called from `_on_runner_done` after
> the auto-commit branch. It routes through op parents, appends to a
> new `Project.scenario_step_history: list[{step_id, node_id,
> terminal_state}]`, halts on non-DONE terminal states, and enqueues
> the next step. For gate steps, it reads the previous agent's
> `brief.md` and uses it verbatim as the gate's contract (placeholder
> markdown if missing). Nodes grew a `scenario_step_id: str | None`
> field stamped by `launch_scenario` + the expander. The first
> Tier 3 scenario `gui-calculator` exercises this path (build agent
> → auto-commit op → passive review gate); see `TEST.md`.

## 1. Motivation

MiniClaw2 is not a nicer chat UI for one vendor. The goal is a **graph IDE for
human-supervised LLM workflows**:

- The atom of computation is a **session**, not a turn — because a session
  is the smallest unit a researcher is willing to delegate to before
  checking in.
- The interesting events are **human gates**, not model output. A node
  matters because it is *blocked waiting on you* or because it produced
  a result you need to validate.
- The substrate (filesystem, conversation, config) is heterogeneous:
  FS state flows linearly, conversation state can fork or resume,
  config/memory can be inherited acausally. Each is a distinct edge
  type — the visual graph makes those distinctions readable instead of
  conflating them.
- Detail is hidden by default. The graph shows *state*; the side panel
  shows transcripts, diffs, and gate payloads on demand.

### 1.1 Guiding principle — investigation-free interface

A user of MiniClaw2 is never asked to investigate internals to decide
whether the system worked. They are given (a) what to do and (b) what
they should see; success is "the observed effect matches the
description." They may know nothing about the node graph, the event
log, the state machine, the gate kinds, or the provider adapters. The
framework's job is to be inspectable when something fails, not to be
required reading when things work.

This principle is foundational and shapes two surfaces:

- **Product UX.** A user supervising work interacts with node tiles,
  gate prompts, and produced artifacts. They never need to read
  `events.jsonl`, distinguish inline gates from checkpoint gates, or
  reason about commit-op rewrites in order to know whether their work
  landed. The side panel is *available* for diagnosis, not *required*
  for usage.
- **Validation / benchmarking.** A demo passes when the produced
  artifact behaves as its brief promised (the calculator computes
  `2+3=5`; the GUI window opens; the review JSON appears at the
  contracted path). Internal correctness — that a gate routed to the
  right runner, that the auto-commit op rewrote `commit_after`, that
  reconnect-replay reconstructed the right stream — is *implied* by
  the visible outcome. If an internal path is broken, the user-visible
  artifact will be broken too, and that is the signal we ground on.
  `TEST.md` is the operational counterpart of this principle.

Engineer-facing unit tests under `backend/tests/` are unaffected by
this principle: they exist for backend hygiene during development.
The principle governs anything a *user* (real or simulated) ever sees.

## 2. Core abstractions

Three primary objects. Everything else is a view over these.

### 2.1 Node — one provider-backed session or one programmatic op

Every node has:

- `id`, `project_id`, `kind ∈ {agent, gate, op}`
- `state ∈ {queued, running, waiting, awaiting-review, done, error, cancelled}`
- `parent_node_id?` — for explicit **resume** edges (provider conversation continuation)
- `context_sources: [node_id]` — for **context** edges (acausal config carryover)
- `provider` — `claude` or `codex`
- `provider_session_id?` — Claude SDK session id or Codex thread id
- `provider_turn_id?` — provider-native turn id when available
- `sdk_session_id?` — legacy alias for old Claude records
- `commit_before?`, `commit_after?` — repo state at start / finish
- `output_kind ∈ {freeform, summary, interface}` — the node result
  contract. `summary` asks the agent to write markdown, `interface`
  asks it to write JSON, and `freeform` preserves the transcript-first
  behavior for exploratory work.
- `output_path?`, `output_contract_snapshot` — project-relative
  artifact path and the exact launch-time instructions injected into
  the provider turn.
- `summary` — short one-liner generated post-completion
- `created_at`, `started_at`, `finished_at`

### 2.2 Project — a workspace = (folder + ordered nodes)

- `id`, `root_path`, `name`
- `head_commit` — latest committed state
- `parent_project_id?`, `parent_commit?` — for forks
- `provider` — default agent backend for new nodes
- `settings_override` — model, permission mode, allowed tools, etc.

Within a project the timeline is strictly ordered: nodes run **one at
a time**. Concurrency comes from forks (new projects), not from
intra-project parallelism. This keeps FS state coherent.

### 2.3 Edge — derived from node/project fields, not stored separately

| Edge | Stored as | Means |
|---|---|---|
| timeline | implicit from `(project_id, sequence)` | FS-state dependency between adjacent nodes |
| resume | `parent_node_id` | Explicit provider conversation continuation (`resume=<sid>` for Claude, `threadId` for Codex); creates a new node that starts from another node's conversation state |
| context | `context_sources[]` | Snapshotted bundle of CLAUDE.md fragments, memory files, settings, allowed-tools, agents — evaluated once at consumer-creation time |
| fork | `parent_project_id` + `parent_commit` | A new project rooted at a git worktree of another project's snapshot |

## 3. Node kinds

```
┌────────────────────────────────────────────────────────────┐
│ agent   │ A provider-backed session. Inline gates allowed. │
│ gate    │ An agent + a post-completion markdown contract. │
│         │ After session ends, node enters awaiting-review │
│         │ until user resolves.                            │
│ op      │ Non-agent state operation: commit, fork-project.│
│         │ Fast, atomic, no SDK. Always immediate.         │
└────────────────────────────────────────────────────────────┘
```

### 3.1 agent

Runs the selected provider with whatever options are assembled from
project settings + context-edge bundle + per-node overrides. A normal
agent node starts a fresh provider session/thread. Timeline adjacency
only means filesystem/workspace ordering; it never implies provider
conversation continuation. Inline human gates (`permission`,
`ask_user`, `plan_approval`) are normalized by the provider adapter and
put the node into `waiting`. Resolving an inline gate continues the
same provider session/turn inside that node — the node returns to
`running`.

Agent nodes may also carry an **output contract**:

- `freeform` — no required artifact. Transcript, tool activity, and
  workspace diff remain the primary evidence.
- `summary` — the runner injects instructions requiring a markdown
  artifact at `.miniclaw2/outputs/<node-id>/result.md` by default,
  with `# Purpose`, `# Method`, and `# Result` sections.
- `interface` — the runner injects instructions requiring a JSON object
  at `.miniclaw2/outputs/<node-id>/result.json` by default, with stable
  keys including `kind`, `summary`, `purpose`, `method`, `result`, and
  `files`.

The side panel treats the configured artifact as the primary dashboard
result when `output_kind != freeform`; transcript/diff/events remain
the audit surface. On completion, MiniClaw2 reads the artifact and uses
it to populate the node's short `summary` when possible. Missing or
invalid artifacts are visible as artifact status, not hidden inside the
transcript.

### 3.2 gate (passive checkpoint node)

A gate node is a **passive human checkpoint** — no provider call, no
agent turn. It exists to render a markdown **brief** and collect a
write-json / no-op response. The brief has a standard three-section
template:

```markdown
# How to run
The exact commands or steps the reviewer should take to exercise what
was built.

# What to verify
Specific behaviors the reviewer should look for.

# Response schema
The JSON keys + shapes the reviewer should put in their response.
```

Lifecycle:

1. A gate node is created with a brief. Two paths:
   - **Scenario-driven** (typical): the previous agent step is
     configured with `output_kind: review_brief`, which injects an
     output contract telling it to write `brief.md`. When that step
     completes, the scenario expander reads the file and uses its
     contents as the gate's brief.
   - **User-launched** (`+ Gate` button): the user types the brief
     directly in the launch modal.
2. The gate node enters `awaiting-review` immediately — `NodeRunner`
   short-circuits in `_run_passive_gate` and skips the provider
   entirely.
3. The frontend renders the brief verbatim in the `gate` tab next to a
   response form.
4. User responds. Two MVP response types:
   - **write-json**: response is written to a path specified in the
     brief; this becomes the documented handoff to a downstream node.
   - **no-op**: the gate was informational; resolution just marks the
     node `done`.
5. Iteration, when wanted, is a manual user action: spawn a follow-up
   agent node, attach a context edge, and reference the JSON file in
   the new prompt. Explicit causality, no surprises.

Why no agent inside the gate: the agent that just finished is the one
that knows what to test (it built it); asking a fresh agent to
re-review its predecessor's output wastes a turn and produces
generic feedback. The brief approach makes the testing instructions
adapt to whatever the agent actually built, and shifts the human
ratification step to where the human is already paying attention.

Why markdown over file-globs or scripts: a markdown brief is plain
text, lives in the project repo as `.miniclaw2/outputs/<id>/brief.md`
(so the agent's audit trail is intact), and the framework only needs
to render it — no parsing of glob patterns, no sandboxed script
execution.

### 3.3 op (programmatic node)

Ops are non-agent, fast, always-immediate state transitions. They appear
on the timeline so the user sees the project's full mutation history,
not just the agent-driven parts.

MVP set:

- **commit** — `git add -A && git commit -m "miniclaw:node:<id>"` on the
  currently checked-out branch (configurable per launching node).
  Auto-appended after an `agent`/`gate` node if the launch option is
  set; otherwise creatable explicitly.
- **fork-project** — `git worktree add` from a chosen commit, creates a
  new `Project` row pointing at the new path, copies context bundle
  from the source project.

Future ops (TBD): `checkout`, `reset`, `import-context`.

Ops always execute immediately — they do not support inline gates.

## 4. Node state machine

```
            ┌──── inline gate ────┐
            ▼                     │
queued ─► running ─► waiting ──── ┘
            │
            │   agent kind ─► done | error | cancelled
            │
            └── gate kind ──► awaiting-review ─► done
```

- `waiting` is a substate of an `agent`/`gate` node *during* its
  session. A node may oscillate `running ↔ waiting` many times.
- `awaiting-review` only exists on `gate` nodes after the session ends.
- `op` nodes skip everything and go `queued → running → done|error`.

## 5. Project / FS model

- One worktree per project. Nodes run one at a time on it.
- Each node records `commit_before` and `commit_after` so the timeline
  can show a project-state diff per node.
- Auto-commit is **opt-in per launch**, defaulting to a project-level
  setting. The commit runs on the currently checked-out branch by
  default; the launching user can override the target.
- Auto-commit is represented as a separate `op` node appended after
  the agent/gate node, not as a hidden side effect.
- Forks create a new project with its own worktree. The new project's
  `parent_project_id` and `parent_commit` make the lineage explicit.

## 6. Inline gates vs checkpoint gates — the distinction

These are different mechanisms that both surface as "the node wants
you":

| | Inline gate | Checkpoint gate (passive) |
|---|---|---|
| Provider call | Inside an agent node's session | None — the gate has no provider call |
| When declared | Implicit (agent calls `AskUserQuestion`/etc.) | Explicit at node launch, with a brief |
| When fires | Mid-session | Immediately when the gate node starts |
| Node state | `waiting` (substate during running) | `awaiting-review` (terminal-but-blocking) |
| Continuation | Resolving resumes the same session | Resolving does not wake any agent; user spawns a follow-up node manually if needed |
| UI signal | Pulsing animation on a running node | Solid color on a finished node |

## 7. Templates (programmable graph)

> A template is a **declarative recipe for a sequence of nodes** with
> named slots and minimal control flow. Templates package workflows
> like "Build → Review → Fix → Snapshot" without hiding node
> boundaries: every step materializes as a visible `Node`, the human
> can interrupt or leave a template at any point, and the graph
> remains the control plane.

The principle: **the engine stays general (low-level operators); the
templates are the high-level layer.** A template never introduces a
new node kind, a new state, or a new edge type. It is a saved
*launch policy* that the registry consults when deciding what node
to create next.

### 7.1 What a template is (and isn't)

For:

- pre-filling launch options (provider, permission mode, context
  sources, gate contract) so the user can declare intent in one click
- chaining nodes that a user would otherwise wire by hand
- expressing the canonical build → review → fix → snapshot loop as a
  single reusable artifact

Not:

- a new node kind (`agent` / `gate` / `op` stay as defined in §3)
- a black-box workflow (no node boundary is ever hidden)
- a Turing-complete recipe language (see §7.4)

### 7.2 File format and on-disk layout

Templates are YAML files. Search order at launch: project →
user → bundled.

```
<project_root>/.miniclaw2/templates/*.yaml     # project-local
~/.miniclaw2/templates/*.yaml                  # user-wide
backend/miniclaw2/templates/bundled/*.yaml     # bundled (one to start)
```

Shape:

```yaml
name: gui-build
description: "Build → review → fix → snapshot loop for GUI work."
slots:
  - name: goal
    label: "What should the app do?"
    required: true
  - name: stack
    label: "Stack hint"
    default: "any"
steps:
  - id: plan
    kind: agent
    permission_mode: plan
    prompt: |
      Design {{slots.goal}} for stack "{{slots.stack}}".
      First give me a plan, file layout, and acceptance criteria.
      Do not modify files.
  - id: build
    kind: agent
    after: plan
    context_sources: [plan]
    prompt: "Implement the plan from {{steps.plan.summary}}."
  - id: review
    kind: gate
    after: build
    contract_template: gui-review.md
  - id: fix
    kind: agent
    after: review
    on_state: reviewed_reject
    context_sources: [review]
    prompt: "Address review notes: {{steps.review.response.notes}}."
    next: review
  - id: snapshot
    kind: op
    op_kind: commit
    after: review
    on_state: reviewed_approve
```

Step `kind` values map 1:1 to `NodeKind`. Step ids are unique within
the template; `after` references must form a DAG. The engine
validates step ids, references, interpolation roots, and `on_state`
values at parse time.

### 7.3 Slot interpolation

`{{...}}` placeholders are resolved against a **whitelisted** set of
roots — arbitrary attribute access is rejected at parse time so the
template stays statically inspectable:

| Root | Meaning |
|---|---|
| `slots.<name>` | A user-provided slot value (string only in v1) |
| `steps.<id>.summary` | The completed node's `summary` string |
| `steps.<id>.state` | Terminal state — `done`, `error`, `reviewed_approve`, `reviewed_reject` |
| `steps.<id>.response.<field>` | Gate node's structured response (`decision`, `notes`, `path`, `data`) |

A reference to a step that does not yet exist on the cursor's current
branch is a runtime error; the engine knows the DAG at parse time so
most such errors fail early.

### 7.4 Control flow (deliberately tiny)

Two primitives, and only two:

- **Branching via `on_state`.** Allowed values: `done`, `error`,
  `reviewed_approve`, `reviewed_reject`. A step fires only when its
  `after` step terminates in the listed state. Omitting `on_state`
  means "any terminal state."
- **Looping via `next:`.** A step may declare `next: <step_id>` to
  point back to an earlier (or any) step. When this step completes,
  the engine re-expands the referenced step with a fresh node id.
  History grows; nothing is overwritten.

No conditionals beyond `on_state`. No expression language. No
`while`, no max-iteration counter. The human is the loop terminator:
stopping the project drops the template's cursor, and the existing
nodes stay where they are.

### 7.5 Gate contracts inside templates

A `gate` step may declare its contract either:

- inline: `contract: |\n  # Expected\n  ...`
- by reference: `contract_template: <name>.md`, resolved against
  `<project_root>/.miniclaw2/contracts/`, then user-wide, then
  bundled.

Inline takes precedence if both are given. The resolved contract
text is snapshotted onto the gate node at expansion time, so editing
the template or contract file mid-run does not change an in-flight
run (same pattern as `system_context_snapshot`).

### 7.6 Runtime model

Three new pieces, all in the registry layer — `NodeRunner` is
unchanged:

- **`TemplateDefinition`** (Pydantic) — parsed YAML. Validates
  step-id uniqueness, DAG consistency, whitelisted interpolation
  roots, allowed `on_state` values, no unknown keys.
- **`TemplateInstance`** — the runtime cursor. Fields: `id`,
  `template_name`, `slot_values`, `snapshotted_yaml`,
  `step_history: [{step_id, node_id, terminal_state}]`. Persisted as
  `templates_runs/<run_id>.json` under the project. The full
  template YAML is snapshotted at launch for reproducibility.
- **`TemplateExpander`** — a pure function:
  `(TemplateInstance, just_finished_node) → list[NodeSpec]`.
  Returns the next node spec(s) to enqueue, or empty when the
  template is exhausted. Called by `ProjectRegistry` on every
  `runner_done` callback, before the user gets a chance to type a
  free-form prompt.

`Node` grows two small fields: `created_by: "user" | "template:<run_id>"`
and `template_step_id: str | None`. These let the UI badge
template-created nodes and let the expander find the next step. Both
default safely for existing on-disk records.

### 7.7 Scope rules

- **One template instance per project at a time.** Nodes serialize
  within a project (§2.2); so do templates. Parallel runs → fork the
  project.
- **The user can leave a template mid-run.** A "Leave template"
  button on the project drops the instance; subsequent nodes go back
  to free-form. Existing template-created nodes stay where they are.
- **Template-created nodes can be edited before they expand.** Ghost
  tiles ahead of the cursor (derived from the template + slot values
  + reachable `on_state` branches) render with a dashed border;
  clicking one opens a per-step override (override the prompt, change
  permission mode, etc.). Overrides attach to the `TemplateInstance`,
  not to the YAML on disk.

### 7.8 UI surface (composer)

Above the existing chat textarea: a `Template: ▾` picker. Default
"None (free text)" — the composer behaves identically to today and
existing chat users never see the template surface.

Selecting a template:

- reveals a slot form populated from the template's `slots` section
- keeps the textarea visible as an optional "additional notes" field,
  appended to the first step's prompt at launch
- changes the Send button to **Launch template**, which atomically
  creates the `TemplateInstance` and the first step's `Node`

While a template is running, the project header shows the active
template name and a **Leave template** button; the timeline renders
ghost tiles for the reachable upcoming steps.

### 7.9 Explicitly out of scope for v1

- Template composition (`include: other-template`).
- User-authored templates from the UI ("save my last N nodes as a
  template").
- Slot types beyond string (no numbers, lists, or file pickers).
- Conditional expressions, jq, embedded Python.
- More than the four `on_state` values listed in §7.4.

These are easy to add once the format has been used. Shipping them
first risks warping the format to fit speculative needs.

## 8. Visual model

- **Workspace** = stacked vertical lanes, one per project.
- Each lane is a horizontal timeline, left → right by `started_at`.
- Nodes are tiles in their lane:
  - `agent` / `gate` / `op` shapes differ
  - color by state: queued grey, running blue, waiting/awaiting-review
    green, done slate, error red, cancelled muted
- Edges:
  - **timeline**: implicit adjacency within a lane (no curve drawn,
    just spatial)
  - **resume**: a connector between two nodes (thicker timeline edge)
  - **context**: curve between lanes (cross-project)
  - **fork**: a new lane branching off a node's right edge
- Clicking a node opens a side panel:
  - default tab: **artifact summary + open gates** (matches "hide detail")
  - other tabs: transcript, activities/tools, snapshot diff, settings

## 9. Persistence sketch

> Phase 0 chose **JSON + JSONL only**. SQLite is deferred until
> cross-project queries (e.g. "list all nodes in `awaiting-review`")
> actually become hot — likely in Phase 4.

Filesystem layout under `$MINICLAW_HOME` (default `~/.miniclaw2/`,
single-user assumption for MVP):

```
projects/<pid>/
  project.json                # full Project model
  nodes/<nid>/
    node.json                 # full Node model, rewritten on each state transition
    events.jsonl              # {seq, event} per line, append-only
    gates.jsonl               # {action: "created"|"resolved", gate} per line
```

- Atomic writes for the JSON files via tmp + rename. Single-writer per
  node is guaranteed by the rule that nodes within a project run
  sequentially (§2.2).
- Future SQLite migration is a flat translation: each JSON file becomes
  one row, each JSONL line one row of an append-only event table.
- WebSocket reconnect strategy (Phase 1 work): client sends
  `(node_id, last_seq)`; backend replays from `events.jsonl` since
  `last_seq` then attaches to the live stream. The JSONL is already
  written in Phase 0; only the replay endpoint is missing.

## 10. Phased plan

Each phase ends with a usable system. The CLI-parity items from
`PROPOSAL.md` are absorbed into Phase 1 and Phase 2.

### Phase 0 — Spine ✓ landed

No UI changes. Migrate to the new domain model.

- [✓] Persistence for `Project`, `Node`, `HumanGate` — JSON + JSONL
  on disk (SQLite deferred; see §9).
- [✓] Node state machine implemented and exercised by existing
  single-project flow. Each session id is now a project id; each user
  prompt becomes a new agent node. New nodes start fresh by default:
  `parent_node_id` and provider session/thread ids are only populated
  for explicit resume edges, not for ordinary timeline adjacency.
- [✓] Generalized `InteractionRequest` → `HumanGate` (inline kind
  only). The wire still emits `interaction_request` events for
  compat; the on-disk record is a `HumanGate`.
- [✓] Per-node JSONL event log (`events.jsonl`). The replay-on-reconnect
  consumer landed as part of the Phase 1 chat-polish pass (see §10.1).
- [✓] Provider lifetime: `NodeRunner` owns the node state machine and
  delegates provider-native IO to an adapter for the node's whole
  lifetime. Claude uses `ClaudeSDKClient`; Codex uses `codex
  app-server` JSON-RPC.

Cheap wins from `PROPOSAL.md` Phase 1 absorbed along the way:

- [✓] Plan-mode happy path fixed — Approve now returns
  `Allow(updated_permissions=[setMode acceptEdits])` instead of
  `Deny(interrupt=True)`.
- [✓] Interrupt wired — Stop button while streaming sends `interrupt`,
  the runner transitions the node to `cancelled`, on-disk state
  reflects it.
- [✓] `ThinkingBlock` surfaced as a `thinking` event; frontend renders
  a collapsible `<details>` panel above the assistant text.

### Phase 1 — Single-project graph

Chat-surface polish from `PROPOSAL.md` Phase 1 landed first so the
timeline UI's side panel can render real content from day one:

- [✓] **Plan-mode happy path**, **interrupt button**, **`ThinkingBlock`
  surface** — landed with Phase 0.
- [✓] **Tool I/O rendering.** `Activity` carries `result` (≤4 KB) and
  `result_kind ∈ {stdout, diff, text, json}`. Claude provider extracts
  `ToolResultBlock.content`; Codex provider pulls `aggregatedOutput`
  (commandExecution) and renders `changes` (fileChange) as a
  unified-diff-ish block. Rendered in `ToolActivity` as a collapsible
  `<details>` — open-by-default on failed, closed on success — with
  red/green/cyan coloring for diffs.
- [✓] **Markdown rendering.** Assistant text passes through
  `react-markdown` with `remark-gfm` and `rehype-highlight`
  (github-dark theme). User messages stay plain. Hand-rolled
  `.md-prose` styles in `index.css`.
- [✓] **Reconnect replay.** New `node_started` server event carries
  `node_id`/`parent_node_id`; new `replay_request` client envelope
  takes `(node_id, since_seq)`. The WS handler consumes
  `store.replay_events`; WebSocket connections attach as project-level
  observers so a reconnect also resumes the live tail.
  `ws.ts` tracks `(activeNodeId, lastSeq)` and replays on every
  reconnect after the first open. 4xxx close codes (e.g. session not
  found) suppress the auto-reconnect loop.
- [ ] **Per-token streaming** — deferred until the pinned
  `claude-agent-sdk` exposes partial messages. Codex already streams
  per-delta.
- [✓] **Initial horizontal timeline + side panel.** A single project now
  renders as a horizontal node timeline. Clicking a node opens a detail
  side panel with prompt metadata, transcript/tool/thinking rendering,
  repo diff rendering, and raw JSONL event inspection via read-only REST
  APIs. `node_updated` events keep node tiles and metadata live without
  relying only on REST refreshes.

Still to do for this phase:

- [✓] **Inline gates render in the side panel.** Permission / ask-user /
  plan-approval requests no longer render under the chat composer; they
  consolidate into a single dynamic `gate` tab on `NodeDetail` that
  also subsumes the previous `Review` tab for checkpoint contracts.
  Tab label switches per request type (permission / ask / plan /
  review). When an inline gate arrives, the timeline auto-selects the
  owning node and the side panel auto-switches to `gate`; if the user
  is parked on a different node, a small amber banner above the chat
  composer surfaces "Node X is awaiting your response" with a
  click-through. Node tiles already pulse for `waiting` /
  `awaiting_review` via existing `stateDot`.
- [✓] **Settings tab in the node detail panel.** Read-only tab on
  `NodeDetail` that displays the launch-time settings snapshot. New
  field `Node.settings_snapshot: dict[str, Any]` is populated by
  `NodeRunner` at runner start with `project.settings_override + cwd +
  provider` (same pattern as `system_context_snapshot`). The tab
  renders provider / model / model-provider / cwd / auto-commit as
  known rows, surfaces any extra snapshot keys, and shows a Context
  section with `system_context_snapshot` size and `context_sources`.
  Old on-disk nodes (without the field) load fine via the Pydantic
  default.
- [✓] **Richer agent node launch controls beyond the chat composer.**
  Gate-node launch is now driven by a dedicated `+ Gate` button in the
  header that opens `GateLaunchModal` (prompt + contract editor); the
  chat composer stays the launcher for ordinary agent nodes.
- [✓] **Tighter per-node snapshot diffs.** When `auto_commit` is on
  for a project, the commit-op rewrites the preceding agent/gate
  node's `commit_after` so `git_state.node_diff` returns a real
  `commit_before..commit_after` two-commit diff. Projects without
  auto-commit still fall back to working-tree diff.

### Phase 2 — Gate nodes, commit ops, resume edges, on-disk context

- [✓] **`gate` node kind — passive checkpoint (redesigned).**
  Originally the gate ran an agent on a launch prompt and then
  entered `awaiting_review`. As of the gate-redesign follow-up the
  node is **purely passive**: `NodeRunner._run_passive_gate` skips
  the provider entirely, enters `awaiting_review` immediately, and
  renders a brief in the `gate` tab on `NodeDetail`. Write-json /
  no-op resolution still validates project-relative paths (rejects
  absolute / `..`) and loops on write errors so the user can fix the
  path without restarting the node. Wire envelope simplified to
  `start_gate_node {brief}`; `InteractionRequest.interaction_type =
  "checkpoint_review"` still carries the brief as `tool_input.contract`.
  Scenario-driven gates source their brief from the previous agent
  step via `output_kind: review_brief` + the scenario expander.
- [✓] **`commit` op node, opt-in auto-append.** New `NodeKind.OP`
  with `op_kind="commit"`. Auto-appended after any `agent`/`gate`
  node that reaches `done` when `project.settings_override.auto_commit`
  is truthy. Runs `git add -A && git commit -m miniclaw:node:<id>`;
  on success the preceding node's `commit_after` is rewritten to the
  new commit hash and a `node_updated` event is broadcast for that
  node. `NodeStarted.kind` distinguishes op-node events so the
  frontend doesn't jump the selection to the op tile.
- [✓] **Resume edges.** A `Resume` button in the `NodeDetail` header
  on a terminal agent/gate node with a provider session sets the next
  launch's `resume_from_node_id`; the chat composer shows a
  "Resuming from node X" banner with a Clear button. The new node
  inherits the parent's `provider_session_id` / `sdk_session_id` and
  starts the SDK/app-server in resume mode. The timeline now overlays
  an SVG layer drawing a dashed bezier from each parent agent/gate
  tile to its resume child, plus a small `↻ {id}` badge on the resumed
  tile so the relationship stays visible when the parent has scrolled
  off-screen. Op auto-append parents are explicitly skipped from edge
  drawing (those are not resumes). Ordinary launches without an
  explicit resume source still start fresh.
- [✓] **Provider-neutral `CONTEXT.md`.** `<project_root>/CONTEXT.md` is
  loaded at each node launch by `backend/miniclaw2/context.py`; injected
  into Claude via `system_prompt.append` on the `claude_code` preset,
  and into Codex by prepending to the first `turn/start` input text on
  fresh threads. The resolved text is snapshotted onto the node as
  `system_context_snapshot` for audit and shown in the side-panel
  summary. Strict filename (no `CLAUDE.md` / `AGENTS.md` fallback),
  project-root lookup only.
- Vendor-specific on-disk context (deferred):
  - `CLAUDE.md` walk (project + user) merged into Claude's preset
  - `.claude/settings.json` + `settings.local.json`
  - `.claude/agents/*.md`
  - `.mcp.json`

### Phase 3 — Templates (programmable graph)

Implements §7. First cut: a registry-level template engine, the
bundled `gui-build` template, and composer UI for picking and filling
templates.

- **YAML format** as in §7.2, parsed and validated at load time.
  Whitelisted interpolation roots (§7.3), `on_state` + `next:` control
  flow (§7.4), no composition.
- **`TemplateDefinition`, `TemplateInstance`, `TemplateExpander`** —
  three classes in `backend/miniclaw2/templates/`. The runner stays
  unchanged; expansion happens on `runner_done` in the registry.
- **Persistence.** `templates_runs/<run_id>.json` per project,
  carrying the YAML snapshot, slot values, and step history.
- **Node schema bump.** Add `created_by` and `template_step_id` to
  `Node`. Existing on-disk records default to `"user"` / `None`.
- **Composer UI.** A `Template: ▾` picker above the chat textarea;
  selecting a template reveals a slot form; the Send button becomes
  Launch template. New WS envelope `start_template_run {name,
  slot_values, notes}` triggers launch.
- **Ghost-node rendering** in `ProjectTimeline.tsx` — derive
  upcoming-step tiles from the active `TemplateInstance` and the
  reachable `on_state` branches.
- **One bundled template:** `gui-build` (build → review → fix →
  snapshot). Bundled `gui-review.md` contract under
  `backend/miniclaw2/templates/bundled/contracts/`.
- **Leave-template affordance** in the project header.

Out of scope for this phase: template composition, user-authored
templates from the UI, slot types beyond string, additional bundled
templates (next two follow once the format has been used in anger).

### Phase 4 — Multi-project, forks, context edges

- Workspace UI: stacked project lanes, drag-and-drop arrangement.
- `fork-project` op: git worktree under the hood, new project row
  with explicit lineage.
- Context edges: at agent-node launch, pick "inherit from node X" —
  backend snapshots a context bundle (CLAUDE.md, memory, settings,
  agents allowlist) and merges it into the new node's options.
- Cross-project visualization (curves between lanes).

### Phase 5 — Affordances and ergonomics

- Slash-command interceptors in the input (`/clear`, `/compact`,
  `/model`, `/cwd`, `/permissions`) — translate to REST/WS calls, not
  model-side.
- `@file` references, `!cmd` execution, image / file attachments.
- Scheduled / cron-triggered nodes.
- Cost rollups per project, per branch, per workspace.
- TBD gate response types beyond write-json / no-op.

## 11. Open questions

To revisit at the end of each phase:

- **Sub-agents.** `TaskStartedMessage` events from the SDK currently
  render as inline activity. If users want to drill into sub-agent
  decisions, we may need to surface them as collapsible child rows on
  the parent node — but **not** as separate graph nodes. Re-evaluate
  after Phase 1.
- **Concurrency within a project.** Strictly sequential for now. If
  users repeatedly want to run two things in the same repo at once,
  consider implicit-fork-on-parallel-drop (Phase 4+).
- **Merge of forked projects back to parent.** Phase 4 creates forks;
  merging them back is a separate design problem. Likely a `merge` op
  node in a later phase.
- **Multi-user / collaboration.** Currently single-user, single-host.
  Persistence schema should not preclude future multi-user, but no
  active design yet.
