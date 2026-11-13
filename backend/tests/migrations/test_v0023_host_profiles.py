from __future__ import annotations

import pytest

from miniclaw2.migrations.sdk import MigrationContext
from miniclaw2.migrations.steps.v0023_host_profiles import MIGRATION
from miniclaw2.migrations.transaction import atomic_json


def _profile(root, directory: str, **overrides) -> None:
    atomic_json(root / f"hosts/{directory}/profile.json", {
        "machine_id": directory, "label": "办公室 Mac", "icon": "laptop", "color": "azure",
        "hostname": "mac.local", "created_at": 1.0, "updated_at": 2.0, **overrides,
    })


def test_upgrade_is_deterministic_and_writes_nothing(tmp_path) -> None:
    context = MigrationContext(tmp_path, "shared", "host-a")
    MIGRATION.upgrade(context)
    MIGRATION.verify(context)
    assert not (tmp_path / "hosts").exists()


def test_verify_accepts_unknown_icon_keys_from_newer_clients(tmp_path) -> None:
    _profile(tmp_path, "host-a", icon="hovercraft")
    MIGRATION.verify(MigrationContext(tmp_path, "shared", "host-b"))


@pytest.mark.parametrize("overrides", [
    {"machine_id": "someone-else"},
    {"label": "   "},
    {"icon": "Not A Key"},
    {"extra": True},
])
def test_verify_rejects_malformed_profiles(tmp_path, overrides) -> None:
    _profile(tmp_path, "host-a", **overrides)
    with pytest.raises(ValueError):
        MIGRATION.verify(MigrationContext(tmp_path, "shared", "host-a"))
