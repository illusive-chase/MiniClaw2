# MiniClaw2

A minimal Claude Code wrapper with a web GUI. Python backend wrapping
[`claude-agent-sdk`](https://pypi.org/project/claude-agent-sdk/) over
FastAPI + WebSocket, paired with a React + Vite frontend.

## Architecture

```
┌────────────────┐  WebSocket   ┌──────────────────┐  claude-agent-sdk
│ React + Vite   │ ───────────▶ │ FastAPI gateway  │ ──────────────────▶ Claude CLI
│  (frontend/)   │ ◀─────────── │  (backend/)      │ ◀──────────────────
└────────────────┘   events     └──────────────────┘   tool / messages
```

- **Backend** (`backend/miniclaw2/`) — a **Project / Node** domain
  model persisted to disk as JSON + JSONL. Each user prompt becomes an
  agent `Node` with its own `claude-agent-sdk` session; conversation
  continuity is preserved via SDK `resume` from the project's previous
  node. A `NodeRunner` drives the state machine
  (`queued → running [↔ waiting] → done|error|cancelled`), translates
  SDK messages into a small event union over the WebSocket, and
  persists every event to `events.jsonl` before pushing.
- **Frontend** (`frontend/`) — chat surface with streaming text deltas,
  inline tool-activity indicators, three interaction dialogs
  (permission, `AskUserQuestion`, `ExitPlanMode`), a Stop button while
  streaming, and a collapsible reasoning panel for `ThinkingBlock`s.

## Scope

In: streaming chat, tool activity, permission / ask-user / plan-approval
interactions, on-disk persistence per project & node, interrupt,
extended-thinking surface.
Out (for now): WebSocket reconnect replay, multi-project workspace UI,
checkpoint gates, on-disk context (CLAUDE.md / settings.json / agents /
MCP) inheritance, auth, cost tracking, native (non-CC) backends.

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
- Plus whatever auth the `claude` CLI already uses on your machine.

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
  runner.py     # NodeRunner — owns one ClaudeSDKClient per node; state machine
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

- Client → server: `user_message`, `interaction_response`, `interrupt`.
- Server → client: `text_delta`, `thinking`, `activity`,
  `interaction_request`, `usage`, `turn_done`, `error`. All carry an
  optional monotonic `seq` (used by the on-disk event log; reconnect
  replay is not yet wired).

Exact shapes: [`backend/miniclaw2/events.py`](backend/miniclaw2/events.py)
and [`frontend/src/types.ts`](frontend/src/types.ts).

## Status

Phase 0 of [`DESIGN.md`](DESIGN.md) is in. The big shifts from the
initial single-file wrapper:

- Domain model on disk: `Project` / `Node` / `HumanGate` survive a
  process restart (JSONL/JSON; SQLite from DESIGN §8 is deferred).
- Node state machine with explicit transitions and an event log per node.
- Three [`PROPOSAL.md`](PROPOSAL.md) Phase 1 items landed as cheap
  wins: plan-mode happy path fixed, Stop / interrupt wired,
  `ThinkingBlock` surfaced as a collapsible block.

The wire protocol is unchanged from before the refactor, so the UI
behaves the same except for the three new affordances above.
