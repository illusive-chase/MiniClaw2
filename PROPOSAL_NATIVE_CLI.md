# Proposal: Remove `claude-agent-sdk`, Drive the Native `claude` CLI Directly

**Status:** proposed
**Author:** design pass, 2026-07-03
**Motivating reference:** `deepcoldy/botmux` — production system driving native `claude`, `codex`, `gemini`, etc. inside PTYs behind an IM bridge.

## 1. Goal

Replace the `claude-agent-sdk` Python dependency with a MiniClaw2-owned adapter that spawns the native `claude` binary in a pseudo-terminal, types prompts into its TUI, observes results by tailing Claude Code's on-disk JSONL transcript, and intercepts `AskUserQuestion` via a `PreToolUse` hook. The `codex` provider is out of scope for this change and stays as-is.

## 2. Why

- **Feature ceiling.** The SDK exposes a curated subset of Claude Code. Anything the CLI ships (skills / plugins, keybindings, slash commands, new tools) becomes accessible without a Python-package release cycle.
- **Debuggability.** JSONL transcripts land in `~/.claude/projects/<hash>/<sid>.jsonl` — the same place a user's own `claude` sessions do. Handoff, replay, and postmortem are trivial (`claude --resume <sid>`).
- **Fewer moving parts.** One binary, one process, one on-disk contract. No SDK version drift; no import hazards; no `CLIConnectionError`/`CLINotFoundError` layer.
- **Alignment with reality.** Users already have `claude` installed. We were paying to re-marshal it through Python.

## 3. Decisions (locked)

| Question | Choice | Rationale |
|---|---|---|
| Permission model | **Bypass all** (`--dangerously-skip-permissions`) | Match botmux. Blast radius is bounded by the project cwd; MiniClaw2 already assumes agents run inside a workspace they own. |
| Plan mode | **Disabled** (`--disallowed-tools EnterPlanMode,ExitPlanMode`) | Match botmux. `GateSubtype.PLAN_APPROVAL` and the `plan-mode-approval` bundled template become dead code — removed in this migration. |
| Per-tool gating | **Removed** | With bypass mode, no `can_use_tool`-style callback fires. `GateSubtype.PERMISSION` becomes dead code — removed. |
| Ask handling | **Intercept via `PreToolUse` matcher=`AskUserQuestion`** | The only gate we keep. Routes through `GateSubtype.ASK_USER`, preserving MiniClaw2's WebSocket UX unchanged. |
| Config isolation | **Use user's `~/.claude`** (idempotent merge, structure-matched) | Simpler; matches botmux; user sees transcripts alongside their own. |
| OS support | **Linux + macOS** | Full JSONL/PTY behavior. `/proc`-based session-rotation checks are Linux-only; macOS falls back to `cwd` equality. |
| Windows | **Not supported** | Out of scope. Node-pty analogue is `pywinpty` — deferrable. |
| Hook bridge transport | **HTTP over `127.0.0.1`** with bearer token | Reuses FastAPI. Simple, blocking POST fits the hook subprocess's synchronous stdin→stdout contract. |
| PTY library | **`ptyprocess`** | Small, well-maintained, Unix-only (matches our OS target). Not `pexpect` — we don't need expect-style pattern matching. |

## 4. Architecture

```
Node run
  │
  ▼
NodeRunner.run() ── calls ──▶ ClaudeNativeProvider.run(ctx)
                                    │
                                    ├─ spawn `claude` in ptyprocess.PtyProcess
                                    │    cwd = project.root_path
                                    │    args = [--session-id | --resume, --model, --dangerously-skip-permissions,
                                    │            --disallowed-tools EnterPlanMode,ExitPlanMode,
                                    │            --append-system-prompt <system_context>,
                                    │            --settings '{"skipDangerousModePermissionPrompt": true,
                                    │                          "permissions": {"defaultMode": "bypassPermissions"}}']
                                    │    env  = os.environ + {MINICLAW_HOOK_URL, MINICLAW_HOOK_TOKEN,
                                    │                          MINICLAW_NODE_ID, MINICLAW_PROJECT_ID}
                                    │
                                    ├─ resolve jsonl_path from realpath(cwd) + session_id
                                    ├─ wait for SessionStart hook signal (see §9) → session ready
                                    │
                                    ├─ write first prompt via TUI: type text, soft-newlines, throttled Enter
                                    │    confirm via JSONL delta (marker "role":"user","content":")
                                    │
                                    ├─ drain JSONL incrementally in a background asyncio.Task,
                                    │    parse events, translate to AgentProviderEvent, yield to caller
                                    │
                                    └─ handle Ask events out-of-band:
                                          FastAPI /hook/ask ── request_gate ── frontend WebSocket ── answer

Global (installed once at daemon start):
  ~/.claude/settings.json
    hooks.PreToolUse[matcher=AskUserQuestion].command = "python -m miniclaw2.claude_hook_bridge"
    hooks.SessionStart.command                        = "python -m miniclaw2.claude_hook_bridge --session-ready"
```

