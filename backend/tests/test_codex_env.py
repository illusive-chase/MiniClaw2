from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.codex_env import (
    CODEX_ENV_FILENAME,
    CodexEnvConfig,
    load_codex_env,
    save_codex_env,
    wrap_command,
)
from miniclaw2.migrations.inventory import EXCLUDED_ROOTS, IGNORED_PATTERNS, scope_for
from miniclaw2.providers.codex import _CodexJsonRpcClient, _codex_env_script
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


def test_env_file_never_syncs() -> None:
    assert CODEX_ENV_FILENAME in EXCLUDED_ROOTS
    assert f"/{CODEX_ENV_FILENAME}" in IGNORED_PATTERNS
    assert scope_for(Path(CODEX_ENV_FILENAME)) is None


def test_disabled_or_missing_config_yields_no_script(tmp_path: Path) -> None:
    assert load_codex_env(tmp_path).resolved_script() is None
    save_codex_env(tmp_path, CodexEnvConfig(enabled=False, script_path="/opt/env.sh"))
    assert load_codex_env(tmp_path).resolved_script() is None


def test_enabling_requires_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_codex_env(tmp_path, CodexEnvConfig(enabled=True, script_path=""))
    with pytest.raises(ValueError):
        save_codex_env(tmp_path, CodexEnvConfig(enabled=True, script_path="env.sh"))
    saved = save_codex_env(tmp_path, CodexEnvConfig(enabled=True, script_path="~/env.sh"))
    assert saved.resolved_script() == Path("~/env.sh").expanduser()


def test_wrapped_command_sources_script_before_exec(tmp_path: Path) -> None:
    script = tmp_path / "env with space.sh"
    script.write_text('export MC_WRAPPED="from script"\n', encoding="utf-8")
    argv = wrap_command(script, ["sh", "-c", 'printf "%s|%s" "$MC_WRAPPED" "$1"', "_", "a$b"])
    result = subprocess.run(argv, capture_output=True, text=True, check=True)
    assert result.stdout == "from script|a$b"


def test_wrapped_command_fails_when_source_fails(tmp_path: Path) -> None:
    script = tmp_path / "bad.sh"
    script.write_text("return 3\n", encoding="utf-8")
    result = subprocess.run(wrap_command(script, ["true"]), capture_output=True, text=True)
    assert result.returncode == 97
    assert "sourcing" in result.stderr


def test_provider_reads_host_local_setting(tmp_path: Path) -> None:
    context = SimpleNamespace(store_root=tmp_path)
    assert _codex_env_script(context) is None  # type: ignore[arg-type]
    script = tmp_path / "env.sh"
    script.write_text(":\n", encoding="utf-8")
    save_codex_env(tmp_path, CodexEnvConfig(enabled=True, script_path=str(script)))
    assert _codex_env_script(context) == script  # type: ignore[arg-type]


def test_client_wraps_argv_and_rejects_missing_script(tmp_path: Path) -> None:
    script = tmp_path / "env.sh"
    script.write_text(":\n", encoding="utf-8")

    class _Proc:
        returncode = None
        stdout = stderr = None

    async def run() -> list[str]:
        with patch(
            "miniclaw2.providers.codex.asyncio.create_subprocess_exec",
            return_value=_Proc(),
        ) as spawn, patch.object(_CodexJsonRpcClient, "_read_stdout"), patch.object(
            _CodexJsonRpcClient, "_read_stderr"
        ):
            client = _CodexJsonRpcClient(env_script=script)
            await client.__aenter__()
            for task in (client._reader_task, client._stderr_task):
                assert task is not None
                task.cancel()
            return list(spawn.call_args.args)

    argv = asyncio.run(run())
    assert argv[-4:] == ["codex", "app-server", "--listen", "stdio://"]
    assert str(script) in argv
    with pytest.raises(RuntimeError, match="不存在"):
        asyncio.run(_CodexJsonRpcClient(env_script=tmp_path / "missing.sh").__aenter__())


def test_codex_env_api_round_trip(tmp_path: Path) -> None:
    os.environ["MINICLAW_CONTEXT_HOME"] = str(tmp_path / "context")
    registry = ProjectRegistry(store=Store(tmp_path / "store"))
    script = tmp_path / "env.sh"
    script.write_text(":\n", encoding="utf-8")
    with patch("miniclaw2.app.install_hooks"), TestClient(create_app(registry)) as client:
        initial = client.get("/global-state/codex-env").json()
        assert initial["enabled"] is False and initial["script_path"] == ""
        assert client.patch("/global-state/codex-env", json={"enabled": True}).status_code == 422
        updated = client.patch(
            "/global-state/codex-env", json={"enabled": True, "script_path": str(script)},
        ).json()
        assert updated["enabled"] is True and updated["script_exists"] is True
        disabled = client.patch("/global-state/codex-env", json={"enabled": False}).json()
        assert disabled["enabled"] is False and disabled["script_path"] == str(script)
    assert (registry.store.root / CODEX_ENV_FILENAME).is_file()
