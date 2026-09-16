from __future__ import annotations

from miniclaw2.migrations.sdk import MigrationContext
from miniclaw2.migrations.steps.v0018_project_persistence_mode import MIGRATION
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.migrations.validation import read_object


def test_upgrade_maps_legacy_project_modes_and_is_idempotent(tmp_path) -> None:
    durable = "projects/durable/project.json"
    ephemeral = "projects/ephemeral/project.json"
    atomic_json(tmp_path / durable, {"id": "durable", "temporary": False})
    atomic_json(tmp_path / ephemeral, {"id": "ephemeral", "temporary": True})
    context = MigrationContext(tmp_path, "shared", "host-a")

    MIGRATION.upgrade(context)
    MIGRATION.verify(context)
    first = {
        durable: (tmp_path / durable).read_bytes(),
        ephemeral: (tmp_path / ephemeral).read_bytes(),
    }
    MIGRATION.upgrade(context)

    assert read_object(tmp_path / durable)["persistence_mode"] == "durable"
    assert read_object(tmp_path / ephemeral)["persistence_mode"] == "ephemeral"
    assert (tmp_path / durable).read_bytes() == first[durable]
    assert (tmp_path / ephemeral).read_bytes() == first[ephemeral]


def test_verify_rejects_conflicting_legacy_flag(tmp_path) -> None:
    relative = "projects/project/project.json"
    atomic_json(
        tmp_path / relative,
        {"id": "project", "temporary": True, "persistence_mode": "durable"},
    )
    context = MigrationContext(tmp_path, "shared", "host-a")

    try:
        MIGRATION.verify(context)
    except ValueError as exc:
        assert "冲突" in str(exc)
    else:
        raise AssertionError("conflicting project mode was accepted")
