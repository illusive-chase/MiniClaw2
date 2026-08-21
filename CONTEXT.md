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
- `PHILOSOPHY.md`: product and architecture destination — the position to
  argue from. `FUTURES.md`: the gap between that destination and the code
  (known divergences, latent hazards, unbuilt directions). Neither
  enumerates what has landed; the code is the ledger of that.

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
  translate external agent I/O. Concurrency is bounded per project and a
  fresh agent session is the default unless `resume_from_node_id` is explicit.
- Root `CONTEXT.md` is stable, provider-neutral launch guidance resolved and
  snapshotted for ordinary agent launches. Keep plans, current work, blockers,
  and detailed feature status out of it; those belong in planspaces, or in
  `FUTURES.md` when they are durable design gaps rather than current work.

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

## Subsystem Skeleton

An orientation map, deliberately coarse: it names what each subsystem owns,
not what it currently supports. Read the code for behavior.

- **Domain and persistence** — `Node` and `Project` records as JSON, plus
  per-node JSONL event and gate logs, under `$MINICLAW_HOME`. A node is
  either an agent session, a fast `op` state transition, or a script
  `verifier`; its `category` decides whether it may reshape the plan. The
  store carries a schema version and migrations, and per-host durable state
  is partitioned by machine id.
- **Providers** — one adapter per external agent CLI, translating its I/O
  onto a shared event and gate vocabulary. Model and provider selection is
  centralized in a preset catalog rather than chosen field by field.
- **Execution** — the runner owns the state machine, launch-prompt
  composition, the preview contract with its inline repair retries, and
  artifact publication. The registry owns project lifecycle, the bounded
  scheduler, and Git operations.
- **ContextSpace** — user-wide reusable context (global, principles,
  planspace manifests) plus the native skill library, connected to projects
  through editable bindings and snapshotted per launch for audit.
- **Graph projection** — each launch materializes the active planspace as a
  real filesystem subtree the agent reads and writes; reap validates what
  came back and folds it into durable state.
- **Templates** — capture a subgraph, declare its arguments and input
  ports, stamp it into a planspace. Bundled templates are the test
  catalogue; user templates live in ContextSpace.
- **Distribution** — the metadata store is a Git repository exchanged with
  a user-provided remote on explicit sync. A durable project's write
  authority comes from a host-local path binding; its creator machine is
  provenance only.
- **Frontend** — one React Flow canvas per project, a polymorphic side
  panel keyed on selection, and a library dock. Layout hints and viewport
  round-trip through the project record; collapse state is local.
