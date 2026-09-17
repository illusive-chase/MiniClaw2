from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from miniclaw2.domain import (
    Node,
    NodeKind,
    NodeState,
    Project,
    RemoteAccessConfig,
    RemoteProjectBinding,
)
from miniclaw2.remote_projection import ProjectionSyncResult, sync_remote_projection
from miniclaw2.remote_transport import RemoteTransportError
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


def _archive(entries: dict[str, bytes | tuple[str, str]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, value in entries.items():
            info = tarfile.TarInfo(name)
            if isinstance(value, tuple):
                info.type = tarfile.SYMTYPE
                info.linkname = value[1]
                archive.addfile(info)
            else:
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
    return output.getvalue()


def _binding(root: Path, **updates: object) -> RemoteProjectBinding:
    binding = RemoteProjectBinding(
        remote=RemoteAccessConfig(ssh_target="gpu-box"),
        projection_path=str(root),
    )
    return binding.model_copy(update=updates)


def test_sync_replaces_projection_preserves_lane_and_reports_distortion(
    tmp_path: Path,
) -> None:
    projection = tmp_path / "projection"
    transport = Mock()
    transport.export_tracked_files.return_value = _archive(
        {"src/app.py": b"first\n", "link": ("symlink", "src/app.py")}
    )

    first = sync_remote_projection(
        _binding(projection), remote_root="/srv/project", transport=transport
    )
    lane = projection / ".miniclaw2" / "graph" / "lane.json"
    lane.parent.mkdir(parents=True)
    lane.write_text("local lane", encoding="utf-8")
    (projection / "local-only.txt").write_text("discard me", encoding="utf-8")
    (projection / "src/app.py").write_text("locally changed\n", encoding="utf-8")
    transport.export_tracked_files.return_value = _archive(
        {"src/app.py": b"second\n", "README.md": b"remote\n"}
    )

    second = sync_remote_projection(
        first.binding, remote_root="/srv/project", transport=transport
    )

    assert second.distorted_paths == ("src/app.py",)
    assert second.file_count == 2
    assert (projection / "src/app.py").read_bytes() == b"second\n"
    assert (projection / "README.md").read_bytes() == b"remote\n"
    assert not (projection / "link").exists()
    assert not (projection / "local-only.txt").exists()
    assert lane.read_text(encoding="utf-8") == "local lane"
    assert second.binding.projection_hashes["src/app.py"] == hashlib.sha256(
        b"second\n"
    ).hexdigest()
    assert second.binding.projection_synced_at is not None


def test_sync_rejects_archive_traversal_without_replacing_projection(
    tmp_path: Path,
) -> None:
    projection = tmp_path / "projection"
    projection.mkdir()
    marker = projection / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    transport = Mock()
    transport.export_tracked_files.return_value = _archive({"../escape": b"bad"})

    with pytest.raises(RemoteTransportError, match="路径越界"):
        sync_remote_projection(
            _binding(projection), remote_root="/srv/project", transport=transport
        )

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "escape").exists()


def test_sync_rejects_symlink_outside_projection(tmp_path: Path) -> None:
    projection = tmp_path / "projection"
    transport = Mock()
    transport.export_tracked_files.return_value = _archive(
        {"outside": ("symlink", "../../secret")}
    )

    with pytest.raises(RemoteTransportError, match="符号链接越界"):
        sync_remote_projection(
            _binding(projection), remote_root="/srv/project", transport=transport
        )

    assert not projection.exists()


def test_sync_rejects_symlinked_projection_root_without_touching_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    control = outside / ".miniclaw2"
    control.mkdir(parents=True)
    marker = control / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    projection = tmp_path / "projection"
    projection.symlink_to(outside, target_is_directory=True)
    transport = Mock()

    with pytest.raises(RemoteTransportError, match="投影根目录不能是符号链接"):
        sync_remote_projection(
            _binding(projection), remote_root="/srv/project", transport=transport
        )

    assert projection.is_symlink()
    assert marker.read_text(encoding="utf-8") == "keep"
    transport.export_tracked_files.assert_not_called()


def test_sync_rejects_symlinked_backup_root_without_touching_target(
    tmp_path: Path,
) -> None:
    projection = tmp_path / "projection"
    projection.mkdir()
    marker = projection / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    backup = tmp_path / ".projection.projection-backup"
    backup.symlink_to(outside, target_is_directory=True)
    transport = Mock()

    with pytest.raises(RemoteTransportError, match="备份目录不能是符号链接"):
        sync_remote_projection(
            _binding(projection), remote_root="/srv/project", transport=transport
        )

    assert backup.is_symlink()
    assert marker.read_text(encoding="utf-8") == "keep"
    transport.export_tracked_files.assert_not_called()


def test_sync_detects_deleted_and_retargeted_symlinks(tmp_path: Path) -> None:
    projection = tmp_path / "projection"
    projection.mkdir()
    (projection / "missing.txt").write_text("then deleted", encoding="utf-8")
    (projection / "link").symlink_to("old-target")
    hashes = {
        "missing.txt": hashlib.sha256(b"then deleted").hexdigest(),
        "link": hashlib.sha256(b"symlink:expected-target").hexdigest(),
    }
    (projection / "missing.txt").unlink()
    transport = Mock()
    transport.export_tracked_files.return_value = _archive({"next.txt": b"next"})

    result = sync_remote_projection(
        _binding(projection, projection_hashes=hashes),
        remote_root="/srv/project",
        transport=transport,
    )

    assert result.distorted_paths == ("link", "missing.txt")


def test_runner_persists_projection_distortion_event(tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    project_root = tmp_path / "repo"
    project_root.mkdir()
    project = Project(root_path=str(project_root))
    store.create_project(project)
    node = store.create_node(
        Node(
            project_id=project.id,
            kind=NodeKind.OP,
            op_kind="unsupported",
            state=NodeState.QUEUED,
            model_preset_id=project.model_preset_id,
        )
    )
    binding = _binding(tmp_path / "projection")

    async def prepare() -> ProjectionSyncResult:
        return ProjectionSyncResult(
            binding=binding,
            distorted_paths=("src/changed.py",),
            file_count=1,
        )

    events: list[dict[str, object]] = []

    async def collect(event: dict[str, object]) -> None:
        events.append(event)

    asyncio.run(
        NodeRunner(node, project, store, collect, prepare_workspace=prepare).run()
    )

    distortion = next(
        event
        for event in events
        if event.get("type") == "activity"
        and event.get("name") == "remote_projection"
    )
    assert distortion["result"] == "src/changed.py"
    replay = store.replay_events(project.id, node.id)
    assert any(
        record["event"].get("name") == "remote_projection" for record in replay
    )


def test_runner_fails_closed_when_projection_sync_fails(tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    project_root = tmp_path / "repo"
    project_root.mkdir()
    project = Project(root_path=str(project_root))
    store.create_project(project)
    node = store.create_node(
        Node(
            project_id=project.id,
            state=NodeState.QUEUED,
            model_preset_id=project.model_preset_id,
        )
    )

    async def prepare() -> ProjectionSyncResult:
        raise RemoteTransportError("连接已断开")

    events: list[dict[str, object]] = []

    async def collect(event: dict[str, object]) -> None:
        events.append(event)

    asyncio.run(
        NodeRunner(node, project, store, collect, prepare_workspace=prepare).run()
    )

    assert node.state is NodeState.ERROR
    assert node.error == "远端源码投影同步失败：连接已断开"
    assert [event["type"] for event in events] == [
        "node_started",
        "error",
        "node_updated",
        "turn_done",
    ]
