# MiniClaw2 Implementation Status

Companion to `PHILOSOPHY.md`. The philosophy doc describes the
destination; this doc is the single source of truth for what the code
actually does. Where the two disagree, the philosophy doc states the
position and this doc names the unresolved gap.

Trunk files are mentioned only as orientation; subordinate modules and
test files are left implicit. Items are bucketed by subsystem, then
split into **Landed** and **Pending**. Pending items are not
sequenced — dependencies are noted inline where they matter.


## 3a. Project-level Git control

Trunk: `backend/miniclaw2/git_state.py`, `frontend/src/canvas/layout.ts`
(commit layer), `frontend/src/panel/SidePanel.tsx` (`GitCommitPanel`).

The governing decision is **derive, don't mirror** (`PHILOSOPHY.md` §6.3):
commit hubs and commit edges are render-time joins keyed on `commit:<sha>`,
never stored records. Three authorities split the facts — node records own
the immutable `commit_before` / `commit_after` pair, the git repository owns
commit existence/metadata/order, and stored op nodes own only the action
record. Mirroring is unwinnable (agents commit mid-run, users commit in
terminals, rebases rewrite shas); derivation turns every drift scenario into
a rendering case instead of a corruption case. Vocabulary: an **epoch** is
the set of executed work nodes sharing one `commit_before` (user-facing
phrasing: "changes since the last commit"); the **ghost** is the dashed hub
for the not-yet-made commit, rendered iff the tree is dirty; a **stale hub**
is a referenced sha no longer in history; the **alias map** is project-level
`git_aliases.json` `{old_sha: new_sha}` captured only when the framework
itself rebases.

### Landed

- `git_status` derives repository, branch/detached, upstream ahead/behind,
  and dirty counts in one porcelain-v2 read, excluding `.miniclaw2/` so the
  pill agrees with what `commit_all` will stage. Untracked files are
  enumerated individually (`--untracked-files=all`); switching to `normal`
  would collapse untracked directories and undercount the pill.
- Node-less `git_status` telemetry: broadcast from `_on_runner_done` after
  terminal nodes and git ops, refetched on window focus to catch
  terminal-side changes. The event deliberately carries no `node_id` — the
  replay buffer holds any node-addressed event until that node's replay is
  ready, which never happens for a synthetic id.
- Header controls: status pill (`git —` / `git clean` / `git 3~` with
  `↑a ↓b`), Commit (selects the ghost; the side panel is the composer — no
  modal), Pull, and Push. All disable when read-only or not a repo.
- Commit and pull are durable op nodes; `parent_node_id=None` marks a manual
  commit and the message rides in `node.prompt` (falls back to
  `miniclaw:node:<id>`). Pull is `git pull --rebase` only; on conflict it
  aborts only rebases it started (a pre-existing user rebase fails the pull
  without touching the tree) and reports the conflicting-file list in
  `node.error`; resolution is deliberately manual. Failed git ops stay
  visible as selectable error tiles; successful ops stop rendering as tiles
  and fold into the commit hub they produced.
- Push is deliberately **not an op** (it mutates remote state, not the
  worktree, so it earns no timeline presence): a direct backend action whose
  errors surface in the header. It rejects queued or active pulls with 409.
- Quiescence is two-layered: the pull endpoint 409s while any node runs or
  queues (checked atomically with the spawn on the event loop), and the
  scheduler re-checks for an active pull op on every launch iteration, so
  nothing starts mid-rebase while never blocking the pull's own launch.
  Manual commit is deliberately unguarded — it snapshots whatever is
  mid-flight, matching auto-commit semantics.
- Native `code_review` nodes extend the scheduler's pull-style exclusivity:
  the pool drains before a queued review and no later node launches until it
  finishes. The runner snapshots the ghost as `reviewed-diff.patch`, detects
  terminal-side divergence, and publishes the normalized native report before
  the user commits the epoch.
- `commit_graph` derives oldest-first hubs from one formatted
  `rev-list --topo-order` pass: `{sha, live, message, ts,
  external_count_before, aliases}`. Referenced set = every node's
  `commit_before`/`commit_after` plus HEAD, resolved transitively through
  the alias map. Stale epochs interleave into trunk order by their member
  nodes' timestamps; `external_count_before` counts unreferenced commits
  between consecutive referenced ones, rendered as a `+N` badge on the
  trunk edge rather than as N hubs.
- Alias capture: the pull op records `git rev-list --reverse
  @{upstream}..HEAD` before and after the rebase; equal lengths give a
  positional old→new map (atomic store write). On length mismatch the map
  is not written and affected epochs render stale — the honest fallback.
  External rebases also render stale. Node records never rewrite.
- Canvas: trunk `root → C₀ → … → ghost` on the project baseline; hubs are
  64px circles (sha7, live solid / stale dashed-amber / HEAD ring / ghost
  dashed showing the dirty count), draggable via `layoutHints`
  key `commit:<sha>`. Epoch source/sink edges touch only the sources and
  sinks of each epoch's dep-sub-DAG (transitive reduction within the
  epoch); a sink targets the earliest rendered **live** commit at-or-after
  its `commit_after`, else the ghost when rendered, else no edge.
- Selection kind `{kind: "commit", sha | null}` routes to `GitCommitPanel`:
  HEAD/live/stale badges, message, timestamp, clickable epoch member list,
  associated op metadata (auto vs manual, parent agent). The ghost variant
  is the commit composer, prefilled from the current epoch's node summaries;
  it re-checks the dirty count and degrades to "working tree clean".
- Edge cases: empty repo renders `root → ghost` only; detached HEAD keeps
  the pill and ring with `branch=None`; no upstream omits ahead/behind and
  fails pull/push with git's own message; `.miniclaw2/`-only changes mean
  dirty count 0 and no ghost.

### Pending

- Push→pull serialization is one-directional: push rejects in-flight pulls,
  but a pull can still be spawned while a push subprocess is in flight
  (push is not a node, so quiescence does not see it).
- `rev-list` failure/timeout logs and renders every referenced commit stale;
  no degraded flag reaches the wire.
- Per-file dirty listing for the composer (counts only today).
- Branch switching / branch UI (the pill shows the branch read-only),
  merge/non-rebase pulls (the trunk stays linear), and concierge conflict
  resolution (auto-abort + manual resolution first) are deliberately out of
  scope.


## 1. Backend domain model

Trunk: `backend/miniclaw2/domain.py`.

### Landed

- `NodeKind ∈ {agent, op, verifier}`.
- `NodeState ∈ {virtual, queued, running, waiting, awaiting_human_input, done, error, cancelled}`.
- `Category ∈ {planning, regular, review}` and
  `ReviewSubtype ∈ {agentic_review, human_interact_review, programmatic_review,
  code_review}`.
