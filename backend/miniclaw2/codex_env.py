"""Host-local environment wrapper for the Codex app-server.

When enabled, MiniClaw2 sources a user-chosen shell script before it execs
``codex app-server``, so a host can load modules, proxies, or a conda env the
backend process itself was not started with. The script path is a fact about
this machine's filesystem, so the file lives at the store root, the inventory
excludes it, and the store ``.gitignore`` lists it: it never syncs.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .migrations.transaction import atomic_json

CODEX_ENV_FILENAME = "codex_env.local.json"


class CodexEnvConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    script_path: str = Field(default="", max_length=4096)

    @field_validator("script_path")
    @classmethod
    def _valid_path(cls, value: str) -> str:
        normalized = value.strip()
        if any(c in normalized for c in "\x00\r\n"):
            raise ValueError("环境脚本路径无效")
        return normalized

    def resolved_script(self) -> Path | None:
        if not self.enabled or not self.script_path:
            return None
        return Path(self.script_path).expanduser()


def codex_env_path(root: Path) -> Path:
    return root / CODEX_ENV_FILENAME


def load_codex_env(root: Path) -> CodexEnvConfig:
    try:
        payload = json.loads(codex_env_path(root).read_text(encoding="utf-8"))
        return CodexEnvConfig.model_validate(payload)
    except (OSError, ValueError, ValidationError):
        return CodexEnvConfig()


def save_codex_env(root: Path, config: CodexEnvConfig) -> CodexEnvConfig:
    validated = CodexEnvConfig.model_validate(config.model_dump())
    if validated.enabled:
        if not validated.script_path:
            raise ValueError("启用环境 wrapper 时必须填写脚本路径")
        script = Path(validated.script_path).expanduser()
        if not script.is_absolute():
            raise ValueError("环境脚本路径必须是绝对路径（可用 ~ 开头）")
    atomic_json(codex_env_path(root), validated.model_dump())
    return validated


def codex_env_status(root: Path) -> dict[str, object]:
    config = load_codex_env(root)
    script = Path(config.script_path).expanduser() if config.script_path else None
    return {
        **config.model_dump(),
        "script_exists": bool(script and script.is_file()),
        "shell": wrapper_shell(),
    }


def wrapper_shell() -> str:
    """``bash`` when available since most env scripts assume it; else POSIX sh."""
    return shutil.which("bash") or "/bin/sh"


def wrap_command(script: Path, argv: list[str]) -> list[str]:
    """Source ``script`` in a shell, then exec ``argv`` with the resulting env.

    Both the script path and the command travel as positional arguments, so
    nothing user-provided is ever interpolated into the shell source.
    """
    source = (
        '__mc_script="$1"; shift; '
        '. "$__mc_script" || { echo "MiniClaw2: sourcing $__mc_script failed" >&2; exit 97; }; '
        'exec "$@"'
    )
    return [wrapper_shell(), "-c", source, "miniclaw2-codex-env", str(script), *argv]
