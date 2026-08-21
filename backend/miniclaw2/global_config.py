"""User-wide MiniClaw2 configuration stored under ``MINICLAW_HOME``."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

_CONFIG_FILENAME = "config.json"
_DEFAULT_CONFIG_PATH = Path(__file__).with_name("default_config.json")
_PRESET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_LOCK = RLock()


class ModelPreset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    provider: Literal["claude", "codex"]
    model: str
    description: str = ""
    model_provider: str | None = None
    service_tier: str | None = None
    reasoning_effort: str | None = None
    status: Literal["active", "compatibility"] = "active"
    is_default: bool = Field(default=False, exclude=True)

    @model_validator(mode="after")
    def validate_text(self) -> "ModelPreset":
        if not _PRESET_ID_RE.fullmatch(self.id):
            raise ValueError(
                "preset id must start with a lowercase letter or digit and contain "
                "only lowercase letters, digits, '.', '_' or '-'"
            )
        if not self.label.strip():
            raise ValueError("preset label is required")
        if not self.model.strip():
            raise ValueError("preset model is required")
        return self

    def metadata(self) -> dict[str, Any]:
        return {
            **self.model_dump(exclude={"is_default"}),
            "is_default": self.is_default,
        }

    def settings_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "model_preset_id": self.id,
            "provider": self.provider,
            "model": self.model,
        }
        if self.model_provider is not None:
            snapshot["model_provider"] = self.model_provider
        if self.service_tier is not None:
            snapshot["service_tier"] = self.service_tier
        if self.reasoning_effort is not None:
            snapshot["reasoning_effort"] = self.reasoning_effort
        return snapshot


class GlobalDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model_preset_id: str
    auto_commit: bool = False
    preferred_language: str | None = None
    concurrency: StrictInt = Field(default=1, ge=1)


class SyncSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote_url: str | None = None


class ToolRequestSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: StrictInt = Field(default=120, ge=1)
    timeout_action: Literal["accept", "reject"] = "accept"


class CodeReviewSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_preset_id: str


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    defaults: GlobalDefaults
    model_presets: list[ModelPreset]
    code_review: CodeReviewSettings
    tool_requests: ToolRequestSettings = Field(default_factory=ToolRequestSettings)
    sync: SyncSettings = Field(default_factory=SyncSettings)

    @model_validator(mode="before")
    @classmethod
    def drop_retired_update_settings(cls, value: Any) -> Any:
        """Self-update never contacts the remote unprompted, so it has no settings."""
        if isinstance(value, dict) and "updates" in value:
            return {key: item for key, item in value.items() if key != "updates"}
        return value

    @model_validator(mode="before")
    @classmethod
    def migrate_code_review_settings(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "code_review" in value:
            return value
        presets = value.get("model_presets")
        defaults = value.get("defaults")
        active_ids: set[str] = set()
        if isinstance(presets, list):
            for preset in presets:
                if isinstance(preset, ModelPreset) and preset.status == "active":
                    active_ids.add(preset.id)
                elif (
                    isinstance(preset, dict)
                    and preset.get("status", "active") == "active"
                    and isinstance(preset.get("id"), str)
                ):
                    active_ids.add(preset["id"])
        if isinstance(defaults, GlobalDefaults):
            default_id = defaults.default_model_preset_id
        elif isinstance(defaults, dict):
            default_id = defaults.get("default_model_preset_id")
        else:
            default_id = None
        model_preset_id = "gpt-5.6" if "gpt-5.6" in active_ids else default_id
        return {
            **value,
            "code_review": {"model_preset_id": model_preset_id},
        }

    @model_validator(mode="after")
    def validate_catalog(self) -> "GlobalConfig":
        ids = [preset.id for preset in self.model_presets]
        if len(ids) != len(set(ids)):
            raise ValueError("model preset ids must be unique")
        if self.defaults.default_model_preset_id not in ids:
            raise ValueError("default_model_preset_id must reference a configured preset")
        default = next(
            preset
            for preset in self.model_presets
            if preset.id == self.defaults.default_model_preset_id
        )
        if default.status != "active":
            raise ValueError("default_model_preset_id must reference an active preset")
        if self.code_review.model_preset_id not in ids:
            raise ValueError("code review model_preset_id must reference a configured preset")
        code_review_preset = next(
            preset
            for preset in self.model_presets
            if preset.id == self.code_review.model_preset_id
        )
        if code_review_preset.status != "active":
            raise ValueError("code review model_preset_id must reference an active preset")
        return self


def miniclaw_home(store_root: Path | None = None) -> Path:
    if store_root is not None:
        return store_root
    base = os.environ.get("MINICLAW_HOME")
    return Path(base).expanduser() if base else Path.home() / ".miniclaw2"


def global_config_path(store_root: Path | None = None) -> Path:
    return miniclaw_home(store_root) / _CONFIG_FILENAME


def load_global_config(store_root: Path | None = None) -> GlobalConfig:
    path = global_config_path(store_root)
    source = path if path.exists() else _DEFAULT_CONFIG_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        return GlobalConfig.model_validate(payload)
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid global config {source}: {exc}") from exc


def ensure_global_config(store_root: Path | None = None) -> Path:
    path = global_config_path(store_root)
    with _LOCK:
        if path.exists():
            load_global_config(store_root)
            return path
        config = load_global_config(store_root)
        save_global_config(config, store_root)
    return path


def save_global_config(
    config: GlobalConfig,
    store_root: Path | None = None,
) -> GlobalConfig:
    validated = GlobalConfig.model_validate(config.model_dump())
    path = global_config_path(store_root)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(validated.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    return validated