- `Node` fields covering ontology in `PHILOSOPHY.md` §6.1: `parent_node_id`,
  `planspace_id`, `context_bundle_id`, `context_bundle_path`,
  `model_preset_id`, `provider_session_id`, `provider_turn_id`, `op_kind`,
  `agent_op_kind`,
  `commit_before`, `commit_after`, `prompt`, `category`, `subtype`,
  `brief`, `review_target`, `prompt_draft`, `scheduled_deps`,
  `pending_extra_principles`, `pending_extra_skills`,
  `resume_from_node_id`,
  `verify_script_ref`, `proposed_by`, `obsolete_reason`, `summary`,
  `error`, `usage`, `artifacts`, `system_context_snapshot`, `settings_snapshot`,
  `created_at`, `started_at`, `finished_at`.
- `Project` fields: `root_path`, `name`, `model_preset_id`,
  `project_context_binding_id`, `active_planspace_id`, `preferred_language`,
  `settings_override`, `temporary`, `template_id`,
  `machine_id`, `machine_label`,
  `layout_hints`, `layout_viewport`, `planspace_view`,
  `created_at`.
- `Project.provider` and `Node.provider` are computed from
  `model_preset_id` for wire/display use and are excluded from persisted JSON.
- `PlanspaceMode ∈ {auto, manual}`. `agent_op_kind` is an extensible
  string discriminator with a current whitelist of `principle_edit`; it is valid
  only on agent nodes. Pending principle and skill selections are virtual-agent
  intent and are promoted into their corresponding launch settings.
- `HumanGate` model is inline-only:
  `GateSubtype ∈ {permission, ask_user}`. `permission` is emitted by
  the Codex adapter only; the native-CLI Claude provider runs with
  `--dangerously-skip-permissions` and never opens a permission gate.
- Atomic JSON write (tmp + rename) via `store.py`; per-node JSONL
  event log; gates.jsonl audit trail.

### Pending

_None — the legacy output ontology has been removed._


## 2. Provider adapters

Trunk: `backend/miniclaw2/providers/` (`base.py`, `claude.py`, `codex.py`).

### Landed

- Provider and concrete-model selection is centralized in
  `backend/miniclaw2/model_catalog.py`. `GET /model-presets` exposes the
  catalog; active presets can be selected for new/edited work, while
  compatibility presets remain resolvable for persisted nodes and reruns.
  Provider, model, service tier, and reasoning effort are derived from the
  preset rather than accepted as independent persisted selectors. Codex
  model-provider selection (including its base URL) remains owned by the
  Codex CLI configuration.
- Claude adapter drives the native `claude` binary directly through a
  PTY (`ptyprocess`). Prompts are typed into the TUI; events are drained
  from Claude Code's on-disk JSONL transcript under
  `~/.claude/projects/<hash>/<sid>.jsonl`. Spawn args pin the session id
  (or `--resume`), disable plan mode via
  `--disallowed-tools EnterPlanMode,ExitPlanMode`, and bypass per-tool
  permission prompts via `--dangerously-skip-permissions`. `CONTEXT.md`
  is appended via `--append-system-prompt`. Assistant text, thinking
  blocks and tool_use/tool_result pairs map onto the same
  `AgentProviderEvent` shapes previously emitted by the SDK. Interrupt
  sends Ctrl-C over the PTY. `AskUserQuestion` is
  intercepted by a `PreToolUse` hook (`claude_hook_bridge`) that POSTs
  to FastAPI, which routes the payload through
  `GateSubtype.ASK_USER` and writes the user's answer back into the
  tool call. See `providers/claude_native/` for the spawn / input /
  transcript / hook plumbing.
- Codex adapter over `codex app-server` JSON-RPC. Per-session thread.
  `CONTEXT.md` prepended to `turn/start` input on fresh threads
  (resumed threads keep the context they were started with). Maps
  Codex `requestUserInput`, command-execution, file-change, and
  permission approvals onto the same wire envelopes. Already streams
  per-delta.
- `NodeRunner` owns the state machine and persistence; providers do
  IO only. Cleanup resolves any open `HumanGate` as denied when the
  node ends.
- Provider stream termination is contractual: before `run()` exhausts,
  a provider must yield `done` (optionally
  `final_state ∈ {done, cancelled}`) or `error`. `NodeRunner` and the
  out-of-band context tasks treat bare generator exhaustion as a
  provider error. The contract text lives on
  `providers/base.AgentProviderEvent`.
- Claude turn termination is explicit: the Claude Code `Stop` hook is the
  authoritative interactive turn-completion signal. Transcript metadata such
  as context-compaction summaries is never treated as a turn boundary. Usage
  is accumulated from unique assistant message ids and emitted once as final
  usage before termination. PTY child death and a configurable 30-minute
  no-progress stall (when no tool is pending) surface as provider errors, or
  as `cancelled` after an interrupt. Set
  `MINICLAW_CLAUDE_STREAM_STALL_SECONDS` to override the stall deadline.
- The ask-gate timeout chain is strictly ordered so each layer gives
  up before the layer beneath it kills the transport: runner-side gate
  supervision 570s (`GateRequest.timeout_seconds`; expiry emits an
  honest error, interrupts the session, and raises `GateTimeoutError`)
  < `/hook/ask` dispatcher wait 590s < hook bridge HTTP timeout 600s
  < installed hook entry timeout 700s. `test_hook_routes.py` asserts
  the ordering. Gates on deadline-free transports (Codex permission
  gates, human review prose) remain unbounded.
- Installed Claude hook entries carry explicit timeouts
  (AskUserQuestion 700s, SessionStart 15s, Stop 15s). The hook callback port is
  set from `MINICLAW2_HOOK_PORT` / `MINICLAW2_PORT` at app startup and
  otherwise captured from the actual HTTP/WS request scope.
- Session-transcript retargeting seeds its JSONL offset from the
  confirmed user marker or matched rotation record and falls back to
  EOF — never offset 0 — so rotated-session history is not replayed as
  current-turn events. Submit-confirmation fingerprinting uses the
  node prompt (not the composed launch header) and scans only the
  expected project hash.

### Pending

- Per-token streaming on Claude. The JSONL transcript is written
  block-at-a-time, not token-at-a-time; revisit if Claude Code gains a
  partial-block stream.
- Cost estimate in the Usage strip (token counts are emitted; dollar
  conversion is not).


## 3. Node kinds

Trunk: `backend/miniclaw2/runner.py`, `backend/miniclaw2/registry.py`.

### Agent — landed

- Fresh provider session/thread on ordinary launch.
- Resume via `parent_node_id` when launched with `resume_from_node_id`;
  inherits the canonical `provider_session_id`. Resumed agent
  surfaces show `↻` continuation context.
- Inline gates (permission for Codex, ask-user for both providers)
  normalize to `waiting` substate; resolution returns the node to
  `running`.
- Launch instructions are composed from (in order): the optional principle-author
  block, the category-aware block, native-skill suggest lines, then the
  planning / regular / review dependency and context blocks. Templates live
  under `backend/miniclaw2/prompts/`; covered by `test_launch_prompt.py` and
  `test_principle_edit_prompt.py`.
