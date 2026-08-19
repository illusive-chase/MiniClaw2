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
  between consecutive referenced ones. Interior gaps render as a `+N`
  badge on the trunk edge; the oldest hub carries its own pre-history
  count because it has no inbound edge. When the source repository is not
  available, referenced hubs use their earliest node-observation timestamp
  (after alias resolution) as a deterministic trunk order; unobserved refs
  sort last by SHA instead of forcing the whole trunk into SHA order.
- Alias capture: the pull op records `git rev-list --reverse
  @{upstream}..HEAD` before and after the rebase; equal lengths give a
  positional old→new map (atomic store write). On length mismatch the map
  is not written and affected epochs render stale — the honest fallback.
  External rebases also render stale. Node records never rewrite.
- Canvas: an oldest-first vertical trunk `C₀ → … → ghost`; hubs are 64px
  circles (sha7, live solid / stale dashed-amber / HEAD ring / ghost dashed
  showing the dirty count), draggable via `layoutHints` key `commit:<sha>`.
  The trunk grows downward without changing lane `x`, and trunk edges carry
  an arrowhead so the older → newer direction is readable. Epoch source/sink
  links touch only the sources and sinks of each epoch's dep-sub-DAG
  (transitive reduction within the epoch), render dashed and arrowed, enter
  the agent tile's top (`epochIn`) and leave its bottom (`epochOut`) so they
  read against the vertical trunk rather than the horizontal dep axis, and
  stay hidden until an endpoint is hovered or selected. A sink targets the earliest
  rendered **live** commit at-or-after its `commit_after`, else the ghost
  when rendered, else no edge.
- Selection kind `{kind: "commit", sha | null}` routes to `GitCommitPanel`:
  HEAD/live/stale badges, message, timestamp, clickable epoch member list,
  associated op metadata (auto vs manual, parent agent). The ghost variant
  is the commit composer, prefilled from the current epoch's node summaries;
  it re-checks the dirty count and degrades to "working tree clean".
- Edge cases: a dirty empty repo renders a parentless ghost only; detached HEAD keeps
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
  `settings_override`, `temporary`, `template_id`, `tag_ids`,
  `machine_id`, `machine_label`,
  `layout_hints`, `layout_viewport`, `planspace_view`,
  `created_at`.
- `Project.provider` and `Node.provider` are computed from
  `model_preset_id` for wire/display use and are excluded from persisted JSON.
- `PlanspaceMode ∈ {auto, manual}`. `agent_op_kind` is an extensible
  string discriminator with a current whitelist of `principle_edit` and
  `library_edit`; it is valid only on agent nodes. Pending principle and skill
  selections are virtual-agent intent and are promoted into their corresponding
  launch settings.
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
  permission approvals onto the same wire envelopes. Fresh non-minimal
  threads also register a host `ask_user` dynamic function tool, allowing
  writable Default-mode agents to pause on the same `ASK_USER` gate and
  continue the current turn after the answer. This experimental protocol is
  pinned to codex-cli >= 0.146.0. Resumed threads do not resend
  `dynamicTools`: post-upgrade threads restore the tool from their rollout,
  while older historical threads retain conversation continuity without
  claiming inline ask-user support. Already streams per-delta.
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
- Permission gates use the global tool-request timeout and resolve with the
  configured automatic accept/reject response when it expires. The default is
  120 seconds and automatic acceptance. Ask-user gates and human review prose
  remain unbounded and do not consume the tool-request timeout.
- Installed Claude hook entries carry explicit timeouts
  (AskUserQuestion uses the runtime's maximum practical timer duration;
  SessionStart and Stop use 15s). The hook callback port is set from
  `MINICLAW2_HOOK_PORT` / `MINICLAW2_PORT` at app startup and otherwise
  captured from the actual HTTP/WS request scope.
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
  presets. Planning agents may explicitly select an active preset for a
  virtual only when the user asks for a particular preset/provider/model;
  their launch block lists the configured active presets and resolved
  provider/model pairs. By default, newly proposed virtuals inherit the
  proposing planning/review node's preset, while rewrites retain the existing
  virtual's preset. Review agents cannot select models. Continuation virtuals
  keep the resume source's preset/session, and reruns may preserve a
  compatibility preset from historical data.
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
- Existing principle-edit nodes retain the normal runner, preview contract,
  provider events, cancellation, replay, and terminal behavior. They remain in
  the whitelist for historical reruns; new authoring starts through the
  librarian below.

### Librarian agents (`library_edit`) - landed

- The librarian is the unified creation entry point for user-wide principles
  and native Agent Skills. It receives `prompts/library_init.md`, classifies the
  user's seed using the eager behavior-guidance versus lazy tool/workflow
  boundary, and authors or refines exactly one entry. Historical
  `principle_edit` nodes remain valid and replay unchanged.
- The runner hashes both libraries before the provider turn and validates the
  single changed entry immediately after DONE, before preview repair. Skills
  must have readable `SKILL.md` frontmatter and a symlink-free tree;
  principles must have a matching manifest and non-empty `CONTEXT.md`. Zero,
  multiple, cross-library, or malformed changes error the node and record the
  result in `settings_snapshot.library_audit`.
- Newly authored skills receive `authored` provenance with the creating node
  id; refinement preserves existing provenance. A terminal librarian refreshes
  both library shelves.
