# MiniClaw2 → native-CLI parity proposal

> **Status note.** `DESIGN.md` is now the source of truth for the
> long-term architecture; this doc remains as a punch list of
> CLI-parity gaps. `DESIGN.md` Phase 0 (the spine) is landed and
> swept up three of the cheap items below — they are marked **✓** in
> place. A provider layer now supports Claude by default and an initial
> Codex app-server adapter per session.

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
first turn. MiniClaw2 passes only `cwd` and `model`.

| Native CLI loads | MiniClaw2 |
|---|---|
| `~/.claude/CLAUDE.md`, `<repo>/CLAUDE.md`, nested CLAUDE.md merged into system prompt | none |
| `~/.claude/projects/.../memory/MEMORY.md` + memory files | none |
| `.claude/settings.json` + `.claude/settings.local.json` (allow/deny, env, hooks, MCP) | none |
| `.claude/agents/*.md` (custom subagents) | none |
| `.claude/skills/*` and bundled skills (`frontend`, `init`, `review`, …) | none — `AskUserQuestion` & `ExitPlanMode` work only because the SDK still emits them |
| `.mcp.json` MCP servers | none |
| `~/.claude/keybindings.json` | n/a (web UI) |

This is the single biggest source of behavioral drift between the two.

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
4. **No tool-output rendering.** `ToolActivity` shows a start/end dot
   plus truncated input only. Edit diffs, Bash stdout, Read content are
   invisible. Native CLI renders diffs and command output inline.
5. **Streaming granularity.** Translation still yields each `TextBlock`
   whole. CLI streams per-token deltas via partial messages.
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

1. **~ Partially closed.** `Project` / `Node` / `HumanGate` now persist
   to disk under `$MINICLAW_HOME` (`store.py`). Page reload still
   drops UI state because the WebSocket reconnect-replay endpoint
   that consumes `events.jsonl` is not yet wired (Phase 1).
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
- [ ] **Render tool I/O.** Add an optional `result` field to `Activity`
  (truncated, ~4 KB). Render Edit as a diff, Bash as stdout, Read as
  a code block in `ToolActivity`. Surface `is_error` distinctly.
- [ ] **Markdown rendering** for assistant text — `react-markdown` plus
  a syntax highlighter. Current `whitespace-pre-wrap` mangles code
  blocks.
- [ ] **Per-token streaming.** Switch to the SDK's partial-message
  stream if available; otherwise chunk `TextBlock.text` ourselves.
- [ ] **Reconnect replay.** Consume `events.jsonl` on WS reconnect:
  client sends `(node_id, last_seq)`, backend tails the JSONL since
  `last_seq` then attaches to the live stream. The JSONL is already
  written; only the endpoint and a tiny client-side `seq` tracker
  are missing.

### Phase 2 — Match the CLI's "what's loaded" contract

Closes most of the behavioral drift in (A).

- **CLAUDE.md merging.** Walk from `cwd` up, plus `~/.claude/CLAUDE.md`,
  concatenate into the system prompt — or use the SDK's
  `setting_sources=["project", "user"]` option, which is the right
  knob.
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
- Phase 1 remaining items are mostly local edits — no schema change,
  no migrations, safe to land incrementally. The reconnect-replay
  item is the only one that touches the wire protocol.
- Phase 2 (`PROPOSAL.md` numbering — on-disk context loading) extends
  `runner._build_options` and adds a small config loader; no
  protocol change. This now also feeds `ContextBundle` records under
  the DESIGN model.
- Phase 3 (session/project UI + queue) is no longer the largest
  commit; the persistence layer is already in. What remains is the
  workspace UI, project switcher, settings surface, and a queue for
  user messages while a node is running.
- Phase 4 items are independent of each other and can be picked off
  individually once Phase 3 lands.
