# MiniClaw2 → native-CLI parity proposal

The current wrapper is a thin slice over `claude-agent-sdk`: one `CCAgent`
per session, SDK messages translated into a six-event WebSocket
protocol, and three interaction dialogs (permission / ask-user / plan).
It works, but the same prompt run in MiniClaw2 vs. the `claude` CLI in
the same directory will behave noticeably differently because almost
none of the on-disk context the CLI reads is loaded here.

This doc inventories the gaps and proposes a four-phase plan.

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

1. **Plan mode is broken on the happy path.** `agent.py:281-284` returns
   `PermissionResultDeny(interrupt=True)` on approve+clear. That tears
   the turn down instead of switching the SDK to `acceptEdits` and
   continuing. The "Approve & execute" button is currently a dead end.
2. **No interrupt button.** Protocol carries `Interrupt`, but
   `App.tsx` never sends it.
3. **No `@file` / `!cmd` / image-paste** input affordances.
4. **No tool-output rendering.** `ToolActivity` shows a start/end dot
   plus truncated input only. Edit diffs, Bash stdout, Read content are
   invisible. Native CLI renders diffs and command output inline.
5. **Streaming granularity.** `_translate` yields each `TextBlock`
   whole. CLI streams per-token deltas via partial messages.
6. **`ThinkingBlock` dropped silently** (`agent.py:206-207`). CLI
   surfaces these as collapsible reasoning.
7. **`AskUserQuestion` UI** lacks previews, multiSelect, and the
   "Other" free-text fallback.
8. **Permission dialog** lacks `updated_input` editing, allow-always
   scoping (session / project), and `suggestions` rendering — the
   protocol carries `suggestions: list[Any]` but the UI ignores it.

### C. Session / persistence

1. Sessions live in-memory (`session.py`). Reload the page → lose
   everything. CLI auto-saves transcripts under `~/.claude/projects/…`.
2. Only one concurrent turn per session (`app.py:84`); no queue.
3. No session list / switcher UI. `App.tsx` always creates a fresh
   session on mount.
4. `cwd` / `model` are settable via REST POST but not surfaced in the
   UI.
5. SDK `resume` is wired (`agent.py:314-315`) but never used because
   the wrapper doesn't expose "open existing session by id".

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

- `ClaudeSDKClient` is created **per turn** (`agent.py:107`) rather
  than per session. Each turn pays connection + auth + CLAUDE.md
  re-load. `resume` papers over conversation continuity but throws
  away tool-permission state, MCP connections, etc.
- Pending-future cleanup in `finally` (`agent.py:152-155`) can race
  with `can_use_tool` callbacks fired late by the SDK.
- `_tool_queue` is an attribute used by the closure created in
  `_make_can_use_tool`; only safe because turns are serialized.
  Concurrency in (C.2) requires reworking.

---

## 2. Phased implementation plan

Each phase lands as a usable improvement on its own.

### Phase 1 — Make the existing surface actually work

Small, high-value fixes within the current architecture.

- **Fix plan-mode happy path.** Approve+execute returns
  `PermissionResultAllow(updated_permissions=[setMode acceptEdits])`
  instead of `Deny(interrupt=True)`. `clear_context` becomes an
  optional "Approve in a fresh context" flow.
- **Wire `Interrupt`.** Stop button in `App.tsx` while `streaming`;
  sends `{type:"interrupt"}`.
- **Render tool I/O.** Add an optional `result` field to `Activity`
  (truncated, ~4 KB). Render Edit as a diff, Bash as stdout, Read as
  a code block in `ToolActivity`. Surface `is_error` distinctly.
- **Markdown rendering** for assistant text — `react-markdown` plus a
  syntax highlighter. Current `whitespace-pre-wrap` mangles code
  blocks.
- **Surface `ThinkingBlock`** as a collapsible block.
- **Per-token streaming.** Switch to the SDK's partial-message stream
  if available; otherwise chunk `TextBlock.text` ourselves.

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

- **Persistent session store.** Replace `SessionRegistry` with SQLite
  or JSONL files under `~/.miniclaw2/sessions/<id>/`. Record `cwd`,
  `model`, `sdk_session_id`, transcript of WS events, pending
  interaction state.
- **Session list / switcher UI.** Sidebar listing sessions; click to
  resume — `POST /sessions` with the saved `sdk_session_id` to attach
  via SDK `resume`.
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

- Phase 1 is mostly local edits — no schema change, no migrations,
  safe to land incrementally.
- Phase 2 requires extending `_build_options` and adding a small
  config loader; no protocol change.
- Phase 3 is the largest commit: persistence schema, session UI, and
  the lifetime refactor for `ClaudeSDKClient` should land together
  because the client-lifetime change interacts with the queue.
- Phase 4 items are independent of each other and can be picked off
  individually once Phase 3 lands.