- Librarian nodes are created and edited exactly like every other virtual: any
  virtual's Classification control offers **Work / Plan / Review / Library**,
  and choosing Library sets `agent_op_kind="library_edit"` while holding
  `category=regular`. The four options are mutually exclusive in the draft
  model, and `domain.py` enforces the pairing (`AUTHORING_AGENT_OP_KINDS`
  require `category=regular`) so a librarian can never also carry a review
  brief. `PATCH /sessions/{sid}/virtuals/{vid}` accepts `agent_op_kind`, so the
  classification is editable after creation rather than fixed at create time.
  Historical `principle_edit` nodes read as Library and keep their original op
  kind unless the user changes classification.

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
  nodes (or the user) read the report artifact. Git Review nodes resolve their
  preset from the independent global `code_review.model_preset_id` setting,
  not from the project; the bundled default is `gpt-5.6` (Codex
  `gpt-5.6-sol`, high reasoning). The ghost composer exposes
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
    plugs/planspaces/<project-binding-slug>.<lane-slug>/{manifest.yaml, events.jsonl}
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
- Principles are markdown-only (`manifest.yaml` + `CONTEXT.md`, no assets) and
  default to `turn` injection because the providers are only symmetric on the
  turn channel: Claude re-applies `system` text on every spawn via
  `--append-system-prompt`, while the Codex adapter can fake a system channel
  only on the first turn of a fresh thread — on resumed threads,
  `system`-injected text silently drops. `turn` re-asserts principles on every
  node, which is what behavior shaping wants anyway; the per-plug `injection`
  override remains for power users, with this caveat.
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
- Planspace IDs are project-scoped as
  `planspaces.<project-binding-slug>.<lane-slug>`. Lane-name collision
  numbering is therefore local to one project binding; different projects can
  use the same unnumbered lane slug.
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
- `GET /principles` enumerates principle summaries,
  `GET /principles/{slug}` loads one full `CONTEXT.md` body on demand, and
  `DELETE /principles/{slug}` removes one with strict slug/path validation.
  The frontend exposes them as behavior-guidance tiles with virtual-node
  selection and drag attach.
- Native Agent Skills (the `SKILL.md` standard) are stored verbatim under
  `contextspace/skills` so community skills import unmodified. Skills are
  deliberately **not plugs**: they never enter bundle composition or (v1)
  bindings, and MiniClaw2 never parses or injects a skill body — attaching one
  makes it *available*; lazy loading from the frontmatter description is the
  provider's job. The library lives under the sync root and rides ordinary
  metadata-sync commits (skills are expected to be markdown plus small
  scripts; large binary payloads bloat the sync repo).
  `GET /skills` returns summary metadata without downloading every `SKILL.md`
  body; `GET /skills/{slug}` loads one full body on demand. Together with
  `POST /skills/import` and `DELETE /skills/{slug}`, these provide inspection,
  local/zip/git import, overwrite, and removal. Imports reject zip traversal
  and symlinks; provenance is stored separately in
  `contextspace/skill-imports.json`.
- Import auto-detects a source containing multiple `SKILL.md` directories when
  no slug is supplied. Every member is validated before any member is replaced,
  then the package is installed as one rollback-capable filesystem unit.
  Per-member provenance records a source-derived `package_id`, the full
  `package_members` list, and `auto_attach_package=true`. Re-importing a member
  alone detaches it from stale package membership; deleting a member rewrites
  the remaining membership lists.
- Skill frontmatter parsing recognizes `version` and
  `metadata.requires.siblings`. Virtual creation/edit, direct launch, rerun,
  and the runner recursively expand sibling dependencies. Selecting any member
  of an imported package also expands the full package. Generated selections
  persist as `{auto_attached: true, required_by, attachment_reason}` with
  `suggest=false`; they are not treated as roots on the next edit, so removing
  the final explicit member collapses all generated attachments. The frontend
  labels them `Pack` or `Dependency` and prevents independent removal or suggest
  toggling.
- The runner records per-skill source path, directory hash, mechanism,
  missing/failed state, and used state in `settings_snapshot.skill_audit`
  (the directory hash exists because skills mutate between runs — it is the
  graph's answer to "what exactly did this node have access to").
  Claude materialization copies (not symlinks, until the plugin loader is
  verified to follow them) all expanded package/dependency members into one
  ephemeral
  `skill-plugin/` dir under the node's run workspace, passes it via a
  node-private `--plugin-dir` on every spawn (so resume works and concurrent
  nodes cannot interfere), and reaps it with the workspace. Each node-private
  Codex app-server receives the exact expanded library directories through
  `skills/extraRoots/set` (protocol verified against codex-cli 0.144.1)
  after `initialize`, before thread start/resume — no shared `~/.codex` or
  worktree mutation. Suggest mode adds one provider-neutral launch line.
  Unsupported/failed Codex protocol calls launch without skills and mark
  those audit entries failed.
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
- Deliberate v1 skill deferrals: import-time safety scanning (the library
  only holds what the user explicitly imported; panel inspectability before
  first attach is the interim control), binding-level "always available in
  this project" skills (v1 is per-node
  opt-in only), marketplace/registry browsing (import takes a path/URL), and
  skill versioning/update tracking (re-import overwrites; the per-launch
  content hash is the audit trail). Skills the native binaries already load
  from vendor config (`.claude/skills`, `~/.codex/skills`) stay invisible to
  the canvas, like the rest of vendor config.
- Principle ordering UI — composition order stays binding order then opt-in
  order.
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
  `TemplateGroupNode`, `TemplateInstanceBoxNode`,
  and `CommitNode`. `ProjectRootNode`, passive `GateNode`, and the old
  phantom composer node have been removed; the project home glyph lives
  in the header and opens `ProjectPanel` plus its direction composer.
  The two template nodes are view-only groupings over `template_instance_id`
  (section 8) — they carry no run state of their own.
- Polymorphic side panel: `AgentPanel`, `ContextNodePanel`,
  `ArtifactPanel`, `OpPanel`, `GitCommitPanel`, lane panel,
  `TemplateInstancePanel`, `ProjectPanel`
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
- Principle and skill attachment uses one searchable hierarchical picker.
  Hyphenated ids form collapsible prefix trees, search returns breadcrumb
  matches, and successful choices feed a per-kind MRU list. Empty libraries
  render a dashed creation entry; libraries containing only single-segment ids
  naturally render as a flat list because the hierarchy builder creates no
  directory rows. Those two measured states close the flat-versus-tree design
  question: there is no separate mode toggle.
