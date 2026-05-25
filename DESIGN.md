# MiniClaw2 long-term design

This document supersedes `PROPOSAL.md` (which remains as a punch list of
CLI-parity gaps in the current wrapper). It captures the architecture we
are converging toward over the next several phases.

> **Status (Phase 0 spine + Phase 1 graph shell in progress).** The spine is in.
> Domain model (`Project`, `Node`, `HumanGate`) persists to disk as
> JSON + JSONL under `$MINICLAW_HOME` (default `~/.miniclaw2`); SQLite
> from §8 is **deferred** in favor of JSON/JSONL while the schema is
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

## 2. Core abstractions

Three primary objects. Everything else is a view over these.

### 2.1 Node — one provider session/turn or one programmatic op

Every node has:

- `id`, `project_id`, `kind ∈ {agent, gate, op}`
- `state ∈ {queued, running, waiting, awaiting-review, done, error, cancelled}`
- `parent_node_id?` — for **resume** edges (SDK conversation continuation)
- `context_sources: [node_id]` — for **context** edges (acausal config carryover)
- `provider` — `claude` or `codex`
- `provider_session_id?` — Claude SDK session id or Codex thread id
- `provider_turn_id?` — provider-native turn id when available
- `sdk_session_id?` — legacy alias for old Claude records
- `commit_before?`, `commit_after?` — repo state at start / finish
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
| resume | `parent_node_id` | Provider conversation continuation (`resume=<sid>` for Claude, `threadId` for Codex); creates a new node, not a continuation of the old one |
| context | `context_sources[]` | Snapshotted bundle of CLAUDE.md fragments, memory files, settings, allowed-tools, agents — evaluated once at consumer-creation time |
| fork | `parent_project_id` + `parent_commit` | A new project rooted at a git worktree of another project's snapshot |

## 3. Node kinds

```
┌────────────────────────────────────────────────────────────┐
│ agent   │ A provider-backed session/turn. Inline gates allowed. │
│ gate    │ An agent + a post-completion markdown contract. │
│         │ After session ends, node enters awaiting-review │
│         │ until user resolves.                            │
│ op      │ Non-agent state operation: commit, fork-project.│
│         │ Fast, atomic, no SDK. Always immediate.         │
└────────────────────────────────────────────────────────────┘
```

### 3.1 agent

Runs the selected provider with whatever options are assembled from
project settings + context-edge bundle + per-node overrides. Inline
human gates (`permission`, `ask_user`, `plan_approval`) are normalized
by the provider adapter and put the node into `waiting`. Resolving an
inline gate continues the same provider session/turn — the node returns
to `running`.

### 3.2 gate (checkpoint node)

A gate node is declared with a **markdown contract** at launch time.
The contract has a standard three-section template:

```markdown
# Expected
What the agent should produce, and where (paths, file types).

# Unexpected
Failure modes, common pitfalls, things to watch for.

# Response protocol
What the reviewer should produce — JSON file path + schema, or "info-only".
```

Lifecycle:

1. User creates the gate with a prompt + a contract `.md` (template
   pre-filled, edited per node).
2. Agent runs to completion oblivious to the gate.
3. On session end, the node enters `awaiting-review`. The frontend
   renders the contract next to the agent's outputs and any files it
   wrote (matched by paths the user can mention in the contract).
4. User responds. Two MVP response types:
   - **write-json**: response is written to a path specified in the
     contract; this becomes the documented handoff to a downstream node.
   - **no-op**: the gate was informational; resolution just marks the
     node `done`.
5. Iteration, when wanted, is a manual user action: spawn a follow-up
   agent node, attach a context edge, and reference the JSON file in
   the new prompt. Explicit causality, no surprises.

Why markdown over file-globs or scripts: a markdown contract is
human-authored, lives in the project repo (versionable), and the
framework only needs to render it — no parsing of glob patterns, no
sandboxed script execution.

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

| | Inline gate | Checkpoint gate |
|---|---|---|
| When declared | Implicit (agent calls `AskUserQuestion`/etc.) | Explicit at node launch |
| When fires | Mid-session | After session completes |
| Node state | `waiting` (substate during running) | `awaiting-review` (terminal-but-blocking) |
| Continuation | Resolving resumes the same session | Resolving does not wake the agent; user spawns a follow-up node manually if needed |
| UI signal | Pulsing animation on a running node | Solid color on a finished node |

