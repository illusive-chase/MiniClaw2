# MiniClaw2

A minimal coding-agent wrapper with a web GUI. Python backend wrapping
provider adapters for Claude Code (`claude-agent-sdk`) and Codex
(`codex app-server`) over FastAPI + WebSocket, paired with a React +
Vite frontend.

## Architecture

```
┌────────────────┐  WebSocket   ┌──────────────────┐  provider adapter
│ React + Vite   │ ───────────▶ │ FastAPI gateway  │ ────────────────▶ Claude SDK
│  (frontend/)   │ ◀─────────── │  (backend/)      │ ────────────────▶ Codex app-server
└────────────────┘   events     └──────────────────┘   tool / messages
```

- **Backend** (`backend/miniclaw2/`) — a **Project / Node** domain
  model persisted to disk as JSON + JSONL. Each user prompt becomes a
  fresh agent `Node` using the project's selected provider. Provider
  conversation continuity is explicit rather than implicit: a node
  starts a new Claude SDK session or Codex app-server thread unless the
  UI later adds a deliberate resume edge. A `NodeRunner` drives the
  state machine (`queued → running [↔ waiting] → done|error|cancelled`),
  translates provider messages into a small event union over the
  WebSocket, and persists every event to `events.jsonl` before pushing.
- **Frontend** (`frontend/`) — single-project workspace with a
  horizontal node timeline, node detail side panel, and chat surface.
  Assistant output is markdown-rendered (`react-markdown` + GFM +
  `highlight.js`); inline tool activity has collapsible output panels
  (stdout/text/json and real diffs when providers supply one); the app
  has a Stop button, a collapsible reasoning panel, repo diff
  inspection, an explicit resume-from-node control with a visible
  SVG connector + `↻ {id}` badge on the timeline, a collapsible
  System-context block in the node summary tab, a read-only
  `Settings` tab driven by `Node.settings_snapshot`, and WebSocket
  reconnect-replay. Permission / ask-user / plan-approval and
  checkpoint-review all share a unified `gate` tab on `NodeDetail`
  that auto-switches when a request arrives; an amber banner above
  the chat composer surfaces requests for nodes that aren't currently
  selected. A `+ Gate` button in the header opens a launch modal
  (prompt + markdown contract) for new checkpoint gate nodes.
- **Project-level context** — a `CONTEXT.md` file at the project root
  is loaded at each node launch and injected provider-neutrally: into
  Claude via `system_prompt.append` on the `claude_code` preset, and
  into Codex by prepending to the `turn/start` input text on fresh
  threads. The resolved text is snapshotted onto the Node
  (`system_context_snapshot`) for audit.

## Scope

In: streaming chat with markdown rendering, tool activity with full
result panels, permission / ask-user / plan-approval interactions,
on-disk persistence per project & node, interrupt, extended-thinking
surface, WebSocket reconnect replay (mid-session drops), Claude and
initial Codex provider adapters, provider-neutral project context
via `CONTEXT.md`, checkpoint gate nodes (markdown contract +
write-json / no-op review), and opt-in auto-commit op nodes with
real two-commit per-node diffs.
Out (for now): multi-project workspace UI, vendor-specific on-disk
context (`CLAUDE.md`, `AGENTS.md`, `.claude/settings.json`,
`.claude/agents`, `.mcp.json`), hard-reload session survival
(session-switcher UI), per-token streaming for Claude, auth, cost
tracking.

See [`DESIGN.md`](DESIGN.md) for the long-term graph-IDE plan and
[`PROPOSAL.md`](PROPOSAL.md) for the CLI-parity punch list (with
landed items marked).

## Run it

**Backend** (Python ≥ 3.11):
```bash
cd backend
pip install -e .
python -m miniclaw2 --reload     # http://127.0.0.1:8000
```
Env:
- `ANTHROPIC_MODEL` (default `claude-sonnet-4-6`)
- `MINICLAW_HOME` (default `~/.miniclaw2`) — root for the on-disk store.
- Claude provider: whatever auth the `claude` CLI already uses on your machine.
- Codex provider: `codex` must be on `PATH` and `codex doctor` should
  show working auth/config. The adapter uses `codex app-server --listen stdio://`
  and does not set `modelProvider`, `approvalPolicy`, or `sandbox` unless
  they are explicitly provided as session overrides, so Codex keeps using
  `$CODEX_HOME/config.toml` defaults such as your `packycode` provider/base URL.