- Agent tiles show category badges for planning / regular / review /
  human-interact review nodes; verifier tiles use the review tone and
  a programmatic label.
- Edge weight is explicit: dependency arrows, the vertical commit trunk,
  resume (`↻` mid-glyph), and error-terminal edges render at rest. Loads,
  produces, and commit epoch links are hidden until an endpoint is
  hovered or selected, and all three render dashed — dashing marks the
  derived, on-demand class, not a per-edge state. Within loads, the dash
  *pattern* carries consumption: tight (`5 3`) for context a run actually
  consumed, sparse (`2 4`) for declared-but-not-yet-run bindings and for
  available-but-unused skills. Loads enter the agent tile's top `loads`
  handle (op tiles keep the default left/right pair). Hovering a commit hub
  also rings its epoch members; hovering a member rings its hub through a
  store that does not rewrite the React Flow node array.
- Dependency edges come only from `scheduled_deps`; nodes with no declared
  dependency have no fabricated `root → node` relation.
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
- `ProjectPanel.tsx` has Project actions (`+ New direction`, skill/pack import,
  initialize/refresh project notes), native-project tag assignment, and a
  Directions section with active badges, mode labels, and hide/show controls.
  Successful tag writes update App's owning session state as well as the open
  panel, so navigation cannot restore and later persist a stale assignment.
  Library *authoring* is not a project action: it is the Library classification
  on an ordinary virtual.
- Principle and skill context tiles are binding-driven: a tile exists only
  when an observed bundle/skill audit or a visible virtual's pending binding
  references it. Pending declarations draw dashed loads; observed principle
  use and used skills draw solid loads; available-but-unused skills remain
  dashed. Unbound user-wide entries do not appear on the canvas.
- `LibraryDock` is the single side-panel collection for Templates,
  Principles, and Skills. Its three sections reuse the attachment hierarchy,
  persist section/directory expansion, search all sections together, and keep
  existing drag MIME payloads unchanged. Leaf actions open an on-demand detail
  preview, surface the entry in the inspector, or delete it; previews can apply
  a template or attach a principle/skill when the selected project/node permits
  it. Completed librarian turns refresh and reveal newly authored entries.
  Persisted panel mode `templates` migrates to `library` on read.
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
- Projects landing page (`ProjectsLanding`) with rename/delete, global project
  tags, multi-select AND filtering, and grouped/name/activity sort modes. A
  three-project recent-activity strip appears only when it does not mostly
  duplicate the full list and remains global while filters are active. Group
  order follows `tags.json` creation order, multi-tag projects appear in every
  matching group, and untagged projects trail the tag groups. User drag reorder
  was judged over-design for v1 and is not implemented. The Tests modal covers
  bundled templates. New-project creation supports a named or temporary
  workspace, cwd creation confirmation, preferred language, and an active
  model preset.
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
- Deliberate exclusions from the retrieval/tagging pass: tags are flat labels,
  template naming is unchanged, there is no project-level skill/principle
  attachment UI, and complete modal focus trapping remains a future
  cross-modal accessibility change rather than a one-off addition here.

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
  or synced but keep flowing to downstream agents on the originating
  workspace. Lane materialization overlays the synced durable published
  copies, so dependencies produced on another device expose their artifacts
  even though that device's `.miniclaw2/outputs/` tree is absent. Publication
  is a parallel, narrower path to the human. Verifier and op previews are
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
  `acceptForSession`. Fresh non-minimal threads register the host
  `ask_user` dynamic tool so Default-mode agents can use that envelope without
  entering Plan mode; malformed calls, unknown dynamic tools, cancelled gates,
  and malformed answers return `success: false` without opening a permission
  gate.
- The Codex adapter accepts both current
  `item/commandExecution/requestApproval` / `item/fileChange/requestApproval`
  requests and the older `execCommandApproval` / `applyPatchApproval` methods.
  The older methods return their historical decision strings
  (`approved`, `approved_for_session`, `denied`, `abort`). They remain for
  compatibility with historical threads and older event recordings even
  though fresh interactive threads now require codex-cli >= 0.146.0.

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
  live under `$MINICLAW_CONTEXT_HOME/templates/<slug>/`, are surfaced in
  the side-panel Library dock, and stamp their subgraph into the *current*
  project's active planspace when dropped onto the canvas.

Templates are being reshaped into *functions* (see the schema v2 section
below): `template.yaml` declares `arguments` (string parameters) and
`inputs` (named upstream ports). The loader validates the declarations and
`launcher.py` resolves them when stamping. There is no concierge prefix. A
template author who wants adaptation beyond parameters bakes a `planning`
virtual into their subgraph.

### Landed — bundled

- `backend/miniclaw2/templates/` provides `loader.py`, `launcher.py`,
  and `bundled/` template definitions with `template.yaml`,
  `lane.yaml`, `prompts/`, `scripts/`, and optional `seed/`.
- Model selection metadata declares `allowed_model_preset_ids`; runtime
  loaders reject legacy `providers` and template-level `model_preset_id`
  fields. For bundled templates this list is a *run matrix* — the Tests panel
  renders one "run" button per entry so one scenario can be compared across
  models — not an apply-time restriction. User templates leave it empty and
  declare `model_preset_id` per lane node instead. Historical template shapes
  are no longer supported. `schema_version`, `arguments`, and `inputs` are
  covered in the schema v2 section below.
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
  template becomes topologically self-contained. They are deliberately not
  converted to generated input names: runtime node ids do not provide a
  stable, meaningful port interface, so authors name and connect those ports
  explicitly in the template editor.
- `virtual_graph.is_connected` performs the undirected BFS used by the
  connectedness check.
