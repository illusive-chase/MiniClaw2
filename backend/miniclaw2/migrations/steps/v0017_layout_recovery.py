from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import quote

from ..sdk import Migration, MigrationContext


def coordinate_space(node: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> str:
    visited: set[str] = set()
    while node["id"] not in visited:
        visited.add(node["id"])
        if node.get("planspace_id"):
            return "planspace:" + node["planspace_id"]
        snapshot = (node.get("settings_snapshot") or {}).get("active_planspace_id")
        if isinstance(snapshot, str) and snapshot:
            return "planspace:" + snapshot
        parent = nodes.get(node.get("parent_node_id"))
        if parent is None:
            break
        node = parent
    return "canvas"


def artifact_owners(nodes: dict[str, dict[str, Any]]) -> dict[str, str]:
    owners = {}
    for node_id, node in nodes.items():
        if node.get("kind") == "op":
            continue
        published = [artifact for artifact in node.get("artifacts", [])
                     if artifact.get("status") == "published"]
        for artifact in published:
            name = quote(artifact["name"], safe="~!*'()-._")
            owners[f"artifact:{node_id}:{name}"] = node_id
        if len(published) > 4:
            owners[f"artifact-overflow:{node_id}"] = node_id
    return owners


def finite_position(position: Any, space: str) -> dict[str, Any] | None:
    if not isinstance(position, dict):
        return None
    result: dict[str, Any] = {"space": space}
    for axis in ("x", "y"):
        value = position.get(axis)
        if type(value) not in (int, float):
            return None
        try:
            value = float(value)
        except OverflowError:
            return None
        if not math.isfinite(value):
            return None
        result[axis] = value
    return result


def pristine_hints(context: MigrationContext) -> list[tuple[str, dict[str, Any]]]:
    shards = sorted(context.input_records("projects/*/hosts/*/layout.json"))
    projects = sorted((relative, payload) for relative, payload in context.input_records("projects/*/project.json")
                      if "layout_hints" in payload or "layout_viewport" in payload)
    return shards + projects


def recover_layout(context: MigrationContext, *, write: bool = True) -> list[dict[str, Any]]:
    sources: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for relative, payload in pristine_hints(context):
        sources.setdefault(relative.split("/")[1], []).append((relative, payload))
    reports = []
    for project_file in context.paths("projects/*/project.json"):
        project_dir = project_file.removesuffix("project.json")
        project_id = project_file.split("/")[1]
        nodes: dict[str, dict[str, Any]] = {}
        owners: dict[str, str] = {}
        for relative in context.paths(project_dir + "hosts/*/nodes/*/node.json"):
            node = context.read(relative)
            parts = relative.split("/")
            node_id = parts[5]
            if node.get("id") != node_id or node.get("project_id") != project_id or node_id in nodes:
                raise ValueError(f"节点路径或跨 host 唯一性无效：{relative}")
            nodes[node_id] = node
            owners[node_id] = parts[3]
        artifacts = artifact_owners(nodes)
        layouts: dict[str, dict[str, Any]] = {}
        changed: set[str] = set()
        project_sources = sources.get(project_id, [])
        source_paths = {relative for relative, _payload in project_sources}
        present_hosts = {relative.split("/")[3] if relative.endswith("/layout.json") else payload.get("machine_id", "")
                         for relative, payload in project_sources}
        hosts = set(owners.values()) | {relative.split("/")[3] for relative in context.paths(project_dir + "hosts/*/host.json")}
        project_sources = project_sources + [(project_dir + f"hosts/{host}/layout.json", {}) for host in sorted(hosts - present_hosts)]
        for relative, payload in project_sources:
            host = relative.split("/")[3] if relative.endswith("/layout.json") else payload.get("machine_id", "")
            hints = payload.get("layout_hints", {})
            report: dict[str, Any] = {
                "project_id": project_id, "host_id": host, "path": relative,
                "source": "present" if hints else "empty" if relative in source_paths else "missing",
                "retained": 0,
                "restored": {kind: 0 for kind in ("node", "foreign_node", "artifact", "git", "lane")},
                "not_restored": {kind: 0 for kind in ("synthetic", "missing", "invalid")},
                "skipped_entries": [], "viewport_discarded": payload.get("layout_viewport") is not None,
            }
            reports.append(report)
            if not isinstance(hints, dict):
                report["not_restored"]["invalid"] += 1
                report["skipped_entries"].append({"id": "layout_hints", "reason": "布局不是对象"})
                continue
            for tile_id, position in sorted(hints.items()):
                producer = tile_id if tile_id in nodes else artifacts.get(tile_id)
                space = "canvas"
                if producer is not None:
                    target = project_dir + f"hosts/{owners[producer]}/node-layout.json"
                    space = coordinate_space(nodes[producer], nodes)
                    kind = "artifact" if tile_id in artifacts else "node" if host == owners[producer] else "foreign_node"
                elif re.fullmatch(r"commit:([0-9a-f]{7,64}|ghost)", tile_id):
                    target, kind = project_dir + "git-layout.json", "git"
                elif re.fullmatch(r"planspace:.+", tile_id):
                    target, kind = project_dir + "lane-layout.json", "lane"
                else:
                    excluded = "synthetic" if tile_id.startswith(("err:", "ctx:", "port:", "tplbox:", "tplport:")) else "missing"
                    if tile_id.startswith(("commit:", "planspace:")):
                        excluded = "invalid"
                    report["not_restored"][excluded] += 1
                    report["skipped_entries"].append({"id": tile_id, "reason": {
                        "synthetic": "合成图元由布局派生，不恢复", "missing": "节点或有效产物不存在", "invalid": "布局图元标识无效",
                    }[excluded]})
                    continue
                recovered = finite_position(position, space)
                if recovered is None:
                    report["not_restored"]["invalid"] += 1
                    report["skipped_entries"].append({"id": tile_id, "reason": "坐标不是有限数值"})
                    continue
                if target not in layouts:
                    layouts[target] = context.read(target) if context.path(target).exists() else {"schema_version": 1, "nodes": {}}
                if tile_id in layouts[target]["nodes"]:
                    report["retained"] += 1
                    continue
                layouts[target]["nodes"][tile_id] = recovered
                changed.add(target)
                report["restored"][kind] += 1
        if write:
            for target in sorted(changed):
                context.replace(target, layouts[target])
    return sorted(reports, key=lambda report: (report["project_id"], report["host_id"], report["path"]))


def upgrade(context: MigrationContext) -> None:
    recover_layout(context)


def verify(context: MigrationContext) -> None:
    if any(sum(report["restored"].values()) for report in recover_layout(context, write=False)):
        raise ValueError("原始输入中的有效布局尚未全部补回")


MIGRATION = Migration(
    source=16,
    target=17,
    scopes=("shared",),
    summary="从本次事务原始快照补回节点、产物、Git 与方向坐标；只补缺，不恢复合成图元与旧 viewport",
    contract="miniclaw2/store/v17:layout-recovery-v1",
    upgrade=upgrade,
    verify=verify,
)
