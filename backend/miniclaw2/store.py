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

from .domain import HumanGate, Node, Project
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
    """Filesystem-backed store. Cheap to instantiate; no caches."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _root()
        (self.root / "projects").mkdir(parents=True, exist_ok=True)
        self.machine: MachineIdentity = ensure_machine_identity(self.root)
        ensure_store_metadata(self.root, self.machine)
        from .global_config import ensure_global_config

        ensure_global_config(self.root)
        self.sync: SyncManager = get_sync_manager(self.root, self.machine)

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

    def node_dir(self, pid: str, nid: str) -> Path:
        return self._project_dir(pid) / "nodes" / nid

    def _node_file(self, pid: str, nid: str) -> Path:
        return self.node_dir(pid, nid) / "node.json"

    def _events_file(self, pid: str, nid: str) -> Path:
        return self.node_dir(pid, nid) / "events.jsonl"

    def _gates_file(self, pid: str, nid: str) -> Path:
        return self.node_dir(pid, nid) / "gates.jsonl"

    def _preview_file(self, pid: str, nid: str) -> Path:
        return self.node_dir(pid, nid) / "preview.json"

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
        self._write_json(
            self._project_file(project.id),
            project.model_dump(exclude={"provider"}),
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
                    out.append(
                        _validate_project_record(
                            pf, self._read_json(pf)
                        ).bind_model_catalog(self.root)
                    )
                except (ValueError, ValidationError):
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
        node.bind_model_catalog(self.root)
        d = self.node_dir(node.project_id, node.id)
        d.mkdir(parents=True, exist_ok=True)
        self._write_json(
            self._node_file(node.project_id, node.id),
            node.model_dump(exclude={"provider"}),
        )
        self.sync.schedule_commit(f"create node {node.id}")
        return node

    def load_node(self, pid: str, nid: str) -> Node | None:
        path = self._node_file(pid, nid)
        if not path.exists():
            return None
        return Node.model_validate(self._read_json(path)).bind_model_catalog(
            self.root
        )

    def update_node(self, node: Node) -> None:
        self.assert_writable()
        node.bind_model_catalog(self.root)
        self._write_json(
            self._node_file(node.project_id, node.id),
            node.model_dump(exclude={"provider"}),
        )
        self.sync.schedule_commit(f"node {node.id} {node.state.value}")

    def delete_node(self, pid: str, nid: str) -> bool:
        self.assert_writable()
        d = self.node_dir(pid, nid)
        if not d.exists():
            return False
        shutil.rmtree(d)
        self.sync.schedule_commit(f"delete node {nid}")
        return True

    def list_nodes(self, pid: str) -> list[Node]:
        nodes_dir = self._project_dir(pid) / "nodes"
        if not nodes_dir.exists():
            return []
        out: list[Node] = []
        for ndir in nodes_dir.iterdir():
            nf = ndir / "node.json"
            if nf.exists():
                try:
                    out.append(
                        Node.model_validate(self._read_json(nf)).bind_model_catalog(
                            self.root
                        )
                    )
                except (ValueError, ValidationError):
                    logger.error(
                        "skipping invalid current-schema node record %s",
                        nf,
                        exc_info=True,
                    )
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