- `launcher.apply_user_template` stamps a user template into the
  project's active planspace via a shared `_stamp_lane` helper.
  Cross-lane origins collapse into the active lane on apply. When an
  anchor tile is provided, root virtuals (those with no in-template
  deps) get an implicit `scheduled_deps=[anchor]`.
- Model resolution on apply is per node, not per template: each agent node
  is stamped with its own `model_preset_id`, captured from the source node at
  save time and editable afterwards. A node that declares none inherits the
  target project's preset, which is also how templates authored before
  per-node models behave. A declared model that is unknown or
  compatibility-only raises and names the node, because silently substituting
  the project preset would run the node on a model the template did not ask
  for. A resume node is the one exception: it continues an existing provider
  session, so it inherits its source's model (the runtime rejects a resume
  virtual whose model differs) and the editor disables its picker.
- REST endpoints:
  `GET /user-templates`, `GET /user-templates/{slug}`,
  `PUT /user-templates/{slug}` (complete editor-state rewrite),
  `DELETE /user-templates/{slug}`,
  `POST /sessions/{sid}/user-templates` (save selection),
  `POST /sessions/{sid}/user-templates/{slug}/apply`.
- Frontend: React Flow shift-click / marquee multi-select
  (`multiSelectionKeyCode="Shift"`) with `selected` state carried
  through graph re-syncs so multi-selection survives `node_updated`
  websocket bumps. Right-click on an agent tile opens a `ContextMenu`
  with "Save as template…"; the resulting `SaveAsTemplateModal` takes a
  name + one-line brief and POSTs to the save endpoint.
- Frontend: the Templates section of `LibraryDock` lists user templates
  (bundled ones stay in the Tests modal), supports drag via the
  `application/x-miniclaw-template` MIME type, and offers deletion.
  For a template that declares neither arguments nor inputs, dropping a card
  onto an agent tile anchors the root virtuals to that tile and dropping onto
  empty canvas leaves them unparented. Templates that declare either one route
  through the instantiation dialog instead (see the schema v2 frontend section
  below).

### Landed — schema v2 (loader层)

Disk format for the function-style template. `backend/miniclaw2/templates/
loader.py` owns parsing; nothing below changes stamp-time behaviour.

- `template.yaml` must declare `schema_version: 2`. A missing or older
  value raises `TemplateError` with a "run the template migration" hint —
  strict single-path parsing, no dual-track v1 fallback. `SCHEMA_VERSION`
  is exported from `miniclaw2.templates`.
- `arguments`: list of `{name, description?, default?}`. `name` matches
  `^[a-z][a-z0-9_]*$` and is unique. An absent `default` key and an
  explicit `default: null` both mean **required**; `default: ""` is a
  supplied empty string and is therefore optional. Empty list is legal.
- `inputs`: list of `{name, description?}` with the same naming and
  uniqueness rules. `arguments` and `inputs` are **independent
  namespaces** — a `topic` argument and a `topic` input coexist because
  their reference syntax differs.
- Placeholder scan: every node prompt is scanned for `{{name}}`.
  Scanned-but-undeclared arguments are appended in memory with
  `declared=False`, so authors can use a new placeholder without editing
  `template.yaml` first. Braces whose body fails the naming rule
  (`{{Bad-Name}}`, `{{a b}}`) stay literal text and are never treated as
  parameters, keeping accidental substitution out of the picture.
- `{{input.<port>}}` referencing an undeclared port is an **error**: that
  declaration drives the instantiation dialog's binding form, so it cannot
  be inferred.
- `in:<name>` in `scheduled_deps` names an input port. Ports are
  out-of-graph source points: `_validate_lane_graph` checks them against
  the declared `inputs`, then excludes them from both the
  "dep must reference an earlier node" ordering rule and cycle detection.
  `TemplateNodeSpec.input_deps` / `.internal_deps` split the two kinds.
- Warnings (structured, non-fatal, exposed for the editor to render):
  `dangling_argument` when a declared argument no longer appears in any
  prompt, and `unreferenced_input` when a declared input is referenced by
  neither `in:<name>` nor `{{input.<name>}}`. Both are warnings by design
  so a half-finished template still loads.
- `Template.metadata()` adds `schema_version`, `arguments` (with
  `description` / `default` / `required` / `declared`), `inputs`, and
  `warnings`. `TemplateSummary` / `TemplateDetail` in `app.py` carry them
  through `GET /templates`, `GET /templates/{name}`,
  `GET /user-templates`, and `GET /user-templates/{slug}`.
- All seven bundled templates carry `schema_version: 2`.
  `serializer.serialize_selection` emits `schema_version: 2`, keeps `inputs`
  empty, and uses the loader's placeholder scanner to persist any `{{name}}`
  already present in a selected prompt as a declared argument. Save-as-
  template therefore follows the same naming and scan rules as loading,
  without attempting to infer named input ports from external dependencies.
- The store schema v8 migration described below upgrades pre-existing user
  templates before the strict loader enumerates them.

### Landed — schema v2 (stamp 与实例记录)

- `POST /sessions/{sid}/user-templates/{slug}/apply` accepts string maps
  `arguments` and `input_bindings` (both default to `{}` for old empty-schema
  callers) and returns the stamped `node_ids` plus a shared `instance_id`.
- Stamp resolves omitted optional arguments from their defaults, including
  `default: ""`; missing required arguments, unknown argument/input names,
  absent bindings, missing nodes, and cross-planspace bindings raise
  `TemplateError` and map to HTTP 400.
- Prompt substitution is one `_PLACEHOLDER_RE.sub` callback using the
  loader's `PARAM_NAME_RE`: replacement strings are literal, so backreferences
  and inserted braces are never interpreted in a second pass.
  `{{input.<port>}}` becomes that node's run-lane preview path. A final scan
  rejects any legal argument or input placeholder left in `prompt_draft`.
