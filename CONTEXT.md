# MiniClaw2 Context

## What This Project Is

MiniClaw2 is a local, graph-oriented IDE for human-supervised coding-agent
workflows. A FastAPI backend runs Claude Code or Codex, persists projects and
their execution graph, and composes durable context; a React Flow frontend
turns that state into an interactive project canvas.

## Repository Map

- `backend/miniclaw2/`: Python runtime, API, persistence, providers,
  ContextSpace, graph materialization, and templates.
- `backend/tests/`: backend unit and API tests. Bundled end-to-end fixtures
  live under `backend/miniclaw2/templates/bundled/`.
- `frontend/src/`: React application. `App.tsx` owns session/UI state,
  `canvas/` materializes the graph, `panel/` contains inspectors, and
  `components/` contains shared workflows.
- `backend/pyproject.toml` and `frontend/package.json`: runtime dependencies
  and build commands.
- `PHILOSOPHY.md`: product and architecture destination.
  `IMPLEMENTATION_STATUS.md`: source-backed ledger of what exists now.

## Working Rules

- Backend requires Python `>=3.11`. From the repository root, use
  `python -m pytest backend/tests`; after an editable install from `backend/`,
  run the app with `python -m miniclaw2 --reload`.
- Frontend uses React 18, strict TypeScript, Vite 5, and Tailwind. From
  `frontend/`, use `npm run dev` while iterating and `npm run build` to run
  TypeScript plus the production build.
- Follow the existing typed Python and typed React style. Keep changes focused;
  the repository has no project-wide formatter or lint command to apply
  opportunistically.
- Treat root `.miniclaw2/`, Python caches, `frontend/dist/`, and
  `frontend/node_modules/` as generated/local state. Project-local
  `.miniclaw2/graph/` and `.miniclaw2/outputs/` are runtime projections, not
  source files.
- `$MINICLAW_HOME` stores projects and defaults to `~/.miniclaw2`.
  ContextSpace uses `$MINICLAW_CONTEXT_HOME` or
  `$MINICLAW_HOME/contextspace`.
- `ProjectRegistry`/`NodeRunner` own lifecycle and persistence; providers only
  translate external agent I/O. A project executes one node at a time, and a
  fresh agent session is the default unless `resume_from_node_id` is explicit.
- Root `CONTEXT.md` is stable, provider-neutral launch guidance resolved and
  snapshotted for ordinary agent launches. Keep plans, current work, blockers,
  and detailed feature status out of it; those belong in planspaces or
  `IMPLEMENTATION_STATUS.md`.

## Entry Points

- HTTP/WebSocket boundary: `backend/miniclaw2/app.py`; wire models:
  `backend/miniclaw2/events.py` and `frontend/src/types.ts`.
- Domain and storage: `backend/miniclaw2/domain.py`,
  `backend/miniclaw2/store.py`, and `backend/miniclaw2/registry.py`; model
  selection: `backend/miniclaw2/model_catalog.py`.
- Execution: `backend/miniclaw2/runner.py`,
  `backend/miniclaw2/launch_prompt.py`, and `backend/miniclaw2/providers/`
  (including `providers/claude_native/` for PTY, transcript, and hooks).
- Context and graph state: `backend/miniclaw2/contextspace.py`,
  `backend/miniclaw2/context_refresh.py`, `backend/miniclaw2/materialize.py`,
  `backend/miniclaw2/reap.py`, `backend/miniclaw2/preview.py`, and
  `backend/miniclaw2/virtual_graph.py`.
- Templates: `backend/miniclaw2/templates/loader.py`,
  `backend/miniclaw2/templates/launcher.py`, and
  `backend/miniclaw2/templates/serializer.py`.
- Frontend graph flow: `frontend/src/App.tsx`, `frontend/src/api.ts`,
  `frontend/src/ws.ts`, `frontend/src/canvas/Canvas.tsx`, and
  `frontend/src/canvas/layout.ts`.