The provider's `.run()` still yields `AgentProviderEvent`s — the `NodeRunner`, `events.py`, and frontend contract stay untouched.

## 5. New Python modules

All new code lives under `backend/miniclaw2/providers/claude_native/` (package). Package layout:

```
providers/
    __init__.py
    base.py                      ← unchanged
    codex.py                     ← unchanged
    claude.py                    ← rewritten (see §5.1)
    claude_native/
        __init__.py              ← exports ClaudeNativeSession
        spawn.py                 ← §7 (PTY spawn, args, env)
        input.py                 ← §8 (typing, submit-key, JSONL confirm)
        transcript.py            ← §9 (JSONL drain, event mapping)
        session_resolver.py      ← §9 (sessions/<pid>.json resolver, /proc scan on Linux)
        keybindings.py           ← §8 (parse ~/.claude/keybindings.json)
        paths.py                 ← path helpers (project-hash, jsonl, pid-state)
        hook_installer.py        ← §10 (idempotent settings.json merge)
```

Separate top-level module:

```
backend/miniclaw2/
    claude_hook_bridge.py        ← §10 (subprocess entrypoint invoked by Claude)
```

### 5.1 `providers/claude.py` — rewritten

Public class name (`ClaudeProvider`) and Protocol conformance stay. Full new sketch:

```python
class ClaudeProvider:
    name = "claude"

    def __init__(self) -> None:
        self._session: ClaudeNativeSession | None = None

    async def run(self, context: AgentProviderContext) -> AsyncIterator[AgentProviderEvent]:
        try:
            self._session = ClaudeNativeSession(
                cwd=context.project.root_path,
                model=self._resolve_model(context),
                session_id=self._resume_id(context),
                system_prompt_append=context.system_context,
                node_id=context.node.id,
                project_id=context.project.id,
            )
            await self._session.start()
            yield AgentProviderEvent(kind="session", session_id=self._session.cli_session_id)

            result = await self._session.send(context.turn_text())
            if not result.submitted:
                yield AgentProviderEvent(kind="error", error=f"submit failed: {result.failure_reason}")
                return

            async for event in self._session.stream_events():
                if event.kind == "ask":
                    async for gate_ev in self._route_ask(event, context):
                        yield gate_ev
                else:
                    yield event
        except ClaudeNativeError as exc:
            yield AgentProviderEvent(kind="error", error=str(exc))
        finally:
            if self._session:
                await self._session.close()

    async def interrupt(self) -> None:
        if self._session:
            await self._session.interrupt()
```

### 5.2 `providers/claude_native/__init__.py` — public API

```python
class ClaudeNativeSession:
    def __init__(
        self,
        cwd: str,
        model: str | None = None,
        session_id: str | None = None,        # None ⇒ fresh; else ⇒ --resume
        system_prompt_append: str = "",
        node_id: str,
        project_id: str,
        data_dir: Path = Path.home() / ".claude",
    ) -> None: ...

    async def start(self) -> None: ...
    """Spawn `claude`. Compute jsonl_path from realpath(cwd) + session_id.
    Wait up to 45s for SessionStart hook signal (see §9)."""

    async def send(self, prompt: str) -> SubmitResult: ...
    """Type prompt into TUI (soft-newlines, throttled). Confirm via JSONL marker.
    Retries up to 3x + fingerprint fallback."""

    async def stream_events(self) -> AsyncIterator[AgentProviderEvent]: ...
    """Yield AgentProviderEvents drained from JSONL until Claude idles."""

    async def interrupt(self) -> None: ...
    """Send Ctrl-C (0x03) to the PTY. Idempotent."""

    async def close(self) -> None: ...
    """Kill PTY child (SIGTERM → SIGKILL after 3s)."""

    @property
    def cli_session_id(self) -> str: ...
    """Latest observed session id (updates on rotation)."""
```

`SubmitResult` is a small dataclass mirroring botmux:

```python
@dataclass(slots=True)
class SubmitResult:
    submitted: bool
    cli_session_id: str | None = None
    failure_reason: str | None = None
    recheck: Callable[[], Awaitable[bool]] | None = None
```

## 6. Removed / renamed

