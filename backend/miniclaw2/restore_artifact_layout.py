from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .domain import Node, NodeLayout, NodePosition
from .migrations.errors import MigrationError
from .migrations.inventory import safe_path
from .migrations.steps.v0016_node_layout import coordinate_space
from .migrations.transaction import backup_payload
from .migrations.validation import read_object
from .node_layout import node_coordinate_space, node_layout_owners


def _verified_record(root: Path, journal: dict[str, Any], relative: str, inventory: dict[str, Any], identifier: str) -> dict[str, Any]:
    payload = backup_payload(root, journal, 0, relative, identifier=identifier)
    if not inventory.get(relative) or payload.digest != inventory[relative]:
        raise ValueError(f"备份摘要不匹配：{payload.origin}")
    return payload.record


def recovery_plan(root: Path, transaction: str, *, project_id: str | None = None) -> dict[str, Any]:
    host = read_object(root / "machine.json")["id"]
    journal = read_object(safe_path(root / ".migration-local/transactions", transaction + "/journal.json"))
    inventory = journal["inputs"][0]
    layouts: dict[str, list[str]] = {}
    for relative in sorted(inventory, key=lambda name: (Path(name).parts[3:4] != (host,), name)):
        if inventory[relative] is None or not Path(relative).match("projects/*/hosts/*/layout.json"):
            continue
        candidate_id = Path(relative).parts[1]
        if project_id is None or candidate_id == project_id:
            layouts.setdefault(candidate_id, []).append(relative)
    projects = []
    for candidate_id, relatives in sorted(layouts.items()):
        project_dir = safe_path(root, f"projects/{candidate_id}")
        if not (project_dir / "project.json").is_file():
            continue
        historical: dict[str, dict[str, Any]] = {}
        for relative in inventory:
            if inventory[relative] is None or not Path(relative).match(f"projects/{candidate_id}/hosts/*/nodes/*/node.json"):
                continue
            payload = _verified_record(root, journal, relative, inventory, transaction)
            node_id = Path(relative).parts[5]
            if payload.get("id") != node_id or payload.get("project_id") != candidate_id or node_id in historical:
                raise ValueError(f"备份节点路径或唯一性无效：{relative}")
            historical[node_id] = payload
        current: dict[str, Node] = {}
        for path in sorted(project_dir.glob("hosts/*/nodes/*/node.json")):
            path = safe_path(root, path.relative_to(root).as_posix())
            node = Node.model_validate(read_object(path))
            if node.id != path.parent.name or node.project_id != candidate_id or node.id in current:
                raise ValueError(f"节点路径或唯一性无效：{path}")
            node.bind_owner_host(path.parents[2].name)
            current[node.id] = node
        owners = node_layout_owners(list(current.values()))
        destination = safe_path(project_dir, f"hosts/{host}/node-layout.json")
        existing = NodeLayout.model_validate(read_object(destination)).nodes if destination.exists() else {}
        binding = safe_path(project_dir, f"hosts/{host}/local.json")
        root_path = read_object(binding).get("root_path", "") if binding.exists() else ""
        additions = {}
        sources = {}
        skipped = {}
        for relative in relatives:
            hints = _verified_record(root, journal, relative, inventory, transaction).get("layout_hints", {})
            if not isinstance(hints, dict):
                raise ValueError(f"备份布局不是对象：{relative}")
            for tile_id, position in sorted(hints.items()):
                if not tile_id.startswith(("artifact:", "artifact-overflow:")) or tile_id in additions:
                    continue
                owner = owners.get(tile_id)
                if owner is None:
                    skipped[tile_id] = "产出物已移除或不再发布"
                elif owner.owner_host_id != host:
                    skipped[tile_id] = "需在生产节点所属设备恢复"
                elif not root_path:
                    skipped[tile_id] = "项目未绑定本机"
                elif tile_id in existing:
                    skipped[tile_id] = "保留已有位置"
                elif owner.id not in historical:
                    skipped[tile_id] = "缺少历史生产节点，不能确认坐标空间"
                else:
                    space = coordinate_space(historical[owner.id], historical)
                    if space != node_coordinate_space(owner, current):
                        skipped[tile_id] = "生产节点所属空间已改变"
                        continue
                    validated = NodePosition.model_validate({**position, "space": space})
                    additions[tile_id] = validated.model_dump()
                    sources[tile_id] = relative
        projects.append({
            "project_id": candidate_id, "name": read_object(project_dir / "project.json").get("name", ""),
            "root_path": root_path, "additions": additions, "sources": sources, "skipped": skipped,
        })
    return {"transaction": transaction, "host_id": host, "projects": projects}


def _request_json(server: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = Request(
        server.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="PATCH" if payload is not None else "GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise ValueError(f"恢复接口返回 {exc.code}：{exc.read().decode(errors='replace')}") from exc


def apply_recovery(plan: dict[str, Any], server: str) -> dict[str, int]:
    prepared = []
    for project in plan["projects"]:
        if not project["additions"]:
            continue
        path = "/sessions/" + quote(project["project_id"], safe="")
        session = _request_json(server, path)
        if session.get("local_machine_id") != plan["host_id"] or session.get("root_path") != project["root_path"]:
            raise ValueError("恢复服务与备份计划的设备或项目绑定不一致")
        if session.get("read_only") or not session.get("bound_here"):
            raise ValueError(f"项目不可写：{project['project_id']}")
        additions = {key: value for key, value in project["additions"].items() if key not in session["node_positions"]}
        prepared.append((project["project_id"], path, additions))
    restored = {}
    for project_id, path, additions in prepared:
        if not additions:
            continue
        session = _request_json(server, path + "/node-layout", {"updates": additions, "only_missing": True})
        if any(key not in session["node_positions"] for key in additions):
            raise ValueError(f"服务未返回恢复后的坐标：{project_id}")
        restored[project_id] = sum(session["node_positions"][key] == value for key, value in additions.items())
    return restored


def main() -> None:
    parser = argparse.ArgumentParser(description="从迁移备份补回产出物位置；默认仅预览，不覆盖已有坐标")
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("MINICLAW_HOME", str(Path.home() / ".miniclaw2"))))
    parser.add_argument("--transaction", required=True)
    parser.add_argument("--project")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="正在运行的新版后端地址（默认 http://127.0.0.1:8000）")
    parser.add_argument("--apply", action="store_true", help="通过后端接口恢复；需先启动使用同一数据目录的服务")
    args = parser.parse_args()
    try:
        plan = recovery_plan(args.root.expanduser().resolve(), args.transaction, project_id=args.project)
        if args.apply:
            print(json.dumps({"restored": apply_recovery(plan, args.server)}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
    except (URLError, TimeoutError) as exc:
        reason = exc.reason if isinstance(exc, URLError) else str(exc)
        root = shlex.quote(str(args.root.expanduser().resolve()))
        parser.exit(1, (
            f"产出物布局恢复未完成：无法连接恢复服务 {args.server} 或等待响应失败（{reason}）。\n"
            "--apply 需要正在运行的新版 MiniClaw2 后端；--root 只指定数据目录，不会启动服务。\n"
            f"若服务尚未启动，请在另一个终端运行：MINICLAW_HOME={root} python -m miniclaw2\n"
            "若后端使用非默认地址或端口，请用 --server 指定实际地址后重试。\n"
            "可安全重试；已保存的坐标不会被覆盖。\n"
        ))
    except (MigrationError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"产出物布局恢复未完成：{exc}\n")


if __name__ == "__main__":
    main()