- `TemplateNodeSpec.internal_deps` translates through the fresh slug-to-node
  map; `input_deps` translates through caller bindings. Templates with inputs
  never inherit the drag anchor. Templates without inputs retain the existing
  root-anchor behavior.
- Every stamp builds all nodes and translated dependencies in memory, checks
  the combined active-lane DAG for cycles, and completes validation before the
  first `create_node`. Write failures roll back nodes created by that call.
- `Node.template_instance_id` is optional and defaults to `None`, so old node
  records still deserialize. All nodes from one stamp share the generated ID,
  which is exposed automatically by node REST payloads.
- Planspace `manifest.yaml` owns `template_instances`, with records containing
  `instance_id`, template slug/name, resolved arguments, input bindings,
  `created_at`, and reserved `parent_instance_id: null`. The project binding
  is checked before reads or writes. `GET /sessions/{sid}/planspaces/
  {planspace_id}/template-instances` exposes these records for group headers.

### Landed — schema v2（存量用户模板迁移）

- Store schema is `user-template-schema-v2-v8` / version 8. Startup upgrades
  older stores after resolving `$MINICLAW_CONTEXT_HOME`, while stores with a
  newer schema still remain untouched/read-only under the existing policy.
- Before rewriting anything, migration copies the complete ContextSpace
  `templates/` tree to `migration-backups/user-template-schema-v2-v8-<timestamp>/
  contextspace/templates/`. Each legacy `template.yaml` receives
  `schema_version: 2`, `arguments: []`, and `inputs: []`; existing declarations
  are preserved and already-v2 manifests are not rewritten.
- Prompt scanning imports the loader's `_PLACEHOLDER_RE` and `PARAM_NAME_RE`
  directly. Every legal `{{argument}}` occurrence in a migrated template is
  emitted through the startup logger with template, prompt path, and line
  number for user confirmation. Invalid names remain literal and are omitted
  from the checklist, matching loader behaviour.
- Migration isolates failures per template: malformed/unreadable templates
  are logged by slug and reason while remaining templates continue. A second
  startup is a no-op because the store schema is already v8 and migrated files
  are stable.

### Landed — serializer v2 与编辑器写回 API

- `PUT /user-templates/{slug}` accepts the template editor's complete state:
  name/brief, agent node definitions with full prompt source, per-node
  `model_preset_id`, and `in:*` dependencies, argument descriptions/defaults,
  and input declarations. Verifier nodes and path-like slugs are rejected. The
  template keeps its slug.
- Missing argument `default` and explicit `default: null` both persist as
  required; `default: ""` remains an optional empty-string default. Prompt
  arguments omitted by the request are appended using the loader's own
  placeholder scanner, so an editor save materializes scan-only arguments.
- New selection saves and editor rewrites share `_materialize_template`:
  files use atomic `.tmp` writes, a complete sibling candidate directory is
  loaded through `_load_from_root`, and only a valid candidate replaces the
  current directory. Any write or validation failure cleans the candidate and
  leaves the previous template intact; a successful update schedules sync.
- Detail endpoints (`GET /templates/{name}` and
  `GET /user-templates/{slug}`) add each node's untruncated `prompt` through a
  detail-only serialization branch. List endpoints continue to expose only
  `prompt_preview`, preventing LibraryDock refreshes from downloading all
  prompt source.

### Landed — 前端实例化弹窗

- `frontend/src/types.ts` mirrors the v2 metadata: `TemplateArgumentMeta`
  (`description` / `default` / `required` / `declared`), `TemplateInputMeta`,
  `TemplateWarningMeta`, and the `schema_version` / `arguments` / `inputs` /
  `warnings` fields on `TemplateSummary`. `default` is `string | null` where
  `null` means required; requiredness is always read from the `required` flag,
  never inferred from `default`, because `default: ""` is an optional empty
  value. `TemplateDetail` aliases the summary and `TemplateNodeSpec.prompt` is
  optional, present only on detail responses.
- `frontend/src/templateInstantiate.ts` holds the dialog's decision logic as
  pure functions, covered by `frontend/tests/template-instantiate.test.ts`
  (registered in `npm test` alongside the canvas-layout suite):
  `templateNeedsInstantiateDialog`, `initialArgumentValues`,
  `argumentsComplete` / `missingRequiredArguments`, `inputBindingsComplete` /
  `unboundInputPorts`, `canSubmitInstantiation`, `initialInputBindings`,
  `inputCandidates`, `buildInstantiateRequest`, `warningText`, and
  `isRetryableApplyStatus`.
- A template with no arguments and no inputs skips the dialog entirely, so
  every pre-v2 template keeps its drag-drop-stamped behaviour with the hovered
  tile still acting as the implicit anchor. Warnings alone never trigger it.
- `components/InstantiateTemplateModal.tsx` follows the
  `SaveAsTemplateModal` skeleton (controlled `open`, per-opening reset and
  autofocus, local `submitting`/`error`, scrim + card + three-part layout) and
  additionally registers a capture-phase Escape listener, which the older
  dialogs still lack. The footer names the outstanding fields, and "创建" stays
  disabled until every required argument holds a non-blank value and every port
  is bound.
- Input candidates mirror the backend's binding rule: active-planspace nodes
  only, obsolete ones filtered out. Dropping onto a node prefills that node as
  the *first* port's binding rather than spending it on the legacy anchor;
  `buildInstantiateRequest` drops `anchor_node_id` whenever ports exist, which
  matches the backend ignoring it in that case. `pruneStaleBindings` clears a
  port whose node stops being a candidate while the dialog is open (the lane
  keeps refreshing over the websocket), so a vanished id cannot reach the
  backend; it returns its input by identity when nothing changed.
- `arguments` are sent for every field the dialog displayed, including
  untouched defaults, because substitution is frozen into `prompt_draft` at
  stamp time. Keys the template does not declare are never sent, so stale form
  state cannot trip the backend's unknown-name check.