- Agent nodes carry their own `model_preset_id`. Project creation, direction
  creation, ordinary virtual creation, and virtual editing select active
  presets. Agent-authored virtual previews cannot select a model: newly
  proposed virtuals inherit the proposing planning/review node's preset, while
  rewrites retain the existing virtual's preset. Continuation virtuals keep
  the resume source's preset/session, and reruns may preserve a compatibility
  preset from historical data.
- `Project.concurrency` is persisted as a positive integer with default `1`.
  `ProjectRuntime` owns runner/task maps keyed by node id; `queued` nodes wait
  without starting providers or consuming slots, and a stable
  `(created_at, id)` scheduler fills capacity after create/promote, limit
  increases, and each runner completion. Lowering the limit leaves active
  runners untouched. Auto-commit ops consume slots and are prioritized after
  their parent agent before ordinary queued work advances.
- Concurrent nodes share the project source worktree by product design. They
  may observe partial peer edits or conflict on source/Git operations. Their
  graph projections are isolated under
  `.miniclaw2/graph/runs/<node-id>/lanes/<lane>/`; only reap validation and
  durable virtual-DAG persistence are serialized per project.
- Launch refuses visibly on stale persisted settings: a present but
  unresolvable `active_planspace_id` errors the node before the
  provider starts (`StaleLaunchSettingsError` → error state + stub
  preview naming the stale id) instead of silently launching without
  the preview-contract lane. An invalid persisted `preferred_language`
  is ignored on read-back (logged); wire input stays strictly
  validated.
- Registry startup repairs persisted `running` / `waiting` /
  `awaiting_human_input` nodes left behind by a dead process to `cancelled`
  with a stub preview. Durable `queued` work survives restart and is scheduled
  from the application lifespan. Error/cancelled agent nodes can be rerun from the UI;
  rerun creates a fresh editable virtual with the original prompt, model,
  lane, dependencies, review fields, and continuation linkage.

### Principle-edit agents - landed

- `agent_op_kind="principle_edit"` marks an ordinary agent node that receives
  `prompts/principle_init.md`. The prompt resolves the ContextSpace principle
  directory and permits only `manifest.yaml` plus `CONTEXT.md`.
- Project → `+ New principle` creates a regular virtual principle-edit node in the
  active direction. Promotion uses the normal runner, preview contract,
  provider events, cancellation, and terminal states; the principle shelf is
  refreshed after the node finishes.

### Review agents — landed

- Reviews are `agent` nodes with `category=review`.
- `agentic_review` launches like any other agent with the review brief
  in the category-aware launch block.
- `human_interact_review` first transitions to
  `awaiting_human_input`, emits
  `interaction_request {interaction_type: "human_review_prose"}`, writes
  the user's prose to durable and materialized `human-review.md`, then
  launches the reviewer agent.
- The reviewer's verdict is represented by its preview and any virtual
  graph mutations reaped from `.miniclaw2/graph/`.
- `code_review` invokes the preset provider's native reviewer against
  uncommitted Git state (`review/start` for Codex — requires codex-cli
  ≥ 0.144.1, whose findings shape the mapping is pinned against; re-confirm
  on upgrade — and bare `/code-review` for Claude Code). It skips lane
  materialization and reap: the framework writes `reviewed-diff.patch`,
  publishes `code-review-report.md` plus Codex's structured findings when
  available, and synthesizes the executed preview. Report-only by design:
  the node never proposes virtual mutations; downstream planning/review
  nodes (or the user) read the report artifact. The ghost composer exposes
  this path through `POST /git/review` and its Review button; creation is
  rejected for non-repo projects, and a clean tree short-circuits to done
  ("working tree clean — nothing to review") without a provider call —
  which makes manual-commit projects the feature's natural home, since
  auto-commit keeps the tree clean between nodes. A changed worktree after
  launch leaves the node done but prefixes the report and preview with a
  stale-snapshot warning. Claude Code accepts optional focus text as the
  slash-command argument, but `/code-review`'s "current diff" scope may
  include committed-but-unpushed work — the argument-string narrowing is
  best-effort guidance, and `reviewed-diff.patch` is the ground truth for
  what should have been reviewed. Codex's `uncommittedChanges` review
  target has no focus channel, so MiniClaw2 labels that limitation in the
  editor and records it in the report and executed preview when focus text
  was supplied.
- Code-review invariants from the post-landing review pass:
  `git_review_snapshot` never degrades silently — a failed or timed-out diff
  raises (→ node error) instead of hashing a partial patch, an unborn HEAD
  diffs against Git's empty tree, and non-UTF-8 bytes are decoded with
  replacement so one bad file cannot zero the audit trail. A published report
  is never clawed back: if the turn ends error/cancelled after publishing,
  the preview records the failure alongside the kept artifacts.
  `reviewed-diff.patch` is written before the RUNNING transition (clean trees
  get a one-line placeholder). `POST /git/review` is idempotent while a
  review is queued/running and the UI derives the button state from nodes,
  not the POST lifetime. code_review virtuals are exempt from the non-empty
  prompt rule everywhere (`_virtual_requires_prompt`: auto-promotion, promote,
  rerun, `update_virtual`, editor Save), and switching a virtual away from
  `code_review` auto-discards the server-populated `review_target` default.
  Provider failures classify precisely: Claude's unknown-command probe only
  matches short anchored replies (a report quoting the phrase publishes), and
  Codex JSON-RPC errors keep their code — only `-32601` maps to the
  upgrade-codex-cli message, everything else surfaces verbatim.

### Review agents — pending

- `code_review` v2 targets: `base_branch` / `commit` / `custom` (the
  `review_target` shape anticipates them; Codex maps directly, Claude Code
  needs per-target prompt design). Also the answer for reviewing committed
  ranges in auto-commit projects, where uncommitted-only reviews
  short-circuit clean.
- Detached Codex review delivery and `reviewThreadId` persistence; v1 runs
  inline on the node's own thread, keeping `provider_session_id` semantics
  untouched.
- Auto-converting findings into virtual fix nodes — revisit once real
  reports show whether Codex's structured findings are reliable enough to
  script against.
