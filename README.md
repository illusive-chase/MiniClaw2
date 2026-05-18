  Backend:
  cd backend
  pip install -e .
  python -m miniclaw2 --reload          # → http://127.0.0.1:8000
  
  Frontend:
  cd frontend
  npm install
  npm run dev                           # → http://127.0.0.1:5173 (proxies 
  /sessions and /ws)

  What's there

  Backend (backend/miniclaw2/)
  - agent.py:CCAgent — port of the old agent/cc.py, trimmed. Wraps
  claude-agent-sdk, translates SDK messages (AssistantMessage / ToolUseBlock /
  ToolResultBlock / Task*Message / ResultMessage) into web events. Stores
  sdk_session_id for resume across turns. can_use_tool posts an
  interaction_request to the queue and awaits a Future the WS layer resolves.
  - session.py:SessionRegistry — in-memory {id → Session}. 
  - events.py — Pydantic models for the wire protocol.
  - app.py — FastAPI app: POST/GET/DELETE /sessions, WS /ws/{id}. One in-flight
  turn per socket; interaction_response resolves the agent's pending Future;
  interrupt cancels the turn task.
  - __main__.py — uvicorn entry.
  
  Frontend (frontend/)
  - App.tsx — creates a session on mount, opens WS, renders chat + active
  interaction modal + token usage in the header.
  - components/Chat.tsx + ToolActivity.tsx — message bubbles with inline
  tool-activity indicators (dot color by status).
  - PermissionDialog.tsx, AskUserDialog.tsx, PlanDialog.tsx — three interaction
  UIs, matching the three interaction_type values.
  - ws.ts — useSessionSocket hook; api.ts — REST helpers; types.ts — mirrors
  backend events.
  
  Notes / known gaps (intentional for "minimal")

  - Sessions are in-memory only. Restart wipes them.
  - Token usage is shown but not cost — no pricing config carried over.
  - ExitPlanMode "approve & execute" currently denies-with-interrupt (so the SDK
   unwinds). The old code's full "clear history, re-issue plan" loop wasn't
  ported because the new backend doesn't keep an internal ChatMessage history.
  Easy to add later.
  - No auth, no CSRF, no rate-limiting — bind to 127.0.0.1 only.
  - I didn't run pip install or npm install myself; you'll see real errors only
  on first run. Tell me if anything blows up and I'll iterate.