| Item | Action |
|---|---|
| `claude-agent-sdk` in `pyproject.toml` | **Remove.** Add `ptyprocess>=0.7`. |
| `GateSubtype.PERMISSION` | **Remove.** Kills per-tool gating (bypass mode has none). |
| `GateSubtype.PLAN_APPROVAL` | **Remove.** Plan mode disabled at spawn. |
| `templates/bundled/plan-mode-approval/` | **Remove.** Verifier script depends on plan approval events. |
| `Node.sdk_session_id` field | **Rename** to `cli_session_id`. Migration: on load, if `sdk_session_id` present and `cli_session_id` absent, copy. |
| `Node.provider_session_id` | **Keep** (still authoritative session pointer for resume). |
| Frontend plan-approval UI | **Remove** (dead code once `GateSubtype.PLAN_APPROVAL` is gone). Track separately. |
| `providers/claude.py` `_make_can_use_tool` | **Remove.** No per-tool callback. |
| `providers/claude.py` `minimal_mode` branch | **Simplify.** Minimal mode still restricts tools — but via `--allowed-tools` at spawn, not a callback (see §7). |
| `context.request_gate_handler` | **Keep.** Still called for `ASK_USER`. |

## 7. Native CLI spawn contract

**Binary:** `claude` (resolved via `shutil.which('claude')` at first spawn; cached).

**Working directory:** `project.root_path`. Passed to `ptyprocess.PtyProcess.spawn(argv, cwd=...)`.

**Realpath rule** — critical: the JSONL path uses `realpath(cwd)` not `cwd`. Symlinked project roots (e.g. `/home/user/proj → /data00/home/user/proj`) will otherwise write to a project hash we never watch. Implemented in `paths.py`:

```python
def project_hash(cwd: str) -> str:
    resolved = os.path.realpath(cwd)
    return re.sub(r"[^A-Za-z0-9-]", "-", resolved)

def jsonl_path(cwd: str, session_id: str, data_dir: Path) -> Path:
    return data_dir / "projects" / project_hash(cwd) / f"{session_id}.jsonl"

def pid_state_path(pid: int, data_dir: Path) -> Path:
    return data_dir / "sessions" / f"{pid}.json"
```

**Argument construction:**

```python
def build_args(
    *,
    session_id: str,          # botmux-owned UUID or resumed
    resume: bool,
    model: str | None,
    system_prompt_append: str,
    tool_allowlist: list[str] | None = None,
) -> list[str]:
    args: list[str] = []
    if resume:
        args += ["--resume", session_id]
    else:
        args += ["--session-id", session_id]
    if model:
        args += ["--model", model]
    args.append("--dangerously-skip-permissions")
    args += ["--settings", json.dumps({
        "skipDangerousModePermissionPrompt": True,
        "permissions": {"defaultMode": "bypassPermissions"},
    })]
    disallowed = ["EnterPlanMode", "ExitPlanMode"]
    args += ["--disallowed-tools", ",".join(disallowed)]
    if tool_allowlist:
        args += ["--allowed-tools", ",".join(tool_allowlist)]
    if system_prompt_append:
        args += ["--append-system-prompt", system_prompt_append]
    return args
```

**Environment injected at spawn:**

```python
env = os.environ.copy()
env["MINICLAW_HOOK_URL"]   = f"http://127.0.0.1:{fastapi_port}/hook/ask"
env["MINICLAW_HOOK_TOKEN"] = hook_token          # rotated per daemon start
env["MINICLAW_NODE_ID"]    = node_id
env["MINICLAW_PROJECT_ID"] = project_id
env["MINICLAW_SESSION_ID"] = session_id
```

The hook subprocess inherits these — that's how it identifies the running Node and reaches FastAPI (§10).

**Terminal size:** `cols=200, rows=50`. Fixed for now — the TUI's soft-wrap doesn't affect the JSONL (which stores raw text). Consider matching frontend viewport later if we surface the PTY buffer to users; not needed for a headless agent.

## 8. PTY input path

**File:** `providers/claude_native/input.py`. Follows botmux's `writeInput` (`src/adapters/cli/claude-code.ts:545-803`) with faithful port to Python.

### 8.1 Typing algorithm

```
split content on '\n'
for each line i:
    if line non-empty: pty.write(line); sleep(throttle)
    if not last line:
        if enter_is_newline_in_keybindings: skip backslash
        else: pty.write('\\'); sleep(throttle)
        pty.write('\r')  # soft-newline
        sleep(throttle)
sleep(submit_delay)     # 500ms; 800ms if content contains image path
pty.write(submit_key_raw)  # '\r' or '\x1b\r' from keybindings
```

- `throttle = 80ms` on the first `send()` after spawn (Ink startup render race), else `30ms`.
- Track first-write per session via a session-instance boolean; no need for WeakSet like botmux (we own the lifecycle).