- Review-blocking commit enforcement — the review→commit gate stays a
  workflow pattern (scheduled deps + the composer's Review button), not a
  hard constraint on commit ops. `/code-review`'s `--fix` / `--comment`
  modes stay outside the report-only contract.

### Verifier — landed

- Verifiers are `kind=verifier`, `category=review`,
  `subtype=programmatic_review` nodes.
- A verifier runs `bash <verify_script_ref>` in the project root with
  `CI=1`, `MINICLAW_HOME`, and `MINICLAW_PROJECT_ID`; it does not start
  a provider session.
- Exit 0 writes an executed preview with `summary="verify passed"` and
  transitions to `done`. Non-zero exit or timeout writes stderr/stdout
  tail into the preview, stores `node.error`, and transitions to
  `error`. Cancellation transitions to `cancelled`.
- Verifier virtuals are template-only. Agent-authored verifier virtuals
  are rejected by reap because they cannot safely supply
  `verify_script_ref`.

### Op — landed

- `commit` and `pull` ops (§3a). The commit op is auto-appended after any
  `agent` node that reaches `done` when
  `project.settings_override.auto_commit` is truthy, and spawned manually
  (`parent_node_id=None`, message in `node.prompt`) from the commit
  composer. Auto-commits use `miniclaw:node:<id>`, staging everything except
  framework-generated `.miniclaw2/` paths (pathspec-excluded and
  defensively unstaged; no-op detection checks the staged diff).
  `.miniclaw2/` is also appended to `.git/info/exclude` at project
  registration and temporary-workspace creation, so auto-commits never
  sweep framework state into the user's history or per-node diffs.
- On success the preceding node's `commit_after` is rewritten to the
  new commit hash; a `node_updated` event broadcasts the change so
  the frontend can refresh diffs.
- `NodeStarted.kind` distinguishes op-node events so the frontend does
  not jump the selection to the op tile.

### Op — pending

- `fork-project` op (`git worktree add` → new `Project` row pointing
  at the new path, context bundle copied from source project).
- Other ops listed in older sketches (`checkout`, `reset`,
  `import-context`) — no concrete need yet.


## 4. ContextSpace

Trunk: `backend/miniclaw2/contextspace.py`.

### Landed

- ContextSpace root resolution: `$MINICLAW_CONTEXT_HOME` or
  `$MINICLAW_HOME/contextspace`. No silent bootstrap on first run.
- On-disk layout matching `PHILOSOPHY.md` §7:
  ```
  contextspace/
    contextspace.yaml
    bindings/projects/<binding-id>.yaml
    plugs/global/{manifest.yaml, CONTEXT.md}
    plugs/principles/<id>/{manifest.yaml, CONTEXT.md}
    skills/<id>/{SKILL.md, scripts/, references/, ...}
    plugs/planspaces/<id>/{manifest.yaml, events.jsonl}
    snapshots/<bundle-id>.json
  ```
- Plug loaders for project-root `CONTEXT.md`, global `CONTEXT.md`, and
  principle `CONTEXT.md`. Planspace plugs are manifest-only; lane state is
  read through the node-private
  `.miniclaw2/graph/runs/<node-id>/lanes/<lane>/` projection.
- Principles can also be opted into per node without changing the project binding.
  Virtuals hold canonical `pending_extra_principles`; promotion snapshots them as
  `settings_snapshot.extra_principles`; bundle composition deduplicates them
  against binding principles, records node-opt-in provenance, and records missing
  plugs explicitly instead of collapsing their context tiles.
- `ProjectBinding`, `PlugRef`, `ComposedContextBundle` carry binding
  resolution; `Project.project_context_binding_id` references the
  current binding; `Project.active_planspace_id` owns the current
  planspace selection. Binding manifests describe available plugs only.
- Provider-neutral `<project_root>/CONTEXT.md` loading at every node
  launch. Claude receives it via `--append-system-prompt`; Codex
  prepends it to fresh-thread `turn/start` input. Resolved text is
  snapshotted on the node as `system_context_snapshot`.
- Context bundle snapshot persisted to
  `snapshots/<bundle-id>.json` with source paths, sha256 hashes,
  plug ids, char counts, and injection modes (`system` / `turn`).
- ContextSpace bootstrap split into idempotent helpers:
  `ensure_contextspace_root`, `ensure_project_binding`, and
  `add_planspace_to_binding`.
- `POST /sessions/{sid}/planspaces` creates a new bound direction,
  activates it, and launches the concierge bootstrap agent node from a
  preset markdown prompt.
- `POST /sessions/{sid}/planspaces/blank` creates a new bound
  direction, activates it, and seeds it with one empty regular virtual
  without launching an agent.
- Per-planspace mode lives in the planspace manifest and is read /
  written through `read_planspace_mode` / `set_planspace_mode`.
- `GET /sessions/{sid}/contextspace` returns binding and plug summaries
  including planspace mode, active lane, and hidden-lane state.
- Whitelisted file reads via `GET /sessions/{sid}/files?role=context`
  expose project-root `CONTEXT.md` for the UI.
- Per-project lane visibility is persisted in
  `Project.planspace_view` and updated via
  `PATCH /sessions/{sid}/planspace-view`.
- Out-of-band `CONTEXT.md` init / refresh tasks live in
  `backend/miniclaw2/context_refresh.py`. They run an agent against the
  project's provider using framework-held preset prompts
  (`prompts/context_init.md`, `prompts/context_refresh.md`) and a tight
  tool allowlist (`Read`, `Glob`, `Grep`, `Write`). The agent writes
  `CONTEXT.md` itself via `Write`; the framework only books
  `.miniclaw2/context.meta.json` on success. In-flight state surfaces on
  `GET /sessions/{sid}/contextspace` and can be cancelled via
  `POST /sessions/{sid}/context/cancel`. The task deliberately does not
  create nodes or append node event streams.
- Provider adapters support a `minimal_mode` flag on
  `AgentProviderContext` that skips `CONTEXT.md` self-injection, passes
  the whitelist to `claude --allowed-tools` on the native Claude
  provider, and forces `approvalPolicy: "never"` on Codex. Used by the
  out-of-band `context_refresh` agent; unused by `NodeRunner`.
- `GET /principles` enumerates principle plugs and `DELETE /principles/{slug}`
  removes one with strict slug/path validation. The frontend exposes them as
  behavior-guidance tiles with virtual-node selection and drag attach.
- Native Agent Skills are stored verbatim under `contextspace/skills`.
  `GET /skills`, `POST /skills/import`, and `DELETE /skills/{slug}` provide
  inspection, local/zip/git import, overwrite, and removal. Imports reject zip
  traversal and symlinks; provenance is stored separately in
  `contextspace/skill-imports.json`.
- The runner records per-skill source path, directory hash, mechanism,
  missing/failed state, and used state in `settings_snapshot.skill_audit`.
  Claude receives a node-private `--plugin-dir`; each node-private Codex
  app-server receives the selected directories through
  `skills/extraRoots/set` before thread start/resume. Suggest mode adds one
  provider-neutral launch line. Unsupported/failed Codex protocol calls launch
  without skills and mark those audit entries failed.
- The canvas renders native skill tiles separately from principles. Dashed
  edges mean available; a confident Claude Skill invocation or Codex read of
  the materialized `SKILL.md` upgrades the persisted edge to solid used.
- ContextSpace changes are versioned by the store repo's metadata-sync
  commits (§10). The nested `git: {expected: true}` expectation in
  `contextspace.yaml` is dropped by the v4 migration, and sync requires
  the ContextSpace root inside `$MINICLAW_HOME` — a divergent
  `MINICLAW_CONTEXT_HOME` refuses sync setup and sync.

### Pending

- Vendor-specific on-disk context is now applied by the native `claude`
  binary itself (`CLAUDE.md` walk, `.claude/settings.json`,
  `.claude/agents`, `.mcp.json`); MiniClaw2 no longer needs to
  re-marshal any of it. What is still missing is a UI surface for
  editing those files.
- Global-plug/binding authoring UI and a direct manifest/markdown editor for
  injection mode, `max_chars`, and existing principle contents. Principle
  creation is currently agent-assisted rather than a structured form.
- Cross-provider reviewer nodes (the ontology supports review agents;
  no UI to configure provider override yet).
- Fork merge semantics.
- Codex per-tool allowlist for out-of-band agent mode. Claude enforces
  the whitelist directly via `--allowed-tools` at spawn; Codex has no
  per-tool spawn flag, so the current `context_refresh` agent relies on
  `approvalPolicy: "never"` plus the preset prompt's self-constraint to
  scope tool use.


## 5. Frontend canvas

Trunk: `frontend/src/canvas/Canvas.tsx`, `frontend/src/canvas/layout.ts`,
`frontend/src/canvas/nodes/`, `frontend/src/canvas/edges/`,
`frontend/src/panel/`.

### Landed

- React Flow canvas; one canvas per project; pan/zoom per-project.
- Node kinds rendered: `AgentNode`, `OpNode`, `ContextNode`,
  `ArtifactNode`, `ErrorTerminalNode`, `PlanspaceLaneNode`,
  `ProjectRootNode`. Passive `GateNode` and the old phantom composer
  node have been removed.
- Polymorphic side panel: `AgentPanel`, `ContextNodePanel`,
  `ArtifactPanel`, `OpPanel`, `GitCommitPanel`, lane panel, `ProjectPanel`
  switched by selection (`SidePanel.tsx`).
- The new-direction composer offers two bootstraps: `Draft with
  concierge` launches the planning bootstrap agent, while `Start blank`
  creates a bound planspace with one empty virtual node and focuses its
  draft field. Both paths expose planspace mode and an active model preset.
- Virtual agent tiles render as dashed plan tiles with category badges,
  draft prompt text, ready/obsolete/dependency footer state, and a
  hover right-edge action stack for promote, continuation virtual,
  dependency virtual, and remove.
- Virtual agent nodes can be edited from the side panel
  (`prompt_draft`, category/subtype/brief, motivation, dependencies,
  model preset, attached principles/native skills, and obsoletion); continuation virtuals lock
  their inherited model preset. Verifier virtuals render as read-only
  programmatic-review steps.
- Agent tiles show category badges for planning / regular / review /
  human-interact review nodes; verifier tiles use the review tone and
  a programmatic label.
- Edges: dependency arrows, derived commit edges (trunk plus epoch
  source/sink, with `+N` external-commit badges), resume (`↻` mid-glyph),
  loads (dashed, auto-hidden unless endpoint hovered/selected), and produces
  (agent to published artifact).
- Published artifact tiles fan beneath their producing agent in the
  `ContextNode` visual language, capped at 4 (more collapses to 3 plus
  a `+k more` overflow tile that opens the agent panel). Dropped
  entries get no tiles; they surface only in `AgentPanel`, greyed with
  their drop reason.
- Op tiles render for queued/running/error ops; done ops fold into the
  commit hub they produced. The op-chevron/timeline edge encodings for ops
  are retired — the commit trunk is the sole encoding of FS state
  (`TimelineEdge` survives for error terminals).
- Error terminal nodes downstream of failed runs carry the `error`
  text in red.
- Planspace lanes render with persisted manifest color overrides
  (falls back to creation-order palette).
- Planspace lane visibility is per-project and persisted; hidden lanes,
  and their nodes are filtered from the canvas, while Project →
  Directions keeps a recovery row.
- The active direction is highlighted at lane level. The lane-header `+`
  creates an unparented empty virtual directly in that lane and opens it
  in the side panel.
- Project `CONTEXT.md` sits in its own neutral top stripe above the
  planspace-colored "loaded context" lane.
- `AgentPanel` shows virtual node draft/provenance/dependencies/brief
  for virtuals; executed nodes show agent input, persisted
  `preview.json`, published/dropped artifacts, activity, thinking, and
  inspect drawer. Markdown/JSON artifacts render inline; HTML opens in
  a sandboxed window.
- Agent tiles and the inspector show model-preset identity. Error/cancelled
  agents expose rerun, which creates and focuses a new virtual instead of
  mutating execution history.
- Newly-created virtuals from blank direction, lane `+`, continuation,
  or dependency actions auto-focus the side-panel draft textarea.
- Mid-lane virtual branching is visible: continuation/dependency
  virtual siblings anchor below their source tile and lanes grow to fit
  the stack while preserving manual layout hints.
- Human-interact review prose form renders inside `AgentPanel` while
  the review node is in `awaiting_human_input`.
- Clicking a lane header opens a lane panel with manual/auto mode
  controls; Project → Directions also shows mode and hide/show.
- `PlanspaceFilePanel.tsx` now handles project-root `CONTEXT.md` only.
- `ProjectPanel.tsx` has Project actions (`+ New direction`, `+ New skill`,
  initialize/refresh project notes) and a Directions section with active
  badges, mode labels, and hide/show controls.
- A user-wide skill shelf is merged into the context-node lane even before a
  live node loads a skill. Skills can be inspected/deleted, attached in the
  virtual editor, or dragged directly onto a virtual tile.
- Persisted node `layout_hints` and the user-owned pan/zoom
  `layout_viewport` round-trip through `project.json` via
  `PATCH /sessions/{sid}/layout-hints`; programmatic fit-to-view does not
  overwrite the saved viewport.
- Tool I/O rendering: `Activity.result` (≤4 KB) + `result_kind ∈
  {stdout, diff, text, json}`. Collapsible `<details>` with diff
  coloring; failed-default-open.
- Markdown rendering for assistant text (`react-markdown` + GFM +
  `highlight.js` with github-dark).
- Reconnect replay: `node_started` server event + `replay_request
  {node_id, since_seq}` client envelope; replay consumes
  `events.jsonl` then attaches to live tail. WS 4xxx close codes
  suppress the auto-reconnect loop.
- Inline pending requests (permission / ask-user)
  render at the top of `AgentPanel`. An amber canvas banner surfaces
  "Node X is awaiting your response" when the user is inspecting a
  different node.
- Inline pending requests (permission / ask-user)
  also expand directly under the waiting agent tile on the canvas,
  using the same response mapping as `AgentPanel`.
- Projects landing page (`ProjectsLanding`) with rename/delete; Tests
  modal for bundled templates. New-project creation supports a named or
  temporary workspace, cwd creation confirmation, preferred language, and an
  active model preset.
- Non-native (or newer-schema) projects render read-only: a badge
  (`read-only · native to <label> · as of <last sync>`) on the canvas
  header and project panel, promote/interrupt/rerun/edit controls
  disabled, pending gates and context menus suppressed, and pan/zoom/
  drag kept session-only — layout is never persisted from a read-only
  viewer (the backend also rejects it).
- Global Settings modal carries the metadata-sync section: remote URL
  setup gated on a privacy acknowledgment (the remote holds full agent
  transcripts, prompts, tool output, and code; use a private remote), a
  `Sync now` / `Set up sync` button, the binary up-to-date/changed
  status, machine label, and last-sync time.

### Pending

- User-editable verifier virtual controls. Agent virtual editing is in,
  but verifier virtuals remain read-only because script selection is a
  template-owned concern.
- Schema-aware review forms (PRD §8.7). Cancelled — user judgment is
  free-form by design; left here as a documented non-goal.


## 6. Preview / virtual-node output model

Executed and virtual nodes both carry a `preview.json` (strict-whitelist
schema). The materialized active-lane subtree is the agent's read/write
surface; the durable node store is the source of truth.

### Landed

- Every executed agent is expected to write its own
  `.miniclaw2/graph/runs/<nid>/lanes/<lane>/nodes/<nid>/preview.json`; the runner reaps it
  into `projects/<pid>/nodes/<nid>/preview.json`. Verifier and op previews are
  framework-written because those node kinds do not run an agent provider.
- Planning and review agents may create or mutate virtual previews in
  the materialized graph; regular agents are rejected if they write
  virtuals.
- UI-created virtuals can be added directly to a planspace as
  unparented drafts, continuation drafts, or scheduled-dependency
  drafts.
- Unrun virtuals can be marked obsolete or hard-deleted. Hard delete
  refuses live dependency blockers, removes the node from durable lane
  state, and broadcasts `node_removed`.
- Virtual preview writes are validated, canonicalized from slugs to
  node ids, cycle-checked, persisted atomically, and surfaced on the
  canvas after reap.
- Missing/malformed own preview or invalid graph writes drive up to
  three inline repair turns in the same provider session; only after
  the retry bound is exhausted does the runner end the node as `error`
  with a framework-written "preview contract abandoned" stub preview.
- Cancelled/error runs skip virtual reap and get framework stub
  previews.
- Executed previews may declare bare artifact filenames. Terminal reap
  validates `.md`/`.json`/`.html` files under
  `.miniclaw2/outputs/<nid>/`, applies per-file/per-node/count caps
  (constants in `artifacts.py`), replaces the durable node artifact
  directory, and stamps published or dropped manifest entries onto
  `Node`. Artifact failures do not enter the preview-repair loop;
  error/cancel stub previews clear any previously published artifacts.
- The category launch templates carry the publishing contract: write
  under the node's own outputs directory (the `<<outputs_path>>`
  substitution resolves the absolute path) and declare the filename in
  the preview's `artifacts` field. Undeclared files are never rendered
  or synced but keep flowing to downstream agents through the untouched
  lane projection (`materialize.py`); publication is a parallel,
  narrower path to the human. Verifier and op previews are
  framework-written and never publish.
