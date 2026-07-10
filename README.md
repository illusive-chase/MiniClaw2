# MiniClaw2

A minimal coding-agent wrapper with a graph-oriented web GUI. The Python
backend wraps provider adapters for Claude Code (native `claude` CLI
driven over a PTY) and Codex (`codex app-server`) over FastAPI +
WebSocket, paired with a React + Vite + React Flow frontend.

## Architecture

```
┌──────────────────────┐  REST / WebSocket  ┌──────────────────┐  provider adapter
│ React Flow workspace │ ─────────────────▶ │ FastAPI gateway  │ ───────────────▶ native `claude` CLI (PTY + JSONL)
│     (frontend/)      │ ◀───────────────── │    (backend/)    │ ───────────────▶ Codex app-server
└──────────────────────┘      events        └──────────────────┘   tool / messages
```

- **Backend** (`backend/miniclaw2/`) — a `Project` / `Node` /
  `HumanGate` domain model persisted to disk as JSON + JSONL. Each
  user prompt becomes a fresh agent `Node` using the project's selected
  provider. Provider conversation continuity is explicit rather than
  implicit: a node starts a new Claude session or Codex app-server
  thread unless it is launched with `resume_from_node_id`. A
  `NodeRunner` drives the state machine (`queued -> running [<-> waiting]
  -> done|error|cancelled`, with `awaiting_human_input` for human
  review nodes),
  translates provider messages into a small event union over WebSocket,
  persists every event to `events.jsonl` before pushing, injects output
  and category-specific launch contracts, and reaps graph previews after
  terminal transitions. Programmatic checks
  run as `verifier` nodes: deterministic scripts with normal node
  previews and error states.
- **Frontend** (`frontend/`) — a projects landing page plus a
  single-project React Flow canvas. The canvas materializes project root,
  agent/verifier, op, context, error-terminal, and planspace-lane nodes
  with dependency, timeline, resume, loads, and op-chevron edges. New
  work is created from direction controls and virtual-node actions. The
  selected item drives a polymorphic `SidePanel`
  (`AgentPanel`, `ContextNodePanel`, `PlanspaceFilePanel`, `OpPanel`,
  `ProjectPanel`) rather than the old fixed `NodeDetail`
  tabs. Assistant output is markdown-rendered (`react-markdown` + GFM +
  `highlight.js`); inline tool activity has collapsible output panels;
  pending ask-user (both providers) and permission (Codex only)
  requests render inside the agent panel; human-review prose requests
  render in the node surface; and WebSocket reconnect replay is handled
  by `ws.ts`.
- **Project and ContextSpace context** — a project-root `CONTEXT.md` is
  always loaded when present and injected provider-neutrally. If the
  project is bound to a ContextSpace, the launch also snapshots the
  active ContextSpace sources according to their manifests into
  `$MINICLAW_HOME/contextspace/snapshots/<bundle-id>.json`. Project
  `CONTEXT.md`, global plugs, and skill plugs are injected according to
  their declared mode. Planspace plugs are manifest-only; the agent reads
  their materialized graph lane instead. The node records
  `context_bundle_id`, `context_bundle_path`, and launch settings for audit.

## Scope

In: persistent project list, graph canvas workspace, streaming markdown
assistant output, tool activity with result panels, ask-user (both
providers) and permission (Codex) interactions, on-disk persistence per
project and node, interrupt, extended-thinking surface, WebSocket
reconnect replay, Claude and Codex provider adapters, provider-neutral
project context via `CONTEXT.md`, ContextSpace bootstrap / binding /
active planspace / bundle snapshots, virtual-node lanes, review agents,
  programmatic verifier nodes, bundled dashboard-launched templates, and
  opt-in auto-commit op nodes with two-commit per-node diffs.

Out (for now): per-token streaming for Claude, auth, cost tracking,
model/settings pickers in the primary UI, multi-project lane
visualization, fork/worktree graph operations, schema-generated review
forms, and automatic ContextSpace git commits. Vendor-specific on-disk
context (`CLAUDE.md`, `.claude/settings.json`, `.claude/agents`,
`.mcp.json`) is now applied by the native `claude` binary itself when
MiniClaw2 spawns it in the project cwd.

