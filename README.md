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
  `CONTEXT.md`, global plugs, and principle plugs are injected according to
  their declared mode. Planspace plugs are manifest-only; the agent reads
  their materialized graph lane instead. The node records
  `context_bundle_id`, `context_bundle_path`, and launch settings for audit.
- **Principles and Agent Skills** - principles are user-wide behavior guidance
  injected eagerly through context bundles. The librarian authors or refines
  principles and native Agent Skills from a user seed, validating exactly one
  changed entry and recording its content hash. Native Agent Skills live under
  `contextspace/skills` and can also be imported without format conversion.
  A source containing multiple `SKILL.md` files imports atomically as a package;
  selecting one package member attaches the complete package, while
  `metadata.requires.siblings` dependencies are resolved recursively. Skills
  are made available per node through Claude's plugin directory or Codex
  app-server's per-process extra skill roots. Launch settings record content
  hashes, paths, attachment provenance, materialization outcomes, and
  conservative used/not-used observations.

## Scope

In: persistent project list, manual Git-backed metadata synchronization,
native-machine project ownership with cross-machine read-only viewing, graph canvas workspace, streaming markdown
assistant output, tool activity with result panels, ask-user (both
providers) and permission (Codex) interactions, on-disk persistence per
project and node, interrupt, extended-thinking surface, WebSocket
reconnect replay, Claude and Codex provider adapters, provider-neutral
project context via `CONTEXT.md`, ContextSpace bootstrap / binding /
planspace resolution / bundle snapshots, virtual-node lanes, review agents,
  programmatic verifier nodes, bundled dashboard-launched templates, and
  opt-in auto-commit op nodes with two-commit per-node diffs.

Out (for now): per-token streaming for Claude, auth, cost tracking,
model/settings pickers in the primary UI, multi-project lane
visualization, fork/worktree graph operations, and schema-generated review
forms. ContextSpace history is included in ordinary metadata sync commits.
Vendor-specific on-disk
context (`CLAUDE.md`, `.claude/settings.json`, `.claude/agents`,
`.mcp.json`) is now applied by the native `claude` binary itself when
MiniClaw2 spawns it in the project cwd.

See [`PHILOSOPHY.md`](PHILOSOPHY.md) for the design position and
[`FUTURES.md`](FUTURES.md) for the gap between it and the code.

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

`--reload` hot-reloads backend Python. Rebuild the frontend (`npm run
build` in `frontend/`) whenever you change UI code.

**Dev** — same command spawns `npm run dev` alongside the backend so
Vite's proxy routes the frontend API paths (including `/global-state`,
`/model-presets`, `/sessions`, `/principles`, `/skills`, `/templates`, and
`/user-templates`) plus `/ws` back to the backend port. Add `--reload` to
enable both backend auto-reload and frontend HMR; without it, neither side
reloads on source changes:

```bash
python -m miniclaw2 --dev [--reload]
# backend:             http://127.0.0.1:8000
# frontend (Vite):     http://127.0.0.1:5173  <-- visit this
```

Ctrl-C stops both processes.

## Metadata Sync

`$MINICLAW_HOME` 可以通过 Git remote 同步。持久项目在当前设备绑定本地目录后
即可执行，创建者设备只记录来源；每台设备只能修改自己分区中的节点。
全局设置和 ContextSpace 在隔离候选树中合并并校验，结构冲突或格式契约冲突则
停止同步，等待人工处理。

临时项目无需绑定目录：同步到新设备后即可继续创建节点，框架自动准备非 Git
缓存目录，缓存被系统清理后也会重建。图、逐字记录与已发布产物是持久状态，
缓存中的普通文件不随设备迁移。续接保留图中的来源关系，但始终启动新的
provider 会话，不复用设备本地 session id。远端节点仍只读，可作为新节点的
上下文来源；临时项目不提供 Git、工作目录打开或绑定功能。

Session API 的 `capabilities.workspace` 与 `capabilities.git_review` 控制上述
入口；删除了未实现的 `artifact_restore` 和含义不清的 `resumable` 声明。
产物仍可从持久记录读取和下载，但不承诺自动还原整个临时工作目录。

Principle files, native skill directories, `skill-imports.json` package and
dependency provenance, and node attachment selections are all part of this
metadata store and sync together. Provider/CLI credentials, macOS Keychain
entries, and other machine-local authentication state are intentionally not
included.

For an existing store and an empty remote, open **Global settings**, enter the
remote URL under **Metadata sync**, acknowledge that the private remote will
contain complete prompts, transcripts, tool output, and code, then choose
**Set up sync**. The equivalent CLI is:

```bash
python -m miniclaw2 sync init <git-url>
```

Run the same command on a fresh machine with an empty `$MINICLAW_HOME` to clone
the remote and generate a new local `machine.json`. After setup, remote I/O is
manual-only: press **Sync now** in Global settings. Durable changes are
committed locally in a roughly 30-second coalescing window, but MiniClaw2 does
not fetch or push on startup, shutdown, or a timer.

升级前同步一次，完成存储迁移后再同步一次。`machine.json` 记录本机设备标识的
哈希（不参与同步），hostname 仅作为显示名称：同一设备改名保留 machine id，
复制到设备标识不同的机器则自动生成新 id，不继承来源机器的路径绑定。
设备标识分别取自 macOS 的 `IOPlatformUUID`、Linux 的系统 `machine-id`
和 Windows 的 `MachineGuid`。自动改名保留同步检查点和自定义显示名称，
仅随 hostname 设置的默认名称会跟随更新，并修复本机所属项目及 host 的标签。

旧版身份没有设备标识且 hostname 已变化时，框架无法可靠区分改名与复制，
会要求明确选择。先停止使用此存储的后端，再执行以下命令之一并重启：

```bash
python -m miniclaw2 machine rename  # 确认只是同一设备改名
python -m miniclaw2 machine copy    # 确认这是另一设备上的副本
```

两个命令都支持 `--label`，不依赖交互终端。已记录设备指纹却暂时无法读取系统
标识时，启动会停止，不会把读取失败当作新设备，也不会允许 `rename` 清除旧指纹。
身份创建、复制和同步检查点写入使用进程间锁；中断的标签修复会在下次启动重试。
运行中执行身份切换后，旧 Store 的写入和旧同步管理器会被拒绝，仍须停止并重启
所有使用该存储的进程。同步状态中的 `hostname_mismatch` 仅是进程内名称变化的
诊断信息，不再作为只读或禁止同步的依据。

同名的旧版副本、共享系统 machine-id 的克隆镜像无法仅靠本机标识自动区分；
首次打开此类副本前应主动运行 `machine copy`。新设备优先使用 `sync init`
克隆远端，不要复制包含本地身份的整个存储目录。

Env:

- `MINICLAW_HOME` (default `~/.miniclaw2`) — root for the on-disk store.
- `MINICLAW_CONTEXT_HOME` (optional) — overrides the default
  `$MINICLAW_HOME/contextspace` ContextSpace root. Metadata sync requires this
  override to be unset or point to that default location.
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

The catalog is fully configuration-driven. On first startup MiniClaw2 writes
`$MINICLAW_HOME/config.json` from the packaged starter configuration; runtime
selection then reads every preset and default from that user-owned file. There
is no separate hard-coded or protected preset catalog. The Projects page's
**Global settings** panel can:

- view the active configuration path and every configured preset;
- add and edit Codex or Claude presets;
- delete any non-default preset that is not referenced by a project;
- configure the default preset, language, concurrency, and auto-commit value
  used by new projects.

The same operations are available over REST:

```text
GET    /global-state
PATCH  /global-state/defaults
POST   /global-state/model-presets
PUT    /global-state/model-presets/{preset_id}
DELETE /global-state/model-presets/{preset_id}
```

Set another active preset as the default before deleting the current default.
Deletion also rejects presets referenced by existing project or node records,
so historical runs remain resolvable.

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
  sync.py          # machine identity, local commits, explicit Git synchronization
  global_config.py # validated global defaults + configurable preset storage
  model_catalog.py # preset lookup and provider derivation from global config
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
config.json             # global defaults and complete model preset catalog
schema.json             # 当前共享格式契约；目标版本由发行迁移清单决定
machine.json            # 本地 UUID、设备指纹哈希、标签和同步检查点（不参与同步）
machine.lock            # 身份写入的进程间锁（不参与同步）
.migration-local/       # 本机完成凭据、维护锁和可恢复事务日志（不参与同步）
migration-backups/      # 原始文件备份，不随代码支持窗口删除（不参与同步）
.gitignore              # excludes machine identity, backups, and temp writes
projects/<pid>/
  project.json          # includes native machine id + display label
  hosts/<machine-id>/nodes/<nid>/
    node.json           # full Node fields, rewritten on each state transition
    events.jsonl        # {schema_version, seq, event} per line, append-only
    gates.jsonl         # {action: "created"|"resolved", gate} per line