- Published artifacts are served from the store copy at
  `GET /sessions/{sid}/nodes/{nid}/artifacts/{name}` (`?raw=1` for
  bytes). `name` must exactly match a `published` manifest entry —
  never a free path, so no enumeration or traversal surface. JSON mode
  truncates inline `text` at 512 KiB with a `truncated` flag; raw
  `.html` is served with
  `Content-Security-Policy: sandbox allow-scripts; connect-src 'none'`
  plus nosniff, forcing agent-authored HTML into an opaque origin with
  no access to the MiniClaw2 API. The endpoint has no native-machine
  gate: read-only machines get full artifact content from the synced
  store copy.
- Anti-self-poisoning prompt is appended last in launch instruction
  composition and applies to preview content as guidance.

### Deferred

- Live mid-session graph-write canvas updates; v1 remains reap-only.
- Live artifact updates during a run — artifacts appear only at
  terminal transition; a rescan on the `waiting` transition is the
  cheap extension if long-running nodes make this painful.
- Non-text artifact types (`.png`, `.svg`, `.csv`, `.pdf`) — the
  pipeline generalizes, but each type needs a rendering and sync-size
  decision.
- Multi-file HTML (companion assets); the single self-contained file
  rule keeps serving, validation, and the sandbox story trivial.
- Inline sandboxed-iframe HTML preview in the side panel (pure
  addition over the current new-window path).
