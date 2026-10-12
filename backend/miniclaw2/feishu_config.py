"""Host-local Feishu settings, relay ledger and API usage counter.

Everything here is per machine and never synchronized: credentials bind one
host to one Feishu app, the ledger maps this host's cards to its own gates,
and the usage counter only counts this host's calls. The inventory excludes
these paths and the store ``.gitignore`` lists them, so ``git add -A`` during
metadata sync can never pick up the app secret.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

FEISHU_CONFIG_FILENAME = "feishu.local.json"
FEISHU_STATE_DIRNAME = "feishu"
RELAY_LEDGER_FILENAME = "relay.json"
USAGE_FILENAME = "usage.json"

#: Terminal ledger records are kept this long for late lookups, then purged.
LEDGER_RETENTION_SECONDS = 15 * 24 * 3600


class FeishuConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: Only gates sending cards; a host with credentials stays connected so it
    #: can still relay clicks that land on it for other hosts.
    enabled: bool = True
    brand: Literal["feishu", "lark"] = "feishu"
    app_id: str = ""
    app_secret: str = ""
    bound_open_id: str | None = None
    binding_source: Literal["created", "joined", "manual"] | None = None
    bound_at: float | None = None
    delay_seconds: int = Field(default=60, ge=0, le=24 * 3600)
    poll_seconds: int = Field(default=30, ge=5, le=3600)

    @property
    def has_credentials(self) -> bool:
        return bool(self.app_id and self.app_secret)


def feishu_config_path(root: Path) -> Path:
    return root / FEISHU_CONFIG_FILENAME


def feishu_state_dir(root: Path) -> Path:
    return root / FEISHU_STATE_DIRNAME


def load_feishu_config(root: Path) -> FeishuConfig:
    path = feishu_config_path(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FeishuConfig()
    try:
        return FeishuConfig.model_validate(payload)
    except ValidationError:
        return FeishuConfig()


def save_feishu_config(root: Path, config: FeishuConfig) -> None:
    _write_private_json(feishu_config_path(root), config.model_dump())


def delete_feishu_config(root: Path) -> None:
    try:
        feishu_config_path(root).unlink()
    except FileNotFoundError:
        pass


def load_relay_ledger(root: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(feishu_state_dir(root) / RELAY_LEDGER_FILENAME)
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict)
    }


def save_relay_ledger(root: Path, ledger: dict[str, dict[str, Any]]) -> None:
    _write_private_json(feishu_state_dir(root) / RELAY_LEDGER_FILENAME, ledger)


def purge_relay_ledger(
    ledger: dict[str, dict[str, Any]],
    *,
    now: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Drop terminal records older than the retention window."""
    moment = time.time() if now is None else now
    kept: dict[str, dict[str, Any]] = {}
    for gate_id, record in ledger.items():
        stamp = record.get("closed_at") or record.get("sent_at") or 0
        if (
            record.get("state") not in {"sending", "sent"}
            and isinstance(stamp, (int, float))
            and moment - stamp > LEDGER_RETENTION_SECONDS
        ):
            continue
        kept[gate_id] = record
    return kept


def current_month(now: float | None = None) -> str:
    return time.strftime("%Y-%m", time.localtime(time.time() if now is None else now))


def load_usage(root: Path) -> dict[str, Any]:
    """This host's Feishu API calls this month, in total and per kind.

    A local estimate for the settings page: the tenant quota also counts
    other apps, and Feishu itself is never asked.
    """
    payload = _read_json(feishu_state_dir(root) / USAGE_FILENAME)
    month = current_month()
    calls = payload.get("calls")
    if payload.get("month") != month or not isinstance(calls, int):
        return {"month": month, "calls": 0, "by_kind": {}}
    by_kind = payload.get("by_kind")
    return {
        "month": month,
        "calls": calls,
        "by_kind": {
            str(kind): count
            for kind, count in (by_kind.items() if isinstance(by_kind, dict) else [])
            if isinstance(count, int)
        },
    }


def record_usage(root: Path, kind: str, calls: int = 1) -> int:
    usage = load_usage(root)
    usage["calls"] += calls
    usage["by_kind"][kind] = usage["by_kind"].get(kind, 0) + calls
    _write_private_json(feishu_state_dir(root) / USAGE_FILENAME, usage)
    return usage["calls"]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
