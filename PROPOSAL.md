# MiniClaw2 → native-CLI parity proposal

> **Status note.** `DESIGN.md` is now the source of truth for the
> long-term architecture; this doc remains as a punch list of
> CLI-parity gaps. `DESIGN.md` Phase 0 (the spine) is landed and
> swept up three of the cheap items below — they are marked **✓** in
> place. A provider layer now supports Claude by default and an initial
> Codex app-server adapter per session. **Phase 1 chat polish (tool
> I/O rendering, markdown rendering, reconnect replay) has since
> landed** as a follow-up pass; per-token streaming is the only
> Phase 1 item still pending (deferred until the SDK exposes partial
> messages).

The current default provider is a slice over `claude-agent-sdk` with a
per-node state machine, a JSONL/JSON store under `$MINICLAW_HOME`, and
three interaction dialogs (permission / ask-user / plan). It works, but
the same prompt run in MiniClaw2 vs. the `claude` CLI in the same
directory will still behave noticeably differently because almost none
of the on-disk context the CLI reads is loaded here.

Codex is available as an initial provider through `codex app-server`
JSON-RPC. It maps Codex text/reasoning/tool/activity/usage events and
server requests (`requestUserInput`, command/file/permission approvals)
onto MiniClaw2's existing WebSocket protocol.

This doc inventories the remaining gaps and the four-phase plan.

---

## 1. Gap analysis

### A. Context the CLI loads automatically that this wrapper ignores

Native `claude` builds the agent's environment from disk before the
first turn. MiniClaw2 takes a provider-neutral shortcut: it loads
`<project_root>/CONTEXT.md` (if present) and injects it into both
Claude (`system_prompt.append`) and Codex (prepended `turn/start`
input on fresh threads). Vendor-specific files are still ignored.

| Native CLI loads | MiniClaw2 |
|---|---|
| `~/.claude/CLAUDE.md`, `<repo>/CLAUDE.md`, nested CLAUDE.md merged into system prompt | provider-neutral `<repo>/CONTEXT.md` instead (project-root only, strict filename) |
| `~/.claude/projects/.../memory/MEMORY.md` + memory files | none |
| `.claude/settings.json` + `.claude/settings.local.json` (allow/deny, env, hooks, MCP) | none |
| `.claude/agents/*.md` (custom subagents) | none |
| `.claude/skills/*` and bundled skills (`frontend`, `init`, `review`, …) | none — `AskUserQuestion` & `ExitPlanMode` work only because the SDK still emits them |
| `.mcp.json` MCP servers | none |
| `~/.claude/keybindings.json` | n/a (web UI) |

`CONTEXT.md` closes the largest single source of behavioral drift
(missing project conventions / repo-specific instructions). The
remaining items are vendor-specific settings/tools and stay TBD.

### B. Tool / interaction surfaces

1. **✓ Plan mode happy path fixed.** The Claude provider returns
   `Allow(updated_permissions=[setMode acceptEdits])` on approve, so
   the SDK switches mode and the turn continues. The `clear_context`
   knob is accepted but no-op; a future "Approve in fresh context"
   affordance is a separate piece of work.
2. **✓ Interrupt wired.** App.tsx renders a Stop button while
   `streaming` and sends `{type: "interrupt"}`; the runner transitions
   the node to `cancelled` and the on-disk state reflects it.
3. **No `@file` / `!cmd` / image-paste** input affordances.
4. **✓ Tool I/O rendering landed.** `Activity` now carries `result`
   (≤4 KB) and `result_kind ∈ {stdout, diff, text, json}`. Claude
   extracts `ToolResultBlock.content`; Codex pulls `aggregatedOutput`
   for commandExecution and renders file-change details as a provider
   diff only when a real patch/diff is supplied, otherwise JSON/text.
   `ToolActivity.tsx` renders the result in a
   collapsible `<details>` (open-by-default on failed) with diff
   coloring.
5. **Streaming granularity (Claude only).** Claude provider still
   yields each `TextBlock` whole — the pinned `claude-agent-sdk>=0.1.40`
   exposes no partial-message option. Codex already streams per-delta.
   Revisit when the SDK is bumped.
6. **✓ `ThinkingBlock` surfaced** as a new `thinking` server event;
   frontend renders a collapsible `<details>` block above the
   assistant text.
7. **~ `AskUserQuestion` / Codex `requestUserInput` UI** supports
   provider-neutral question ids, multi-select, "Other", and secret
   inputs. It still lacks richer previews.