- `api.ts`'s `applyUserTemplate` now takes an `{anchor_node_id, arguments,
  input_bindings}` payload, throws `ApiError` (carrying status + detail) like
  the rest of the module, and returns `instance_id` alongside `node_ids` for
  group-collapse keying.
- Errors stay inside the dialog so filled-in values survive: a 400 detail
  renders in place for correction, and 409 (`turn in progress`, `context
  refresh in progress`) renders as a retryable amber notice. Author-side
  `warnings` render as advisory lines and never block instantiation.
- `App.tsx` fetches the summary on drop rather than reading the dock's cache,
  so a template edited since the last library refresh still gets its current
  argument list, then either opens the dialog or stamps directly. Both paths
  call `refreshNodes()` because manual-lane stamps emit no `node_updated`.

### Landed — 前端模板编辑器

Entry points: the "编辑" button on each `LibraryDock` template card, and
"保存并编辑" in `SaveAsTemplateModal`, which is the intended path after a
selection save — the captured prompts still need their literals turned into
`{{placeholders}}` and any dropped external dependency re-pointed at a named
input port.

- `frontend/src/templateEditor.ts` holds the editor's decision logic as pure
  functions, covered by `frontend/tests/template-editor.test.ts` (registered in
  `npm test` beside the canvas-layout and instantiate suites): placeholder
  scanning, `argumentReferences` / `inputReferences`, argument resolution and
  `upsertArgument` / `pruneArguments`, port add / rename / delete / connect,
  node add / remove / connect / resume, `topologicalOrder`,
  `validateEditorState`, and `buildRewritePayload`.
- `scanPlaceholders`, `PARAM_NAME_RE`, and the placeholder regex mirror
  `loader.py` exactly, including its rule that a body failing the naming
  pattern stays literal text. The scan powers the panel's read-only "referenced
  by" lists; `dangling_argument` / `unreferenced_input` are **not** re-derived
  locally — they are read from the backend's `warnings`, and the one-click
  cleanup passes those warning names back through `pruneArguments`.
- The editor loads through `GET /user-templates/{slug}` for each node's
  untruncated `prompt`. `prompt_preview` is never used as edit source; a node
  whose payload lacks `prompt` loads empty rather than silently truncated.
- Requiredness is a two-state control, not an empty text field: a "有默认值"
  checkbox toggles `default` between `null` (required) and `""`, so "no
  default" and "the default is the empty string" stay distinguishable in the
  UI as they are on disk.
- Input ports are a pure frontend node type (`TemplatePortNode`) — not an
  agent, no state, never scheduled. Connecting one to a body node writes
  `scheduled_deps: ["in:<name>"]`. Renaming rewrites both reference forms
  together (`in:<name>` deps and `{{input.<name>}}` placeholders) because
  leaving either behind makes the template unloadable; since `arguments` and
  `inputs` are independent namespaces, a same-spelled `{{alpha}}` argument is
  deliberately left untouched by an `alpha` port rename. Deleting a port drops
  its deps but leaves prompt placeholders alone, and validation then blocks the
  save rather than silently editing prompt bodies.
- `validateEditorState` mirrors the loader's hard rules (duplicate/reserved
  node ids, undeclared ports in deps or placeholders, unknown deps, review
  subtype/brief presence, `resume_from` also being a dep, cycles, verifier
  nodes) and gates the save button. It is not a second source of truth: the
  backend still writes a candidate directory and reads it back through
  `_load_from_root`, so anything not covered here surfaces as a 400.
- `buildRewritePayload` emits nodes in topological order because `lane.yaml`
  carries topology in file order — the loader requires each dep to name an
  earlier entry, so drawing an edge "backwards" is a legal graph edit that must
  be normalized rather than rejected. Every resolved argument is sent,
  including scan-only ones, which is how a typed placeholder becomes a declared
  parameter. Each node's `model_preset_id` travels in the payload, so editing a
  node's model is what changes the model it will be stamped with. `lane_mode`
  and `schema_version` are omitted by design; the backend owns them.
- Save failures keep all editor state: the backend leaves the old template
  intact on a 400, so the detail renders in the editor and the author corrects
  in place. A successful save reloads from the response so backend-persisted
  arguments and fresh warnings replace local guesses.
- The canvas is a dedicated React Flow surface rather than the project
  `Canvas`: that component is built around `NodeInfo` (run state, planspace
  lanes, commits, session-persisted layout hints), none of which a template
  has. Reused are the pieces carrying the visual language — the same
  interaction config, the `DependencyEdge` / `ResumeEdge` renderers, and node
  tiles matching `AgentNode`'s geometry. Node positions are editor-local;
  a template has no persisted layout and inventing one would put view state
  into the on-disk schema.
- Dependencies are drawn with a two-click gesture (source ↘, then target)
  because ports and body nodes are different shapes with different legal
  targets; clicking an edge removes it. Cycles are refused at the gesture so
  the error lands next to the edge just drawn.
- Per proposal §5 and §8 the editor offers **no run affordance** (a template is
  a static definition; trying it out means instantiating it into a project) and
  **no template nesting**.

### Landed — 前端 group 渲染与折叠 black box

Proposal §6.2: nodes stamped from one template read as a single operator on the
main canvas. Scheduling and storage are untouched — this is a layout and
rendering layer over the `template_instance_id` that `launcher` already stamps
plus the planspace-level instance records it already writes.

- `NodeInfo.template_instance_id` and `TemplateInstanceRecord` are in
  `frontend/src/types.ts`; `listTemplateInstances` in `api.ts` reads
  `GET /sessions/{sid}/planspaces/{planspace_id}/template-instances`. App fetches
  per lane that owns a stamped node, keyed on the joined lane ids so unrelated
  node updates do not refetch. A failed fetch is warn-only: the group still
  renders, with a generic label instead of the template name and arguments.
- `clusterTemplateInstances` in `canvas/layout.ts` runs as a **pre-pass before
  placement**, which is what makes clustering possible at all: the placement
  pass is single-pass and order-dependent, so a member appearing before the
  sibling it depends on could not be positioned relative to it. Cluster geometry
  is then reserved as one contiguous block per instance, claimed from the lane
  cursor by the first member placed, so `laneCursors` stays monotonic and
  append-don't-reflow still holds for everything placed afterwards.
- `placeInstanceBlock` sits in the agent `??` chain **after** `placeRerunInLane`
  and **before** `placeAnchoredVirtualInLane`. Ahead of the anchored-virtual
  branch because a member's `scheduled_deps` point at its own siblings, which
  would otherwise stack the instance diagonally instead of clustering it; a
  saved `layoutHints` entry is still checked first inside the helper, so a manual
  drag continues to win. It returns null for every non-member, and a
  template-free graph is asserted byte-identical with and without instance
  inputs.
- An instance whose visible members do not all share one planspace is **not**
  clustered: a frame cannot span two lanes, so those nodes fall back to ordinary
  placement rather than disappearing.
- Both the expanded frame (`TemplateGroupNode`) and the collapsed box
  (`TemplateInstanceBoxNode`) feed `recordChildExtent` and flow through the
  shared `resizePlanspaceLanes` path, so the lane grows to contain them instead
  of clipping. The frame is a lane **sibling** of its members, not their React
  Flow parent — member drag, `extent` and lane fitting keep behaving exactly as
  outside a group. It is `draggable: false` and derived from member bounds on
  every rebuild (so `Canvas` deliberately excludes it from runtime position and
  measured-size carry-over), and takes pointer events only on its header band so
  clicks and marquee selection reach the members underneath.
- Collapsed is the default for a freshly stamped instance. Collapsing hides the
  members and redirects both endpoints of every boundary-crossing edge onto the
  box — inbound edges are the input bindings, outbound edges are downstream
  consumers of the sinks — de-duplicating pairs that collapse together and
  dropping fully-internal edges instead of emitting self-loops. The box claims
  the lane slot the frame held, so expanding and collapsing does not walk the
  instance sideways. Dependency resolution, promote-readiness and lane-tail
  detection continue to scan every lane-visible node, so a hidden member never
  makes its upstream look like a tail.
- Collapse is **view state only**: a `Record<sessionId, instanceId[]>` under
  `miniclaw.collapsedTemplateInstances` in `localStorage`, following the
  `miniclaw.panelState` reader/writer pattern. It is keyed by session because
  instance ids are only unique within a project. Nothing about it is sent to the
  backend and no node field carries it.
- Output semantics (§4.3) need no declaration: a member with no downstream
  *inside* the instance is a sink, and an outside consumer is exactly what makes
  it an output rather than disqualifying it. Attaching downstream of an instance
  — the box's ↘ affordance, or the side panel's 新建下游节点 — expands
  `scheduled_deps` to **all** sinks. The expanded view keeps every per-node
  affordance, so connecting to one internal node stays possible: the black box
  is the default reading, not a boundary.
- Per §8 there is no nesting. `parent_instance_id` is carried on the record type
  and is always null; no nested rendering exists.
- `frontend/tests/canvas-layout.test.ts` gains 13 template cases covering
  clustering, hint override, reversed node order, cross-lane degradation, lane
  sizing, sink detection (including resume edges and external consumers), the
  collapsed box with edge redirection and progress rollup, positional stability
  across collapse, and the non-template regression guard. Existing assertions
  were only added to, never weakened.

### Pending

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
  tags.json                       # global ordered project tags
  contextspace/...                # see §4
  ```
