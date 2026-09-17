from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.parse import quote

import pytest

from miniclaw2.domain import ArtifactRef, GitLayout, LaneLayout, Node, NodeLayout, Project
from miniclaw2.migrations.catalog import check_manifest, marker, read_manifest
from miniclaw2.migrations.cli import main
from miniclaw2.migrations.coordinator import coordinator
from miniclaw2.migrations.errors import MigrationError
from miniclaw2.migrations.impact import layout_impact, layout_recovery_guidance
from miniclaw2.migrations.inventory import files
from miniclaw2.migrations.sdk import MigrationContext
from miniclaw2.migrations.steps.v0016_node_layout import MIGRATION as V16
from miniclaw2.migrations.steps.v0017_layout_recovery import MIGRATION, recover_layout
from miniclaw2.migrations.sync_tree import normalize
from miniclaw2.migrations.transaction import atomic_json, file_digest
from miniclaw2.migrations.validation import read_object, validate


ARTIFACT_NAME = "报告 a:b/#%~!*'().html"
ARTIFACT_ID = "artifact:child:" + quote(ARTIFACT_NAME, safe="~!*'()-._")


def seed(root: Path, version: int = 15, *, published: int = 5) -> None:
    project = Project(id="project", root_path="/tmp/layout-recovery", machine_id="host-a")
    parent = Node(id="parent", project_id=project.id, model_preset_id="opus-4-8",
                  settings_snapshot={"active_planspace_id": "history"})
    child = Node(id="child", project_id=project.id, model_preset_id="opus-4-8", parent_node_id="parent")
    child.artifacts = [ArtifactRef(name=ARTIFACT_NAME if index == 0 else f"report-{index}.md",
                                  bytes=0, mtime=0, sha256="", status="published") for index in range(published)]
    child.artifacts.append(ArtifactRef(name="dropped.md", bytes=0, mtime=0, sha256="", status="dropped"))
    hints = {
        "parent": {"x": 10, "y": 20}, "child": {"x": 30, "y": 40},
        ARTIFACT_ID: {"x": 50, "y": 60}, "artifact-overflow:child": {"x": 70, "y": 80},
        "commit:abc1234": {"x": 90, "y": 100}, "commit:ghost": {"x": 110, "y": 120},
        "planspace:history": {"x": 130, "y": 140}, "err:broken": {"x": 1, "y": 2},
    }
    atomic_json(root / "schema.json", marker(version))
    project_payload = project.model_dump(exclude={"provider", "root_path", "node_positions"})
    if version == 14:
        project_payload.update(root_path=project.root_path, layout_hints=hints,
                               layout_viewport={"x": 1, "y": 2, "zoom": 1})
    atomic_json(root / "projects/project/project.json", project_payload)
    for node, owner in ((parent, "host-a"), (child, "host-b")):
        directory = root / "projects/project" / ("nodes" if version == 14 else f"hosts/{owner}/nodes")
        atomic_json(directory / node.id / "node.json", node.model_dump(exclude={"provider", "owner_host_id"}))
    if version == 15:
        for host in ("host-a", "host-b"):
            directory = root / f"projects/project/hosts/{host}"
            atomic_json(directory / "host.json", {"label": host, "bound_at": 1, "repo": {}})
            atomic_json(directory / "layout.json", {
                "layout_hints": hints if host == "host-a" else {"commit:abc1234": {"x": 999, "y": 999}},
                "layout_viewport": {"x": 1, "y": 2, "zoom": 1},
            })


def layouts(root: Path) -> dict[str, bytes]:
    return {relative: (root / relative).read_bytes() for relative in files(root) if relative.endswith("-layout.json")}