See [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) for the
single source of truth on what has landed, and
[`PHILOSOPHY.md`](PHILOSOPHY.md) for the design position.

## Run It

One-time install (Python >= 3.11, Node.js):

```bash
cd backend && pip install -e . && cd ..
cd frontend && npm install && npm run build && cd ..
```

Then a single command runs the full app. Two modes:

**Prod (default)** — FastAPI serves the built frontend and API on the
same origin:

```bash
python -m miniclaw2 --host 127.0.0.1 --port 8000 [--reload]
# UI + API: http://127.0.0.1:8000
```

`--reload` hot-reloads backend Python only. Rebuild the frontend
(`npm run build` in `frontend/`) whenever you change UI code.

**Dev** — same command spawns `npm run dev` alongside the backend so
Vite HMR is live; Vite's proxy routes `/model-presets`, `/sessions`,
`/templates`, and `/ws` back to the backend port:

```bash
python -m miniclaw2 --dev [--reload]
# backend:             http://127.0.0.1:8000
# frontend (Vite HMR): http://127.0.0.1:5173  <-- visit this
```

Ctrl-C stops both processes.

Env:

- `MINICLAW_HOME` (default `~/.miniclaw2`) — root for the on-disk store.
- `MINICLAW_CONTEXT_HOME` (optional) — overrides the default
  `$MINICLAW_HOME/contextspace` ContextSpace root.
- `MINICLAW_FRONTEND_DIST` (optional) — override the served frontend
  build directory. Set automatically by `python -m miniclaw2` to
  `<repo>/frontend/dist`; only export manually for non-editable
  installs.
- Claude provider: whatever auth the `claude` CLI already uses on your machine.
- Codex provider: `codex` must be on `PATH` and `codex doctor` should
  show working auth/config. The adapter uses
  `codex app-server --listen stdio://`, launched from the project cwd.
  The selected preset supplies `model` and reasoning effort. The model
  provider (including its `base_url`) is inherited from Codex config;
  approval and sandbox settings come from session overrides or
  `$CODEX_HOME/config.toml`, with the project cwd writable by default.

Create a Codex-backed project manually:

```bash
curl -X POST http://127.0.0.1:8000/sessions \
  -H 'content-type: application/json' \
  -d '{"cwd":"'"$PWD"'","model_preset_id":"gpt-5.6","name":"MiniClaw2"}'
```

Model selection is preset-based. Query `GET /model-presets` for the
available ids. Presets with `status: active` can be selected for new or
edited work; `status: compatibility` presets remain resolvable for old
data but cannot be newly selected. Current request bodies use
`model_preset_id` and reject the old `provider`, `model`, and
`model_provider` selection fields.

Opt a project into auto-commit (a `commit` op node is appended after
each agent/gate node reaches `done`, rewriting that node's
`commit_after` so the per-node diff becomes a real two-commit diff):

```bash
curl -X POST http://127.0.0.1:8000/sessions \
  -H 'content-type: application/json' \
  -d '{"cwd":"'"$PWD"'","auto_commit":true}'
```

## Layout