8. **Permission dialog** lacks `updated_input` editing, allow-always
   project scoping, and `suggestions` rendering. Session-scoped allow
   is wired for Codex as `acceptForSession`.

### C. Session / persistence

1. **~ Mid-session WS drops now recoverable.** `Project` / `Node` /
   `HumanGate` persist to disk under `$MINICLAW_HOME` (`store.py`).
   The reconnect-replay endpoint that consumes `events.jsonl` is
   wired: `node_started` server event carries the active node id;
   `replay_request {node_id, since_seq}` client envelope drives
   `store.replay_events`, and the new socket re-attaches to live
   project broadcasts. Hard page reload still drops UI state
   because `App.tsx` creates a fresh session on mount — that's a
   session-switcher concern (Phase 3), not chat polish.
2. Only one concurrent node per project (`registry.ProjectRuntime`);
   no queue.
3. No session/project list / switcher UI. `App.tsx` always creates a
   fresh project on mount.
4. `cwd` / `model` are settable via REST POST but not surfaced in the
   UI.
5. **~ Used implicitly.** Provider resume now fires automatically: each
   new node inherits `provider_session_id` from its predecessor in the
   project (`registry.start_node`). Claude still writes the legacy
   `sdk_session_id`; Codex uses its thread id.

### D. Settings / runtime knobs

- No model picker, permission-mode selector (`default` / `plan` /
  `acceptEdits` / `bypassPermissions`), allowed/disallowed tools, or
  env injection.
- No cost estimate (`Usage` events carry tokens but no $).
- No hooks lifecycle (PreToolUse / PostToolUse / Stop /
  UserPromptSubmit).
- No CronCreate / background tasks / worktree tools — these are
  CC-CLI niceties not in the SDK surface, but noted for completeness.

### E. Backend hygiene

- **✓ Structurally addressed.** `NodeRunner` now owns the state machine
  and persistence only; provider-native IO lives in `providers/claude.py`
  and `providers/codex.py`.
- Provider cleanup can still race with late provider callbacks. The
  state machine resolves any open `HumanGate` as denied when the node
  ends.
- Gate futures live on `NodeRunner`; this is safe because nodes within
  a project are serialized (DESIGN §2.2). Cross-project concurrency is
  fine since each project has its own runner.

---

## 2. Phased implementation plan

Each phase lands as a usable improvement on its own.

### Phase 1 — Make the existing surface actually work

Small, high-value fixes within the current architecture.

- [✓] **Plan-mode happy path** — landed in Phase 0 (`runner.py`).
- [✓] **Wire `Interrupt`** — landed in Phase 0 (Stop button in
  `App.tsx`, runner cancels the SDK reader and transitions the node
  to `cancelled`).
- [✓] **Surface `ThinkingBlock`** — landed in Phase 0 as a new
  `thinking` server event + `<details>` block in `Chat.tsx`.
- [✓] **Render tool I/O.** `Activity` gained `result` (≤4 KB) and
  `result_kind ∈ {stdout, diff, text, json}`. Claude pulls
  `ToolResultBlock.content`; Codex pulls `aggregatedOutput` for
  commandExecution and renders `changes` for fileChange as
  unified-diff-ish text. `ToolActivity.tsx` renders results in a
  collapsible `<details>` (open-by-default on failed) with red/green
  diff coloring.
- [✓] **Markdown rendering** for assistant text — `react-markdown` +
  `remark-gfm` + `rehype-highlight` (github-dark) in `Chat.tsx`. User
  messages stay plain. Hand-rolled `.md-prose` styles in
  `index.css` to avoid the `@tailwindcss/typography` dep.
- [ ] **Per-token streaming.** Deferred. Pinned
  `claude-agent-sdk>=0.1.40` exposes no partial-message option;
  server-side chunking would be cosmetic only. Codex already streams.
  Revisit when the SDK is bumped.
- [✓] **Reconnect replay.** `NodeRunner` emits `node_started` as the
  first event of each turn. Client tracks `(activeNodeId, lastSeq)`
  in `ws.ts`; on reconnect sends `{type: "replay_request", node_id,
  since_seq}`. Backend `app.py` consumes `store.replay_events`
  synchronously before resuming the live tail. 4xxx WS close codes
  (session not found) suppress the auto-reconnect loop. Hard reload
  (new session on mount) is a separate Phase 3 concern.

### Phase 2 — Match the CLI's "what's loaded" contract

Closes most of the behavioral drift in (A).