- Atomic JSON writes (tmp + rename). Each node has one event writer; different
  nodes may write their independent records concurrently, while project-wide
  virtual-DAG reconciliation is narrowly serialized.
- The current store schema is version **10**. Version 9 was consumed by
  `d31f7c2` for `Node.rev`; version 10 adds project `tag_ids`. The older design
  note saying this change was 8→9 is historical. Adding any `Project` field has
  two inseparable costs: add the defaulted field and advance the store schema in
  the same commit. `Project` forbids extra fields, while `list_projects` skips
  records that fail validation, so writing a new field without the version gate
  would make an older build silently omit the whole project.
- Global tags persist atomically in `$MINICLAW_HOME/tags.json` as an ordered v1
  collection with stable ids, unique case-insensitive names, a bounded palette,
  and a 32-tag limit. Project records retain ordered tag-id references; unknown
  ids are dropped on load, and deleting a tag strips it from every project.
  Cross-machine `tags.json` conflicts follow the same v1 policy as
  `schema.json`: sync aborts without selecting either side, and the user must
  resolve them manually. An id-based union is deferred unless actual use shows
  conflicts are frequent.
- `last_activity_at` is **not** a `Project` field and is never written to
  `project.json`. It is a `Store` in-memory derived index over every host's
  nodes, using the maximum of each node's `finished_at`, `started_at`, and
  `created_at`, with project `created_at` as the no-node fallback. Store
  construction performs one full rebuild, `NodeRunner._transition` updates it
  on started/finished transitions, and a successful sync rebuilds it with the
  same node scan. This expresses “whoever last touched the project” across all
  hosts while avoiding a git-tracked cache write on every transition.
- The persistence and migration-backfill paragraphs of design §1.5 are
  superseded by that in-memory index. The still-valid parts are the discrete
  transition update points rather than every `update_node`, and frontend sort
  fallback `last_activity_at ?? created_at`. Because every value can be rebuilt
  from node timestamps, persisting it would only add synchronization and merge
  conflicts for a cache.
- Registry initialization repairs persisted active nodes left by a previous
  process to cancelled terminal records, including framework stub previews,
  without blocking other projects on a malformed entry; queued nodes remain
  pending and resume scheduling after startup.