contextspace/
  contextspace.yaml
  bindings/projects/<binding-id>.yaml
  plugs/planspaces/<project-binding-slug>.<lane-slug>/
    manifest.yaml
  plugs/global/<slug>/{manifest.yaml, CONTEXT.md}
  plugs/principles/<slug>/{manifest.yaml, CONTEXT.md}
  skills/<slug>/{SKILL.md, ...}
  skill-imports.json    # import, package-membership, and auto-attach provenance
  snapshots/<bundle-id>.json
  templates/<slug>/{template.yaml,lane.yaml,prompts/}
```

Planspace IDs are project-scoped as
`planspaces.<project-binding-slug>.<lane-slug>`. The lane slug is unique only
inside its project binding, so two projects can both have an unnumbered
`direction` lane.

The launching node's own lane is materialized in the project workspace
before a run:

```
<project_root>/.miniclaw2/graph/lanes/<planspace-id>/nodes/<nid>/
  preview.json
  transcript.json       # terminal nodes
  human-review.md       # human-interact reviews
  artifacts/            # local outputs plus published artifacts from the store
```

Files under `.miniclaw2/outputs/<nid>/` are opaque optional artifacts;
MiniClaw2 does not impose the former `output_kind`/result-file ontology or
render artifact graph nodes.

## Wire Protocol

The HTTP/WS shape is the "session"-based compat layer: each session id
is a project id, and each `user_message` spawns a fresh agent node.

`GET /sessions` 返回项目摘要，不包含 `node_positions`、`git_positions`、
`lane_positions`；打开项目时通过 `GET /sessions/{sid}` 获取完整布局。
列表聚合使用轻量节点索引，正常写入立即更新，同步发布后重建；读取时检查文件签名，
只重读新增或发生变化的节点，避免陈旧计数。活动时间仍取所有节点创建、开始、完成
时间的最大值，空项目回落到项目创建时间。

HTTP 响应支持 gzip 协商（`Accept-Encoding: gzip`），仅压缩至少 1024 字节的响应；
WebSocket 不受影响。

`GET /sessions/{sid}/nodes` 返回精简节点：不含 `system_context_snapshot`、
`launch_instructions_snapshot`，`prompt` 最多保留 120 个 Unicode 字符，并通过
`prompt_truncated` 标明是否截断。`settings_snapshot`、`prompt_draft` 保持完整；
`prompt_argument_names` 保留完整提示词或草稿中的模板参数，避免截断影响画布芯片。
`node_started`、`node_updated`、`turn_done` 的 `node` 载荷（含历史事件回放）采用
同一投影；持久化节点、事件日志、单节点详情及变更接口响应仍保留完整内容。
前端仅在选中节点时调用 `GET /sessions/{sid}/nodes/{nid}`，按项目、节点与 `rev`
缓存详情（最多 32 条），隔离过期选择的响应，加载失败可重试；详情不写回画布列表。
可运行 `cd frontend && npm run test:nodes` 检查投影、2000 节点画布一致性和缓存；
设置 `BROWSER_BIN` 后运行 `npm run test:nodes:browser` 检查真实浏览器切换竞态。

`GET /sessions/{sid}/context-bundles` 按节点 ID 批量返回终态非 op 节点的上下文来源，
只保留 `sources` 和画布泳道颜色所需的 `active_planspace` 元数据，不传输正文。
可用重复的 `node_ids` 查询参数筛选增量节点；缺失或损坏的快照返回 `null`，
无快照引用及非终态节点不进入结果。前端首次读取合为一次请求、一次画布数据发布，
随后仅为新终态或快照引用变化的节点补取；大量增量时退回整批，避免过长 URL。
完整正文仅在节点详情或上下文卡片被选中时，通过原有单节点端点读取，不写回共享摘要。
`cd frontend && npm run test:context` 覆盖批量请求、竞态及 2000 节点画布一致性。
设置 `BROWSER_BIN` 后运行 `npm run test:context:browser` 验证实际 React 加载和切换行为。

画布启用 `onlyRenderVisibleElements`，只挂载与视口相交的节点和边；未选中／悬浮时
本就透明的来源、产物及提交关联边不进入 DOM，显示规则不变。React Flow 11 的边还依赖
DOM 测量出的锚点，因此 `ViewportHandleBounds` 会为尚未挂载的节点补入布局锚点，
保证首次打开时两端均在视口外的跨视口边仍可见；挂载后的真实测量优先，不被补全覆盖。
新增节点和未测量节点的尺寸更新同样补全。升级 React Flow 或修改节点 Handle 时，
须同步检查 `viewportHandles.ts` 并运行浏览器回归，不能只保留裁剪开关。
存在待响应交互时暂时关闭视口裁剪，避免卡片下方的提问／权限表单在平移时被卸载、
丢失未提交的回答、工具参数及备注；全部交互结束后恢复裁剪。

`cd frontend && npm run test:viewport` 使用可重复生成的 411 × 5 合成夹具，验证
2055 节点的完整／精简投影一致性、尺寸完整性和所有边的锚点。夹具不包含用户存储或提示词。
设置 `BROWSER_BIN` 后运行 `npm run test:viewport:browser`，在真实无头浏览器中验证
DOM 数量、跨视口边、框选、Shift 多选、右键菜单／平移、跨视口长按连线、定位／视口恢复、
泳道隐藏恢复、模板折叠、适配及缩放，以及提问／权限表单离屏后的草稿保留、提交和裁剪恢复；
报告滚动帧间隔，但只对挂载数量设回归阈值。
测试使用临时浏览器配置与调试管道，不启动服务、不占用端口，并只清理自己启动的进程。
隐藏泳道的来源摘要仍保留给资源库计数及恢复使用；未引入缩放级别简化卡片或服务端分页。

- Project/session REST APIs:
  `GET /sessions`, `POST /sessions`, `PATCH /sessions/{sid}`,
  `PATCH /sessions/{sid}/preferences`,
  `PATCH /sessions/{sid}/layout-hints`,
  `PATCH /sessions/{sid}/planspace-view`, and
  `DELETE /sessions/{sid}`.
- Global configuration REST APIs:
  `GET /global-state`, `PATCH /global-state/defaults`, and
  `/global-state/model-presets` (`POST`, `PUT`, `DELETE`).
- ContextSpace REST APIs:
  `GET /sessions/{sid}/contextspace`,
  `PATCH /sessions/{sid}/contextspace`,
  `GET /sessions/{sid}/context-bundles`,
  `POST /sessions/{sid}/context/init`,
  `POST /sessions/{sid}/context/refresh`,
  `POST /sessions/{sid}/context/cancel`,
  `GET /sessions/{sid}/files`,
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
- Principle REST APIs: `GET /principles`, `DELETE /principles/{slug}`.
- Agent Skill REST APIs: `GET /skills`, `POST /skills/import`, and
  `DELETE /skills/{slug}`. Import accepts a local path, zip, or Git URL; a
  multi-skill source with no explicit slug is imported as one skill package.
- Client -> server:
  `user_message {text, resume_from_node_id?, extra_principles?, extra_skills?,
  agent_op_kind?, model_preset_id?}` (the node it spawns carries no
  planspace lane; lane-scoped work is created through the REST
  endpoints above, which name their target lane explicitly),
  `interaction_response`, `interrupt`, and
  `replay_request {node_id, since_seq}`.
- Server -> client:
  `node_started` (carries `kind`, `category`, `subtype`, and agent
  `prompt`), `node_updated`,
  `text_delta`, `thinking`, `activity` (with optional `result` +
  `result_kind`), `interaction_request` (`permission`, `ask_user`,
  or `human_review_prose`), `usage`, `turn_done`,
  and `error`. Events carry monotonic `seq` values for reconnect
  replay; persisted envelopes carry an event schema version. 历史
  `checkpoint_review` 在存储迁移时转换，运行时只读取当前载体。

Current ask-user responses use
`response.answers.<question-id>.answers: string[]`; human reviews use
`response.prose`; permission responses use provider-neutral `allow`,
`message`, `updated_input`, `scope`, and `interrupt`. Provider adapters
translate that shape to vendor-specific decision vocabularies.

Exact shapes: [`backend/miniclaw2/events.py`](backend/miniclaw2/events.py)
and [`frontend/src/types.ts`](frontend/src/types.ts).

## Status

持久化格式迁移、三步支持窗口、数据域清单与恢复命令见
[`docs/schema-migrations.md`](docs/schema-migrations.md)。更新程序后的启动自动迁移；
过旧或过新的存储进入维护模式，不自动下载旧迁移器，也不显示误导性的空项目列表。

The code is the ledger of what has landed — this file does not enumerate
it, because such a list goes stale in a way code cannot.

For the design position, read [`PHILOSOPHY.md`](PHILOSOPHY.md). For the
gap between that position and the code — known divergences, latent
hazards that are invisible where they matter, and directions argued
through but not built — read [`FUTURES.md`](FUTURES.md).