```
backend/miniclaw2/
  domain.py        # Project, Node, HumanGate + state enums
  store.py         # JSON/JSONL filesystem store under $MINICLAW_HOME
  model_catalog.py # model preset catalog and provider derivation
  runner.py        # provider-neutral NodeRunner state machine
  providers/       # native Claude CLI (PTY+JSONL) and Codex app-server adapters
  registry.py      # ProjectRegistry orchestration over the store
  events.py        # Pydantic models for the WS protocol
  app.py           # FastAPI: REST + WebSocket gateway
  contextspace.py  # ContextSpace bindings, plugs, and bundle snapshots
  context_refresh.py # out-of-band CONTEXT.md init/refresh tasks
  git_state.py     # git helpers for commit ids and read-only diffs
  preview.py       # strict executed/virtual preview schemas
  materialize.py   # durable lane -> agent-visible graph projection
  reap.py          # validate and persist graph writes after a run
  replay.py        # versioned replay upgrades + live buffering
  workspace.py     # temporary workspace creation / cleanup
  templates/       # bundled template loader, launcher, verifier scripts
  __main__.py      # uvicorn entry

frontend/src/
  App.tsx                  # routing, WS handling, graph workspace shell
  canvas/
    Canvas.tsx             # React Flow canvas
    layout.ts              # graph materialization and layout
    nodes/                 # Agent, Op, Context, ErrorTerminal, PlanspaceLane, Root
    edges/TimelineEdge.tsx # Dependency, Timeline, Resume, Loads, OpChevron edges
  panel/
    SidePanel.tsx          # polymorphic inspector dispatch
    AgentPanel.tsx         # result/activity/pending/Inspect drawer
    ContextNodePanel.tsx   # context source inspector
    OpPanel.tsx            # commit-op transition + diff
    PlanspaceFilePanel.tsx # project CONTEXT.md viewer
    ProjectPanel.tsx       # project settings + ContextSpace activation
  components/
    ProjectsLanding.tsx    # persistent project list
    NewProjectModal.tsx    # create/select cwd + model preset
    TestsPanel.tsx         # bundled template launcher modal
    PendingGateInline.tsx  # ask-user / permission response dispatch
    ToolActivity.tsx       # provider tool result rendering
    PermissionDialog.tsx, AskUserDialog.tsx
  ws.ts                    # useSessionSocket + reconnect replay
  api.ts                   # REST helpers
  types.ts                 # mirror of backend events / models
```

On-disk layout (under `$MINICLAW_HOME`, default `~/.miniclaw2`):

```
projects/<pid>/
  project.json
  nodes/<nid>/
    node.json           # full Node fields, rewritten on each state transition
    events.jsonl        # {schema_version, seq, event} per line, append-only
    gates.jsonl         # {action: "created"|"resolved", gate} per line

contextspace/
  contextspace.yaml
  bindings/projects/<binding-id>.yaml
  plugs/planspaces/<slug>/
    manifest.yaml
  plugs/global/<slug>/{manifest.yaml, CONTEXT.md}
  plugs/skills/<slug>/{manifest.yaml, CONTEXT.md, assets/}
  snapshots/<bundle-id>.json
  templates/<slug>/{template.yaml,lane.yaml,prompts/}
```

The active lane is materialized in the project workspace before a run:

```
<project_root>/.miniclaw2/graph/lanes/<planspace-id>/nodes/<nid>/
  preview.json
  transcript.json       # terminal nodes
  human-review.md       # human-interact reviews
  artifacts/            # copied from .miniclaw2/outputs/<nid>/ when present
```

Files under `.miniclaw2/outputs/<nid>/` are opaque optional artifacts;
MiniClaw2 does not impose the former `output_kind`/result-file ontology or
render artifact graph nodes.

## Wire Protocol

The HTTP/WS shape is the "session"-based compat layer: each session id
is a project id, and each `user_message` spawns a fresh agent node.

- Project/session REST APIs:
  `GET /sessions`, `POST /sessions`, `PATCH /sessions/{sid}`,
  `PATCH /sessions/{sid}/preferences`,
  `PATCH /sessions/{sid}/layout-hints`,
  `PATCH /sessions/{sid}/planspace-view`, and
  `DELETE /sessions/{sid}`.
- ContextSpace REST APIs:
  `GET /sessions/{sid}/contextspace`,
  `PATCH /sessions/{sid}/contextspace`,
  `POST /sessions/{sid}/context/init`,
  `POST /sessions/{sid}/context/refresh`,
  `POST /sessions/{sid}/context/cancel`,
  `GET /sessions/{sid}/files`,
  `POST /sessions/{sid}/planspaces`,
  `POST /sessions/{sid}/planspaces/blank`,
  `PATCH /sessions/{sid}/planspaces/{planspace_id}/mode`, and
  `GET /sessions/{sid}/nodes/{nid}/context-bundle`.
