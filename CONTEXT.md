# MiniClaw2 Context

## What This Project Is

MiniClaw2 is a graph-oriented IDE for human-supervised coding-agent workflows. A Python FastAPI backend persists projects, nodes, gates, events, ContextSpace snapshots, and provider state while wrapping Claude Code SDK and Codex app-server adapters; a React + Vite + React Flow frontend renders persistent projects, a graph canvas, side panels, inline permission/review flows, and bundled demo scenarios.

## Repo Shape

- `backend/`: Python package (`pyproject.toml`) with runtime code in `backend/miniclaw2/`, provider adapters in `backend/miniclaw2/providers/`, bundled scenario definitions in `backend/miniclaw2/scenarios/bundled/`, and backend tests in `backend/tests/`.
- `frontend/`: Vite React app (`package.json`, `vite.config.ts`, `tsconfig.json`, Tailwind config) with app code in `frontend/src/`; no separate frontend test suite is visible.
- Root docs: `README.md` for architecture/run notes, `PHILOSOPHY.md` for product direction, `TEST.md` and `TESTING.zh.md` for scenario/demo testing.
- Runtime/local artifacts: root `.miniclaw2/`, `.claude/settings.local.json`, caches, build outputs, and `node_modules/` are not source; `.gitignore` excludes the main generated artifacts.

## Conventions and Guardrails

- Backend requires Python `>=3.11`. Install/run from `backend/` with `pip install -e .` and `python -m miniclaw2 --reload` or the `miniclaw2` console script. The server defaults to `127.0.0.1:8000`.
- Frontend uses Node/npm with Vite 5, React 18, TypeScript strict mode, and Tailwind. From `frontend/`: `npm install`, `npm run dev` for `127.0.0.1:5173`, and `npm run build` for `tsc -b && vite build`.
- Backend tests are `unittest`-style under `backend/tests/`; run from `backend/` with `python -m unittest discover -s tests`. Scenario validation also exists through the UI Tests modal and backend `verify.sh` runner.
- No Python formatter/linter config or frontend ESLint/Prettier config is visible. Match local style: typed Python with Pydantic/dataclasses/asyncio, typed React components, strict TypeScript, Tailwind utility classes, and small filesystem-first helpers.
- The backend store lives under `$MINICLAW_HOME` by default (`~/.miniclaw2`), while ContextSpace uses `$MINICLAW_CONTEXT_HOME` or `$MINICLAW_HOME/contextspace`. Do not treat generated store files or project-local `.miniclaw2/outputs/` as source.
- Project `CONTEXT.md` is provider-neutral launch context when present. New agent nodes start fresh unless explicitly launched with `resume_from_node_id`; a project runs only one node at a time. Inline gates and passive review gates are part of the state machine, not frontend-only UI.

## Where To Look

- Backend API and WebSocket gateway: `backend/miniclaw2/app.py`; protocol models: `backend/miniclaw2/events.py` mirrored by `frontend/src/types.ts`.
- Domain and persistence: `backend/miniclaw2/domain.py`, `backend/miniclaw2/store.py`, `backend/miniclaw2/registry.py`.
- Node execution: `backend/miniclaw2/runner.py`; provider interface/adapters: `backend/miniclaw2/providers/base.py`, `claude.py`, and `codex.py`; git/diff helpers in `backend/miniclaw2/git_state.py`.
- Context and memory: `backend/miniclaw2/contextspace.py`, `backend/miniclaw2/context_refresh.py`, prompt presets in `backend/miniclaw2/prompts/`.
- Scenarios: `backend/miniclaw2/scenarios/loader.py`, `launcher.py`, `verify.py`, and per-scenario `scenario.yaml`, `brief.md`, `acceptance.md`, `verify.sh`.
- Frontend shell and state flow: `frontend/src/App.tsx`, REST helpers in `frontend/src/api.ts`, WebSocket/replay handling in `frontend/src/ws.ts`.
- Graph UI: `frontend/src/canvas/Canvas.tsx`, `frontend/src/canvas/layout.ts`, node renderers in `frontend/src/canvas/nodes/`, and edge renderers in `frontend/src/canvas/edges/`.
- Inspector and controls: `frontend/src/panel/SidePanel.tsx` plus `AgentPanel`, `GatePanel`, `OpPanel`, `ProjectPanel`, `PlanspacePanel`; shared UI in `frontend/src/components/`.