### 8.2 Submit-key resolution

Read `~/.claude/keybindings.json` (or `<data_dir>/keybindings.json`). Extract the entry with `context: "Chat"`, look up bindings mapped to `chat:submit`. Preference order:

1. `meta+enter` / `alt+enter` → `\x1b\r`
2. `enter` → `\r`
3. Anything else (`ctrl+enter`, `cmd+enter`, `shift+enter`) → **fail fast**. These require Kitty keyboard protocol / modifyOtherKeys; a plain PTY can't send them distinguishably.

If `enter` is remapped to `chat:newline`, fallback to `enter` won't submit — return failure with a clear message rather than a phantom submit.

Env override: `CLAUDE_CODE_SUBMIT_KEY` (matches botmux). Documented but not required.

### 8.3 Submit confirmation via JSONL

After sending the submit key:

```
base_byte = size(jsonl_path) before typing
poll jsonl every 100ms for 800ms:
    read bytes [base_byte, current_size]
    if contains '"role":"user","content":"' OR '"operation":"enqueue"': return True
retry sending submit key up to 2x (3 total), 800ms each poll
if still no marker:
    fingerprint = collapse_whitespace(content)[:30]
    scan siblings in <projects>/<hash>/*.jsonl (mtime > now-60s): fingerprint match?
    scan ALL <projects>/*/<*>.jsonl (mtime > now-60s): fingerprint match?
if still no match: return {submitted: False, recheck: closure}
```

The `recheck` closure lets the caller re-verify after a delay (cold-start hook chains can defer the JSONL append 5–15s past the in-band budget).

**Session-id rotation mid-submit** — port of botmux's `resolveJsonlFromPid`:

```python
def resolve_from_pid(pid: int, expected_cwd: str, data_dir: Path) -> ResolvedSession | None:
    p = pid_state_path(pid, data_dir)
    if not p.exists(): return None
    try: data = json.loads(p.read_text())
    except Exception: return None
    if data.get("pid") != pid: return None
    sid = data.get("sessionId")
    if not sid or not UUID_RE.match(sid): return None
    cwd = data.get("cwd")
    if not isinstance(cwd, str): return None

    proc_start = data.get("procStart")
    if isinstance(proc_start, str):
        live = read_proc_starttime(pid)    # Linux only; None on macOS
        if live is None and sys.platform == "linux": return None
        if live is not None:
            if live != proc_start: return None
            return ResolvedSession(sid, jsonl_path(cwd, sid, data_dir))
    if os.path.realpath(cwd) != os.path.realpath(expected_cwd): return None
    return ResolvedSession(sid, jsonl_path(cwd, sid, data_dir))
```

On macOS `procStart` verification is skipped; `realpath(cwd)` equality is the only guard. Acceptable because MiniClaw2 does not use in-pane `/clear` — each Node is a fresh session; rotation only happens on explicit `--resume`, which is a fresh spawn with a fresh pid file.

## 9. Output / observation

**File:** `providers/claude_native/transcript.py`. Port of `src/services/claude-transcript.ts`.

### 9.1 Incremental drain

```python
def drain(path: Path, from_offset: int) -> tuple[list[dict], int, str]:
    """Return (events, new_offset, pending_tail).
    - Empty result if file missing.
    - If size < from_offset: rotated/truncated → restart from 0.
    - Trailing partial line (no '\n') is not parsed; kept for next drain.
    - Malformed JSON lines are skipped silently."""
```

Wrap in an async watcher that polls every 100ms (no `inotify` — cross-platform, and JSONL writes are chunky):

```python
async def watch_jsonl(self, path: Path) -> AsyncIterator[dict]:
    offset = 0
    idle_ticks = 0
    while not self._cancelled:
        events, offset, _tail = drain(path, offset)
        for ev in events: yield ev
        if not events:
            idle_ticks += 1
            if idle_ticks > IDLE_TICK_LIMIT and self._pty_output_quiescent():
                break
        else:
            idle_ticks = 0
        await asyncio.sleep(0.1)
```

`_pty_output_quiescent()` = no PTY bytes in the last 2 seconds (belt-and-braces; JSONL is authoritative but slow tools might delay).

### 9.2 Event mapping

Claude Code's JSONL event shape (subset):

```
{"type":"user", "message":{"role":"user", "content":"<text>"}, ...}
{"type":"assistant", "message":{"role":"assistant", "content":[<blocks>]}, ...}
{"type":"attachment", "attachment":{"type":"queued_command","prompt":"..."}}
{"type":"summary", ...}
```

Assistant `content` is a list of blocks:
- `{"type": "text", "text": "..."}`
- `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}`
- `{"type": "thinking", "thinking": "..."}`