@pytest.mark.parametrize("version", [15])
def test_transaction_preserves_layout_and_original_bytes(tmp_path: Path, version: int) -> None:
    seed(tmp_path, version)
    original = {relative: (tmp_path / relative).read_bytes() for relative in files(tmp_path)}
    coordinator(tmp_path).apply("host-a", accept_data_loss=True)
    owner = "host-a" if version == 14 else "host-b"
    node_layout = NodeLayout.model_validate(read_object(tmp_path / f"projects/project/hosts/{owner}/node-layout.json"))
    assert node_layout.nodes["child"].model_dump() == {"x": 30, "y": 40, "space": "planspace:history"}
    assert node_layout.nodes[ARTIFACT_ID].model_dump() == {"x": 50, "y": 60, "space": "planspace:history"}
    assert node_layout.nodes["artifact-overflow:child"].x == 70
    git = GitLayout.model_validate(read_object(tmp_path / "projects/project/git-layout.json"))
    lane = LaneLayout.model_validate(read_object(tmp_path / "projects/project/lane-layout.json"))
    assert git.nodes["commit:abc1234"].x == 90
    assert "commit:ghost" not in git.nodes
    assert lane.nodes["planspace:history"].x == 130
    assert all(b"err:broken" not in content and b"viewport" not in content for content in layouts(tmp_path).values())
    assert not list(tmp_path.glob("projects/*/hosts/*/layout.json"))
    backups = list((tmp_path / "migration-backups").glob("*/0"))
    assert len(backups) == 1
    assert all((backups[0] / relative).read_bytes() == content for relative, content in original.items())
    assert read_object(tmp_path / "schema.json") == marker()
    validate(tmp_path)


@pytest.mark.parametrize("version", [15])
def test_normalize_matches_transaction_and_is_idempotent(tmp_path: Path, version: int) -> None:
    root, normalized = tmp_path / "root", tmp_path / "normalized"
    seed(root, version)
    shutil.copytree(root, normalized)
    coordinator(root).apply("host-b", accept_data_loss=True)
    normalize(normalized, frozenset({V16.contract}))
    assert layouts(normalized) == layouts(root)
    before = layouts(normalized)
    normalize(normalized)
    assert layouts(normalized) == before