- Node REST APIs:
  `GET /sessions/{sid}/nodes`,
  `GET /sessions/{sid}/nodes/{nid}`,
  `GET /sessions/{sid}/nodes/{nid}/events`,
  `GET /sessions/{sid}/nodes/{nid}/diff`,
  `GET /sessions/{sid}/nodes/{nid}/preview`,
  `POST /sessions/{sid}/nodes/{nid}/rerun`, and virtual create/edit/delete/
  promote endpoints under `/sessions/{sid}/virtuals`.
- Template REST APIs:
  `GET /templates`, `GET /templates/{name}`, and
  `POST /templates/{name}/run`; user templates use `/user-templates` and
  `/sessions/{sid}/user-templates` endpoints.
- Skill REST APIs: `GET /skills`, `DELETE /skills/{slug}`.
- Client -> server:
  `user_message {text, resume_from_node_id?, extra_skills?,
  agent_op_kind?, model_preset_id?}`,
  `interaction_response`, `interrupt`, and
  `replay_request {node_id, since_seq}`.
- Server -> client:
  `node_started` (carries `kind`, `category`, `subtype`, and agent
  `prompt`), `node_updated`,
  `text_delta`, `thinking`, `activity` (with optional `result` +
  `result_kind`), `interaction_request` (`permission`, `ask_user`,
  or `human_review_prose`), `usage`, `turn_done`,
  and `error`. Events carry monotonic `seq` values for reconnect
  replay; persisted envelopes carry an event schema version and upgrade
  legacy `checkpoint_review` records before runtime delivery.

Current ask-user responses use
`response.answers.<question-id>.answers: string[]`; human reviews use
`response.prose`; permission responses use provider-neutral `allow`,
`message`, `updated_input`, `scope`, and `interrupt`. Provider adapters
translate that shape to vendor-specific decision vocabularies.

Exact shapes: [`backend/miniclaw2/events.py`](backend/miniclaw2/events.py)
and [`frontend/src/types.ts`](frontend/src/types.ts).

## Status

The current code has moved beyond the original chat-wrapper plan:

- Domain model on disk: `Project` / `Node` / `HumanGate` survive a
  process restart via JSON/JSONL. SQLite from `DESIGN.md` remains
  deferred.
- Provider layer is split out of the state machine. Projects and agents
  select a model preset; its provider and concrete model are derived from
  the central catalog. The default preset is `gpt-5.6` via Codex;
  compatibility presets remain available only for existing data.
- New nodes start fresh by default. Resume edges are explicit and copy
  the parent's provider session/thread id into the child node.
- The graph UI redesign is partially landed: persistent projects,
  React Flow canvas, context and error-terminal nodes, virtual-node actions,
  polymorphic side panel, project-root ContextSpace controls, and
  template test modal are current. Some PRD polish remains.
- `CONTEXT.md` plus ContextSpace bundle snapshots are in. Vendor-
  specific config (`CLAUDE.md` walk, `.claude/settings.json`,
  `.claude/agents`, `.mcp.json`) is now applied by the native `claude`
  binary itself when MiniClaw2 spawns it in the project cwd.
- `commit` op nodes are in. With `auto_commit:true`, a commit op is
  appended after each successful agent node and rewrites the
  preceding node's `commit_after`.
- The bundled template catalogue contains 7 templates:
  `hello-text`, `bash-uname`, `write-readme`, `interrupt-midstream`,
  `context-md-respected`, `resume-fix-after-reject`, and
  `gui-calculator`. `permission-approve` and `plan-mode-approval` were
  dropped when the native-CLI Claude provider disabled per-tool gating
  and plan mode; `reconnect-replay` was dropped because it required a
  test-only UI hook.

Remaining near-term work is graph UI polish and fork/worktree graph
ops.
