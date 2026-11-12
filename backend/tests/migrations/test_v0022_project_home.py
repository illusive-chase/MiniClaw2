from __future__ import annotations

import pytest

from miniclaw2.migrations.sdk import MigrationContext
from miniclaw2.migrations.steps.v0022_project_home import MIGRATION
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.migrations.validation import read_object


def _project(root, project_id: str, **extra) -> None:
    atomic_json(root / f"projects/{project_id}/project.json", {
        "id": project_id, "model_preset_id": "opus-4-8", "created_at": 100.0, **extra,
    })


def _node(root, project_id: str, host: str, node_id: str, **times) -> None:
    atomic_json(root / f"projects/{project_id}/hosts/{host}/nodes/{node_id}/node.json", {
        "id": node_id, "project_id": project_id, **times,
    })


def test_upgrade_writes_default_icon_and_activity_ordered_home(tmp_path) -> None:
    _project(tmp_path, "old", created_at=50.0)
    _project(tmp_path, "busy")
    _project(tmp_path, "tied-b", created_at=300.0)
    _project(tmp_path, "tied-a", created_at=300.0)
    _project(tmp_path, "chosen", icon={"glyph": "rocket", "hue": "plum"})
    _project(tmp_path, "shelved", archived_at=999.0)
    _node(tmp_path, "busy", "host-a", "n1", created_at=10.0, started_at=20.0, finished_at=900.0)
    _node(tmp_path, "busy", "host-b", "n2", created_at=400.0)
    _node(tmp_path, "chosen", "host-a", "n3", created_at=500.0, finished_at=None)
    context = MigrationContext(tmp_path, "shared", "host-a")

    MIGRATION.upgrade(context)
    MIGRATION.verify(context)

    assert read_object(tmp_path / "projects/old/project.json")["icon"] == {"glyph": "code", "hue": "coral"}
    assert read_object(tmp_path / "projects/chosen/project.json")["icon"] == {"glyph": "rocket", "hue": "plum"}
    home = read_object(tmp_path / "home.json")
    assert home == {"version": 1, "items": [
        {"project": "busy"}, {"project": "chosen"}, {"project": "tied-a"},
        {"project": "tied-b"}, {"project": "old"},
    ]}


def test_upgrade_is_idempotent_and_keeps_an_existing_home(tmp_path) -> None:
    _project(tmp_path, "a")
    context = MigrationContext(tmp_path, "shared", "host-a")
    MIGRATION.upgrade(context)
    first = {path: (tmp_path / path).read_bytes() for path in ("home.json", "projects/a/project.json")}
    MIGRATION.upgrade(context)
    assert {path: (tmp_path / path).read_bytes() for path in first} == first


def test_verify_rejects_missing_icon_or_home_drift(tmp_path) -> None:
    _project(tmp_path, "a")
    context = MigrationContext(tmp_path, "shared", "host-a")
    with pytest.raises(ValueError, match="图标"):
        MIGRATION.verify(context)
    MIGRATION.upgrade(context)
    _project(tmp_path, "b", icon={"glyph": "code", "hue": "coral"})
    with pytest.raises(ValueError, match="missing"):
        MIGRATION.verify(context)