- [✓] **Provider-neutral `CONTEXT.md`.** A single
  `<project_root>/CONTEXT.md` is loaded by `backend/miniclaw2/context.py`
  and threaded through `AgentProviderContext.system_context`. Claude
  uses `system_prompt={"type":"preset","preset":"claude_code","append":<text>}`;
  Codex prepends to the first `turn/start` input on fresh threads. The
  resolved text is snapshotted on the node and rendered in the side
  panel. Strict filename, project-root only. This is the
  provider-neutral substitute for CLAUDE.md merging.
- **CLAUDE.md merging (deferred).** Native CLI walks from `cwd` up plus
  `~/.claude/CLAUDE.md`. Could be wired via the SDK's
  `setting_sources=["project", "user"]` option later, but `CONTEXT.md`
  covers the project-context use case without provider-format
  negotiation.
- **`.claude/settings.json` + `settings.local.json` loading.** Pass
  `permissions`, `env`, `hooks`, `mcpServers`, `allowedTools`,
  `disallowedTools` into `ClaudeAgentOptions`. SDK accepts most
  directly; the rest map to `additional_directories` / `allowed_tools`.
- **Custom agents.** Read `.claude/agents/*.md` → pass as `agents=`.
- **MCP servers.** Parse `.mcp.json` and pass `mcp_servers=`.

### Phase 3 — Session lifecycle that survives a reload

- **Persistent session store.** Continue extending the JSON/JSONL store
  under `$MINICLAW_HOME`. Record `cwd`, `model`, provider ids,
  transcript of WS events, pending interaction state.
- **Session list / switcher UI.** Sidebar listing sessions; click to
  resume — `POST /sessions` with the saved provider ids to attach via
  Claude `resume` or Codex `thread/resume`.
- **`ClaudeSDKClient` lifetime = session lifetime**, not turn. Hold
  the client in `CCAgent` across turns; close only on session
  deletion. Keeps MCP connections, permission state, skill caches warm.
- **Settings UI:** model picker, permission-mode dropdown, cwd
  selector, tool allowlist.
- **Queue user messages** while a turn is in-flight instead of erroring.

### Phase 4 — CLI-parity affordances

- **Slash commands**: at minimum `/clear`, `/compact`, `/model`,
  `/cwd`, `/permissions`. Pure frontend interceptors that translate
  into REST/WS calls; not actual model-side commands.
- **`@file` references**: client-side parser; replace with file
  contents up to a size cap (or pass through if the SDK learns to do
  this natively).
- **`!cmd`**: `/exec` REST endpoint that streams stdout into the next
  user message, gated behind permission.
- **Image paste / attachments**: extend `UserMessage` with
  `attachments: [{type:"image", data:base64}]`, forwarded to the SDK
  as content blocks.
- **Hooks**: intercept `PreToolUse` / `PostToolUse` callbacks from the
  SDK and shell out to user-configured commands.
- **Cost estimate** in the Usage strip; recompute on each `Usage`
  event using published per-model rates.

---

## 3. Sequencing notes

- Phase 0 of `DESIGN.md` (spine: domain model, store, runner,
  registry) and the three cheap wins above landed together; the
  refactor that DESIGN-Phase-0 demands subsumed what would have been
  the most invasive parts of this proposal's Phase 1 and Phase 3.
- Phase 1 landed in a second pass after Phase 0: tool I/O, markdown,
  and reconnect replay are in. The wire protocol picked up two new
  envelopes (`node_started` server, `replay_request` client) and one
  field extension (`Activity.result` + `result_kind`); no migrations.
   Replay now uses project-level WebSocket observers plus a tested
   replay/live buffer so reconnects keep receiving live events after
   the JSONL gap is replayed. Per-token streaming stays deferred. The
   DESIGN Phase 1 graph shell has also started with read-only
   node/event/diff APIs, explicit `node_updated` events, a single-project
   timeline, and a node detail side panel.
- Phase 2 (`PROPOSAL.md` numbering — on-disk context loading) took the
  provider-neutral path first: a `CONTEXT.md` loader
  (`backend/miniclaw2/context.py`) feeds
  `AgentProviderContext.system_context`, which Claude appends to its
  preset and Codex prepends to fresh-thread input. The resolved text
  is snapshotted on the node (`system_context_snapshot`). No protocol
  change. Vendor-specific loading (CLAUDE.md walk, `.claude/settings`,
  agents, MCP) is deferred behind this slice.
- Phase 3 (session/project UI + queue) is no longer the largest
  commit; the persistence layer is already in. What remains is the
  workspace UI, project switcher, settings surface, and a queue for
  user messages while a node is running.
- Phase 4 items are independent of each other and can be picked off
  individually once Phase 3 lands.