The frontend has a Claude/Codex selector. You can also create a
Codex-backed session manually:
```bash
curl -X POST http://127.0.0.1:8000/sessions \
  -H 'content-type: application/json' \
  -d '{"cwd":"'"$PWD"'","provider":"codex"}'
```

Opt a project into auto-commit (a `commit` op node appended after each
agent/gate node reaches `done`, rewriting that node's `commit_after`
so the per-node diff becomes a real two-commit diff):
```bash
curl -X POST http://127.0.0.1:8000/sessions \
  -H 'content-type: application/json' \
  -d '{"cwd":"'"$PWD"'","auto_commit":true}'
```

**Frontend**:
```bash
cd frontend
npm install
npm run dev                      # http://127.0.0.1:5173
```
Vite proxies `/sessions` and `/ws` to the backend.

## Layout

```
backend/miniclaw2/
  domain.py     # Project, Node, HumanGate, ContextBundle (Pydantic) + state enums
  store.py      # JSON/JSONL filesystem store under $MINICLAW_HOME
  runner.py     # provider-neutral NodeRunner state machine
  providers/    # Claude SDK and Codex app-server adapters
  registry.py   # ProjectRegistry — in-memory orchestration over the store
  events.py     # Pydantic models for the WS protocol
  app.py        # FastAPI: REST + WebSocket gateway (legacy /sessions URL shape)
  context.py    # CONTEXT.md loader (project-root, provider-neutral)
  git_state.py  # small git helpers for commit ids and read-only diffs
  replay.py     # replay/live buffering for reconnecting WS observers
  __main__.py   # uvicorn entry

frontend/src/
  App.tsx       # session + chat + Stop button + interaction dispatch
  components/ProjectTimeline.tsx  # single-project horizontal timeline
  components/NodeDetail.tsx       # selected node transcript/events panel
  ws.ts         # useSessionSocket hook
  api.ts        # REST helpers
  types.ts      # mirror of backend events
  components/   # Chat, ToolActivity, PermissionDialog, AskUserDialog, PlanDialog,
                # GateLaunchModal, GateReviewPanel
```

On-disk layout (under `$MINICLAW_HOME`, default `~/.miniclaw2`):

```
projects/<pid>/
  project.json
  nodes/<nid>/
    node.json           # full Node fields, rewritten on each state transition
    events.jsonl        # {seq, event} per line, append-only
    gates.jsonl         # {action: "created"|"resolved", gate} per line
```

## Wire protocol

The HTTP/WS shape is the "session"-based legacy compat layer: each
session id is a project id, and each `user_message` spawns a fresh
agent node.

- REST read APIs for the Phase 1 workspace shell:
  `GET /sessions/{sid}/nodes`,
  `GET /sessions/{sid}/nodes/{nid}`, and
  `GET /sessions/{sid}/nodes/{nid}/events`, plus
  `GET /sessions/{sid}/nodes/{nid}/diff`.
- `POST /sessions` accepts an optional `auto_commit: bool` that
  stores into `project.settings_override["auto_commit"]` and triggers
  the commit-op auto-append.
- Client → server: `user_message`, `start_gate_node {prompt, contract}`,
  `interaction_response`, `interrupt`,
  `replay_request {node_id, since_seq}`.
- Server → client: `node_started` (now with `kind` to distinguish
  agent/gate/op tiles), `node_updated`, `text_delta`,
  `thinking`, `activity` (now with optional `result` + `result_kind`),
  `interaction_request` (with `interaction_type` extended to include
  `"checkpoint_review"` for gate-node contracts), `usage`, `turn_done`,
  `error`. All carry a monotonic `seq` that
  drives the on-disk event log and reconnect replay: clients track
  `(activeNodeId, lastSeq)` and send `replay_request` after every
  reconnect.

`interaction_response` remains backward-compatible with Claude's
`allow/message/updated_input` shape and also accepts Codex-style
`decision`, `scope`, `interrupt`, and raw `response` payloads.

Exact shapes: [`backend/miniclaw2/events.py`](backend/miniclaw2/events.py)
and [`frontend/src/types.ts`](frontend/src/types.ts).

## Status

