# MiniClaw2

A minimal coding-agent wrapper with a graph-oriented web GUI. The Python
backend wraps provider adapters for Claude Code (`claude-agent-sdk`) and
Codex (`codex app-server`) over FastAPI + WebSocket, paired with a React
+ Vite + React Flow frontend.

## Architecture

```
┌──────────────────────┐  REST / WebSocket  ┌──────────────────┐  provider adapter
│ React Flow workspace │ ─────────────────▶ │ FastAPI gateway  │ ───────────────▶ Claude SDK
│     (frontend/)      │ ◀───────────────── │    (backend/)    │ ───────────────▶ Codex app-server
└──────────────────────┘      events        └──────────────────┘   tool / messages
```

- **Backend** (`backend/miniclaw2/`) — a `Project` / `Node` /
  `HumanGate` domain model persisted to disk as JSON + JSONL. Each
  user prompt becomes a fresh agent `Node` using the project's selected
  provider. Provider conversation continuity is explicit rather than
  implicit: a node starts a new Claude SDK session or Codex app-server
  thread unless it is launched with `resume_from_node_id`. A
  `NodeRunner` drives the state machine (`queued -> running [<-> waiting]
  -> done|error|cancelled`, with `awaiting_review` for passive gates),
  translates provider messages into a small event union over WebSocket,
  persists every event to `events.jsonl` before pushing, injects output
  contracts (`freeform`, `summary`, `interface`, or `review_brief`), and
  applies ContextSpace memory-delta artifacts after terminal transitions.
- **Frontend** (`frontend/`) — a projects landing page plus a
  single-project React Flow canvas. The canvas materializes project root,
  agent, gate, op, artifact, context, and phantom-composer nodes with
  timeline, resume, produces, reviews, and loads edges. Launching work is
  done through the dashed `PhantomNode` composer on the canvas, not a
  `+ Node` modal. The selected item drives a polymorphic `SidePanel`
  (`AgentPanel`, `GatePanel`, `ArtifactPanel`, `ContextNodePanel`,
  `OpPanel`, `ProjectPanel`) rather than the old fixed `NodeDetail`
  tabs. Assistant output is markdown-rendered (`react-markdown` + GFM +
  `highlight.js`); inline tool activity has collapsible output panels;
  pending permission / ask-user / plan requests render inside the agent
  panel; passive review gates render a response form in `GatePanel`; a
  `VerifyCard` appears on scenario projects after nodes reach terminal
  states; and WebSocket reconnect replay is handled by `ws.ts`.
- **Project and ContextSpace context** — a project-root `CONTEXT.md` is
  always loaded when present and injected provider-neutrally. If the
  project is bound to a ContextSpace, the launch also snapshots the
  active planspace sources (`STATUS.md`, `PLAN.md`, and enabled
  plugs according to their manifests) into
  `$MINICLAW_HOME/contextspace/snapshots/<bundle-id>.json`. Project
  `CONTEXT.md` goes into system context; planspace sources are injected
  as turn context. The node records `context_bundle_id`,
  `context_bundle_path`, `context_sources`, and launch settings for
  audit.

## Scope

In: persistent project list, graph canvas workspace, streaming markdown
assistant output, tool activity with result panels, permission /
ask-user / plan-approval interactions, on-disk persistence per project
and node, interrupt, extended-thinking surface, WebSocket reconnect
replay, Claude and Codex provider adapters, provider-neutral project
context via `CONTEXT.md`, ContextSpace bootstrap / binding / active
planspace / bundle snapshots, memory-delta writeback for safe
`STATUS.md` observations, passive checkpoint gate nodes, node output
contracts (`summary` markdown, `interface` JSON, `review_brief`
markdown), bundled dashboard-launched scenarios, and opt-in auto-commit
op nodes with two-commit per-node diffs.

Out (for now): vendor-specific on-disk context (`CLAUDE.md`,
`AGENTS.md`, `.claude/settings.json`, `.claude/agents`, `.mcp.json`),
per-token streaming for Claude, auth, cost tracking, model/settings
pickers in the primary UI, multi-project lane visualization, template
runs, fork/worktree graph operations, schema-generated review forms,
and automatic ContextSpace git commits.

See [`DESIGN.md`](DESIGN.md) for the long-term graph-IDE plan,
[`PROPOSAL.md`](PROPOSAL.md) for the CLI-parity punch list, and
[`UI_REDESIGN_PRD.md`](UI_REDESIGN_PRD.md) for the graph UI rationale
and remaining polish gaps.