User content on tool-result lines is an **array**:
- `{"type": "tool_result", "tool_use_id": "...", "content": [...], "is_error": bool}`

The direct submit marker only matches string-content user messages — array-content tool-result lines never false-match.

Mapping to `AgentProviderEvent`:

| JSONL shape | Emitted event |
|---|---|
| `assistant` + `text` block | `TextDelta(text=block.text + '\n' if not already)` |
| `assistant` + `tool_use` block | `Activity(kind='tool', status='start', id=block.id, name=block.name, summary=truncate(block.input))` — cache in `pending_tools` |
| `assistant` + `thinking` block | `Thinking(text=block.thinking)` |
| `user` (array content) + `tool_result` | Pop `pending_tools[tool_use_id]`, set `status='failed'` if `is_error` else `'finish'`, `result=truncate(_flatten(content), 4096)`, `result_kind=_kind_for_tool(...)`. Yield the mutated Activity. |
| `summary` / result event | `Usage(input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, final=True)` |

`_flatten` and `_kind_for_tool` are copied verbatim from current `providers/claude.py` — the diff/stdout/text discrimination stays identical.

### 9.3 Task tool progress

The SDK's `TaskStartedMessage` / `TaskProgressMessage` / `TaskNotificationMessage` correspond to Claude Code's Task tool. In JSONL these appear as tool_use / tool_result blocks with `name="Task"` and a task_id in the input. The current shape emitted (`Activity(kind='agent', status='start|progress|finish|failed', ...)`) is reconstructable from those blocks — treat Task specially in `_translate_tool_use` to emit `kind='agent'` instead of `kind='tool'` and skip caching (Task progress is many events over one tool call).

**Open item:** need to confirm the exact JSONL shape Claude Code writes for Task progress ticks. Verify empirically with a `claude` run that spawns a Task subagent, capture the JSONL, decide shape. Doable in the prototype stage before finalizing this mapping.

### 9.4 Session id capture

- **On start:** first `user` or `assistant` event has `sessionId` — capture, cross-check against the `--session-id` we passed.
- **On rotation:** `resolve_from_pid()` is called any time a `send()` confirmation fails on the pinned jsonl. On a match, update `cli_session_id`, re-pin `jsonl_path`, and emit `AgentProviderEvent(kind="session", session_id=new)`.

### 9.5 Ready signal

Botmux uses a `SessionStart` hook to know when the input box is "genuinely rendered" and gate the first prompt. We install the same hook (§10). When the hook subprocess is called with `--session-ready`, it POSTs to `/hook/session-ready` with the `MINICLAW_SESSION_ID` env var; FastAPI resolves the awaiting session and sets an `asyncio.Event` on it. `ClaudeNativeSession.start()` waits on that event for up to 45s before returning; on timeout, log a warning and proceed anyway (the delay is only a safety belt for pathological startups).

## 10. AskUserQuestion hook

### 10.1 Hook installer

`providers/claude_native/hook_installer.py`. Call once per daemon start (from FastAPI startup event). Idempotent, structure-matched merge into `~/.claude/settings.json`. Match logic mirrors botmux:

```python
def install_hooks(settings_path: Path = Path.home() / ".claude" / "settings.json") -> None:
    entry = ClaudeHookEntry(
        type="command",
        command=f'"{sys.executable}" -m miniclaw2.claude_hook_bridge',
    )
    ready_entry = ClaudeHookEntry(
        type="command",
        command=f'"{sys.executable}" -m miniclaw2.claude_hook_bridge --session-ready',
    )
    settings = _read_json(settings_path) or {}
    hooks = settings.setdefault("hooks", {})

    # PreToolUse matcher=AskUserQuestion
    _replace_group(hooks, "PreToolUse", matcher="AskUserQuestion", entry=entry,
                   is_ours=lambda e: "claude_hook_bridge" in e.get("command", ""))

    # SessionStart (no matcher)
    _replace_group(hooks, "SessionStart", matcher=None, entry=ready_entry,
                   is_ours=lambda e: "claude_hook_bridge" in e.get("command", "") and "--session-ready" in e.get("command", ""))

    _atomic_write(settings_path, json.dumps(settings, indent=2))
```

Matching by substring `claude_hook_bridge` — not exact command equality — so a dev install (`python -m ...` in a virtualenv) and a wheel install don't leave duplicate hooks. Same principle as botmux's `.includes('cli.js') && endsWith('hook <cliId>')` check.

**Atomic write:** `os.replace` on a temp file in the same directory. Concurrent Claude processes read this file every request; a half-written JSON would break unrelated sessions.

### 10.2 Hook subprocess entrypoint