Phase 0 spine + Phase 1 chat polish from [`DESIGN.md`](DESIGN.md) are
in. The shifts from the initial single-file wrapper:

- Domain model on disk: `Project` / `Node` / `HumanGate` survive a
  process restart (JSONL/JSON; SQLite from DESIGN §8 is deferred).
- Node state machine with explicit transitions and an event log per node.
- Provider layer split out of the state machine. Claude remains the
  default provider; Codex can be selected per session.
- [`PROPOSAL.md`](PROPOSAL.md) Phase 1 (chat polish) is landed:
  plan-mode happy path, Stop / interrupt, `ThinkingBlock` surface,
  tool I/O rendering (stdout/text/json and provider-supplied diffs as
  collapsible result panels in `ToolActivity`), markdown rendering
  for assistant text, and WebSocket reconnect replay
  (`node_started` + `replay_request` consumes `events.jsonl` and the
  reconnected socket re-attaches to live events).
- DESIGN Phase 1 is now in progress: the single-project horizontal
  timeline and selected-node side panel are in, backed by read-only
  node/event/diff REST endpoints and explicit `node_updated` events.
  Chat remains the launch surface for new agent nodes while the graph
  workflow matures.
- Per-token streaming for the Claude provider stays deferred until
  the pinned SDK exposes partial messages; Codex already streams
  per-delta.

A first slice of DESIGN Phase 2 also landed: a provider-neutral
`CONTEXT.md` is loaded from the project root and injected into both
Claude (via `system_prompt.append`) and Codex (prepended to the first
`turn/start` input on fresh threads). The resolved text is
snapshotted onto the Node and surfaced in the side-panel summary.
Vendor-specific loading (CLAUDE.md walk, `.claude/settings.json`,
custom agents, `.mcp.json`) is intentionally out of scope.

The DESIGN Phase 2 centerpieces have since landed too:

- **`gate` node kind.** A `+ Gate` button in the header opens a
  modal with a prompt textarea and a contract editor pre-filled with
  a three-section template (`# Expected` / `# Unexpected` /
  `# Response protocol`). Submitting sends `start_gate_node` over the
  WebSocket. The agent runs, and on completion the node enters
  `awaiting_review`. The `NodeDetail` side panel grows a `Review` tab
  rendering the contract via `react-markdown` plus a write-json /
  no-op response form. Write-json validates the path
  (project-relative only, no `..`) and loops on errors so the
  reviewer can fix the path without restarting the node.
- **`commit` op node.** When the project has `auto_commit:true`, a
  `commit` op node is auto-appended after each agent/gate node that
  reaches `done`. The op runs `git add -A && git commit -m
  miniclaw:node:<id>`; on success it rewrites the preceding node's
  `commit_after` to the new commit hash. Op tiles render narrower in
  the timeline and the selection deliberately does not jump to them
  (`node_started.kind` distinguishes agent/gate/op events).

A subsequent **Phase 1/2 polish sweep** then landed three follow-ups:

- **Inline gates moved into the side panel.** Permission / ask-user /
  plan-approval and checkpoint-review share a single dynamic `gate`
  tab on `NodeDetail`; the bottom-of-chat dialog is gone. When a
  request fires, the timeline auto-selects the owning node and the
  side panel auto-switches to `gate`. An amber banner above the chat
  composer surfaces requests for nodes the user isn't currently
  parked on.
- **Settings tab in `NodeDetail`.** Read-only inspector for what the
  node was launched with, backed by a new
  `Node.settings_snapshot: dict[str, Any]` populated at runner start
  with `project.settings_override + cwd + provider`. Pydantic default
  keeps older on-disk records loading cleanly.
- **Resume-edge connectors on the timeline.** `ProjectTimeline`
  overlays an SVG bezier from each parent agent/gate tile to its
  resume child (computed via tile refs, recomputed on scroll /
  resize). Each resumed tile also gets a `↻ {id}` badge so the
  relationship stays visible when the parent is off-screen. Op
  auto-append parents are deliberately skipped — drawn edges only
  ever mean conversation continuation.

Next up: DESIGN Phase 3 (Templates — programmable graph), or the
deferred vendor-specific on-disk context (CLAUDE.md walk,
`.claude/settings.json`, `.claude/agents`, `.mcp.json`).
