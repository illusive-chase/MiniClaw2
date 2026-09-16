from __future__ import annotations

from collections import Counter
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodePosition, NodeState, Project
from miniclaw2.migrations.errors import MigrationError
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


StoreFixture = tuple[Store, list[Project], dict[str, list[Node]]]


def _populate_store(
    root: Path, node_counts: list[int]
) -> StoreFixture:
    store = Store(root)
    projects: list[Project] = []
    nodes_by_project: dict[str, list[Node]] = {}
    for project_index, node_count in enumerate(node_counts):
        project = store.create_project(
            Project(root_path=str(root), created_at=float(project_index + 1))
        )
        projects.append(project)
        nodes: list[Node] = []
        positions_by_owner: dict[str, dict[str, dict[str, int | str]]] = {}
        for node_index in range(node_count):
            owner = "peer" if node_index == node_count - 1 else store.machine.id
            node = Node(
                project_id=project.id,
                model_preset_id=project.model_preset_id,
                created_at=float(node_count - node_index + 10),
                state=(
                    NodeState.QUEUED
                    if node_index in {0, node_count - 1}
                    else NodeState.DONE
                ),
                finished_at=float(node_index + 100) if node_index == 1 else None,
                planspace_id="lane" if node_index % 2 else None,
            ).bind_owner_host(owner)
            host = root / "projects" / project.id / "hosts" / owner
            atomic_json(
                host / "nodes" / node.id / "node.json",
                node.model_dump(exclude={"provider", "owner_host_id"}),
            )
            positions_by_owner.setdefault(owner, {})[node.id] = {
                "x": node_index * 10,
                "y": project_index * 20,
                "space": "planspace:lane" if node.planspace_id else "canvas",
            }
            nodes.append(node)
        for owner, positions in positions_by_owner.items():
            atomic_json(
                root / "projects" / project.id / "hosts" / owner / "node-layout.json",
                {"schema_version": 1, "nodes": positions},
            )
        nodes_by_project[project.id] = nodes
    return store, projects, nodes_by_project


@pytest.fixture
def populated_store(tmp_path: Path) -> StoreFixture:
    return _populate_store(tmp_path, [5, 4, 3, 0])


def _reads(reader: Mock, filename: str) -> Counter[Path]:
    return Counter(
        call.args[0] for call in reader.call_args_list if call.args[0].name == filename
    )


def test_list_nodes_reads_only_target_project_once(populated_store: StoreFixture) -> None:
    store, projects, nodes_by_project = populated_store
    project = projects[0]
    with patch.object(store, "_read_json", wraps=store._read_json) as reader:
        nodes = store.list_nodes(project.id)
    assert [node.id for node in nodes] == [
        node.id
        for node in sorted(nodes_by_project[project.id], key=lambda node: node.created_at)
    ]
    counts = _reads(reader, "node.json")
    assert len(counts) == len(nodes)
    assert set(counts.values()) == {1}
    assert {path.parents[4].name for path in counts} == {project.id}
    assert {path.parent.name for path in _reads(reader, "project.json")} == {project.id}
    assert not _reads(reader, "node-layout.json")
    assert store.project_last_activity_at(project.id) == 101.0
    assert {node.owner_host_id for node in nodes} == {store.machine.id, "peer"}


def test_missing_project_does_not_scan_other_projects(populated_store: StoreFixture) -> None:
    store, _, _ = populated_store
    with patch.object(store, "_read_json", wraps=store._read_json) as reader:
        assert store.list_nodes("missing") == []
        assert store.project_last_activity_at("missing") is None
    assert not _reads(reader, "node.json")
    assert not _reads(reader, "project.json")
    assert not _reads(reader, "node-layout.json")


def test_metadata_listing_does_not_load_nodes_or_layout(populated_store: StoreFixture) -> None:
    store, projects, _ = populated_store
    with patch.object(store, "_read_json", wraps=store._read_json) as reader:
        loaded = store.list_projects(include_node_positions=False)
    assert {project.id for project in loaded} == {project.id for project in projects}
    assert all(project.root_path == str(store.root) for project in loaded)
    assert all(project.node_positions == {} for project in loaded)
    assert not _reads(reader, "node.json")
    assert not _reads(reader, "node-layout.json")


