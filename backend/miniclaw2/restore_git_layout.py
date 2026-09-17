from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from .domain import GitLayout, LaneLayout, NodePosition
from .git_layout import GHOST_GIT_NODE_ID
from .migrations.errors import MigrationError
from .migrations.inventory import safe_path
from .migrations.transaction import backup_payload, fsync_directory
from .migrations.validation import read_object


def recovery_plan(
    root: Path, transaction: str, preferred_host: str, *, kind: Literal["git", "lane"] = "git",
) -> dict[str, Any]:
    model = GitLayout if kind == "git" else LaneLayout
    prefix = "commit:" if kind == "git" else "planspace:"
    journal = read_object(safe_path(root / ".migration-local/transactions", transaction + "/journal.json"))
    inventory = journal["inputs"][0]
    candidates: dict[str, dict[str, NodePosition]] = {}
    sources: dict[str, dict[str, str]] = {}
    layouts = sorted(
        (relative for relative in inventory if Path(relative).match("projects/*/hosts/*/layout.json")),
        key=lambda relative: (Path(relative).parts[3] != preferred_host, relative),
    )
    for relative in layouts:
        if inventory[relative] is None:
            continue
        payload = backup_payload(root, journal, 0, relative, identifier=transaction)
        if payload.digest != inventory[relative]:
            raise ValueError(f"备份摘要不匹配：{payload.origin}")
        project_id = Path(relative).parts[1]
        if not safe_path(root, f"projects/{project_id}/project.json").is_file():
            continue
        hints = payload.record.get("layout_hints", {})
        if not isinstance(hints, dict):
            raise ValueError(f"备份布局不是对象：{payload.origin}")
        positions = candidates.setdefault(project_id, {})
        provenance = sources.setdefault(project_id, {})
        for node_id, position in sorted(hints.items()):
            if not node_id.startswith(prefix):
                continue
            if kind == "git" and node_id == GHOST_GIT_NODE_ID:
                continue
            validated = model.model_validate({"schema_version": 1, "nodes": {
                node_id: {"x": position["x"], "y": position["y"], "space": "canvas"},
            }}).nodes[node_id]
            if node_id not in positions:
                positions[node_id] = validated
                provenance[node_id] = relative
    projects = []
    for project_id, positions in sorted(candidates.items()):
        if not positions:
            continue
        destination = safe_path(root, f"projects/{project_id}/{kind}-layout.json")
        existing = model.model_validate(read_object(destination)).nodes if destination.exists() else {}
        additions = {key: value.model_dump() for key, value in positions.items() if key not in existing}
        projects.append({
            "project_id": project_id,
            "name": read_object(destination.parent / "project.json").get("name", ""),
            "existing_file": destination.exists(),
            "preserved": len(existing),
            "additions": additions,
            "sources": {key: sources[project_id][key] for key in additions},
        })
    return {"transaction": transaction, "preferred_host": preferred_host, "kind": kind, "projects": projects}


def apply_recovery(root: Path, plan: dict[str, Any]) -> dict[str, int]:
    kind = plan.get("kind", "git")
    if kind not in {"git", "lane"}:
        raise ValueError("未知布局种类")
    model = GitLayout if kind == "git" else LaneLayout
    label = "Git" if kind == "git" else "方向"
    for project in plan["projects"]:
        if project["existing_file"] and project["additions"]:
            raise ValueError(f"已有 {label} 布局仍有可补入位置，请通过运行中服务的 {kind}-layout 接口补入：{project['project_id']}")
    restored: dict[str, int] = {}
    for project in plan["projects"]:
        additions = project["additions"]
        if not additions:
            continue
        path = safe_path(root, f"projects/{project['project_id']}/{kind}-layout.json")
        payload = model.model_validate({"schema_version": 1, "nodes": additions}).model_dump()
        descriptor, temporary = tempfile.mkstemp(prefix=f".{kind}-layout-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.link(temporary, path)
            restored[project["project_id"]] = len(additions)
            fsync_directory(path.parent)
        finally:
            os.unlink(temporary)
    return restored


def main() -> None:
    parser = argparse.ArgumentParser(description="从事务原始备份恢复 Git 或方向坐标；默认仅预览，写入仅创建新文件")
    parser.add_argument("--root", type=Path, default=Path.home() / ".miniclaw2")
    parser.add_argument("--transaction", required=True)
    parser.add_argument("--preferred-host")
    parser.add_argument("--kind", choices=("git", "lane"), default="git")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    try:
        host = args.preferred_host or read_object(root / "machine.json")["id"]
        plan = recovery_plan(root, args.transaction, host, kind=args.kind)
        result = {
            "transaction": plan["transaction"],
            "kind": plan["kind"],
            "preferred_host": host,
            "projects": [{
                "project_id": project["project_id"], "name": project["name"],
                "additions": len(project["additions"]), "preserved": project["preserved"],
                "existing_file": project["existing_file"],
            } for project in plan["projects"]],
        }
        if args.apply:
            result["restored"] = apply_recovery(root, plan)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (MigrationError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"布局恢复未完成：{exc}\n")


if __name__ == "__main__":
    main()
