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

Single-writer-per-node is guaranteed by the runtime (nodes within a
project run sequentially), so writes need no extra locking.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .domain import HumanGate, Node, Project


def _root() -> Path:
    base = os.environ.get("MINICLAW_HOME")
    return Path(base).expanduser() if base else Path.home() / ".miniclaw2"


class Store:
    """Filesystem-backed store. Cheap to instantiate; no caches."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _root()
        (self.root / "projects").mkdir(parents=True, exist_ok=True)

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
        d = self._project_dir(project.id)
        (d / "nodes").mkdir(parents=True, exist_ok=True)
        self._write_json(self._project_file(project.id), project.model_dump())
        return project

    def update_project(self, project: Project) -> None:
        self._write_json(self._project_file(project.id), project.model_dump())

    def load_project(self, pid: str) -> Project | None:
        path = self._project_file(pid)
        if not path.exists():
            return None
        return Project.model_validate(self._read_json(path))

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
                out.append(Project.model_validate(self._read_json(pf)))
        return out

    def delete_project(self, pid: str) -> bool:
        d = self._project_dir(pid)
        if not d.exists():
            return False
        shutil.rmtree(d)
        return True

    # ---- node ----

    def create_node(self, node: Node) -> Node:
        d = self.node_dir(node.project_id, node.id)
        d.mkdir(parents=True, exist_ok=True)
        self._write_json(self._node_file(node.project_id, node.id), node.model_dump())
        return node

    def load_node(self, pid: str, nid: str) -> Node | None:
        path = self._node_file(pid, nid)
        if not path.exists():
            return None
        return Node.model_validate(self._read_json(path))

    def update_node(self, node: Node) -> None:
        self._write_json(self._node_file(node.project_id, node.id), node.model_dump())

    def list_nodes(self, pid: str) -> list[Node]:
        nodes_dir = self._project_dir(pid) / "nodes"
        if not nodes_dir.exists():
            return []
        out: list[Node] = []
        for ndir in nodes_dir.iterdir():
            nf = ndir / "node.json"
            if nf.exists():
                out.append(Node.model_validate(self._read_json(nf)))
        out.sort(key=lambda n: n.created_at)
        return out

    def latest_node(self, pid: str) -> Node | None:
        nodes = self.list_nodes(pid)
        return nodes[-1] if nodes else None

    # ---- node preview ----

    def write_node_preview(self, pid: str, nid: str, text: str) -> None:
        path = self._preview_file(pid, nid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def read_node_preview(self, pid: str, nid: str) -> str | None:
        path = self._preview_file(pid, nid)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # ---- events ----

    def append_event(self, pid: str, nid: str, seq: int, event: dict[str, Any]) -> None:
        path = self._events_file(pid, nid)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"seq": seq, "event": event}, ensure_ascii=False) + "\n")
            f.flush()

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
                    out.append(rec)
        return out

    # ---- gates ----

    def append_gate(self, pid: str, gate: HumanGate, action: str) -> None:
        path = self._gates_file(pid, gate.node_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"action": action, "gate": gate.model_dump()}, ensure_ascii=False)
                + "\n"
            )
            f.flush()

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
