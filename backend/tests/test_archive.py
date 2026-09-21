from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from miniclaw2.app import create_app
from miniclaw2.contextspace import (
    create_planspace,
    read_planspace_archived,
    set_planspace_archived,
    set_planspace_mode,
)
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.registry import (
    PlanspaceArchivedError,
    ProjectArchivedError,
    ProjectRegistry,
)
from miniclaw2.store import Store


@pytest.fixture
def archive_registry(tmp_path: Path) -> tuple[ProjectRegistry, Project]:
    os.environ["MINICLAW_CONTEXT_HOME"] = str(tmp_path / "context")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = Store(tmp_path / "store")
    project = store.create_project(Project(root_path=str(workspace), name="archive"))
    return ProjectRegistry(store=store), project


def test_project_archive_is_reversible_and_blocks_execution(
    archive_registry: tuple[ProjectRegistry, Project],
) -> None:
    registry, project = archive_registry
    node = registry.create_virtual(
        project.id,
        prompt_draft="kept",
        planspace_id=registry.create_blank_planspace(
            project.id,
            title="Lane",
            seed="seed",
            mode="manual",
        ).node.planspace_id,
    )
    assert node is not None

    archived, busy = registry.archive_project(project.id)

    assert busy == []
    assert archived is not None and archived.archived_at is not None
    assert registry.store.list_projects(include_archived=False) == []
    with pytest.raises(ProjectArchivedError):
        registry.list_nodes(project.id)
    with pytest.raises(ProjectArchivedError):
        registry.start_node(project.id, "blocked")

    restored = registry.unarchive_project(project.id)
    assert restored is not None and restored.archived_at is None
    assert {item.id for item in registry.list_nodes(project.id) or []} >= {node.id}


def test_project_archive_rejects_queued_work(
    archive_registry: tuple[ProjectRegistry, Project],
) -> None:
    registry, project = archive_registry
    queued = registry.store.create_node(
        Node(
            project_id=project.id,
            model_preset_id=project.model_preset_id,
            state=NodeState.QUEUED,
        )
    )

    archived, busy = registry.archive_project(project.id)

    assert archived is None
    assert busy == [queued.id]
    assert project.archived_at is None


def test_lane_archive_filters_nodes_before_registry_projection(
    archive_registry: tuple[ProjectRegistry, Project],
) -> None:
    registry, project = archive_registry
    archived_lane = registry.create_blank_planspace(
        project.id,
        title="Old",
        seed="old",
        mode="manual",
    ).node.planspace_id
    visible_lane = registry.create_blank_planspace(
        project.id,
        title="Current",
        seed="current",
        mode="manual",
    ).node.planspace_id
    assert archived_lane and visible_lane
    set_planspace_mode(
        project,
        archived_lane,
        "auto",
        store_root=registry.store.root,
    )

    changed, busy = registry.set_planspace_archive(
        project.id,
        archived_lane,
        archived=True,
    )

    assert changed and busy == []
    assert read_planspace_archived(
        project,
        archived_lane,
        store_root=registry.store.root,
    )
    assert archived_lane not in registry._auto_lane_ids(project)
    with patch.object(Node, "model_validate", wraps=Node.model_validate) as validate:
        assert {node.planspace_id for node in registry.list_nodes(project.id) or []} == {
            visible_lane
        }
    assert validate.call_count == 1
    with pytest.raises(PlanspaceArchivedError):
        registry.create_virtual(
            project.id,
            prompt_draft="blocked",
            planspace_id=archived_lane,
        )

    changed, busy = registry.set_planspace_archive(
        project.id,
        archived_lane,
        archived=False,
    )
    assert changed and busy == []
    assert {node.planspace_id for node in registry.list_nodes(project.id) or []} == {
        archived_lane,
        visible_lane,
    }


def test_project_cannot_archive_another_projects_lane(
    archive_registry: tuple[ProjectRegistry, Project],
    tmp_path: Path,
) -> None:
    registry, project = archive_registry
    other = registry.store.create_project(
        Project(root_path=str(tmp_path / "other"), name="other")
    )
    foreign_lane = create_planspace(
        other,
        title="Foreign",
        store_root=registry.store.root,
    )

    with pytest.raises(ValueError, match="unknown planspace for project"):
        set_planspace_archived(
            project,
            foreign_lane,
            True,
            store_root=registry.store.root,
        )
    assert not read_planspace_archived(
        other,
        foreign_lane,
        store_root=registry.store.root,
    )


def test_archive_api_keeps_metadata_entry_but_closes_canvas(
    archive_registry: tuple[ProjectRegistry, Project],
) -> None:
    registry, project = archive_registry
    registry.create_blank_planspace(
        project.id,
        title="Lane",
        seed="seed",
        mode="manual",
    )

    with patch("miniclaw2.app.install_hooks"), TestClient(create_app(registry)) as client:
        response = client.patch(
            f"/sessions/{project.id}/archive",
            json={"archived": True},
        )
        assert response.status_code == 200
        assert response.json()["archived_at"] is not None
        assert client.get(f"/sessions/{project.id}").status_code == 200
        assert client.get(f"/sessions/{project.id}/nodes").status_code == 409
        assert client.get(f"/sessions/{project.id}/contextspace").status_code == 409
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/ws/{project.id}"):
                pass
        assert closed.value.code == 4403

        response = client.patch(
            f"/sessions/{project.id}/archive",
            json={"archived": False},
        )
        assert response.status_code == 200
        assert response.json()["archived_at"] is None
        assert client.get(f"/sessions/{project.id}/nodes").status_code == 200
