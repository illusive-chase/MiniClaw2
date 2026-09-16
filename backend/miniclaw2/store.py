"""JSONL/JSON disk store for projects, nodes, gates, and event streams.

Layout under ``$MINICLAW_HOME`` (default ``~/.miniclaw2``)::

    projects/
      <pid>/
        project.json
        hosts/
          <machine-id>/
            nodes/
              <nid>/
                node.json
                events.jsonl
                gates.jsonl
Each node has a single runner and therefore a single event writer. Different
nodes in one project may run concurrently; project-wide graph reconciliation
is serialized by ``ProjectRuntime`` while per-node records remain independent.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .domain import ContextLayout, GitLayout, GitPosition, HumanGate, LaneLayout, LanePosition, Node, NodeLayout, NodePosition, NodeState, Project, UNBOUND_ROOT_PATH
from .node_layout import node_coordinate_space, node_layout_owners
from .git_state import is_git_repo, normalized_origin_url, root_commits
from .replay import EVENT_SCHEMA_VERSION
from .migrations.coordinator import open_storage
from .migrations.errors import MigrationError
from .migrations.transaction import atomic_json
from .migrations.access import storage_methods
from .sync import (
    MachineIdentity,
    SyncError,
    SyncManager,
    ensure_machine_identity,
    ensure_store_metadata,
    get_sync_manager,
    load_machine_identity,
    schema_is_newer,
)
from .tags import (
    Tag,
    create_tag as create_global_tag,
    delete_tag as delete_global_tag,
    load_tags,
    update_tag as update_global_tag,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeSummary:
    id: str
    state: NodeState
    owner_host_id: str
    last_activity_at: float


NodeSignature = tuple[int, int, int, int]
NodeSummaryEntry = tuple[NodeSignature, NodeSummary]


class StoreReadOnlyError(RuntimeError):
    """The store cannot safely accept writes on this machine."""


def _root() -> Path:
    base = os.environ.get("MINICLAW_HOME")
    return Path(base).expanduser() if base else Path.home() / ".miniclaw2"


@storage_methods
class Store:
    """Filesystem-backed store with a per-project node-owner path index."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or _root()).expanduser()
        self.coordinator = open_storage(self.root)
        (self.root / "projects").mkdir(parents=True, exist_ok=True)
        self.machine: MachineIdentity = ensure_machine_identity(self.root)
        ensure_store_metadata(self.root, self.machine)
        from .global_config import ensure_global_config

        try:
            ensure_global_config(self.root)
        except (OSError, ValueError) as exc:
            raise MigrationError("migration_failed", str(exc), self.root / "config.json") from exc
        self.sync: SyncManager = get_sync_manager(self.root, self.machine)
        self._owner_index: dict[str, dict[str, str]] = {}
        self._last_activity_index: dict[str, float] = {}
        self._node_summary_index: dict[str, dict[Path, NodeSummaryEntry]] = {}
        self.sync.add_success_callback(self.invalidate_owner_index)
        self.sync.add_success_callback(self._refresh_last_activity_after_sync)
        self.sync.add_publication_callback(self.invalidate_owner_index)
        self.sync.add_publication_callback(self._refresh_last_activity_after_sync)
        self.refresh_last_activity_index()

    @property
    def read_only_reason(self) -> str | None:
        try:
            self.coordinator.assert_current()
        except MigrationError as exc:
            return str(exc)
        if schema_is_newer(self.root):
            return "store schema is newer than this MiniClaw2 version"
        try:
            if load_machine_identity(self.root).id != self.machine.id:
                return "设备身份已变更，请重启 MiniClaw2 后再写入"
        except SyncError:
            return "设备身份文件不可用，请恢复后重启 MiniClaw2"
        return None

    # ---- paths ----

    def _project_dir(self, pid: str) -> Path:
        return self.root / "projects" / pid

    def _project_file(self, pid: str) -> Path:
        return self._project_dir(pid) / "project.json"

    def _hosts_dir(self, pid: str) -> Path:
        return self._project_dir(pid) / "hosts"

    def _host_dir(self, pid: str, machine_id: str) -> Path:
        return self._hosts_dir(pid) / machine_id

    def is_bound_here(self, pid: str) -> bool:
        """Whether this host has a local checkout path for the project."""
        return (self._host_dir(pid, self.machine.id) / "local.json").is_file()

    def _owner_mid(self, pid: str, nid: str) -> str:
        return self._owner_index.get(pid, {}).get(nid, self.machine.id)

    def node_dir(self, pid: str, nid: str) -> Path:
        owner = self._owner_mid(pid, nid)
        return self._host_dir(pid, owner) / "nodes" / nid

    def _node_file(self, pid: str, nid: str) -> Path:
        return self.node_dir(pid, nid) / "node.json"

    def _events_file(self, pid: str, nid: str) -> Path:
        return self.node_dir(pid, nid) / "events.jsonl"

    def _gates_file(self, pid: str, nid: str) -> Path:
        return self.node_dir(pid, nid) / "gates.jsonl"

    def _preview_file(self, pid: str, nid: str) -> Path:
        return self.node_dir(pid, nid) / "preview.json"

    def _git_aliases_file(self, pid: str) -> Path:
        return self._host_dir(pid, self.machine.id) / "git_aliases.json"

    def _head_file(self, pid: str, machine_id: str) -> Path:
        return self._host_dir(pid, machine_id) / "head.json"

    def invalidate_owner_index(self) -> None:
        """Drop path ownership cached before a sync or layout migration."""
        self._owner_index.clear()

    def refresh_last_activity_index(self) -> None:
        """Rebuild project activity timestamps from all persisted nodes."""
        self._last_activity_index.clear()
        self._node_summary_index.clear()
        for project in self.list_projects(include_node_positions=False):
            self._list_nodes_for_project(project.id, project)

    def _refresh_last_activity_after_sync(self) -> None:
        try:
            self.refresh_last_activity_index()
        except Exception:  # noqa: BLE001
            logger.exception("failed to refresh project activity after sync")

    def project_last_activity_at(self, pid: str) -> float | None:
        if pid not in self._last_activity_index:
            project = self._load_project(pid)
            if project is None:
                return None
            self._list_nodes_for_project(project.id, project)
        return self._last_activity_index.get(pid)

    def record_project_activity(self, pid: str, activity_at: float) -> None:
        current = self._last_activity_index.get(pid)
        if current is None or activity_at > current:
            self._last_activity_index[pid] = activity_at

    @staticmethod
    def _node_signature(path: Path) -> NodeSignature:
        stat = path.stat()
        return stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size

    def _cache_node_summary(
        self, node: Node, path: Path, signature: NodeSignature
    ) -> NodeSummaryEntry:
        summary = NodeSummary(
            id=node.id,
            state=node.state,
            owner_host_id=path.parents[2].name,
            last_activity_at=max(
                timestamp
                for timestamp in (node.finished_at, node.started_at, node.created_at)
                if timestamp is not None
            ),
        )
        entry = (signature, summary)
        self._node_summary_index.setdefault(node.project_id, {})[path] = entry
        return entry

    def node_summaries(self, pid: str) -> list[NodeSummary]:
        """复用轻量投影；只重读文件签名发生变化的节点。"""
        cached = self._node_summary_index.get(pid, {})
        fresh: dict[Path, NodeSummaryEntry] = {}
        owners: dict[str, str] = {}
        for path in self._hosts_dir(pid).glob("*/nodes/*/node.json"):
            signature = self._node_signature(path)
            entry = cached.get(path)
            if entry is None or entry[0] != signature:
                try:
                    node = self.load_node(pid, path.parent.name)
                except ValueError as exc:
                    raise MigrationError("migration_failed", str(exc), path) from exc
                if node is None:
                    continue
                entry = self._node_summary_index[pid][path]
            summary = entry[1]
            if summary.id in owners:
                raise MigrationError(
                    "migration_failed", "节点出现在多个 host 分区", path
                )
            owners[summary.id] = summary.owner_host_id
            fresh[path] = entry
        self._owner_index[pid] = owners
        self._node_summary_index[pid] = fresh
        return [entry[1] for entry in fresh.values()]

    def refresh_local_fingerprint(self, project: Project) -> bool:
        if project.temporary or not self.is_bound_here(project.id):
            return False
        roots = root_commits(project.root_path)
        path = self._host_dir(project.id, self.machine.id) / "host.json"
        try:
            payload = self._read_json(path)
        except (OSError, ValueError):
            payload = {"label": self.machine.label, "bound_at": time.time()}
        observed_is_repo = is_git_repo(project.root_path)
        repo = payload.get("repo")
        # A binding that no other host could vouch for must not acquire a Git
        # identity later: the refresh would publish exactly the unverified
        # fingerprint the binding declined to claim.
        unverified = payload.get("unverified_binding") is True
        if not roots or unverified:
            changed = payload.get("is_repo") is not observed_is_repo or repo != {}
            if not changed:
                return False
            payload["is_repo"] = observed_is_repo
            payload["repo"] = {}
            self._write_json(path, payload)
            return True
        if (
            payload.get("is_repo") is True
            and isinstance(repo, dict)
            and repo.get("root_commit") == roots[0]
        ):
            return False
        payload["is_repo"] = True
        payload["repo"] = {
            "root_commit": roots[0],
            "root_commits": roots,
            "origin_url": normalized_origin_url(project.root_path),
        }
        self._write_json(path, payload)
        return True

    def list_hosts(self, pid: str) -> list[dict[str, Any]]:
        hosts_dir = self._hosts_dir(pid)
        if not hosts_dir.is_dir():
            return []
        hosts: list[dict[str, Any]] = []
        heads = self.read_host_heads(pid)
        for host_dir in sorted(hosts_dir.iterdir()):
            path = host_dir / "host.json"
            if not path.is_file():
                continue
            try:
                payload = self._read_json(path)
            except (OSError, ValueError):
                continue
            payload["mid"] = host_dir.name
            head = heads.get(host_dir.name)
            if head is not None:
                payload.update(head)
            hosts.append(payload)
        return hosts

    def write_host_head(self, pid: str, payload: dict[str, Any]) -> None:
        if self.read_only_reason is not None or not self.is_bound_here(pid):
            return
        self._write_json(self._head_file(pid, self.machine.id), payload)

    def read_host_heads(self, pid: str) -> dict[str, dict[str, Any]]:
        hosts_dir = self._hosts_dir(pid)
        if not hosts_dir.is_dir():
            return {}
        heads: dict[str, dict[str, Any]] = {}
        paths = sorted(hosts_dir.glob("*/head.json"))
        try:
            committed_at = self.sync.file_commit_times(paths)
        except SyncError:
            committed_at = {}
        for path in paths:
            try:
                payload = self._read_json(path)
            except (OSError, ValueError):
                continue
            head = payload.get("head")
            if not isinstance(head, str) or re.fullmatch(r"[0-9a-fA-F]{40}", head) is None:
                continue
            payload.pop("recorded_at", None)
            relative_path = path.relative_to(self.root)
            if relative_path in committed_at:
                payload["recorded_at"] = committed_at[relative_path]
            heads[path.parent.name] = payload
        return heads

    def read_git_aliases(self, pid: str) -> dict[str, str]:
        path = self._git_aliases_file(pid)
        if not path.exists():
            return {}
        try:
            payload = self._read_json(path)
        except (OSError, ValueError):
            return {}
        return {
            str(old): str(new)
            for old, new in payload.items()
            if isinstance(old, str) and isinstance(new, str) and old and new
        }

    def write_git_aliases(self, pid: str, aliases: dict[str, str]) -> None:
        self.assert_writable()
        self._write_json(self._git_aliases_file(pid), dict(aliases))
        self.sync.schedule_commit(f"update git aliases for project {pid}")

    # ---- project ----

    def list_tags(self) -> list[Tag]:
        return load_tags(self.root)

    def create_tag(self, name: str, color: str | None = None) -> Tag:
        self.assert_writable()
        tag = create_global_tag(self.root, name, color)
        self.sync.schedule_commit(f'create tag "{tag.name}"')
        return tag

    def update_tag(
        self,
        tag_id: str,
        *,
        name: str | None = None,
        color: str | None = None,
    ) -> Tag | None:
        self.assert_writable()
        tag = update_global_tag(self.root, tag_id, name=name, color=color)
        if tag is not None:
            self.sync.schedule_commit(f'update tag "{tag.name}"')
        return tag

    def delete_tag(self, tag_id: str) -> bool:
        self.assert_writable()
        deleted = delete_global_tag(self.root, tag_id)
        if deleted:
            self.sync.schedule_commit(f"delete tag {tag_id}")
        return deleted

    def remove_tag_from_projects(self, tag_id: str) -> set[str]:
        """Remove a deleted tag reference without altering host-local metadata."""
        self.assert_writable()
        changed: set[str] = set()
        projects_dir = self.root / "projects"
        if not projects_dir.is_dir():
            return changed
        for project_file in sorted(projects_dir.glob("*/project.json")):
            try:
                payload = self._read_json(project_file)
            except (OSError, ValueError):
                logger.error(
                    "failed to remove tag from project record %s",
                    project_file,
                    exc_info=True,
                )
                continue
            tag_ids = payload.get("tag_ids")
            if not isinstance(tag_ids, list) or tag_id not in tag_ids:
                continue
            payload["tag_ids"] = [existing for existing in tag_ids if existing != tag_id]
            self._write_json(project_file, payload)
            changed.add(project_file.parent.name)
        if changed:
            self.sync.schedule_commit(f"remove tag {tag_id} from projects")
        return changed

    def create_project(self, project: Project) -> Project:
        self.assert_writable()
        if not project.machine_id:
            project.machine_id = self.machine.id
        if not project.machine_label:
            project.machine_label = self.machine.label
        project.bind_model_catalog(self.root)
        d = self._project_dir(project.id)
        host_dir = self._host_dir(project.id, self.machine.id)
        (host_dir / "nodes").mkdir(parents=True, exist_ok=True)
        self._write_json(host_dir / "local.json", {"root_path": project.root_path})
        self._write_json(host_dir / "node-layout.json", NodeLayout(schema_version=1, nodes={}).model_dump())
        roots = [] if project.temporary else root_commits(project.root_path)
        observed_is_repo = not project.temporary and is_git_repo(project.root_path)
        repo: dict[str, Any] = {}
        if roots:
            repo.update(
                {
                    "root_commit": roots[0],
                    "root_commits": roots,
                    "origin_url": normalized_origin_url(project.root_path),
                }
            )
        self._write_json(
            host_dir / "host.json",
            {
                "label": self.machine.label,
                "bound_at": time.time(),
                "repo": repo,
                "is_repo": observed_is_repo,
            },
        )
        payload = project.model_dump(
            exclude={"provider", "root_path", "node_positions"}
        )
        self._write_json(self._project_file(project.id), payload)
        self.sync.schedule_commit(f'create project "{project.name or project.id}"')
        return project

    def prepare_temporary_workspace(self, project: Project) -> None:
        from .workspace import create_temporary_root

        if not project.temporary:
            return
        self.assert_writable()
        host_dir = self._host_dir(project.id, self.machine.id)
        local_file = host_dir / "local.json"
        local = self._read_json(local_file) if local_file.is_file() else {}
        root = local.get("root_path")
        if not isinstance(root, str) or not Path(root).is_dir():
            root = create_temporary_root()
            self._write_json(local_file, {"root_path": root})
        project.root_path = root
        (host_dir / "nodes").mkdir(parents=True, exist_ok=True)
        if not (host_dir / "host.json").is_file():
            self._write_json(host_dir / "host.json", {
                "label": self.machine.label,
                "bound_at": time.time(),
                "repo": {},
                "is_repo": False,
            })
        if not (host_dir / "node-layout.json").is_file():
            self._write_json(host_dir / "node-layout.json", NodeLayout(schema_version=1, nodes={}).model_dump())

    def update_project(self, project: Project) -> None:
        self.assert_writable()
        project.bind_model_catalog(self.root)
        host_dir = self._host_dir(project.id, self.machine.id)
        if self.is_bound_here(project.id):
            self._write_json(host_dir / "local.json", {"root_path": project.root_path})
        payload = project.model_dump(
            exclude={"provider", "root_path", "node_positions"}
        )
        self._write_json(
            self._project_file(project.id),
            payload,
        )
        self.sync.schedule_commit(f'update project "{project.name or project.id}"')

    def _read_node_layout(self, path: Path) -> dict[str, NodePosition]:
        try:
            return NodeLayout.model_validate(self._read_json(path)).nodes
        except (OSError, ValueError) as exc:
            raise MigrationError("migration_failed", str(exc), path) from exc

    def read_node_positions(
        self, pid: str, *, nodes: list[Node] | None = None
    ) -> dict[str, NodePosition]:
        if nodes is None:
            nodes = self._list_nodes_for_project(pid, None)
        nodes_by_id = {node.id: node for node in nodes}
        layout_owners = node_layout_owners(nodes)
        positions: dict[str, NodePosition] = {}
        for path in sorted(self._hosts_dir(pid).glob("*/node-layout.json")):
            try:
                layout = self._read_node_layout(path)
            except MigrationError as exc:
                if path.parent.name == self.machine.id:
                    raise
                logger.warning("跳过无法读取的远端节点布局 %s: %s", path, exc)
                continue
            for node_id, position in layout.items():
                node = layout_owners.get(node_id)
                if node is not None and node.owner_host_id == path.parent.name:
                    if position.space == node_coordinate_space(node, nodes_by_id):
                        positions[node_id] = position
        return positions

    def read_git_positions(self, pid: str) -> dict[str, GitPosition]:
        path = self._project_file(pid).parent / "git-layout.json"
        if not path.exists():
            return {}
        try:
            return GitLayout.model_validate(self._read_json(path)).nodes
        except (OSError, ValueError) as exc:
            raise MigrationError("migration_failed", str(exc), path) from exc

    def update_git_positions(
        self, pid: str, updates: dict[str, GitPosition], remove: list[str],
    ) -> dict[str, GitPosition]:
        self.assert_writable()
        if not self.is_bound_here(pid):
            raise ValueError("项目未绑定到本机，不能修改 Git 位置")
        validated = GitLayout(schema_version=1, nodes=updates).nodes
        GitLayout(schema_version=1, nodes={key: GitPosition(x=0, y=0, space="canvas") for key in remove})
        merged = self.read_git_positions(pid)
        merged.update(validated)
        for node_id in remove:
            merged.pop(node_id, None)
        atomic_json(self._project_file(pid).parent / "git-layout.json",
                    GitLayout(schema_version=1, nodes=merged).model_dump())
        self.sync.schedule_commit(f"更新 Git 节点位置 {pid}")
        return merged

    def read_lane_positions(self, pid: str) -> dict[str, LanePosition]:
        path = self._project_file(pid).parent / "lane-layout.json"
        if not path.exists():
            return {}
        try:
            return LaneLayout.model_validate(self._read_json(path)).nodes
        except (OSError, ValueError) as exc:
            raise MigrationError("migration_failed", str(exc), path) from exc

    def update_lane_positions(
        self, pid: str, updates: dict[str, LanePosition], remove: list[str],
    ) -> dict[str, LanePosition]:
        self.assert_writable()
        if not self.is_bound_here(pid):
            raise ValueError("项目未绑定到本机，不能修改方向位置")
        validated = LaneLayout(schema_version=1, nodes=updates).nodes
        LaneLayout(schema_version=1, nodes={key: LanePosition(x=0, y=0, space="canvas") for key in remove})
        merged = self.read_lane_positions(pid)
        merged.update(validated)
        for node_id in remove:
            merged.pop(node_id, None)
        atomic_json(self._project_file(pid).parent / "lane-layout.json",
                    LaneLayout(schema_version=1, nodes=merged).model_dump())
        self.sync.schedule_commit(f"更新方向位置 {pid}")
        return merged

    def read_context_positions(self, pid: str) -> dict[str, NodePosition]:
        path = self._project_file(pid).parent / "context-layout.json"
        if not path.exists():
            return {}
        try:
            return ContextLayout.model_validate(self._read_json(path)).nodes
        except (OSError, ValueError) as exc:
            raise MigrationError("migration_failed", str(exc), path) from exc

    def update_context_positions(
        self, pid: str, updates: dict[str, NodePosition], remove: list[str],
    ) -> dict[str, NodePosition]:
        self.assert_writable()
        if not self.is_bound_here(pid):
            raise ValueError("项目未绑定到本机，不能修改上下文位置")
        validated = ContextLayout(schema_version=1, nodes=updates).nodes
        ContextLayout(schema_version=1, nodes={key: NodePosition(x=0, y=0, space="canvas") for key in remove})
        merged = self.read_context_positions(pid)
        merged.update(validated)
        for node_id in remove:
            merged.pop(node_id, None)
        atomic_json(self._project_file(pid).parent / "context-layout.json",
                    ContextLayout(schema_version=1, nodes=merged).model_dump())
        self.sync.schedule_commit(f"更新上下文位置 {pid}")
        return merged

    def update_node_positions(
        self,
        pid: str,
        updates: dict[str, NodePosition],
        remove: list[str],
        *,
        only_missing: bool = False,
    ) -> dict[str, NodePosition]:
        self.assert_writable()
        if not self.is_bound_here(pid):
            raise ValueError("项目未绑定到本机，不能修改节点位置")
        if only_missing and remove:
            raise ValueError("补回缺失位置时不能删除已有位置")
        node_records = self._list_nodes_for_project(pid, None)
        nodes = {node.id: node for node in node_records}
        layout_owners = node_layout_owners(node_records)
        for node_id in set(updates) | set(remove):
            node = layout_owners.get(node_id)
            if node is None or node.owner_host_id != self.machine.id:
                raise ValueError(f"只能修改本机拥有的节点及其已发布产物或错误卡片位置：{node_id}")
        validated = {node_id: NodePosition.model_validate(position) for node_id, position in updates.items()}
        for node_id, position in validated.items():
            if position.space != node_coordinate_space(layout_owners[node_id], nodes):
                raise ValueError(f"节点坐标空间已改变：{node_id}")
        local_path = self._host_dir(pid, self.machine.id) / "node-layout.json"
        merged = self._read_node_layout(local_path) if local_path.exists() else {}
        if only_missing:
            for node_id, position in validated.items():
                existing = merged.get(node_id)
                if existing is None or existing.space != position.space:
                    merged[node_id] = position
        else:
            merged.update(validated)
        for node_id in remove:
            merged.pop(node_id, None)
        self._write_json(local_path,
                         NodeLayout(schema_version=1, nodes=merged).model_dump())
        self.sync.schedule_commit(f"update node positions {pid}")
        return self.read_node_positions(pid, nodes=node_records)

    def _load_project(self, pid: str) -> Project | None:
        project_file = self._project_file(pid)
        if not project_file.exists():
            return None
        try:
            payload = self._read_json(project_file)
            if any(
                candidate.is_file()
                for candidate in (project_file.parent / "nodes").rglob("*")
            ):
                raise ValueError("当前格式不允许未分区节点")
            local_file = self._host_dir(pid, self.machine.id) / "local.json"
            local_payload = self._read_json(local_file) if local_file.is_file() else {}
            payload.update(
                root_path=local_payload.get("root_path", UNBOUND_ROOT_PATH),
                node_positions={},
            )
            return _validate_project_record(project_file, payload).bind_model_catalog(
                self.root
            )
        except (OSError, ValueError, ValidationError) as exc:
            raise MigrationError("migration_failed", str(exc), project_file) from exc

    def list_projects(self, *, include_node_positions: bool = True) -> list[Project]:
        projects_dir = self.root / "projects"
        out: list[Project] = []
        if not projects_dir.exists():
            return out
        try:
            known_tag_ids = {tag.id for tag in self.list_tags()}
        except ValueError:
            logger.error("failed to load tags while listing projects", exc_info=True)
            known_tag_ids = set()
        for pdir in sorted(projects_dir.iterdir()):
            if not pdir.is_dir():
                continue
            project = self._load_project(pdir.name)
            if project is None:
                continue
            project.tag_ids = [
                tag_id for tag_id in project.tag_ids if tag_id in known_tag_ids
            ]
            if include_node_positions:
                project.node_positions = self.read_node_positions(project.id)
            out.append(project)
        return out

    def delete_project(self, pid: str) -> bool:
        self.assert_writable()
        d = self._project_dir(pid)
        if not d.exists():
            return False
        shutil.rmtree(d)
        self._last_activity_index.pop(pid, None)
        self._node_summary_index.pop(pid, None)
        self.sync.schedule_commit(f"delete project {pid}")
        return True

    # ---- node ----

    def create_node(self, node: Node) -> Node:
        self.assert_writable()
        if not node.origin_machine_id:
            node.origin_machine_id = self.machine.id
        node.bind_model_catalog(self.root)
        d = self.node_dir(node.project_id, node.id)
        d.mkdir(parents=True, exist_ok=True)
        self._write_json(
            self._node_file(node.project_id, node.id),
            node.model_dump(exclude={"provider", "owner_host_id"}),
        )
        self._owner_index.setdefault(node.project_id, {})[node.id] = self.machine.id
        node.bind_owner_host(self.machine.id)
        path = self._node_file(node.project_id, node.id)
        self._cache_node_summary(node, path, self._node_signature(path))
        self.sync.schedule_commit(f"create node {node.id}")
        return node

    def load_node(self, pid: str, nid: str) -> Node | None:
        matches = list(self._hosts_dir(pid).glob(f"*/nodes/{nid}/node.json"))
        if len(matches) > 1:
            raise MigrationError("migration_failed", f"节点出现在多个 host 分区：{nid}")
        if not matches:
            return None
        path = matches[0]
        signature = self._node_signature(path)
        node = Node.model_validate(self._read_json(path)).bind_model_catalog(self.root)
        if node.id != nid or node.project_id != pid:
            raise MigrationError("migration_failed", "节点 id 或项目归属与路径不一致", path)
        # The partition a record physically lives in is the only thing that
        # decides who may rewrite it. Deriving the owner from the loaded path
        # keeps synchronized provenance fields out of local write authority.
        owner = path.parents[2].name
        self._owner_index.setdefault(pid, {})[nid] = owner
        self._cache_node_summary(node, path, signature)
        return node.bind_owner_host(owner)

    def update_node(self, node: Node) -> None:
        self.assert_writable()
        node.bind_model_catalog(self.root)
        path = self._node_file(node.project_id, node.id)
        try:
            persisted_rev = int(self._read_json(path).get("rev", 0))
        except (OSError, ValueError, TypeError):
            persisted_rev = 0
        node.rev = max(node.rev, persisted_rev) + 1
        self._write_json(
            path,
            node.model_dump(exclude={"provider", "owner_host_id"}),
        )
        self._cache_node_summary(node, path, self._node_signature(path))
        self.sync.schedule_commit(f"node {node.id} {node.state.value}")

    def delete_node(self, pid: str, nid: str) -> bool:
        self.assert_writable()
        d = self.node_dir(pid, nid)
        if not d.exists():
            return False
        shutil.rmtree(d)
        self._owner_index.get(pid, {}).pop(nid, None)
        self._node_summary_index.get(pid, {}).pop(d / "node.json", None)
        self.sync.schedule_commit(f"delete node {nid}")
        return True

    def list_nodes(self, pid: str) -> list[Node]:
        project = self._load_project(pid)
        return self._list_nodes_for_project(pid, project)

    def _list_nodes_for_project(
        self,
        pid: str,
        project: Project | None,
    ) -> list[Node]:
        node_files = list(self._hosts_dir(pid).glob("*/nodes/*/node.json"))
        out: list[Node] = []
        owners: dict[str, str] = {}
        summaries: dict[Path, NodeSummaryEntry] = {}
        for nf in node_files:
            try:
                signature = self._node_signature(nf)
                node = Node.model_validate(self._read_json(nf)).bind_model_catalog(
                    self.root
                )
                owner = nf.parents[2].name
                if node.id != nf.parent.name or node.project_id != pid or node.id in owners:
                    raise ValueError("节点路径、项目归属或跨 host 唯一性不满足当前契约")
                owners[node.id] = owner
                out.append(node.bind_owner_host(owner))
                summaries[nf] = self._cache_node_summary(node, nf, signature)
            except (ValueError, ValidationError) as exc:
                raise MigrationError("migration_failed", str(exc), nf) from exc
        self._owner_index[pid] = owners
        self._node_summary_index[pid] = summaries
        out.sort(key=lambda n: n.created_at)
        if project is not None:
            self._last_activity_index[pid] = max(
                (
                    timestamp
                    for node in out
                    for timestamp in (
                        node.finished_at,
                        node.started_at,
                        node.created_at,
                    )
                    if timestamp is not None
                ),
                default=project.created_at,
            )
        return out

    def latest_node(self, pid: str) -> Node | None:
        nodes = self.list_nodes(pid)
        return nodes[-1] if nodes else None

    # ---- node preview ----

    def write_node_preview(self, pid: str, nid: str, text: str) -> None:
        self.assert_writable()
        path = self._preview_file(pid, nid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        self.sync.schedule_commit(f"update preview for node {nid}")

    def read_node_preview(self, pid: str, nid: str) -> str | None:
        path = self._preview_file(pid, nid)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # ---- events ----

    def append_event(self, pid: str, nid: str, seq: int, event: dict[str, Any]) -> None:
        self.assert_writable()
        path = self._events_file(pid, nid)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "schema_version": EVENT_SCHEMA_VERSION,
                        "seq": seq,
                        "event": event,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            f.flush()
        self.sync.schedule_commit(f"update transcript for node {nid}")

    def replay_events(self, pid: str, nid: str, since_seq: int = 0) -> list[dict[str, Any]]:
        path = self._events_file(pid, nid)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if type(rec.get("schema_version")) is not int or rec["schema_version"] != EVENT_SCHEMA_VERSION:
                    raise MigrationError("migration_failed", "事件不是当前格式", path)
                if rec.get("seq", 0) > since_seq:
                    event = rec.get("event")
                    if isinstance(event, dict):
                        event.setdefault("node_id", nid)
                    out.append(rec)
        return out

    # ---- gates ----

    def append_gate(self, pid: str, gate: HumanGate, action: str) -> None:
        self.assert_writable()
        path = self._gates_file(pid, gate.node_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"action": action, "gate": gate.model_dump()}, ensure_ascii=False)
                + "\n"
            )
            f.flush()
        self.sync.schedule_commit(f"update gate for node {gate.node_id}")

    # ---- low-level ----

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def assert_writable(self) -> None:
        reason = self.read_only_reason
        if reason is not None:
            raise StoreReadOnlyError(reason)


def _validate_project_record(path: Path, payload: dict[str, Any]) -> Project:
    preset_id = payload.get("model_preset_id")
    if not isinstance(preset_id, str) or not preset_id.strip():
        raise ValueError(f"{path}: project requires model_preset_id")
    return Project.model_validate(payload)