- Artifact history across reruns (reap replaces the store copy; prior
  versions survive only in `$MINICLAW_HOME` git history) and zip/export
  bundling.


## 7. CLI-parity gaps

Native Claude Code is now driven directly, so most CLI-parity items are
answered by "whatever the CLI already does." The remaining gaps are the
UI affordances and features MiniClaw2 does not expose on top of it.

### Landed

- Native `claude` binary driven directly via PTY + JSONL transcript.
  Anything the CLI ships (skills, plugins, slash commands, new tools,
  `CLAUDE.md` walk, `.claude/settings.json`, `.claude/agents`,
  `.mcp.json`) is applied by Claude itself when spawned in the project
  cwd.
- Provider-neutral `<project_root>/CONTEXT.md` appended via
  `--append-system-prompt`.
- Interrupt wired (Stop button → Ctrl-C to PTY → `cancelled`).
- `thinking` blocks surfaced as a `thinking` event.
- Tool I/O result rendering for both providers.
- Markdown rendering for assistant text.
- Reconnect replay over the JSONL log.
- `AskUserQuestion` intercepted via a `PreToolUse` hook and routed
  through MiniClaw2's `ASK_USER` gate.
- Codex `requestUserInput`, command/file/permission approvals mapped
  onto the same gate envelope; session-scoped allow via
  `acceptForSession`.
- The Codex adapter accepts both current
  `item/commandExecution/requestApproval` / `item/fileChange/requestApproval`
  requests and the older `execCommandApproval` / `applyPatchApproval` methods.
  The older methods return their historical decision strings
  (`approved`, `approved_for_session`, `denied`, `abort`). They remain until
  the minimum supported Codex app-server version is defined.

### Pending

- `@file` / `!cmd` / image-paste input affordances.
- Top-level `InteractionRequest.suggestions` has no separate renderer. The
  question renderer already supports option descriptions, multi-select, and
  provider question shapes that explicitly request a free-text `Other` (or
  secret) input.
- Post-creation project settings UI for changing cwd, the default model
  preset, and provider tool allowlists. Cwd/model selection already exists at
  project creation, and model selection exists on new directions/virtuals.
- Queue user messages while a turn is in-flight (currently rejects).
- Slash commands (`/clear`, `/compact`, `/model`, `/cwd`,
  `/permissions`) as frontend interceptors.
- Cost estimate (per-model rates × token counts).


## 8. Templates

Two flavours share the YAML format but diverge on entry point and apply-
time behaviour:

- **Bundled** templates ship with the backend, are testing-only, reach
  the user through the Tests modal, and create a fresh temporary project
  each run.
- **User** templates are authored via the UI from a canvas selection,
  live under `$MINICLAW_CONTEXT_HOME/templates/<slug>/`, are surfaced by
  a left-side library dock, and stamp their subgraph into the *current*
  project's active planspace when dropped onto the canvas.

Templates apply verbatim — no parameter substitution, no concierge
prefix. A template author who wants adaptation bakes a `planning`
virtual into their subgraph.

### Landed — bundled

- `backend/miniclaw2/templates/` provides `loader.py`, `launcher.py`,
  and `bundled/` template definitions with `template.yaml`,
  `lane.yaml`, `prompts/`, `scripts/`, and optional `seed/`.
