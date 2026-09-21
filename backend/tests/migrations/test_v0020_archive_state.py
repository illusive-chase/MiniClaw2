from __future__ import annotations

from miniclaw2.migrations.sdk import MigrationContext
from miniclaw2.migrations.steps.v0020_archive_state import MIGRATION
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.migrations.validation import read_object


def test_upgrade_adds_null_archive_timestamp_and_is_idempotent(tmp_path) -> None:
    relative = "projects/project/project.json"
    atomic_json(tmp_path / relative, {"id": "project", "name": "Example"})
    context = MigrationContext(tmp_path, "shared", "host-a")

    MIGRATION.upgrade(context)
    MIGRATION.verify(context)
    first = (tmp_path / relative).read_bytes()
    MIGRATION.upgrade(context)

    assert read_object(tmp_path / relative)["archived_at"] is None
    assert (tmp_path / relative).read_bytes() == first


def test_verify_rejects_invalid_archive_timestamp(tmp_path) -> None:
    relative = "projects/project/project.json"
    atomic_json(tmp_path / relative, {"id": "project", "archived_at": "yesterday"})
    context = MigrationContext(tmp_path, "shared", "host-a")

    try:
        MIGRATION.verify(context)
    except ValueError as exc:
        assert "归档时间无效" in str(exc)
    else:
        raise AssertionError("invalid archive timestamp was accepted")
