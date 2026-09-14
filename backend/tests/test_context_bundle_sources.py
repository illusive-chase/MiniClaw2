from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodeKind, NodeState, Project
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


@pytest.fixture
def bundle_api(tmp_path: Path):
    store = Store(tmp_path / "store")
    project = store.create_project(Project(root_path=str(tmp_path)))
    foreign = store.create_project(Project(root_path=str(tmp_path)))
    registry = ProjectRegistry(store=store)
    bundle = {
        "bundle_id": "snapshot",
        "created_at": 1,
        "sources": [{
            "scope": "contextspace", "kind": "principle", "path": "principles/test.md",
            "sha256": "hash", "chars": 100, "injection": "system",
            "plug_id": "principles.test",
        }],
        "active_planspace": {"id": "lane", "color": "#123456", "title": "泳道"},
        "system_text": "系统上下文" * 1000,
        "turn_text": "轮次上下文" * 1000,
    }
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(bundle), encoding="utf-8")
    nodes = {}
    for state in NodeState:
        nodes[state] = store.create_node(Node(
            project_id=project.id, model_preset_id=project.model_preset_id,
            state=state, context_bundle_path=str(snapshot),
        ))
    store.create_node(Node(
        project_id=foreign.id, model_preset_id=foreign.model_preset_id,
        state=NodeState.DONE, context_bundle_path=str(snapshot),
    ))
    client = TestClient(create_app(registry))
    try:
        yield client, registry, project, nodes, bundle, snapshot
    finally:
        client.close()


def test_sources_only_and_full_detail_contract(bundle_api) -> None:
    client, registry, project, nodes, bundle, snapshot = bundle_api
    reads: Counter[Path] = Counter()
    original_read = Path.read_text

    def read_text(path: Path, *args, **kwargs):
        reads[path] += 1
        return original_read(path, *args, **kwargs)

    with patch.object(Path, "read_text", read_text), patch.object(
        registry.store, "list_nodes", wraps=registry.store.list_nodes,
    ) as list_nodes:
        response = client.get(f"/sessions/{project.id}/context-bundles")
    assert response.status_code == 200
    list_nodes.assert_called_once_with(project.id)
    expected = {"sources": bundle["sources"], "active_planspace": bundle["active_planspace"]}
    assert response.json() == {
        nodes[state].id: expected for state in (NodeState.DONE, NodeState.ERROR, NodeState.CANCELLED)
    }
    assert reads[snapshot] == 3
    detail = client.get(f"/sessions/{project.id}/nodes/{nodes[NodeState.DONE].id}/context-bundle")
    assert detail.json() == bundle
    assert json.loads(snapshot.read_text()) == bundle


def test_filtering_and_missing_projects(bundle_api) -> None:
    client, registry, project, nodes, bundle, snapshot = bundle_api
    node_id = nodes[NodeState.DONE].id
    response = client.get(f"/sessions/{project.id}/context-bundles", params=[
        ("node_ids", node_id), ("node_ids", node_id),
        ("node_ids", nodes[NodeState.RUNNING].id), ("node_ids", "foreign-or-missing"),
    ])
    assert set(response.json()) == {node_id}
    assert client.get("/sessions/missing/context-bundles").status_code == 404
    assert client.get(f"/sessions/{project.id}/context-bundles?node_ids=missing").json() == {}


def test_missing_corrupt_and_legacy_references(bundle_api, monkeypatch, tmp_path: Path) -> None:
    client, registry, project, nodes, bundle, snapshot = bundle_api
    context_root = tmp_path / "contextspace"
    (context_root / "snapshots").mkdir(parents=True)
    monkeypatch.setenv("MINICLAW_CONTEXT_HOME", str(context_root))
    (context_root / "snapshots" / "legacy.json").write_text(json.dumps(bundle))
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{")
    malformed = tmp_path / "malformed.json"
    malformed.write_text("[]")
    invalid_source = tmp_path / "invalid-source.json"
    invalid_source.write_text('{"sources": [null]}')
    invalid_utf8 = tmp_path / "invalid-utf8.json"
    invalid_utf8.write_bytes(b"\xff")
    additions = {}
    for label, updates in {
        "legacy": {"context_bundle_id": "legacy"},
        "relative": {"context_bundle_path": "snapshots/legacy.json"},
        "missing": {"context_bundle_path": str(tmp_path / "missing.json")},
        "corrupt": {"context_bundle_path": str(corrupt)},
        "malformed": {"context_bundle_path": str(malformed)},
        "invalid_source": {"context_bundle_path": str(invalid_source)},
        "invalid_utf8": {"context_bundle_path": str(invalid_utf8)},
        "no_bundle": {},
        "op": {"kind": NodeKind.OP, "context_bundle_path": str(snapshot)},
        "verifier": {
            "kind": NodeKind.VERIFIER, "context_bundle_path": str(snapshot),
            "category": "review", "subtype": "programmatic_review", "verify_script_ref": "/tmp/check.sh",
            "brief": {"check_what": "来源", "expected": "可读取", "abnormal": "缺失"},
        },
    }.items():
        additions[label] = registry.store.create_node(Node(
            project_id=project.id, model_preset_id=project.model_preset_id,
            state=NodeState.DONE, **updates,
        ))
    response = client.get(f"/sessions/{project.id}/context-bundles")
    assert response.status_code == 200
    result = response.json()
    for label in ("missing", "corrupt", "malformed", "invalid_source", "invalid_utf8"):
        assert result[additions[label].id] is None
    for label in ("legacy", "relative", "verifier"):
        assert result[additions[label].id]["sources"] == bundle["sources"]
    for label in ("no_bundle", "op"):
        assert additions[label].id not in result


def test_2000_nodes_read_each_snapshot_once(bundle_api) -> None:
    client, registry, project, nodes, bundle, snapshot = bundle_api
    scale_nodes = [nodes[NodeState.DONE].model_copy(update={
        "id": f"node-{index}", "context_bundle_path": str(snapshot.with_name(f"bundle-{index}.json")),
    }) for index in range(2000)]
    reads: Counter[Path] = Counter()
    text = json.dumps(bundle)
    original_read = Path.read_text

    def read_text(path: Path, *args, **kwargs):
        if path.name.startswith("bundle-"):
            reads[path] += 1
            return text
        return original_read(path, *args, **kwargs)

    with patch.object(registry, "list_nodes", return_value=scale_nodes) as list_nodes, patch.object(
        Path, "read_text", read_text,
    ):
        response = client.get(f"/sessions/{project.id}/context-bundles")
    assert response.status_code == 200
    list_nodes.assert_called_once_with(project.id)
    assert len(response.json()) == 2000
    assert reads == Counter({Path(node.context_bundle_path): 1 for node in scale_nodes})
    assert len(response.content) < len(text.encode()) * 2000 * 0.1


def test_empty_project(bundle_api) -> None:
    client, registry, project, nodes, bundle, snapshot = bundle_api
    empty = registry.store.create_project(Project(root_path=project.root_path))
    empty_client = TestClient(create_app(ProjectRegistry(store=registry.store)))
    try:
        response = empty_client.get(f"/sessions/{empty.id}/context-bundles")
        assert response.status_code == 200
        assert response.json() == {}
    finally:
        empty_client.close()
