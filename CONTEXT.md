# MiniClaw2 Context

## What This Project Is

MiniClaw2 is a graph-oriented IDE for human-supervised coding-agent
workflows. A Python FastAPI backend persists projects, nodes, gates,
events, ContextSpace snapshots, provider state, and template metadata.
A React + Vite + React Flow frontend renders persistent projects, the
canvas, side panels, inline human interactions, ContextSpace controls,
and bundled testing templates.

## Repo Shape

- `backend/`: Python package with runtime code in `backend/miniclaw2/`,
  provider adapters in `backend/miniclaw2/providers/`, bundled template
  definitions in `backend/miniclaw2/templates/bundled/`, and backend
  tests in `backend/tests/`.
- `frontend/`: Vite React app with app code in `frontend/src/`, canvas
  code under `frontend/src/canvas/`, panel code under
  `frontend/src/panel/`, and REST/WS helpers in `api.ts` / `ws.ts`.
- Root docs: `PHILOSOPHY.md` is the product/architecture destination;
  `IMPLEMENTATION_STATUS.md` is the current implementation ledger.
  Other docs may be stale unless recently reconciled with those two.
- Runtime/local artifacts: root `.miniclaw2/`, `.claude/`, Python
  caches, frontend build output, and `node_modules/` are generated or
  local-only. Do not treat generated store files or project-local
  `.miniclaw2/outputs/` as source.

## Conventions and Guardrails

- Backend requires Python `>=3.11`. Install/run from `backend/` with
  `pip install -e .` and `python -m miniclaw2 --reload` or the
  `miniclaw2` console script. The server defaults to `127.0.0.1:8000`.
- Frontend uses Node/npm with Vite 5, React 18, TypeScript strict mode,
  and Tailwind. From `frontend/`: `npm install`, `npm run dev` for
  `127.0.0.1:5173`, and `npm run build` for `tsc -b && vite build`.
- Backend tests live under `backend/tests/`; run them with
  `python -m pytest backend/tests` from the repository root, or run
  focused files while iterating.
- Match local style: typed Python with Pydantic/dataclasses/asyncio,
  typed React components, strict TypeScript, Tailwind utility classes,
  and small filesystem-first helpers. There is no broad formatter pass
  expected for unrelated files.
- The backend store lives under `$MINICLAW_HOME` by default
  (`~/.miniclaw2`), while ContextSpace uses `$MINICLAW_CONTEXT_HOME` or
  `$MINICLAW_HOME/contextspace`.
- Project `CONTEXT.md` is provider-neutral launch context when present.
  New agent nodes start fresh unless explicitly launched with
  `resume_from_node_id`; a project runs only one node at a time.
  Human ask gates and review nodes are part of the backend state
  machine, not frontend-only UI.

## Where To Look

- Backend API and WebSocket gateway: `backend/miniclaw2/app.py`;
  protocol models: `backend/miniclaw2/events.py`, mirrored by
  `frontend/src/types.ts`.
- Domain and persistence: `backend/miniclaw2/domain.py`,
  `backend/miniclaw2/store.py`, and `backend/miniclaw2/registry.py`.
- Node execution: `backend/miniclaw2/runner.py`; provider
  interface/adapters: `backend/miniclaw2/providers/base.py`,
  `backend/miniclaw2/providers/claude.py`,
  `backend/miniclaw2/providers/claude_native/`, and
  `backend/miniclaw2/providers/codex.py`.
- Claude native integration: PTY spawning and environment setup in
  `providers/claude_native/spawn.py`; hook install/runtime in
  `providers/claude_native/hook_installer.py` and `hook_runtime.py`;
  hook subprocess entrypoint in `backend/miniclaw2/claude_hook_bridge.py`.
- Context and memory: `backend/miniclaw2/contextspace.py`,
  `backend/miniclaw2/context_refresh.py`, and prompt presets in
  `backend/miniclaw2/prompts/`.
- Templates: bundled template loading/launching in
  `backend/miniclaw2/templates/`; user-authored templates are stored
  under the ContextSpace templates root.
- Frontend shell and state flow: `frontend/src/App.tsx`, REST helpers
  in `frontend/src/api.ts`, WebSocket/replay handling in
  `frontend/src/ws.ts`.
- Graph UI: `frontend/src/canvas/Canvas.tsx`,
  `frontend/src/canvas/layout.ts`, node renderers in
  `frontend/src/canvas/nodes/`, and edge renderers in
  `frontend/src/canvas/edges/`.
- Inspector and controls: `frontend/src/panel/SidePanel.tsx`,
  `frontend/src/panel/AgentPanel.tsx`, and shared UI in
  `frontend/src/components/`.