def test_activity_refresh_reads_each_node_once(populated_store: StoreFixture) -> None:
    store, projects, nodes_by_project = populated_store
    with patch.object(store, "_read_json", wraps=store._read_json) as reader:
        store.refresh_last_activity_index()
    counts = _reads(reader, "node.json")
    assert len(counts) == sum(map(len, nodes_by_project.values()))
    assert set(counts.values()) == {1}
    assert not _reads(reader, "node-layout.json")
    assert store.project_last_activity_at(projects[-1].id) == projects[-1].created_at


def test_activity_cache_miss_reads_only_target_project(populated_store: StoreFixture) -> None:
    store, projects, _ = populated_store
    with patch.object(store, "_read_json", wraps=store._read_json) as reader:
        assert store.project_last_activity_at(projects[0].id) == 101.0
    counts = _reads(reader, "node.json")
    assert len(counts) == 5
    assert set(counts.values()) == {1}
    assert {path.parents[4].name for path in counts} == {projects[0].id}
    assert not _reads(reader, "node-layout.json")


def test_store_initialization_reads_each_node_once(populated_store: StoreFixture) -> None:
    store, _, nodes_by_project = populated_store
    with patch.object(Store, "_read_json", wraps=Store._read_json) as reader:
        Store(store.root)
    counts = _reads(reader, "node.json")
    assert len(counts) == sum(map(len, nodes_by_project.values()))
    assert set(counts.values()) == {1}
    assert not _reads(reader, "node-layout.json")


def test_layout_update_reads_nodes_once(populated_store: StoreFixture) -> None:
    store, projects, nodes_by_project = populated_store
    project = projects[0]
    nodes = nodes_by_project[project.id]
    position = NodePosition(x=55, y=66, space="canvas")
    with patch.object(store, "_read_json", wraps=store._read_json) as reader:
        positions = store.update_node_positions(project.id, {nodes[0].id: position}, [])
    counts = _reads(reader, "node.json")
    assert len(counts) == len(nodes)
    assert set(counts.values()) == {1}
    assert positions[nodes[0].id] == position
    assert set(positions) == {node.id for node in nodes}


@pytest.mark.parametrize("node_counts", [[5, 4, 3, 0], [407, *([26] * 17), 22], [2000]])
def test_sessions_read_no_node_bodies_or_layout(tmp_path: Path, node_counts: list[int]) -> None:
    store, projects, nodes_by_project = _populate_store(tmp_path, node_counts)
    registry = ProjectRegistry(store)
    client = TestClient(create_app(registry=registry))
    try:
        with patch.object(store, "_read_json", wraps=store._read_json) as reader:
            response = client.get("/sessions")
        assert response.status_code == 200, response.text
        assert not _reads(reader, "node.json")
        for filename in ("node-layout.json", "git-layout.json", "lane-layout.json", "context-layout.json"):
            assert not _reads(reader, filename)
        infos = {info["id"]: info for info in response.json()}
        for project in projects:
            nodes = nodes_by_project[project.id]
            info = infos[project.id]
            assert info["turns"] == len(nodes)
            assert info["queued_count"] == (1 if nodes else 0)
            assert not {"node_positions", "git_positions", "lane_positions", "context_positions"} & info.keys()
            assert info["last_activity_at"] == max(
                (
                    timestamp
                    for node in nodes
                    for timestamp in (node.created_at, node.finished_at)
                    if timestamp is not None
                ),
                default=project.created_at,
            )
        target = projects[0]
        node = nodes_by_project[target.id][0]
        store.update_node_positions(
            target.id, {node.id: NodePosition(x=99, y=77, space="canvas")}, []
        )
        node.state = NodeState.DONE
        node.finished_at = 10000.0
        store.update_node(node)
        with patch.object(store, "_read_json", wraps=store._read_json) as reader:
            response = client.get(f"/sessions/{target.id}")
        assert response.status_code == 200, response.text
        counts = _reads(reader, "node.json")
        assert len(counts) == len(nodes_by_project[target.id])
        assert set(counts.values()) == {1}
        info = response.json()
        assert info["node_positions"][node.id] == {"x": 99, "y": 77, "space": "canvas"}
        assert info["queued_count"] == 0
        assert info["last_activity_at"] == 10000.0
    finally:
        client.close()


