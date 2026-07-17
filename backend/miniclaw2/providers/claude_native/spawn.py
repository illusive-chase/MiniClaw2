"""PTY spawn contract for the native Claude Code CLI.

Owns argument construction (§7) and the environment injected into the
child process (§7 / §10).
"""

from __future__ import annotations

import json
import os
import shutil


DISALLOWED_TOOLS = ("EnterPlanMode", "ExitPlanMode")


class ClaudeBinaryNotFoundError(RuntimeError):
    """Raised when we can't locate the ``claude`` binary on PATH."""


def resolve_claude_binary() -> str:
    """Resolve the ``claude`` binary once per process, cached via ``lru_cache``."""
    resolved = _cached_resolve()
    if resolved is None:
        raise ClaudeBinaryNotFoundError(
            "'claude' binary not found on PATH; install Claude Code first"
        )
    return resolved


def _cached_resolve() -> str | None:
    # Cached on first successful lookup so repeated spawns don't re-scan
    # PATH. We can't decorate a nested function with lru_cache and re-raise
    # cleanly, so use a module-level cache.
    global _CACHED_BIN
    if _CACHED_BIN is not None:
        return _CACHED_BIN
    override = os.environ.get("MINICLAW_CLAUDE_BIN")
    if override:
        _CACHED_BIN = override
        return _CACHED_BIN
    _CACHED_BIN = shutil.which("claude")
    return _CACHED_BIN


_CACHED_BIN: str | None = None


def build_argv(
    *,
    binary: str,
    session_id: str,
    resume: bool,
    model: str | None,
    system_prompt_append: str,
    effort: str | None = None,
    tool_allowlist: list[str] | None = None,
    plugin_dir: str | None = None,
) -> list[str]:
    args: list[str] = [binary]
    if resume:
        args += ["--resume", session_id]
    else:
        args += ["--session-id", session_id]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    args.append("--dangerously-skip-permissions")
    args += [
        "--settings",
        json.dumps(
            {
                "skipDangerousModePermissionPrompt": True,
                "permissions": {"defaultMode": "bypassPermissions"},
            },
            separators=(",", ":"),
        ),
    ]
    args += ["--disallowed-tools", ",".join(DISALLOWED_TOOLS)]
    if tool_allowlist:
        args += ["--allowed-tools", ",".join(tool_allowlist)]
    if plugin_dir:
        args += ["--plugin-dir", plugin_dir]
    if system_prompt_append:
        args += ["--append-system-prompt", system_prompt_append]
    return args


def build_env(
    *,
    hook_url: str,
    hook_token: str,
    node_id: str,
    project_id: str,
    session_id: str,
) -> dict[str, str]:
    env = os.environ.copy()
    env["MINICLAW_HOOK_URL"] = hook_url
    env["MINICLAW_HOOK_TOKEN"] = hook_token
    env["MINICLAW_NODE_ID"] = node_id
    env["MINICLAW_PROJECT_ID"] = project_id
    env["MINICLAW_SESSION_ID"] = session_id
    return env


PTY_COLS = 200
PTY_ROWS = 50
