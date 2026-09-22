from __future__ import annotations

from miniclaw2.migrations.sdk import MigrationContext
from miniclaw2.migrations.steps.v0021_diff_review import MIGRATION
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.migrations.validation import read_object


def test_upgrade_adds_disabled_diff_review_and_is_idempotent(tmp_path) -> None:
    relative = "projects/project/hosts/host-a/nodes/node/node.json"
    atomic_json(tmp_path / relative, {"id": "node"})
    context = MigrationContext(tmp_path, "shared", "host-a")

    MIGRATION.upgrade(context)
    MIGRATION.verify(context)
    first = (tmp_path / relative).read_bytes()
    MIGRATION.upgrade(context)

    assert read_object(tmp_path / relative)["diff_review"] is False
    assert (tmp_path / relative).read_bytes() == first


def test_verify_rejects_non_boolean_diff_review(tmp_path) -> None:
    relative = "projects/project/hosts/host-a/nodes/node/node.json"
    atomic_json(tmp_path / relative, {"id": "node", "diff_review": "yes"})
    context = MigrationContext(tmp_path, "shared", "host-a")

    try:
        MIGRATION.verify(context)
    except ValueError as exc:
        assert "diff_review 设置无效" in str(exc)
    else:
        raise AssertionError("invalid diff_review was accepted")