- Current template metadata declares only `allowed_model_preset_ids`;
  runtime loaders reject legacy `providers` and singular
  `model_preset_id` fields. Historical template shapes are no longer
  supported.
- REST exposes `GET /templates`, `GET /templates/{name}`, and
  `POST /templates/{name}/run`. The old `/scenarios` and
  `/sessions/{sid}/verify` endpoints have been removed.
- `Project.template_id` records provenance for projects launched from a
  template. The old `scenario_name`, `scenario_step_history`, and
  `scenario_step_id` fields are gone.
- Template launch creates a temporary git workspace, applies
  provider/permission/auto-commit settings, creates a planspace, writes
  every lane entry as a virtual node, persists each virtual preview, and
  performs one auto-promotion pass.
- `lane.yaml` supports `agent` and `verifier` steps,
  `scheduled_deps`, review briefs, and `resume_from` for agent
  continuations. `promote_virtual` copies provider session/thread ids
  from the done resume parent.
- The Tests modal now lists bundled templates and opens the resulting
  project. There is no template-specific verify card or scenario-future
  node renderer; templates use the normal canvas, side panel, virtual
  editing, human-review form, and interrupt controls.
- Bundled templates: `hello-text`, `bash-uname`, `write-readme`,
  `interrupt-midstream`, `context-md-respected`,
  `resume-fix-after-reject`, and `gui-calculator`. `permission-approve`
  and `plan-mode-approval` were dropped when the native-CLI Claude
  provider disabled per-tool gating and plan mode. `reconnect-replay`
  was intentionally dropped because it required a test-only UI hook.

### Landed — user-authored

- On-disk layout: `contextspace/templates/<slug>/{template.yaml,
  lane.yaml, prompts/<slug>.md}`. No `scripts/`, no `seed/` — user
  templates are agent-only. Loader reuses `_load_from_root` so bundled
  and user templates share the parsing pipeline.
- `templates/serializer.py`: `serialize_selection` captures a set of
  node ids from a project. Executed terminal nodes collapse to virtual
  form (session/commit/transcript metadata dropped, `prompt` folded into
  the emitted `prompt_file`); `op` nodes are silently filtered.
- Save validation: rejects verifiers in the selection, empty selections,
  transient states (queued / running / waiting / awaiting_human_input),
  `resume_from` links leaving the selection, disconnected selections
  (must be one component under `scheduled_deps ∪ resume_from_node_id`),
  and slug collisions. External `scheduled_deps` are dropped so the
  template becomes topologically self-contained.
- `virtual_graph.is_connected` performs the undirected BFS used by the
  connectedness check.
- `launcher.apply_user_template` stamps a user template into the
  project's active planspace via a shared `_stamp_lane` helper.
  Cross-lane origins collapse into the active lane on apply. When an
  anchor tile is provided, root virtuals (those with no in-template
  deps) get an implicit `scheduled_deps=[anchor]`.
- REST endpoints:
  `GET /user-templates`, `GET /user-templates/{slug}`,
  `DELETE /user-templates/{slug}`,
  `POST /sessions/{sid}/user-templates` (save selection),
  `POST /sessions/{sid}/user-templates/{slug}/apply`.
- Frontend: React Flow shift-click / marquee multi-select
  (`multiSelectionKeyCode="Shift"`) with `selected` state carried
  through graph re-syncs so multi-selection survives `node_updated`
  websocket bumps. Right-click on an agent tile opens a `ContextMenu`
  with "Save as template…"; the resulting `SaveAsTemplateModal` takes a
  name + one-line brief and POSTs to the save endpoint.
- Frontend: `TemplateLibraryDock` mounts on the left of the canvas,
  lists user templates only (bundled ones stay in the Tests modal),
  supports drag via the `application/x-miniclaw-template` MIME type,
  and offers a hover-revealed delete affordance. Dropping a card onto
  an agent tile anchors the root virtuals to that tile; dropping onto
  empty canvas leaves root virtuals unparented.

### Pending

- Template parameters / slot interpolation / branching DSL.
- Template versioning beyond the instantiated node snapshots.
- Template export / import / sharing across machines.
- Library-card mini-DAG preview.


## 9. Multi-project / forks

Project-local bounded concurrency is implemented independently of forks.
The multi-project/worktree isolation surface described here has not been built.

### Status: not started

- Workspace UI for stacked project lanes.
- `fork-project` op (`git worktree add`).
- Context edges crossing project boundaries.
- Fork visualization, fork merge semantics.


## 10. Persistence

Trunk: `backend/miniclaw2/store.py`, `backend/miniclaw2/replay.py`.

### Landed

- JSON + JSONL on disk under `$MINICLAW_HOME` (default
  `~/.miniclaw2/`). Single-user assumption.
- Layout:
  ```
  projects/<pid>/project.json
  projects/<pid>/nodes/<nid>/node.json
  projects/<pid>/nodes/<nid>/events.jsonl
  projects/<pid>/nodes/<nid>/gates.jsonl
  projects/<pid>/nodes/<nid>/artifacts/<name>
  contextspace/...                # see §4
  ```
- Atomic JSON writes (tmp + rename). Each node has one event writer; different
  nodes may write their independent records concurrently, while project-wide
  virtual-DAG reconciliation is narrowly serialized.
- Registry initialization repairs persisted active nodes left by a previous
  process to cancelled terminal records, including framework stub previews,
  without blocking other projects on a malformed entry; queued nodes remain
  pending and resume scheduling after startup.
- Store schema v6 writes one canonical shape: only `model_preset_id`
  persists provider selection, only `provider_session_id` persists
  provider conversation identity, and ContextSpace/language selections
  live in typed Project fields. Runtime loading accepts only this canonical
  shape; v5 added the defaulted node artifact manifest. The v6 migration moves
  injected skill plugs to principles, rewrites live settings/bindings/templates
  and `principle_edit`, leaves immutable bundle snapshots untouched, and adds
  the native skill namespace. Startup backs up affected records first.
- `$MINICLAW_HOME` can be initialized as a Git repository and exchanged with
  a user-provided remote only through `miniclaw2 sync init <git-url>` or the
  Global settings **Sync now** action. Local durable writes are committed on a
  coalescing timer; no startup, shutdown, or periodic remote I/O occurs.
- `machine.json` is gitignored and carries a generated UUID, hostname/label,
  and the last successful sync checkpoint. Project records persist the native
  machine UUID and label. Registry and API guards reject every project
  mutation on non-native machines while retaining graph, transcript, preview,
  and history reads. Stale active states from remote projects are not repaired
  or scheduled locally.
- Sync uses fetch, merge, and push. Normal conflicts retry with Git's
  local-hunk-wins strategy; `schema.json` conflicts abort. New project
  subtrees remain single-writer, failed pushes roll the local merge back, and
  both-non-empty bootstrap is refused.
- A hostname mismatch between `machine.json` and the actual host is the
  copied-store detector: startup asks once whether the machine was
  renamed (keep uuid, refresh hostname/label) or the store was copied
  (regenerate uuid, which demotes projects native to the original
  machine to read-only). Sync refuses to run while the mismatch is
  unresolved.
