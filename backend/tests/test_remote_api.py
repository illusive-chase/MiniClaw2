from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.registry import ProjectRegistry
from miniclaw2.remote_transport import RemoteRepositoryProbe, RemoteTransportError
from miniclaw2.store import Store


@pytest.mark.parametrize("mode", ["existing", "cloned", "init"])
def test_remote_entry_and_host_local_configuration(tmp_path: Path, mode: str) -> None:
    registry = ProjectRegistry(Store(tmp_path / "store"))
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w"):
        pass
    transport = Mock()
    transport.probe_repository.return_value = RemoteRepositoryProbe(root_commits=("a" * 40,))
    transport.export_tracked_files.return_value = archive.getvalue()
    payload = {"persistence_mode": "remote", "remote": {"target_id": "test", "root_path": "/srv/test"},
               "remote_access": {"ssh_target": "test"}, "remote_initialization": mode}
    if mode == "cloned":
        payload["remote_clone_url"] = "git@host:owner/repo.git"
    with patch.object(registry._remote_transport_pool(), "get", return_value=transport), patch("miniclaw2.app.install_hooks"):
        with TestClient(create_app(registry)) as client:
            response = client.post("/sessions", json=payload)
            assert response.status_code == 200, response.text
            pid = response.json()["id"]
            if mode == "existing":
                transport.initialize_repository.assert_not_called()
            else:
                transport.initialize_repository.assert_called_once_with("/srv/test", mode, payload.get("remote_clone_url"))
            host_path = registry.store.root / f"projects/{pid}/hosts/{registry.store.machine.id}/host.json"
            audit = json.loads(host_path.read_text())["remote_initialization"]
            assert audit["mode"] == mode
            assert audit.get("clone_url") == payload.get("remote_clone_url")
            configured = client.put(f"/sessions/{pid}/remote-access", json={
                "ssh_target": "test", "codex_remote_experimental": True, "sandbox": "externalSandbox",
            })
            assert configured.status_code == 200, configured.text
            assert configured.json()["remote_access"]["codex_remote_experimental"]
            shared = (registry.store.root / f"projects/{pid}/project.json").read_text()
            assert "ssh_target" not in shared and "externalSandbox" not in shared
            with patch.object(registry, "quiescent", return_value=False):
                assert client.put(f"/sessions/{pid}/remote-access", json={"ssh_target": "test"}).status_code == 409
            original = registry.store.read_remote_binding(pid)
            transport.probe_repository.return_value = RemoteRepositoryProbe(root_commits=("b" * 40,))
            assert client.put(f"/sessions/{pid}/remote-access", json={"ssh_target": "other"}).status_code == 400
            assert registry.store.read_remote_binding(pid) == original


def test_failed_initialization_does_not_create_project_or_delete_remote(tmp_path: Path) -> None:
    registry = ProjectRegistry(Store(tmp_path / "store"))
    transport = Mock()
    transport.probe_repository.side_effect = RemoteTransportError("没有根提交")
    with patch.object(registry._remote_transport_pool(), "get", return_value=transport), patch("miniclaw2.app.install_hooks"):
        with TestClient(create_app(registry)) as client:
            response = client.post("/sessions", json={
                "persistence_mode": "remote", "remote": {"target_id": "test", "root_path": "/srv/test"},
                "remote_access": {"ssh_target": "test"}, "remote_initialization": "init",
            })
            assert response.status_code == 400
            assert "/srv/test" in response.json()["detail"]
            assert "未自动删除" in response.json()["detail"]
            assert not registry.list_projects()
            transport.run.assert_not_called()