## 7. Visual model

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
  - default tab: **summary + open gates** (matches "hide detail")
  - other tabs: transcript, activities/tools, snapshot diff, settings

## 8. Persistence sketch

> Phase 0 chose **JSON + JSONL only**. SQLite is deferred until
> cross-project queries (e.g. "list all nodes in `awaiting-review`")
> actually become hot — likely in Phase 3.

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

## 9. Phased plan

Each phase ends with a usable system. The CLI-parity items from
`PROPOSAL.md` are absorbed into Phase 1 and Phase 2.

### Phase 0 — Spine ✓ landed

No UI changes. Migrate to the new domain model.

- [✓] Persistence for `Project`, `Node`, `HumanGate` — JSON + JSONL
  on disk (SQLite deferred; see §8).
- [✓] Node state machine implemented and exercised by existing
  single-session flow. Each session id is now a project id; each user
  prompt becomes a new agent node with implicit `parent_node_id` set
  to the project's previous node, and `provider_session_id` inherited
  so the selected provider resumes the conversation.
- [✓] Generalized `InteractionRequest` → `HumanGate` (inline kind
  only). The wire still emits `interaction_request` events for
  compat; the on-disk record is a `HumanGate`.
- [✓] Per-node JSONL event log (`events.jsonl`). The replay-on-reconnect
  consumer landed as part of the Phase 1 chat-polish pass (see §9.1).
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

- Richer agent node launch controls beyond the chat composer.
- Settings tab in the node detail panel.
- Tighter per-node snapshot diffs once Phase 2 commit ops/checkpoints
  are available; the current diff tab falls back to working-tree diff
  when a node has no distinct `commit_after`.
- Inline gates render in the side panel; node tile pulses green.

### Phase 2 — Gate nodes, commit ops, resume edges, on-disk context

- `gate` node kind: contract editor at launch (template pre-filled),
  awaiting-review UI in the side panel, write-json / no-op resolution.
- `commit` op node, opt-in auto-append.
- Resume edges: "fork conversation" affordance on a finished agent node
  creates a child node with `parent_node_id` set; SDK called with
  `resume=<sid>`.
- On-disk context loaded into agent options at launch:
  - CLAUDE.md merging (project + user)
  - `.claude/settings.json` + `settings.local.json`
  - `.claude/agents/*.md`
  - `.mcp.json`

### Phase 3 — Multi-project, forks, context edges

- Workspace UI: stacked project lanes, drag-and-drop arrangement.
- `fork-project` op: git worktree under the hood, new project row
  with explicit lineage.
- Context edges: at agent-node launch, pick "inherit from node X" —
  backend snapshots a context bundle (CLAUDE.md, memory, settings,
  agents allowlist) and merges it into the new node's options.
- Cross-project visualization (curves between lanes).

### Phase 4 — Affordances and ergonomics

- Slash-command interceptors in the input (`/clear`, `/compact`,
  `/model`, `/cwd`, `/permissions`) — translate to REST/WS calls, not
  model-side.
- `@file` references, `!cmd` execution, image / file attachments.
- Scheduled / cron-triggered nodes.
- Cost rollups per project, per branch, per workspace.
- TBD gate response types beyond write-json / no-op.

## 10. Open questions

To revisit at the end of each phase:

- **Sub-agents.** `TaskStartedMessage` events from the SDK currently
  render as inline activity. If users want to drill into sub-agent
  decisions, we may need to surface them as collapsible child rows on
  the parent node — but **not** as separate graph nodes. Re-evaluate
  after Phase 1.
- **Concurrency within a project.** Strictly sequential for now. If
  users repeatedly want to run two things in the same repo at once,
  consider implicit-fork-on-parallel-drop (Phase 3+).
- **Merge of forked projects back to parent.** Phase 3 creates forks;
  merging them back is a separate design problem. Likely a `merge` op
  node in a later phase.
- **Multi-user / collaboration.** Currently single-user, single-host.
  Persistence schema should not preclude future multi-user, but no
  active design yet.
