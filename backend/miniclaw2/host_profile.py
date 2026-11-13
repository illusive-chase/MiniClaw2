"""Host display profiles persisted as ``hosts/<machine-id>/profile.json``.

A profile is the one place a host's user-chosen name, icon, and color live,
and it travels with sync so every device shows the same name. Each file has a
single writer — the host it describes — so two devices never edit the same
record and the directory can never conflict on merge.

``machine.json.label`` is a host-local cache of this host's own label, kept
for commit author names. Project records still carry the label a host had
when it created or bound the project (``project.json.machine_label``,
``projects/*/hosts/<mid>/host.json.label``); those are creation-time
snapshots, and display prefers the profile over them.

Icon keys mirror ``project_icons``: our own keys are stored, never icon
library names, reads accept any well-formed key, and writes require one this
build can render. ``hostIcons.ts`` holds the render side.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .migrations.transaction import atomic_json
from .project_icons import PROJECT_HUES


HOSTS_DIRECTORY = "hosts"
PROFILE_FILENAME = "profile.json"
MAX_HOST_LABEL_LENGTH = 40

HOST_GLYPHS: tuple[str, ...] = (
    "laptop", "monitor", "server", "cloud", "container", "cpu", "house",
    "building", "flask", "rocket", "box", "terminal",
)
HOST_HUES: tuple[str, ...] = PROJECT_HUES

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class HostProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    machine_id: str
    label: str
    icon: str
    color: str
    hostname: str = ""
    created_at: float
    updated_at: float

    @field_validator("machine_id")
    @classmethod
    def _valid_machine_id(cls, value: str) -> str:
        if not value or "/" in value or value in {".", ".."}:
            raise ValueError("invalid host machine id")
        return value

    @field_validator("label")
    @classmethod
    def _valid_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("host label must not be empty")
        if len(normalized) > MAX_HOST_LABEL_LENGTH:
            raise ValueError(f"host label must be at most {MAX_HOST_LABEL_LENGTH} characters")
        return normalized

    @field_validator("icon", "color")
    @classmethod
    def _well_formed(cls, value: str) -> str:
        if not _KEY_RE.match(value):
            raise ValueError(f"malformed host icon key: {value!r}")
        return value


def require_known_look(profile: HostProfile) -> HostProfile:
    """Reject keys this build cannot render; every write path calls this."""
    if profile.icon not in HOST_GLYPHS:
        raise ValueError(f"unknown host icon: {profile.icon}")
    if profile.color not in HOST_HUES:
        raise ValueError(f"unknown host color: {profile.color}")
    return profile


def profile_path(root: Path, machine_id: str) -> Path:
    return root / HOSTS_DIRECTORY / machine_id / PROFILE_FILENAME


def default_host_icon() -> str:
    if Path("/.dockerenv").exists() or os.environ.get("container"):
        return "container"
    if sys.platform == "darwin":
        return "laptop"
    if sys.platform == "win32":
        return "monitor"
    return "server"


def default_host_color(machine_id: str) -> str:
    digest = hashlib.sha256(machine_id.encode("utf-8")).digest()
    return HOST_HUES[digest[0] % len(HOST_HUES)]


def new_profile(machine_id: str, *, label: str, hostname: str) -> HostProfile:
    now = time.time()
    return HostProfile(
        machine_id=machine_id,
        label=label.strip()[:MAX_HOST_LABEL_LENGTH] or machine_id[:8],
        icon=default_host_icon(),
        color=default_host_color(machine_id),
        hostname=hostname,
        created_at=now,
        updated_at=now,
    )


def load_profile(root: Path, machine_id: str) -> HostProfile | None:
    path = profile_path(root, machine_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        profile = HostProfile.model_validate(payload)
    except (OSError, ValueError, ValidationError):
        return None
    return profile if profile.machine_id == machine_id else None


def load_profiles(root: Path) -> dict[str, HostProfile]:
    directory = root / HOSTS_DIRECTORY
    if not directory.is_dir():
        return {}
    profiles: dict[str, HostProfile] = {}
    for host_dir in sorted(directory.iterdir()):
        profile = load_profile(root, host_dir.name)
        if profile is not None:
            profiles[host_dir.name] = profile
    return profiles


def save_profile(root: Path, profile: HostProfile) -> None:
    atomic_json(profile_path(root, profile.machine_id), profile.model_dump())


def validate_profile_file(path: Path, machine_id: str) -> HostProfile:
    """Structural check for sync and migration: the directory names the writer."""
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    profile = HostProfile.model_validate(payload)
    if profile.machine_id != machine_id:
        raise ValueError("host 资料的 machine_id 与目录名不一致")
    return profile