def test_node_summary_tracks_writes_and_deletions(populated_store: StoreFixture) -> None:
    store, projects, _ = populated_store
    registry = ProjectRegistry(store)
    project = projects[-1]
    assert registry.node_summary(project).last_activity_at == project.created_at
    node = store.create_node(
        Node(
            project_id=project.id,
            model_preset_id=project.model_preset_id,
            created_at=0.0,
            state=NodeState.QUEUED,
            origin_machine_id="peer",
        )
    )
    with patch.object(store, "_read_json", wraps=store._read_json) as reader:
        summary = registry.node_summary(project)
    assert not _reads(reader, "node.json")
    assert (summary.turns, summary.queued_count, summary.last_activity_at) == (1, 1, 0.0)
    node.state = NodeState.RUNNING
    node.started_at = 200.0
    assert registry.node_summary(project) == summary
    store.update_node(node)
    for state, finished_at, expected_activity in (
        (NodeState.RUNNING, None, 200.0),
        (NodeState.DONE, 300.0, 300.0),
        (NodeState.DONE, 100.0, 200.0),
    ):
        node.state = state
        node.finished_at = finished_at
        store.update_node(node)
        with patch.object(store, "_read_json", wraps=store._read_json) as reader:
            summary = registry.node_summary(project)
        assert not _reads(reader, "node.json")
        assert (summary.turns, summary.queued_count) == (1, 0)
        assert summary.last_activity_at == expected_activity
    assert store.delete_node(project.id, node.id)
    summary = registry.node_summary(project)
    assert (summary.turns, summary.queued_count) == (0, 0)
    assert summary.last_activity_at == project.created_at
    assert store.delete_project(project.id)
    assert project.id not in store._node_summary_index


def test_node_summary_rereads_only_externally_changed_nodes(
    populated_store: StoreFixture,
) -> None:
    store, projects, nodes_by_project = populated_store
    registry = ProjectRegistry(store)
    project = projects[0]
    node = nodes_by_project[project.id][0]
    node.state = NodeState.DONE
    node.finished_at = 999.0
    atomic_json(
        store.node_dir(project.id, node.id) / "node.json",
        node.model_dump(exclude={"provider", "owner_host_id"}),
    )
    with patch.object(store, "_read_json", wraps=store._read_json) as reader:
        summary = registry.node_summary(project)
    assert sum(_reads(reader, "node.json").values()) == 1
    assert summary.turns == len(nodes_by_project[project.id])
    assert summary.queued_count == 0
    assert summary.last_activity_at == 999.0
    with patch.object(store, "_read_json", wraps=store._read_json) as reader:
        assert registry.node_summary(project) == summary
    assert not _reads(reader, "node.json")


def test_node_summary_rejects_invalid_changed_record(populated_store: StoreFixture) -> None:
    store, projects, nodes_by_project = populated_store
    registry = ProjectRegistry(store)
    project = projects[0]
    node = nodes_by_project[project.id][0]
    payload = node.model_dump(exclude={"provider", "owner_host_id"})
    payload["state"] = "invalid"
    atomic_json(store.node_dir(project.id, node.id) / "node.json", payload)
    with pytest.raises(MigrationError):
        registry.node_summary(project)


def test_reopened_store_seeds_node_summaries(populated_store: StoreFixture) -> None:
    store, projects, nodes_by_project = populated_store
    reopened = Store(store.root)
    with patch.object(reopened, "_read_json", wraps=reopened._read_json) as reader:
        for project in projects:
            assert len(reopened.node_summaries(project.id)) == len(nodes_by_project[project.id])
    assert not _reads(reader, "node.json")


@pytest.mark.parametrize("callbacks", ["_success_callbacks", "_publication_callbacks"])
def test_sync_rebuilds_node_summaries(populated_store: StoreFixture, callbacks: str) -> None:
    store, projects, nodes_by_project = populated_store
    registry = ProjectRegistry(store)
    project = projects[0]
    removed = nodes_by_project[project.id][0]
    (store.node_dir(project.id, removed.id) / "node.json").unlink()
    incoming = Node(
        project_id=project.id,
        model_preset_id=project.model_preset_id,
        state=NodeState.QUEUED,
        created_at=1000.0,
        origin_machine_id=store.machine.id,
    )
    atomic_json(
        store.root / "projects" / project.id / "hosts" / "peer" / "nodes"
        / incoming.id / "node.json",
        incoming.model_dump(exclude={"provider", "owner_host_id"}),
    )
    for callback in getattr(store.sync, callbacks):
        callback()
    with patch.object(store, "_read_json", wraps=store._read_json) as reader:
        summary = registry.node_summary(project)
    assert not _reads(reader, "node.json")
    assert summary.turns == len(nodes_by_project[project.id])
    assert summary.queued_count == 0
    assert summary.last_activity_at == 1000.0