- A store whose `schema_version` is newer than the code opens read-only
  (`Store.read_only_reason`): startup repair, scheduling, and all
  mutating operations are blocked, and the wire `read_only` flag is set
  on every project.
- Local commits carry structured per-boundary messages coalesced on the
  debounce timer and are authored as `MiniClaw2 (<machine label>)`, so
  `git log` doubles as a cross-machine activity ledger.
- Sync status is computed locally as a binary up-to-date/changed (dirty
  tree, HEAD past the last synced commit, or a failed prior sync).
  Manual-only sync means remote divergence is discovered at Sync-now
  time, and viewer freshness on non-native projects is exactly as fresh
  as the last button press — the read-only badge's "as of" timestamp is
  what keeps a stale `running` state honest.
- Reconnect replay reads `events.jsonl` from `since_seq` then attaches
  to the live tail. New event envelopes carry `schema_version: 2`; existing
  records without a version are treated as version 1, and historical
  `checkpoint_review` requests are upgraded to `human_review_prose` before
  runtime delivery. This replay compatibility remains necessary until the
  append-only historical event logs are rewritten. Project-level WebSocket
  observers continue to receive live events after the JSONL gap is replayed.

### Pending

- SQLite migration. Deferred until cross-project queries (e.g. "list
  all nodes in `awaiting_human_input` across the workspace") actually
  become hot.
- Adopt-project ownership transfer (rewrite `machine_id` + `root_path`,
  invalidate stale provider sessions). Until built, a retired or dead
  machine's projects remain read-only everywhere.
- Sync retention/compaction — `events.jsonl` transcripts grow the store
  repo monotonically; archiving, compaction, or LFS is deferred until
  repo size hurts.
- Encryption at rest (git-crypt/age) for partially trusted remotes.
- Workspace `outputs/<nid>/` stays local to the native project machine;
  its explicitly published, size-capped subset lives under durable node
  metadata and syncs with the project subtree.
- Cross-machine live streaming (backend-to-backend event relay); viewer
  freshness beyond pull-on-sync is a different feature.
- Merging two non-empty stores at bootstrap. Multi-master writing is an
  explicit non-goal — the native-machine convention is the concurrency
  model.


## 11. Wire envelopes (current set)

Quick reference; the on-disk shape is authoritative.

- Server → client (all runner-emitted envelopes carry `node_id`):
  `node_started {node_id, parent_node_id, kind, provider, model_preset_id,
  category, subtype, agent_op_kind, prompt}`,
  `node_updated`, `node_removed {id}`, `interaction_request
  {interaction_type, ...}` with `interaction_type ∈ {"permission",
  "ask_user", "human_review_prose"}` (`checkpoint_review` appears only
  in the versioned replay upgrader; `permission` is emitted by Codex
  only), `text_delta`, `thinking`, `activity`, `usage`,
  `turn_done`, `error`. Registry-emitted and node-less: `git_status
  {is_repo, head, branch, detached, upstream, ahead, behind, dirty_count}`
  (ephemeral, `seq: 0`; must never carry a `node_id` — see §3a).
- Client → server: user prompt with optional `resume_from_node_id`,
  `extra_principles`, structured/string-compatible `extra_skills`,
  `agent_op_kind`, and `model_preset_id`; `interrupt`;
  interaction response;
  `replay_request {node_id, since_seq}`. Interrupt is
  `interrupt {node_id}`; interaction responses may include their owner
  `node_id`, with gate-id lookup retained for compatibility.
- Interaction responses use one carrier per kind: ask-user answers at
  `response.answers`, human-review text at `response.prose`, and
  provider-neutral permission fields (`allow`, `scope`, `interrupt`,
  `updated_input`, `message`). Provider adapters own vendor decisions.
- `/sessions` is the sole current project API namespace; it is historical
  naming, not an alias backed by a second `/projects` implementation.
- `POST /sessions/{sid}/planspaces` accepts deprecated `user_seed` as an input
  alias for `seed` and returns `Deprecation` / `Warning` headers when used.
- `SessionInfo.provider` remains a response-only value derived from
  `model_preset_id`; it is not persisted and is retained only for HTTP response
  compatibility.
- REST: `GET /model-presets`; `GET /global-state`,
  `POST /global-state/sync/setup`, `POST /global-state/sync`; project CRUD, preferences, node/event
  introspection, failed-node rerun, per-node diff/preview/context-bundle reads,
  published artifact JSON/raw reads,
  `GET /sessions/{sid}/git` (status + derived commit descriptors),
  `POST /sessions/{sid}/git/commit {message}` (spawns the commit op),
  `POST /sessions/{sid}/git/review` (spawns the queued code_review node, or
  idempotently returns the in-flight one; 400 for non-repo projects — the
  scheduler guard, not the endpoint, owns serialization),
  `POST /sessions/{sid}/git/pull` (409 unless quiescent; spawns the pull op),
  `POST /sessions/{sid}/git/push` (direct action; 409 with git stderr or
  while a pull is in flight),
  `PATCH /sessions/{sid}/layout-hints`,
  `PATCH /sessions/{sid}/planspace-view`,
  `PATCH /sessions/{sid}/planspaces/{planspace_id}/mode`,
  `POST /sessions/{sid}/planspaces {title, seed, mode?, model_preset_id?}`
  (creates a new planspace + activates it + launches the concierge planning
  agent),
  `POST /sessions/{sid}/planspaces/blank`
  `{title?, seed, mode?, model_preset_id?}`
  (creates a new planspace + activates it + creates one empty virtual),
  `POST /sessions/{sid}/virtuals` (creates an editable virtual in a planspace,
  with optional model preset, attached principles, native skills, and
  `agent_op_kind`),
  `PATCH /sessions/{sid}/virtuals/{vid}` (edits or obsoletes a
  virtual),
  `DELETE /sessions/{sid}/virtuals/{vid}` (hard-deletes an unrun
  virtual, returning blockers when other live nodes depend on it),
  `POST /sessions/{sid}/virtuals/{vid}/promote` (manual promotion,
  returns and broadcasts the promoted node),
  `POST /sessions/{sid}/context/init`,
  `POST /sessions/{sid}/context/refresh`,
  `POST /sessions/{sid}/context/cancel`,
  `GET /sessions/{sid}/files`,
  `GET /sessions/{sid}/nodes/{nid}/preview` (durable preview text),
  `POST /sessions {auto_commit, ...}`,
  `GET /principles`, `DELETE /principles/{slug}`,
  `GET /skills`, `POST /skills/import`, `DELETE /skills/{slug}`,
  `GET /user-templates`, `GET /user-templates/{slug}`,
  `DELETE /user-templates/{slug}`,
  `POST /sessions/{sid}/user-templates {name, brief, node_ids}` (saves
  a canvas selection as a user template),
  `POST /sessions/{sid}/user-templates/{slug}/apply {anchor_node_id?}`
  (stamps a user template into the active planspace).

No client-facing `start_gate_node` envelope remains; reviews are
ordinary agent nodes with `category=review`.
