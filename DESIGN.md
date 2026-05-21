# MiniClaw2 long-term design

This document supersedes `PROPOSAL.md` (which remains as a punch list of
CLI-parity gaps in the current wrapper). It captures the architecture we
are converging toward over the next several phases.

## 1. Motivation

MiniClaw2 is not a nicer Claude chat UI. The goal is a **graph IDE for
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

### 2.1 Node — one Claude session or one programmatic op

Every node has:

- `id`, `project_id`, `kind ∈ {agent, gate, op}`
- `state ∈ {queued, running, waiting, awaiting-review, done, error, cancelled}`
- `parent_node_id?` — for **resume** edges (SDK conversation continuation)
- `context_sources: [node_id]` — for **context** edges (acausal config carryover)
- `sdk_session_id?` — set for `agent`/`gate` once the SDK initializes
- `commit_before?`, `commit_after?` — repo state at start / finish
- `summary` — short one-liner generated post-completion
- `created_at`, `started_at`, `finished_at`

### 2.2 Project — a workspace = (folder + ordered nodes)

- `id`, `root_path`, `name`
- `head_commit` — latest committed state
- `parent_project_id?`, `parent_commit?` — for forks
- `settings_override` — model, permission mode, allowed tools, etc.

Within a project the timeline is strictly ordered: nodes run **one at
a time**. Concurrency comes from forks (new projects), not from
intra-project parallelism. This keeps FS state coherent.

### 2.3 Edge — derived from node/project fields, not stored separately

| Edge | Stored as | Means |
|---|---|---|
| timeline | implicit from `(project_id, sequence)` | FS-state dependency between adjacent nodes |
| resume | `parent_node_id` | SDK conversation continuation (`resume=<sid>`); creates a new node, not a continuation of the old one |
| context | `context_sources[]` | Snapshotted bundle of CLAUDE.md fragments, memory files, settings, allowed-tools, agents — evaluated once at consumer-creation time |
| fork | `parent_project_id` + `parent_commit` | A new project rooted at a git worktree of another project's snapshot |

## 3. Node kinds

```
┌────────────────────────────────────────────────────────────┐
│ agent   │ A Claude session. Inline human gates allowed.   │
│ gate    │ An agent + a post-completion markdown contract. │
│         │ After session ends, node enters awaiting-review │
│         │ until user resolves.                            │
│ op      │ Non-agent state operation: commit, fork-project.│
│         │ Fast, atomic, no SDK. Always immediate.         │
└────────────────────────────────────────────────────────────┘
```

### 3.1 agent

Runs the SDK with whatever options are assembled from project settings
+ context-edge bundle + per-node overrides. Inline human gates
(`permission`, `ask_user`, `plan_approval`) flow through the existing
`can_use_tool` callback and put the node into `waiting`. Resolving an
inline gate continues the same session — the node returns to `running`.

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

- SQLite under `~/.miniclaw2/` (single-user assumption for MVP).
- Tables: `project`, `node`, `human_gate`, `context_bundle`, `event_log`.
- Transcript events streamed to JSONL per node (`nodes/<id>/events.jsonl`)
  for cheap append + replay on reconnect.
- WebSocket reconnect strategy: client sends `(session_id, last_seq)`;
  backend replays events from JSONL since `last_seq` then attaches to
  live stream.

## 9. Phased plan

Each phase ends with a usable system. The CLI-parity items from
`PROPOSAL.md` are absorbed into Phase 1 and Phase 2.

### Phase 0 — Spine

No UI changes. Migrate to the new domain model.

- SQLite persistence for `Project`, `Node`, `HumanGate`, `ContextBundle`.
- Node state machine implemented and exercised by existing single-session
  flow (current UI keeps working as "default project with one agent
  node").
- Generalize `InteractionRequest` → `HumanGate` (inline kind only).
- Per-node JSONL event log for reconnect replay.
- Client lifetime: `ClaudeSDKClient` is held by the node for its whole
  session (no more reconnect per turn).

### Phase 1 — Single-project graph

- Horizontal timeline UI for one project.
- Agent nodes: create / launch / cancel / inspect.
- Node detail side panel — this is where the old `PROPOSAL.md` Phase 1
  items live: **plan-mode fix**, **interrupt button**, **tool I/O
  rendering (Edit diff, Bash stdout, Read content)**, **markdown
  rendering**, **thinking-block surface**, **per-token streaming**.
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
