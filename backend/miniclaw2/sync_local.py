"""Host-local sync checkpoints and settings under ``$MINICLAW_HOME/.sync-local``.

Nothing here syncs. These are the facts Git cannot express about the remotes
(when this host last probed one, which upstream commit it already announced,
when a project's worktree was first seen dirty) plus this host's own cadence
preferences. They live outside ``config.json`` on purpose: a peer on an older
build validates ``GlobalConfig`` with ``extra="forbid"`` and would reject new
fields, and two machines legitimately want different probe intervals.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from .migrations.transaction import atomic_json


DIRECTORY = ".sync-local"
SETTINGS_FILENAME = "settings.json"
STATE_FILENAME = "state.json"


class SyncLocalSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metadata_overdue_hours: StrictInt = Field(default=72, ge=1, le=24 * 60)
    project_overdue_hours: StrictInt = Field(default=24, ge=1, le=24 * 60)
    metadata_probe_enabled: StrictBool = True
    metadata_probe_minutes: StrictInt = Field(default=10, ge=5, le=24 * 60)
    source_probe_enabled: StrictBool = True
    source_probe_minutes: StrictInt = Field(default=30, ge=10, le=24 * 60)


class SyncLocal:
    """One process-wide view of the two files, serialized by its own lock."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        return self.root / DIRECTORY

    def settings(self) -> SyncLocalSettings:
        path = self.directory / SETTINGS_FILENAME
        try:
            return SyncLocalSettings.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError):
            return SyncLocalSettings()

    def update_settings(self, changes: dict[str, Any]) -> SyncLocalSettings:
        with self._lock:
            merged = SyncLocalSettings.model_validate({**self.settings().model_dump(), **changes})
            atomic_json(self.directory / SETTINGS_FILENAME, merged.model_dump())
            return merged

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._read_state()

    def section(self, name: str) -> dict[str, Any]:
        value = self.state().get(name)
        return dict(value) if isinstance(value, dict) else {}

    def update_section(self, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Merge ``changes`` into one top-level section; ``None`` deletes a key."""
        with self._lock:
            state = self._read_state()
            section = state.get(name)
            section = dict(section) if isinstance(section, dict) else {}
            for key, value in changes.items():
                if value is None:
                    section.pop(key, None)
                else:
                    section[key] = value
            state[name] = section
            atomic_json(self.directory / STATE_FILENAME, state)
            return section

    def _read_state(self) -> dict[str, Any]:
        path = self.directory / STATE_FILENAME
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}


_INSTANCES: dict[Path, SyncLocal] = {}
_INSTANCES_LOCK = threading.Lock()


def sync_local(root: Path) -> SyncLocal:
    resolved = root.expanduser().resolve()
    with _INSTANCES_LOCK:
        instance = _INSTANCES.get(resolved)
        if instance is None:
            instance = SyncLocal(resolved)
            _INSTANCES[resolved] = instance
        return instance