`miniclaw2/claude_hook_bridge.py`. Invoked by Claude Code as a subprocess each time `AskUserQuestion` fires (or on `SessionStart`).

```python
def main() -> int:
    if "--session-ready" in sys.argv:
        return _post_session_ready()
    return _handle_ask()

def _handle_ask() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return _passthrough()

    if payload.get("hook_event_name") != "PreToolUse":  return _passthrough()
    if payload.get("tool_name") != "AskUserQuestion":   return _passthrough()

    url   = os.environ.get("MINICLAW_HOOK_URL")
    token = os.environ.get("MINICLAW_HOOK_TOKEN")
    node  = os.environ.get("MINICLAW_NODE_ID")
    if not (url and token and node):                    return _passthrough()

    try:
        resp = requests.post(
            url,
            json={"node_id": node, "payload": payload},
            headers={"Authorization": f"Bearer {token}"},
            timeout=600,   # long: waits for human answer
        )
        resp.raise_for_status()
    except Exception:
        return _passthrough()

    directive = resp.json()   # server-side already-formed
    sys.stdout.write(json.dumps(directive))
    return 0

def _passthrough() -> int:
    # Empty stdout + exit 0 → Claude falls back to native TUI prompt.
    # CRITICAL: do NOT emit `allow` with empty answers; that submits an empty
    # answer to the tool.
    return 0
```

**Passthrough semantics** are the botmux invariant (`src/core/ask-hook/claude-code.ts:124-130`): if we can't reach the daemon or the payload isn't an Ask event, print nothing so Claude behaves as if no hook were installed. Never invent an empty allow.

**HTTP timeout 600s:** matches typical human-answer latency in practice. If FastAPI has no answer after 10 minutes it should return a 504, at which point we passthrough.

### 10.3 FastAPI endpoint

Add to `app.py` (in the same FastAPI app the frontend already talks to):

```python
@app.post("/hook/ask")
async def hook_ask(request: Request) -> JSONResponse:
    _require_hook_token(request)
    body = await request.json()
    node_id = body["node_id"]
    payload = body["payload"]

    parsed = _parse_ask_payload(payload)          # from claude_native/ask_payload.py
    if parsed is None:
        raise HTTPException(400, "not an ask payload")

    gate = GateRequest(
        subtype=GateSubtype.ASK_USER,
        tool_name="AskUserQuestion",
        tool_input={"questions": parsed.questions},
        provider_request_id=payload.get("hook_request_id"),
    )
    node_runner = _runner_for(node_id)
    response = await node_runner.dispatch_gate(gate)  # same path as before

    directive = _format_ask_directive(response, parsed)
    return JSONResponse(content=directive)

@app.post("/hook/session-ready")
async def hook_session_ready(request: Request) -> JSONResponse:
    _require_hook_token(request)
    body = await request.json()
    session_id = body["session_id"]
    _signal_session_ready(session_id)
    return JSONResponse({"ok": True})
```

