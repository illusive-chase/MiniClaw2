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

- **Backend** (`backend/miniclaw2/`) — one `CCAgent` per session. Translates
  SDK messages (`AssistantMessage`, `ToolUseBlock`, `ToolResultBlock`,
  `Task*Message`, `ResultMessage`) into a small event union sent over the
  WebSocket. The SDK's `can_use_tool` callback is bridged into an
  `interaction_request` event the frontend resolves.
- **Frontend** (`frontend/`) — chat surface with streaming text deltas,
  inline tool-activity indicators, and three interaction dialogs:
  permission prompt, `AskUserQuestion`, and `ExitPlanMode`.

## Scope (minimal)

In: streaming chat, tool activity, permission / ask-user / plan-approval
interactions, multi-session via session IDs (in-memory).
Out (for now): disk persistence, auth, cost tracking, sub-agents, custom
tools, native (non-CC) backends.

## Run it

**Backend** (Python ≥ 3.11):
```bash
cd backend
pip install -e .
python -m miniclaw2 --reload     # http://127.0.0.1:8000
```
Env: `ANTHROPIC_MODEL` (default `claude-sonnet-4-6`) plus whatever auth
the `claude` CLI already uses on your machine.

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
  agent.py      # CCAgent — wraps claude-agent-sdk, yields web events
  session.py    # in-memory session registry
  events.py     # Pydantic models for the WS protocol
  app.py        # FastAPI: REST + WebSocket
  __main__.py   # uvicorn entry

frontend/src/
  App.tsx       # session + chat + interaction dispatch
  ws.ts         # useSessionSocket hook
  api.ts        # REST helpers
  types.ts      # mirror of backend events
  components/   # Chat, ToolActivity, PermissionDialog, AskUserDialog, PlanDialog
```

## Wire protocol

- Client → server: `user_message`, `interaction_response`, `interrupt`.
- Server → client: `text_delta`, `activity`, `interaction_request`, `usage`,
  `turn_done`, `error`.

Exact shapes: [`backend/miniclaw2/events.py`](backend/miniclaw2/events.py)
and [`frontend/src/types.ts`](frontend/src/types.ts).