## Run It

**Backend** (Python >= 3.11):

```bash
cd backend
pip install -e .
python -m miniclaw2 --reload     # http://127.0.0.1:8000
```

Env:

- `MINICLAW_ANTHROPIC_MODEL` (default `claude-sonnet-4-6`)
- `MINICLAW_HOME` (default `~/.miniclaw2`) — root for the on-disk store.
- `MINICLAW_CONTEXT_HOME` (optional) — overrides the default
  `$MINICLAW_HOME/contextspace` ContextSpace root.
- Claude provider: whatever auth the `claude` CLI already uses on your machine.
- Codex provider: `codex` must be on `PATH` and `codex doctor` should
  show working auth/config. The adapter uses
  `codex app-server --listen stdio://` and does not set
  `modelProvider`, `approvalPolicy`, or `sandbox` unless they are
  explicitly provided as session overrides, so Codex keeps using
  `$CODEX_HOME/config.toml` defaults.

Create a Codex-backed project manually:

```bash
curl -X POST http://127.0.0.1:8000/sessions \
  -H 'content-type: application/json' \
  -d '{"cwd":"'"$PWD"'","provider":"codex","name":"MiniClaw2"}'
```

Opt a project into auto-commit (a `commit` op node is appended after
each agent/gate node reaches `done`, rewriting that node's
`commit_after` so the per-node diff becomes a real two-commit diff):

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

Vite proxies `/sessions`, `/scenarios`, and `/ws` to the backend.

## Layout

```
backend/miniclaw2/
  domain.py        # Project, Node, HumanGate, ContextBundle + state enums
  store.py         # JSON/JSONL filesystem store under $MINICLAW_HOME
  runner.py        # provider-neutral NodeRunner state machine
  providers/       # Claude SDK and Codex app-server adapters
  registry.py      # ProjectRegistry orchestration over the store
  events.py        # Pydantic models for the WS protocol
  app.py           # FastAPI: REST + WebSocket gateway
  contextspace.py  # ContextSpace bindings, bundle snapshots, memory deltas
  context.py       # legacy CONTEXT.md loader helper
  git_state.py     # git helpers for commit ids and read-only diffs
  artifacts.py     # output artifact loading / summarization
  replay.py        # replay/live buffering for reconnecting WS observers
  workspace.py     # temporary workspace creation / cleanup
  scenarios/       # bundled scenario loader, launcher, verifier
  __main__.py      # uvicorn entry

frontend/src/
  App.tsx                  # routing, WS handling, graph workspace shell
  canvas/
    Canvas.tsx             # React Flow canvas
    layout.ts              # graph materialization and layout
    nodes/                 # Agent, Gate, Op, Artifact, Context, Phantom, Root nodes
    edges/                 # Timeline, Resume, Produces, Reviews, Loads edges
  panel/
    SidePanel.tsx          # polymorphic inspector dispatch
    AgentPanel.tsx         # result/activity/pending/Inspect drawer
    GatePanel.tsx          # passive review response form
    ArtifactPanel.tsx      # result.md/result.json/brief.md viewer
    ContextNodePanel.tsx   # context source inspector
    OpPanel.tsx            # commit-op transition + diff
    ProjectPanel.tsx       # project settings + ContextSpace activation
  components/
    ProjectsLanding.tsx    # persistent project list + Tests modal
    NewProjectModal.tsx    # create/select cwd + provider
    TestsPanel.tsx         # bundled scenario launcher
    VerifyCard.tsx         # verify.sh + human acceptance checklist
    ToolActivity.tsx       # provider tool result rendering
    PermissionDialog.tsx, AskUserDialog.tsx, PlanDialog.tsx
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
    events.jsonl        # {seq, event} per line, append-only
    gates.jsonl         # {action: "created"|"resolved", gate} per line

contextspace/
  contextspace.yaml
  bindings/projects/<binding-id>.yaml
  plugs/planspaces/<slug>/
    manifest.yaml
    STATUS.md
    PLAN.md
    SKILLS.md
    events.jsonl
    inbox/<node-id>.memory-delta.json
  snapshots/<bundle-id>.json
```

Agent node output artifacts live in the project workspace by default:

```
<project_root>/.miniclaw2/outputs/<nid>/result.md          # output_kind=summary
<project_root>/.miniclaw2/outputs/<nid>/result.json        # output_kind=interface
<project_root>/.miniclaw2/outputs/<nid>/brief.md           # output_kind=review_brief
<project_root>/.miniclaw2/outputs/<nid>/memory-delta.json  # optional ContextSpace writeback
```

## Wire Protocol

The HTTP/WS shape is the "session"-based compat layer: each session id
is a project id, and each `user_message` spawns a fresh agent node.

- Project/session REST APIs:
  `GET /sessions`, `POST /sessions`, `PATCH /sessions/{sid}`,
  `DELETE /sessions/{sid}`.
- ContextSpace REST APIs:
  `GET /sessions/{sid}/contextspace`,
  `PATCH /sessions/{sid}/contextspace`,
  `POST /sessions/{sid}/contextspace/bootstrap`, and
  `GET /sessions/{sid}/nodes/{nid}/context-bundle`.
- Node REST APIs:
  `GET /sessions/{sid}/nodes`,
  `GET /sessions/{sid}/nodes/{nid}`,
  `GET /sessions/{sid}/nodes/{nid}/events`,
  `GET /sessions/{sid}/nodes/{nid}/diff`, and
  `GET /sessions/{sid}/nodes/{nid}/artifact`.
- Scenario REST APIs:
  `GET /scenarios`, `GET /scenarios/{name}`,
  `POST /scenarios/{name}/run`, and
  `POST /sessions/{sid}/verify`.
- Client -> server:
  `user_message {text, resume_from_node_id?, output_kind?, output_path?}`,
  `interaction_response`, `interrupt`, and
  `replay_request {node_id, since_seq}`. `output_kind:
  "review_brief"` triggers an automatic follow-up passive gate after
  the agent completes and any auto-commit op finishes.
- Server -> client:
  `node_started` (carries `kind` and agent `prompt`), `node_updated`,
  `text_delta`, `thinking`, `activity` (with optional `result` +
  `result_kind`), `interaction_request` (`permission`, `ask_user`,
  `plan_approval`, or `checkpoint_review`), `usage`, `turn_done`, and
  `error`. Events carry monotonic `seq` values for reconnect replay.

`interaction_response` remains backward-compatible with Claude's
`allow/message/updated_input` shape and also accepts Codex-style
`decision`, `scope`, `interrupt`, and raw `response` payloads.

Exact shapes: [`backend/miniclaw2/events.py`](backend/miniclaw2/events.py)
and [`frontend/src/types.ts`](frontend/src/types.ts).

## Status

The current code has moved beyond the original chat-wrapper plan:

- Domain model on disk: `Project` / `Node` / `HumanGate` survive a
  process restart via JSON/JSONL. SQLite from `DESIGN.md` remains
  deferred.
- Provider layer is split out of the state machine. Claude remains the
  default provider; Codex can be selected per project/scenario.
- New nodes start fresh by default. Resume edges are explicit and copy
  the parent's provider session/thread id into the child node.
- The graph UI redesign is partially landed: persistent projects,
  React Flow canvas, context/artifact nodes, phantom composer,
  polymorphic side panel, project-root ContextSpace controls, and
  scenario test modal are current. Some PRD polish remains, notably
  op-as-edge-chevron and inline gate expansion directly inside canvas
  tiles; today ops are still graph nodes and inline gates render in
  the agent side panel.
- `CONTEXT.md` plus ContextSpace bundle snapshots are in. Vendor-
  specific loading (`CLAUDE.md` walk, `.claude/settings.json`,
  `.claude/agents`, `.mcp.json`) is still intentionally deferred.
- Passive `gate` node flow is in. Agents launched with
  `output_kind=review_brief` write `brief.md`; the registry then
  spawns a gate that renders a write-json / no-op response form.
  Gate write-json responses stamp `review_outcome` and update the
  source node's human `acceptance_state` / `verdict_*` fields.
- `commit` op nodes are in. With `auto_commit:true`, a commit op is
  appended after each successful agent/gate node and rewrites the
  preceding node's `commit_after`.
- The bundled scenario catalogue contains 10 scenarios:
  `hello-text`, `bash-uname`, `write-readme`, `permission-approve`,
  `plan-mode-approval`, `interrupt-midstream`, `context-md-respected`,
  `resume-fix-after-reject`, `reconnect-replay`, and `gui-calculator`.

Remaining near-term work is graph UI polish, template/programmed graph
runs from `DESIGN.md` Phase 3, and the deferred vendor-specific context
loading needed for tighter native CLI parity.
