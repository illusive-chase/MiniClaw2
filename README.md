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
  model persisted to disk as JSON + JSONL. Each user prompt becomes an
  agent `Node` using the project's selected provider. Claude projects
  preserve continuity via SDK `resume`; Codex projects preserve
  continuity via app-server `threadId`. A `NodeRunner` drives the state machine
  (`queued → running [↔ waiting] → done|error|cancelled`), translates
  provider messages into a small event union over the WebSocket, and
  persists every event to `events.jsonl` before pushing.
- **Frontend** (`frontend/`) — chat surface with streaming text deltas,
  markdown-rendered assistant output (`react-markdown` + GFM +
  `highlight.js`), inline tool-activity indicators with collapsible
  result panels (Bash stdout, Edit diffs, Read content), three
  interaction dialogs (permission, ask-user, plan approval), a Stop
  button while streaming, a collapsible reasoning panel, and
  WebSocket reconnect-replay.

## Scope

In: streaming chat with markdown rendering, tool activity with full
result panels, permission / ask-user / plan-approval interactions,
on-disk persistence per project & node, interrupt, extended-thinking
surface, WebSocket reconnect replay (mid-session drops), Claude and
initial Codex provider adapters.
Out (for now): multi-project workspace UI, checkpoint gates,
provider-specific on-disk context inheritance, hard-reload session
survival (session-switcher UI), per-token streaming for Claude, auth,
cost tracking.

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
  __main__.py   # uvicorn entry

frontend/src/
  App.tsx       # session + chat + Stop button + interaction dispatch
  ws.ts         # useSessionSocket hook
  api.ts        # REST helpers
  types.ts      # mirror of backend events
  components/   # Chat, ToolActivity, PermissionDialog, AskUserDialog, PlanDialog
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

- Client → server: `user_message`, `interaction_response`, `interrupt`,
  `replay_request {node_id, since_seq}`.
- Server → client: `node_started`, `text_delta`, `thinking`, `activity`
  (now with optional `result` + `result_kind`), `interaction_request`,
  `usage`, `turn_done`, `error`. All carry a monotonic `seq` that
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
  tool I/O rendering (Bash stdout / Edit diffs / Read content as
  collapsible result panels in `ToolActivity`), markdown rendering
  for assistant text, and WebSocket reconnect replay
  (`node_started` + `replay_request` consumes `events.jsonl`).
- Per-token streaming for the Claude provider stays deferred until
  the pinned SDK exposes partial messages; Codex already streams
  per-delta.

Next up (DESIGN Phase 1 remainder): the per-project horizontal
timeline UI with a node detail side panel that the rendering pieces
above will move into.
