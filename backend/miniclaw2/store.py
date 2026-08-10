"""JSONL/JSON disk store for projects, nodes, gates, and event streams.

Layout under ``$MINICLAW_HOME`` (default ``~/.miniclaw2``)::

    projects/
      <pid>/
        project.json
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
import shutil
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .domain import HumanGate, Node, Project, UNBOUND_ROOT_PATH
from .replay import EVENT_SCHEMA_VERSION, upgrade_event_record
from .sync import (
    MachineIdentity,
    SyncManager,
    ensure_machine_identity,
    ensure_store_metadata,
    get_sync_manager,
    machine_hostname_mismatch,
    schema_is_newer,
)


logger = logging.getLogger(__name__)


class StoreReadOnlyError(RuntimeError):
    """The store cannot safely accept writes on this machine."""


def _root() -> Path:
    base = os.environ.get("MINICLAW_HOME")
    return Path(base).expanduser() if base else Path.home() / ".miniclaw2"


class Store:
    """Filesystem-backed store with a per-project node-owner path index."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _root()
        (self.root / "projects").mkdir(parents=True, exist_ok=True)
        self.machine: MachineIdentity = ensure_machine_identity(self.root)
        ensure_store_metadata(self.root, self.machine)
        from .global_config import ensure_global_config

        ensure_global_config(self.root)
        self.sync: SyncManager = get_sync_manager(self.root, self.machine)
        self._owner_index: dict[str, dict[str, str]] = {}
        self.sync.add_success_callback(self.invalidate_owner_index)

    @property
    def read_only_reason(self) -> str | None:
        if schema_is_newer(self.root):
            return "store schema is newer than this MiniClaw2 version"
        if machine_hostname_mismatch(self.machine):
            return (
                "machine hostname changed; resolve rename versus copied store first"
            )
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

    def _is_shared(self, pid: str) -> bool:
        return self._hosts_dir(pid).is_dir()

    def _owner_mid(self, pid: str, nid: str) -> str | None:
        if not self._is_shared(pid):
            return None
        return self._owner_index.get(pid, {}).get(nid, self.machine.id)

    def node_dir(self, pid: str, nid: str) -> Path:
        owner = self._owner_mid(pid, nid)
        if owner is None:
            return self._project_dir(pid) / "nodes" / nid
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
        if self._is_shared(pid):
            return self._host_dir(pid, self.machine.id) / "git_aliases.json"
        return self._project_dir(pid) / "git_aliases.json"

    def invalidate_owner_index(self) -> None:
        """Drop path ownership cached before a sync or layout migration."""
        self._owner_index.clear()

    def has_host_binding(self, pid: str, machine_id: str) -> bool:
        return (self._host_dir(pid, machine_id) / "host.json").is_file()

    def list_hosts(self, pid: str) -> list[dict[str, Any]]:
        hosts_dir = self._hosts_dir(pid)
        if not hosts_dir.is_dir():
            return []
        hosts: list[dict[str, Any]] = []
        for host_dir in sorted(hosts_dir.iterdir()):
            path = host_dir / "host.json"
            if not path.is_file():
                continue
            try:
                payload = self._read_json(path)
            except (OSError, ValueError):
                continue
            payload["mid"] = host_dir.name
            hosts.append(payload)
        return hosts

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

    def create_project(self, project: Project) -> Project:
        self.assert_writable()
        if not project.machine_id:
            project.machine_id = self.machine.id
        if not project.machine_label:
            project.machine_label = self.machine.label
        project.bind_model_catalog(self.root)
        d = self._project_dir(project.id)
        (d / "nodes").mkdir(parents=True, exist_ok=True)
        self._write_json(
            self._project_file(project.id),
            project.model_dump(exclude={"provider"}),
        )
        self.sync.schedule_commit(f'create project "{project.name or project.id}"')
        return project

    def update_project(self, project: Project) -> None:
        self.assert_writable()
        project.bind_model_catalog(self.root)
        if project.sharing == "shared" or self._is_shared(project.id):
            host_dir = self._host_dir(project.id, self.machine.id)
            self._write_json(host_dir / "local.json", {"root_path": project.root_path})
            self._write_json(
                host_dir / "layout.json",
                {
                    "layout_hints": project.layout_hints,
                    "layout_viewport": project.layout_viewport,
                },
            )
            payload = project.model_dump(
                exclude={"provider", "root_path", "layout_hints", "layout_viewport"}
            )
        else:
            payload = project.model_dump(exclude={"provider"})
        self._write_json(
            self._project_file(project.id),
            payload,
        )
        self.sync.schedule_commit(f'update project "{project.name or project.id}"')

    def list_projects(self) -> list[Project]:
        projects_dir = self.root / "projects"
        out: list[Project] = []
        if not projects_dir.exists():
            return out
        for pdir in sorted(projects_dir.iterdir()):
            if not pdir.is_dir():
                continue
            pf = pdir / "project.json"
            if pf.exists():
                try:
                    payload = self._read_json(pf)
                    if (pdir / "hosts").is_dir():
                        local_dir = pdir / "hosts" / self.machine.id
                        local_payload: dict[str, Any] = {}
                        layout_payload: dict[str, Any] = {}
                        if (local_dir / "local.json").is_file():
                            local_payload = self._read_json(local_dir / "local.json")
                        if (local_dir / "layout.json").is_file():
                            layout_payload = self._read_json(local_dir / "layout.json")
                        payload.update(
                            {
                                "sharing": "shared",
                                "root_path": local_payload.get(
                                    "root_path", UNBOUND_ROOT_PATH
                                ),
                                "layout_hints": layout_payload.get(
                                    "layout_hints", {}
                                ),
                                "layout_viewport": layout_payload.get(
                                    "layout_viewport"
                                ),
                            }
                        )
                    out.append(
                        _validate_project_record(
                            pf, payload
                        ).bind_model_catalog(self.root)
                    )
                except (OSError, ValueError, ValidationError):
                    logger.error(
                        "skipping invalid current-schema project record %s",
                        pf,
                        exc_info=True,
                    )
        return out

    def delete_project(self, pid: str) -> bool:
        self.assert_writable()
        d = self._project_dir(pid)
        if not d.exists():
            return False
        shutil.rmtree(d)
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
        if self._is_shared(node.project_id):
            self._owner_index.setdefault(node.project_id, {})[node.id] = self.machine.id
            node.bind_owner_host(self.machine.id)
        self.sync.schedule_commit(f"create node {node.id}")
        return node

    def load_node(self, pid: str, nid: str) -> Node | None:
        path = self._node_file(pid, nid)
        if not path.exists() and self._is_shared(pid):
            matches = list(self._hosts_dir(pid).glob(f"*/nodes/{nid}/node.json"))
            if matches:
                path = matches[0]
                owner = path.parents[2].name
                self._owner_index.setdefault(pid, {})[nid] = owner
        if not path.exists():
            return None
        node = Node.model_validate(self._read_json(path)).bind_model_catalog(self.root)
        owner = self._owner_index.get(pid, {}).get(nid)
        if owner is None:
            project = next((item for item in self.list_projects() if item.id == pid), None)
            owner = project.machine_id if project is not None else ""
        return node.bind_owner_host(owner)

    def update_node(self, node: Node) -> None:
        self.assert_writable()
        node.bind_model_catalog(self.root)
        self._write_json(
            self._node_file(node.project_id, node.id),
            node.model_dump(exclude={"provider", "owner_host_id"}),
        )
        self.sync.schedule_commit(f"node {node.id} {node.state.value}")

    def delete_node(self, pid: str, nid: str) -> bool:
        self.assert_writable()
        d = self.node_dir(pid, nid)
        if not d.exists():
            return False
        shutil.rmtree(d)
        self._owner_index.get(pid, {}).pop(nid, None)
        self.sync.schedule_commit(f"delete node {nid}")
        return True

    def list_nodes(self, pid: str) -> list[Node]:
        project = next((item for item in self.list_projects() if item.id == pid), None)
        if self._is_shared(pid):
            node_files = list(self._hosts_dir(pid).glob("*/nodes/*/node.json"))
        else:
            node_files = list((self._project_dir(pid) / "nodes").glob("*/node.json"))
        out: list[Node] = []
        owners: dict[str, str] = {}
        for nf in node_files:
            try:
                node = Node.model_validate(self._read_json(nf)).bind_model_catalog(
                    self.root
                )
                owner = nf.parents[2].name if self._is_shared(pid) else (
                    project.machine_id if project is not None else ""
                )
                owners[node.id] = owner
                out.append(node.bind_owner_host(owner))
            except (ValueError, ValidationError):
                logger.error(
                    "skipping invalid current-schema node record %s",
                    nf,
                    exc_info=True,
                )
        self._owner_index[pid] = owners
        out.sort(key=lambda n: n.created_at)
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
                if rec.get("seq", 0) > since_seq:
                    upgraded = upgrade_event_record(rec)
                    event = upgraded.get("event")
                    if isinstance(event, dict):
                        event.setdefault("node_id", nid)
                    out.append(upgraded)
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