- Store schema v7 writes one canonical shape: only `model_preset_id`
  persists provider selection, only `provider_session_id` persists
  provider conversation identity, and ContextSpace/language selections
  live in typed Project fields. Runtime loading accepts only this canonical
  shape; v5 added the defaulted node artifact manifest. The v6 migration moves
  injected skill plugs to principles, rewrites live settings/bindings/templates
  and `principle_edit`, leaves immutable bundle snapshots untouched, and adds
  the native skill namespace. Startup backs up affected records first. A
  rewrite miss is visible, not silent: a stale `skills.<slug>` id resolves
  against the new native-skill library, fails, and surfaces as a missing-skill
  flag on the next launch instead of misinjecting.
- Explicitly shared projects partition durable host-owned state under
  `projects/<pid>/hosts/<mid>/`. Nodes, layout, and git aliases are written only
  by their owning host, while shared `project.json` retains cross-device
  identity and planspace visibility. `hosts/<mid>/local.json` is gitignored and
  is the only place a shared project stores that device's absolute root path.
  `planspace_view` intentionally remains shared; canvas layout is host-local.
- A shared host records `hosts/<mid>/head.json` immediately before an explicit
  sync commit. The synchronized snapshot contains HEAD, branch, capture time,
  and a dirty boolean; peer displays always retain the capture time rather than
  presenting it as live state. The Git surface classifies referenced commits as
  `live`, `peer`, `unfetched`, `stale`, or `unverified`, returns stable host
  columns and nearest referenced parent SHAs, and the canvas renders those
  columns without fabricating peer worktree ghosts. Default peer rows are
  placed after their visible parents. Merge commits can expose multiple edges,
  but overlapping merge-edge routing remains a known limit.
- Foreign virtual work is claimed by copying its task intent into a new local
  queued node with `promoted_from`, then writing
  `hosts/<local-mid>/claims/<vid>.json`. The source node is never modified.
  Pending principle and skill selections move into the claimed node's launch
  snapshot, and retrying a claim on the same host returns the existing local
  node. Concurrent claims on different hosts are detected and shown with their
  resulting node ids; they are not prevented because synchronized metadata
  cannot provide a truthful cross-host lock. Dependency launch guidance marks
  foreign-host previews and warns that their absolute paths and environment
  details belong to that host.
- Schema v12 prepartitions every durable project owned by the current machine:
  nodes and Git aliases live under `hosts/<owner-mid>/`, while checkout paths
  and canvas layout move into gitignored `local.json` and host-local
  `layout.json`. `project.json.sharing` is now only a policy flag; directory
  shape determines storage layout. This also prevents a device-native
  project's absolute checkout path from syncing to another machine. Foreign
  projects and temporary projects are not migrated by the local device.
- The owner partition records the repository root fingerprint during migration
  or project creation. A repository with no commit remains partitioned but not
  shareable; the existing pre-sync callback fills the fingerprint after its
  first commit. Once ready, enabling sharing is a project-level flag flip that
  any device may perform, including a non-owner device. It no longer requires
  the owner process, an idle runner, or a live owner checkout. A second device
  still must explicitly bind a checkout whose root commit matches the recorded
  fingerprint. Temporary projects remain ineligible. Removing a host binding,
  disabling sharing, and ownership transfer remain deferred.
- Creation of sharing requests has been retired: the Registry method, HTTP
  endpoint, frontend API, and request button are absent. During the transition,
  v11 records under `sharing-requests/<pid>/<rid>/` remain readable and their
  existing accept/reject/cancel endpoints remain available. Status is derived
  on read, so any historical pending or rejected request becomes `fulfilled`
  once another device directly enables sharing. This compatibility path can be
  removed after synchronized stores no longer contain open request records.
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
  both-non-empty bootstrap is refused. Shared projects permit multi-host
  writes only through disjoint `hosts/<mid>/` subtrees; global singleton files
  such as `config.json` and ContextSpace manifests retain the existing merge
  behavior.
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

- SQLite migration. The cross-project query that used to be this item's
  trigger ("list all nodes in `awaiting_human_input` across the workspace")
  now ships as `GET /active-nodes` on the JSON store, so it no longer
  motivates the migration. `ActiveNodesIndex` (`backend/miniclaw2/active_nodes.py`)
  keys a small per-node fact cache on each record's mtime and size and
  re-parses only changed files; a full parse of every `node.json` costs
  roughly 1ms per node, so a warm sweep of a 358-node store runs in ~12ms
  against ~450ms for the naive version. Deferred until a query arrives that
  this pattern cannot serve — one needing ordering or filtering across all
  nodes rather than the small non-terminal subset.
- Removing or replacing a shared host binding, including archival or transfer
  of its `hosts/<mid>/nodes/` ownership. Device-native projects still have no
  adopt-project ownership transfer.
- Compaction of terminal sharing-request records. They stay on disk as history
  and, once their project is gone, as `orphaned` entries; no retention policy
  removes them yet.
- Sync retention/compaction — `events.jsonl` transcripts grow the store
  repo monotonically; archiving, compaction, or LFS is deferred until
  repo size hurts.
- Encryption at rest (git-crypt/age) for partially trusted remotes.
- Workspace `outputs/<nid>/` stays local to the native project machine;
  its explicitly published, size-capped subset lives under durable node
  metadata and syncs with the project subtree.
- Cross-machine live streaming (backend-to-backend event relay); viewer
  freshness beyond pull-on-sync is a different feature.
- Merging two non-empty stores at bootstrap remains unsupported. Multi-host
  writing is available only to explicitly shared projects, where each host
  writes its own `hosts/<mid>/` subtree. Device-native projects retain the
  single-native-machine convention. This partitioning does not prevent two
  hosts from independently claiming equivalent virtual work; duplicate claims
  are intentionally detected after synchronization rather than locked.


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
  `PATCH /global-state/code-review`,
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