`_parse_ask_payload` extracts the questions list from `tool_input.questions` (same as botmux's `parseQuestions` in `src/core/ask-hook/claude-code.ts:63-89`).

`_format_ask_directive` returns:

```python
{
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {
            "questions": parsed.raw_questions,  # written back verbatim
            "answers": {q.text: ", ".join(selected_labels) for q in parsed.questions if selected},
        },
    },
}
```

If the user typed a free-text response instead of selecting labels, put that string in `answers[q.text]` (Claude Code's AskUserQuestion natively accepts arbitrary text — the "Other" path). See botmux's `formatAnswer` (lines 91–122).

`_require_hook_token` checks `Authorization: Bearer <MINICLAW_HOOK_TOKEN>` — the token is generated at daemon start (`secrets.token_urlsafe(32)`) and shared via env with spawned Claude processes. No token = 403. This means a rogue local process can't call `/hook/ask` unless it can read our env — acceptable local trust boundary.

## 11. Lifecycle & interrupt

**Start:** `spawn` returns immediately; `start()` `await`s the SessionStart signal (or 45s timeout).

**Send:** blocking `await`; returns `SubmitResult` after JSONL confirmation or retry exhaustion.

**Stream:** async generator; yields events until:
- Claude writes a `summary` / result-shaped event (canonical end-of-turn), OR
- Both JSONL and PTY output are quiescent for `IDLE_TICK_LIMIT * 100ms` (default 2s), OR
- The caller cancels.

**Interrupt:** `pty.write(b'\x03')`. Claude Code intercepts Ctrl-C to abort the current turn; the JSONL will contain a partial assistant message plus an interrupt marker. `stream_events` sees the tail and closes.

**Close:** `pty.terminate()` (SIGTERM). If still alive after 3s, `pty.terminate(force=True)` (SIGKILL).

## 12. Migration steps

Ordered; each is a self-contained commit.

1. **Add `claude_native/` package with tests.** No wiring yet. Unit tests cover: `project_hash`, `keybindings.json` parsing, JSONL drain (including truncation, partial line, malformed line, marker match), pid-file resolver (mock filesystem).
2. **Add hook bridge subprocess (`claude_hook_bridge.py`) and FastAPI endpoints.** No hooks installed yet. Test with a manually crafted JSON payload piped into `python -m miniclaw2.claude_hook_bridge`.
3. **Add hook installer**; wire into FastAPI startup (`@app.on_event("startup")`). After this commit, running the daemon writes hooks to `~/.claude/settings.json`. Verify with an interactive `claude` session — hook fires but returns passthrough (no `MINICLAW_HOOK_URL` set).
4. **Ship `ClaudeNativeSession`.** Full spawn / send / stream. Not yet wired into `ClaudeProvider`. Adds an e2e test: spawn `claude`, send "print hello", assert JSONL contains a user marker and an assistant text block.
5. **Rewrite `providers/claude.py`.** `ClaudeProvider` now wraps `ClaudeNativeSession`. Preserve existing event shapes.
6. **Delete SDK code paths.** Remove `claude-agent-sdk` from `pyproject.toml`. Remove `_make_can_use_tool`. Remove `GateSubtype.PERMISSION` and `GateSubtype.PLAN_APPROVAL` (breaks frontend plan UI — companion change to remove that UI).
7. **Delete `plan-mode-approval` bundled template.** Its verifier depends on `interaction_type=="plan_approval"` events.
8. **Rename `Node.sdk_session_id` → `cli_session_id`** with a load-time migration in `store.py`.
9. **Docs.** Update README, CONTEXT.md, IMPLEMENTATION_STATUS.md to reflect native-CLI provider. Remove any mention of `claude-agent-sdk`.

Each step ships to `main` independently. Rollback of step 6 alone restores SDK behavior if we discover a regression at step 5.

## 13. Testing

**Unit** (`backend/tests/`):

- `test_project_hash.py` — realpath vs symlink, path with dots/underscores → hash correctness.
- `test_keybindings.py` — all documented submit keys, Ctrl+Enter fail path, missing file, malformed JSON.
- `test_jsonl_drain.py` — truncation reset, partial trailing line preserved, marker detection (both `role:user` and `enqueue`), tool_result array-content NOT matching the user marker.
- `test_pid_resolver.py` — valid file + realpath match, mismatched pid, malformed JSON, non-UUID sessionId, cwd mismatch, macOS branch (no `procStart`), Linux branch with mismatched starttime.
- `test_hook_installer.py` — fresh settings.json, existing hook (idempotent no-op), old-style hook (removed & replaced), atomic write leaves no partial file on crash.
- `test_ask_payload_format.py` — button select, free-text, multi-select, multiple questions.

**E2E** (`backend/tests/e2e/`, opt-in via `MINICLAW_E2E=1`):

- `test_spawn_hello.py` — spawn, send "print 'hello'", assert TextDelta with 'hello'.
- `test_ask_userquestion.py` — spawn, ask via a system-prompt-baked directive to call AskUserQuestion, verify FastAPI receives the hook POST and Claude accepts the answer.
- `test_resume.py` — spawn, send "remember X", close, spawn `--resume`, send "what was X", assert recall.
- `test_symlink_cwd.py` — cwd is a symlink; verify JSONL path resolves correctly.

**Manual smoke test protocol** (documented in TESTING.md):

1. Start daemon → verify `~/.claude/settings.json` has our hooks.
2. Launch a Node with prompt "list files in this dir".
3. Verify streaming Bash tool activity in frontend.
4. Launch a Node that uses AskUserQuestion (prompt: "ask me which color I prefer using AskUserQuestion tool"). Verify frontend shows options; select one; verify Claude uses the answer.
5. Interrupt a running Node mid-tool. Verify graceful cancel.
6. Resume a Node. Verify context recall.

## 14. Removed features (explicit)

Users will lose:

- **Per-tool permission approval.** Every tool now runs immediately. Justification: MiniClaw2 runs in a project workspace the agent owns; users who need finer control can use OS-level sandboxing.
- **Plan-mode approval.** Plan mode is disabled at spawn. The `plan-mode-approval` template will be removed; frontend plan-approval UI becomes dead code.
- **`GateSubtype.PERMISSION` / `PLAN_APPROVAL`** as programmatic surfaces. `ASK_USER` is the sole remaining gate subtype.
- **`setting_sources: []` isolation.** MiniClaw2 now shares `~/.claude/settings.json` with the user's own `claude`. Our hook is added idempotently and structurally identified so it coexists cleanly with the user's other hooks, but user-configured settings (permission mode, disabled tools, etc.) will apply to MiniClaw2's sessions. Documented explicitly in README.

## 15. Open risks

| Risk | Mitigation |
|---|---|
| Claude Code changes the JSONL event shape between versions | Version-pin `claude` in a companion doc; run the e2e suite in CI against a known-good CLI version. On breakage, mapping fixes are localized to `transcript.py`. |
| PTY paste-burst detector changes | Throttle constants (`30ms` / `80ms`) are exposed as env vars for tuning; monitor. |
| Task tool progress event shape unknown | §9.3 mitigation: verify empirically in the prototype phase. Fall back to emitting a single `Activity(kind='tool', name='Task')` if the progressive shape isn't recoverable. |
| macOS PID reuse (no `procStart`) | Documented; unlikely to bite since Nodes don't `/clear`. If it becomes real, add a supplementary cwd + start-time check via `psutil.Process(pid).create_time()`. |
| Hook subprocess cold-start latency (~200ms Python startup per Ask) | Acceptable: Ask events are rare (once per turn at most). If problematic, ship a compiled entrypoint (Rust / Cython) later. |
| User's `~/.claude/settings.json` has conflicting hooks | Our matcher is `AskUserQuestion` specifically; user hooks on other matchers coexist. Same-matcher user hooks would fire in addition to ours — document this. |
| `--dangerously-skip-permissions` blast radius | Bounded by cwd. Enforce project-root ownership check at daemon startup (already implicit; make explicit). |
| Hook token leakage | Token is process-local (env var). Rotated per daemon start. Never logged. |
| Two MiniClaw2 daemons running against the same `~/.claude/settings.json` | Both install the same hook; only one FastAPI can be reached via `MINICLAW_HOOK_URL` env of a given spawn. Not a real concern (single-daemon per user). |

## 16. Non-goals

- **Codex adapter change.** Codex uses `codex app-server` — a real programmatic API — and stays unchanged in this migration.
- **Multi-CLI abstraction.** We are not building botmux's `CliAdapter` polymorphism (Aiden / OpenCode / Cursor / Gemini / etc.). MiniClaw2 supports two providers: Claude and Codex. If a third is ever added, we revisit.
- **tmux/zellij backends.** Botmux supports adopting live tmux panes. MiniClaw2 owns the process lifecycle; no adoption path.
- **Windows.** Deferred.
- **Skill injection.** Botmux ships built-in skills via `--plugin-dir`. Not needed for MiniClaw2 — our system prompt is delivered via `--append-system-prompt`.

## 17. Appendix: file-by-file diff summary

| File | Action | Est. LOC |
|---|---|---|
| `pyproject.toml` | Remove `claude-agent-sdk`; add `ptyprocess` | −1 / +1 |
| `providers/base.py` | Unchanged | 0 |
| `providers/codex.py` | Unchanged | 0 |
| `providers/claude.py` | Rewritten | ~120 |
| `providers/claude_native/__init__.py` | New | ~30 |
| `providers/claude_native/spawn.py` | New | ~80 |
| `providers/claude_native/input.py` | New | ~200 |
| `providers/claude_native/transcript.py` | New | ~180 |
| `providers/claude_native/session_resolver.py` | New | ~100 |
| `providers/claude_native/keybindings.py` | New | ~90 |
| `providers/claude_native/paths.py` | New | ~30 |
| `providers/claude_native/hook_installer.py` | New | ~120 |
| `providers/claude_native/ask_payload.py` | New | ~80 |
| `claude_hook_bridge.py` | New | ~90 |
| `app.py` | +2 endpoints, +startup hook install | +60 |
| `domain.py` | Remove `PERMISSION`/`PLAN_APPROVAL`; rename `sdk_session_id` → `cli_session_id` | −6 / +4 |
| `store.py` | Field migration on load | +8 |
| `events.py` | Remove `plan_approval` from Literal | −1 |
| `templates/bundled/plan-mode-approval/` | Delete | −n |
| Frontend plan-approval components | Delete | −n (separate PR) |

Rough total: ~1200 LOC added, ~200 LOC removed (excluding template + frontend cleanup).

---

**Next actions** (pending review):
1. Approve or push back on §3 / §14.
2. On approval, land steps 1–3 of §12 as an initial PR (adds `claude_native/` + hook bridge scaffolding; no behavior change).
3. Prototype step 4 in a branch; empirically capture Task tool JSONL shape (§9.3).
4. Sequence remaining steps.