def test_shared_results_do_not_depend_on_machine_identity(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    seed(first)
    shutil.copytree(first, second)
    coordinator(first).apply("host-a", accept_data_loss=True)
    coordinator(second).apply("host-b", accept_data_loss=True)
    assert layouts(first) == layouts(second)


@pytest.mark.parametrize("published", [4, 5])
def test_artifact_identifiers_and_overflow_threshold(tmp_path: Path, published: int) -> None:
    seed(tmp_path, published=published)
    source = tmp_path / "projects/project/hosts/host-a/layout.json"
    payload = read_object(source)
    payload["layout_hints"].update({
        "artifact:child:dropped.md": {"x": 1, "y": 2},
        "artifact:child:" + ARTIFACT_NAME: {"x": 1, "y": 2},
        "artifact:missing:report.md": {"x": 1, "y": 2},
    })
    atomic_json(source, payload)
    normalize(tmp_path, frozenset({V16.contract}))
    nodes = read_object(tmp_path / "projects/project/hosts/host-b/node-layout.json")["nodes"]
    assert ARTIFACT_ID in nodes
    assert ("artifact-overflow:child" in nodes) == (published > 4)
    assert "artifact:child:dropped.md" not in nodes
    assert "artifact:child:" + ARTIFACT_NAME not in nodes
    assert "artifact:missing:report.md" not in nodes


def test_bad_supplemental_entries_are_reported_and_skipped(tmp_path: Path) -> None:
    seed(tmp_path)
    source = tmp_path / "projects/project/hosts/host-a/layout.json"
    payload = read_object(source)
    invalid = {
        "commit:0000000": {"x": True, "y": 1}, "commit:0000001": {"x": "12", "y": 1},
        "commit:0000002": {"x": float("nan"), "y": 1}, "commit:0000003": {"x": float("inf"), "y": 1},
        "commit:0000004": None, "commit:0000005": {"x": 1}, "commit:bad": {"x": 1, "y": 2},
        "planspace:": {"x": 1, "y": 2}, "artifact-overflow:child": {"x": False, "y": 1},
    }
    payload["layout_hints"].update(invalid)
    atomic_json(source, payload)
    impact = layout_impact(tmp_path)
    assert sum(report["not_restored"]["invalid"] for report in impact) == len(invalid)
    assert set(invalid) <= {entry["id"] for report in impact for entry in report["skipped_entries"]}
    normalize(tmp_path, frozenset({V16.contract}))
    for content in layouts(tmp_path).values():
        assert not set(invalid).intersection(json.loads(content)["nodes"])


def test_only_missing_and_repeated_step_preserve_newer_bytes(tmp_path: Path) -> None:
    root, source = tmp_path / "root", tmp_path / "source"
    seed(root)
    shutil.copytree(root, source)
    context = MigrationContext(root, "shared", "host-a", source)
    V16.upgrade(context)
    for relative, tile in (("hosts/host-b/node-layout.json", ARTIFACT_ID),
                           ("git-layout.json", "commit:abc1234"), ("lane-layout.json", "planspace:history")):
        target = root / "projects/project" / relative
        payload = read_object(target) if target.exists() else {"schema_version": 1, "nodes": {}}
        payload["nodes"][tile] = {"x": 444, "y": 555, "space": "canvas"}
        atomic_json(target, payload)
    MIGRATION.upgrade(context)
    MIGRATION.verify(context)
    before = layouts(root)
    MIGRATION.upgrade(context)
    assert layouts(root) == before
    assert all(not any(report["restored"].values()) for report in recover_layout(context, write=False))
    for relative, tile in (
        ("projects/project/hosts/host-b/node-layout.json", ARTIFACT_ID),
        ("projects/project/git-layout.json", "commit:abc1234"),
        ("projects/project/lane-layout.json", "planspace:history"),
    ):
        assert json.loads(before[relative])["nodes"][tile]["x"] == 444


@pytest.mark.parametrize("version", [15])
def test_plan_reports_recovery_without_touching_sources(tmp_path: Path, version: int, capsys: pytest.CaptureFixture[str]) -> None:
    seed(tmp_path, version)
    before = {relative: (tmp_path / relative).read_bytes() for relative in files(tmp_path)}
    main(["plan", "--root", str(tmp_path)])
    plan = json.loads(capsys.readouterr().out)
    assert plan["target"] == 18 and plan["minimum"] == 15
    assert "v17 显式修复 v16" in plan["layout_note"]
    assert "commit:ghost" in plan["layout_note"]
    assert "不恢复" in plan["layout_note"]
    assert sum(report["restored"]["artifact"] for report in plan["layout_impact"]) == 2
    assert sum(report["restored"]["git"] for report in plan["layout_impact"]) == 1
    assert sum(report["restored"]["lane"] for report in plan["layout_impact"]) == 1
    assert sum(report["not_restored"]["synthetic"] for report in plan["layout_impact"]) == 2
    assert before == {relative: (tmp_path / relative).read_bytes() for relative in files(tmp_path)}


def test_v16_reports_history_but_does_not_restore_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(tmp_path)
    backup = tmp_path / "migration-backups/old-transaction/0"
    backup.mkdir(parents=True)
    shutil.copytree(tmp_path / "projects", backup / "projects")
    atomic_json(tmp_path / ".migration-local/transactions/old-transaction/journal.json", {
        "phase": "ready", "inputs": [{relative: file_digest(backup / relative) for relative in files(backup)}],
    })
    V16.upgrade(MigrationContext(tmp_path, "shared", "host-a"))
    atomic_json(tmp_path / "schema.json", marker(16))
    before = layouts(tmp_path)
    main(["plan", "--root", str(tmp_path)])
    plan = json.loads(capsys.readouterr().out)
    report = plan["layout_recovery"][0]
    assert report["transaction"] == "old-transaction"
    assert report["missing"]["artifact"] == 2
    assert len(report["commands"]) == 3
    assert all("--transaction old-transaction" in command for command in report["commands"])
    assert any("--kind lane" in command for command in report["commands"])
    coordinator(tmp_path).apply("host-a")
    assert layouts(tmp_path) == before
    assert read_object(tmp_path / "schema.json") == marker()
    assert layout_recovery_guidance(tmp_path)[0]["missing"] == report["missing"]


def test_v16_without_local_backup_does_not_claim_recovery(tmp_path: Path) -> None:
    seed(tmp_path)
    V16.upgrade(MigrationContext(tmp_path, "shared", "host-a"))
    atomic_json(tmp_path / "schema.json", marker(16))
    report = layout_recovery_guidance(tmp_path)[0]
    assert report["transaction"] is None
    assert "原升级设备" in report["message"]
    assert report["commands"] == []


@pytest.mark.parametrize("case,expected", [
    ("explicit", "planspace:direct"), ("snapshot", "planspace:snapshot"),
    ("parent", "planspace:parent"), ("cycle", "canvas"), ("missing_parent", "canvas"),
])
def test_recovered_positions_follow_historical_coordinate_space(tmp_path: Path, case: str, expected: str) -> None:
    seed(tmp_path)
    parent_path = tmp_path / "projects/project/hosts/host-a/nodes/parent/node.json"
    child_path = tmp_path / "projects/project/hosts/host-b/nodes/child/node.json"
    parent, child = read_object(parent_path), read_object(child_path)
    if case == "explicit":
        child.update(planspace_id="direct", settings_snapshot={"active_planspace_id": "snapshot"})
    elif case == "snapshot":
        child["settings_snapshot"] = {"active_planspace_id": "snapshot"}
    elif case == "parent":
        parent["planspace_id"] = "parent"
    elif case == "cycle":
        parent.update(settings_snapshot={}, parent_node_id="child")
    else:
        child["parent_node_id"] = "absent"
    atomic_json(parent_path, parent)
    atomic_json(child_path, child)
    normalize(tmp_path, frozenset({V16.contract}))
    nodes = read_object(tmp_path / "projects/project/hosts/host-b/node-layout.json")["nodes"]
    assert nodes["child"]["space"] == expected
    assert nodes[ARTIFACT_ID]["space"] == expected


def test_op_artifacts_are_excluded(tmp_path: Path) -> None:
    seed(tmp_path)
    path = tmp_path / "projects/project/hosts/host-b/nodes/child/node.json"
    payload = read_object(path)
    payload.update(kind="op", category=None)
    atomic_json(path, payload)
    normalize(tmp_path, frozenset({V16.contract}))
    nodes = read_object(tmp_path / "projects/project/hosts/host-b/node-layout.json")["nodes"]
    assert set(nodes) == {"child"}


def test_invalid_first_source_does_not_hide_valid_later_source(tmp_path: Path) -> None:
    seed(tmp_path)
    path = tmp_path / "projects/project/hosts/host-a/layout.json"
    payload = read_object(path)
    payload["layout_hints"]["commit:abc1234"] = {"x": "bad", "y": 1}
    atomic_json(path, payload)
    assert sum(report["not_restored"]["invalid"] for report in layout_impact(tmp_path)) == 1
    normalize(tmp_path, frozenset({V16.contract}))
    assert read_object(tmp_path / "projects/project/git-layout.json")["nodes"]["commit:abc1234"]["x"] == 999


def test_fully_recovered_history_does_not_report_missing_layout(tmp_path: Path) -> None:
    seed(tmp_path)
    coordinator(tmp_path).apply("host-a", accept_data_loss=True)
    assert layout_recovery_guidance(tmp_path) == []


def test_historical_backup_corruption_is_not_reported_as_recoverable(tmp_path: Path) -> None:
    seed(tmp_path)
    coordinator(tmp_path).apply("host-a", accept_data_loss=True)
    path = next((tmp_path / "migration-backups").glob("*/0/projects/project/hosts/host-a/layout.json"))
    atomic_json(path, {"layout_hints": {}})
    with pytest.raises(MigrationError, match="摘要不匹配"):
        layout_recovery_guidance(tmp_path)


def test_release_keeps_published_edges_and_confirmation() -> None:
    check_manifest()
    manifest = read_manifest()
    assert manifest["minimum"] == 15 and manifest["target"] == 18
    assert [(entry["source"], entry["target"]) for entry in manifest["steps"]] == [(15, 16), (16, 17), (17, 18)]
    assert V16.destructive and not MIGRATION.destructive
