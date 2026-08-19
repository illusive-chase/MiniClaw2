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
    sharing-requests/
      <pid>/
        <rid>/
          request.json

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
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .domain import HumanGate, Node, Project, UNBOUND_ROOT_PATH
from .replay import EVENT_SCHEMA_VERSION, upgrade_event_record
from .sharing_requests import (
    CANCELLATION_FILENAME,
    DECISION_FILENAME,
    REQUEST_FILENAME,
    SharingCancellation,
    SharingDecision,
    SharingDecisionValue,
    SharingRequest,
    SharingRequestRecord,
    load_records,
    load_request,
    new_request_id,
    request_dir,
    write_record,
)
from .sync import (
    MachineIdentity,
    SyncManager,
    ensure_machine_identity,
    ensure_store_metadata,
    get_sync_manager,
    machine_hostname_mismatch,
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
        self._last_activity_index: dict[str, float] = {}
        self.sync.add_success_callback(self.invalidate_owner_index)
        self.sync.add_success_callback(self._refresh_last_activity_after_sync)
        self.refresh_last_activity_index()

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

    def _head_file(self, pid: str, machine_id: str) -> Path:
        return self._host_dir(pid, machine_id) / "head.json"

    def _claim_file(self, pid: str, machine_id: str, vid: str) -> Path:
        if not vid or Path(vid).name != vid:
            raise ValueError("invalid virtual node id")
        return self._host_dir(pid, machine_id) / "claims" / f"{vid}.json"

    def invalidate_owner_index(self) -> None:
        """Drop path ownership cached before a sync or layout migration."""
        self._owner_index.clear()

    def refresh_last_activity_index(self) -> None:
        """Rebuild project activity timestamps from all persisted nodes."""
        self._last_activity_index.clear()
        for project in self.list_projects():
            self._list_nodes_for_project(project.id, project)

    def _refresh_last_activity_after_sync(self) -> None:
        try:
            self.refresh_last_activity_index()
        except Exception:  # noqa: BLE001
            logger.exception("failed to refresh project activity after sync")

    def project_last_activity_at(self, pid: str) -> float | None:
        if pid not in self._last_activity_index:
            project = next((item for item in self.list_projects() if item.id == pid), None)
            if project is None:
                return None
            self._list_nodes_for_project(project.id, project)
        return self._last_activity_index.get(pid)

    def record_project_activity(self, pid: str, activity_at: float) -> None:
        current = self._last_activity_index.get(pid)
        if current is None or activity_at > current:
            self._last_activity_index[pid] = activity_at

    def has_host_binding(self, pid: str, machine_id: str) -> bool:
        return (self._host_dir(pid, machine_id) / "host.json").is_file()

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
        if self.read_only_reason is not None or not self._is_shared(pid):
            return
        self._write_json(self._head_file(pid, self.machine.id), payload)

    def read_host_heads(self, pid: str) -> dict[str, dict[str, Any]]:
        hosts_dir = self._hosts_dir(pid)
        if not hosts_dir.is_dir():
            return {}
        heads: dict[str, dict[str, Any]] = {}
        for path in sorted(hosts_dir.glob("*/head.json")):
            try:
                payload = self._read_json(path)
            except (OSError, ValueError):
                continue
            head = payload.get("head")
            if not isinstance(head, str) or re.fullmatch(r"[0-9a-fA-F]{40}", head) is None:
                continue
            heads[path.parent.name] = payload
        return heads

    def write_claim(self, pid: str, vid: str, payload: dict[str, Any]) -> None:
        self.assert_writable()
        if not self._is_shared(pid):
            raise ValueError("claims require a shared project")
        claim = dict(payload)
        claim["claimed_by"] = self.machine.id
        self._write_json(self._claim_file(pid, self.machine.id, vid), claim)
        self.sync.schedule_commit(f"claim virtual node {vid}")

    def list_claims(self, pid: str) -> dict[str, list[dict[str, Any]]]:
        hosts_dir = self._hosts_dir(pid)
        if not hosts_dir.is_dir():
            return {}
        claims: dict[str, list[dict[str, Any]]] = {}
        for path in sorted(hosts_dir.glob("*/claims/*.json")):
            try:
                payload = self._read_json(path)
            except (OSError, ValueError):
                continue
            claimed_by = payload.get("claimed_by")
            as_node = payload.get("as_node")
            claimed_at = payload.get("claimed_at")
            if (
                not isinstance(claimed_by, str)
                or claimed_by != path.parents[1].name
                or not isinstance(as_node, str)
                or not as_node
                or not isinstance(claimed_at, (int, float))
            ):
                continue
            claims.setdefault(path.stem, []).append(payload)
        return claims

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

    # ---- sharing requests ----

    def list_sharing_requests(self) -> list[SharingRequestRecord]:
        """Every request on disk, with status normalized against projects."""
        projects = {project.id: project for project in self.list_projects()}
        return load_records(self.root, projects)

    def sharing_requests_for_project(
        self,
        project: Project,
    ) -> list[SharingRequestRecord]:
        return load_records(
            self.root,
            {project.id: project},
            only_project_id=project.id,
        )

    def create_sharing_request(self, project: Project) -> SharingRequestRecord:
        """Record this device's request that ``project``'s host enable sharing.

        Idempotent per device: an open request from this machine is returned
        as-is rather than accumulating duplicates the host has to triage.
        """
        self.assert_writable()
        existing = next(
            (
                record
                for record in self.sharing_requests_for_project(project)
                if record.request.requester_machine_id == self.machine.id
                and record.is_open
            ),
            None,
        )
        if existing is not None:
            return existing
        request = SharingRequest(
            id=new_request_id(),
            project_id=project.id,
            observed_owner_machine_id=project.machine_id,
            requester_machine_id=self.machine.id,
            requester_machine_label=self.machine.label,
            requested_at=time.time(),
        )
        write_record(
            request_dir(self.root, project.id, request.id) / REQUEST_FILENAME,
            request,
        )
        self.sync.schedule_commit(
            f'request sharing for project "{project.name or project.id}"'
        )
        return next(
            record
            for record in self.sharing_requests_for_project(project)
            if record.id == request.id
        )

    def cancel_sharing_request(self, pid: str, rid: str) -> bool:
        """Withdraw a request this device created."""
        self.assert_writable()
        request = load_request(self.root, pid, rid)
        if request is None:
            return False
        if request.requester_machine_id != self.machine.id:
            raise PermissionError("only the requesting device can cancel this request")
        write_record(
            request_dir(self.root, pid, rid) / CANCELLATION_FILENAME,
            SharingCancellation(
                request_id=rid,
                cancelled_by_machine_id=self.machine.id,
                cancelled_at=time.time(),
            ),
        )
        self.sync.schedule_commit(f"cancel sharing request {rid}")
        return True

    def write_sharing_decision(
        self,
        project: Project,
        rid: str,
        decision: SharingDecisionValue,
    ) -> bool:
        """Record the native host's answer to a request.

        Ownership is checked against the project record rather than the
        request's ``observed_owner_machine_id``: the request only reports what
        the requester last synced, while the project names the current host.
        """
        self.assert_writable()
        request = load_request(self.root, project.id, rid)
        if request is None:
            return False
        if project.machine_id != self.machine.id:
            raise PermissionError("only the native host can decide this request")
        write_record(
            request_dir(self.root, project.id, rid) / DECISION_FILENAME,
            SharingDecision(
                request_id=rid,
                decision=decision,
                decided_by_machine_id=self.machine.id,
                decided_at=time.time(),
            ),
        )
        self.sync.schedule_commit(f"{decision} sharing request {rid}")
        return True

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
        try:
            known_tag_ids = {tag.id for tag in self.list_tags()}
        except ValueError:
            logger.error("failed to load tags while listing projects", exc_info=True)
            known_tag_ids = set()
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
                    project = _validate_project_record(pf, payload)
                    project.tag_ids = [
                        tag_id
                        for tag_id in project.tag_ids
                        if tag_id in known_tag_ids
                    ]
                    out.append(project.bind_model_catalog(self.root))
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
        self._last_activity_index.pop(pid, None)
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
        return self._list_nodes_for_project(pid, project)

    def _list_nodes_for_project(
        self,
        pid: str,
        project: Project | None,
    ) -> list[Node]:
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
