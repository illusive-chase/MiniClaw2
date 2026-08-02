"""One-time migration to project-scoped planspace ids.

Run a dry-run first::

    python -m miniclaw2.migrate_planspaces

Then stop MiniClaw2 and apply the migration::

    python -m miniclaw2.migrate_planspaces --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class LaneCopy:
    old_id: str
    new_id: str
    source: Path
    destination: Path


@dataclass
class MigrationPlan:
    home: Path
    context_root: Path
    bindings: dict[Path, dict[str, Any]]
    binding_maps: dict[str, dict[str, str]]
    lane_copies: list[LaneCopy]
    json_updates: dict[Path, dict[str, Any]]

    @property
    def changed_lane_ids(self) -> int:
        return sum(
            old_id != new_id
            for mapping in self.binding_maps.values()
            for old_id, new_id in mapping.items()
        )


def build_plan(home: Path, context_root: Path) -> MigrationPlan:
    home = home.expanduser().resolve()
    context_root = context_root.expanduser().resolve()
    bindings: dict[Path, dict[str, Any]] = {}
    binding_maps: dict[str, dict[str, str]] = {}
    lane_copies: list[LaneCopy] = []
    planspaces_root = context_root / "plugs" / "planspaces"

    for binding_path in sorted(
        (context_root / "bindings" / "projects").glob("*.yaml")
    ):
        binding = _read_yaml(binding_path)
        binding_id = _required_string(binding, "id", binding_path)
        if not binding_id.startswith("project."):
            raise ValueError(f"无效的 project binding id：{binding_id!r}")
        scope = binding_id.split(".", 1)[1]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", scope):
            raise ValueError(f"binding id 无法用作 planspace scope：{binding_id!r}")

        raw_plugs = binding.get("plugs") or []
        if not isinstance(raw_plugs, list):
            raise ValueError(f"{binding_path}：plugs 必须是列表")
        used_local_slugs: set[str] = set()
        mapping: dict[str, str] = {}
        rewritten_plugs: list[Any] = []
        for item in raw_plugs:
            old_id = _plug_id(item)
            if old_id is None or not old_id.startswith("planspaces."):
                rewritten_plugs.append(item)
                continue
            source = planspaces_root / old_id.split(".", 1)[1]
            manifest_path = source / "manifest.yaml"
            if not manifest_path.exists():
                raise ValueError(f"planspace manifest 不存在：{manifest_path}")
            manifest = _read_yaml(manifest_path)
            scoped_prefix = f"planspaces.{scope}."
            if old_id.startswith(scoped_prefix):
                base_slug = old_id[len(scoped_prefix):]
            else:
                title = manifest.get("title")
                base_slug = (
                    _slugify(title or "direction")
                    if isinstance(title, str)
                    else old_id.split(".", 1)[1]
                )
            local_slug = _unique_slug(base_slug, used_local_slugs)
            used_local_slugs.add(local_slug)
            new_id = f"planspaces.{scope}.{local_slug}"
            mapping[old_id] = new_id
            rewritten_plugs.append(_replace_plug_id(item, new_id))
            destination = planspaces_root / new_id.split(".", 1)[1]
            lane_copies.append(
                LaneCopy(
                    old_id=old_id,
                    new_id=new_id,
                    source=source,
                    destination=destination,
                )
            )
        if mapping:
            rewritten = dict(binding)
            rewritten["plugs"] = rewritten_plugs
            bindings[binding_path] = rewritten
            binding_maps[binding_id] = mapping

    _validate_destinations(lane_copies)
    json_updates = _build_store_updates(home, bindings, binding_maps)
    return MigrationPlan(
        home=home,
        context_root=context_root,
        bindings=bindings,
        binding_maps=binding_maps,
        lane_copies=lane_copies,
        json_updates=json_updates,
    )


def apply_plan(plan: MigrationPlan) -> Path | None:
    if plan.changed_lane_ids == 0:
        return None
    backup_root = (
        plan.home
        / "migration-backups"
        / f"project-scoped-planspaces-{int(time.time())}"
    )
    _backup(plan, backup_root)

    for move in plan.lane_copies:
        if move.source.resolve() != move.destination.resolve():
            shutil.copytree(move.source, move.destination)
        manifest_path = move.destination / "manifest.yaml"
        manifest = _read_yaml(manifest_path)
        manifest["id"] = move.new_id
        _atomic_write_yaml(manifest_path, manifest)

    for path, payload in plan.bindings.items():
        _atomic_write_yaml(path, payload)
    for path, payload in plan.json_updates.items():
        _atomic_write_json(path, payload)

    destinations = {move.destination.resolve() for move in plan.lane_copies}
    sources = {move.source.resolve() for move in plan.lane_copies}
    for source in sorted(sources):
        if source not in destinations and source.exists():
            shutil.rmtree(source)
    return backup_root


def _build_store_updates(
    home: Path,
    bindings: dict[Path, dict[str, Any]],
    binding_maps: dict[str, dict[str, str]],
) -> dict[Path, dict[str, Any]]:
    updates: dict[Path, dict[str, Any]] = {}
    project_bindings: dict[str, str] = {}
    for binding_path, binding in bindings.items():
        binding_id = _required_string(binding, "id", binding_path)
        project = binding.get("project")
        if isinstance(project, dict):
            pid = project.get("miniclaw_project_id")
            if isinstance(pid, str) and pid:
                project_bindings[pid] = binding_id

    global_candidates: dict[str, set[str]] = {}
    for mapping in binding_maps.values():
        for old_id, new_id in mapping.items():
            global_candidates.setdefault(old_id, set()).add(new_id)

    for project_path in sorted((home / "projects").glob("*/project.json")):
        project = _read_json(project_path)
        pid = _required_string(project, "id", project_path)
        explicit = project.get("project_context_binding_id")
        binding_id = (
            explicit
            if isinstance(explicit, str) and explicit
            else project_bindings.get(pid)
        )
        mapping = binding_maps.get(binding_id or "", {})

        changed = False
        active = project.get("active_planspace_id")
        if isinstance(active, str) and active:
            migrated = _resolve_lane_id(active, mapping, global_candidates, project_path)
            if migrated != active:
                project["active_planspace_id"] = migrated
                changed = True
        view = project.get("planspace_view")
        if isinstance(view, dict):
            migrated_view: dict[str, Any] = {}
            for lane_id, preferences in view.items():
                migrated_id = _resolve_lane_id(
                    lane_id, mapping, global_candidates, project_path
                )
                migrated_view[migrated_id] = preferences
            if migrated_view != view:
                project["planspace_view"] = migrated_view
                changed = True
        if changed:
            updates[project_path] = project

        nodes_dir = project_path.parent / "nodes"
        for node_path in sorted(nodes_dir.glob("*/node.json")):
            node = _read_json(node_path)
            node_changed = False
            lane_id = node.get("planspace_id")
            if isinstance(lane_id, str) and lane_id:
                migrated = _resolve_lane_id(
                    lane_id, mapping, global_candidates, node_path
                )
                if migrated != lane_id:
                    node["planspace_id"] = migrated
                    node_changed = True
            snapshot = node.get("settings_snapshot")
            if isinstance(snapshot, dict):
                snapshot_lane = snapshot.get("active_planspace_id")
                if isinstance(snapshot_lane, str) and snapshot_lane:
                    migrated = _resolve_lane_id(
                        snapshot_lane, mapping, global_candidates, node_path
                    )
                    if migrated != snapshot_lane:
                        snapshot["active_planspace_id"] = migrated
                        node_changed = True
            if node_changed:
                updates[node_path] = node

            preview_path = node_path.with_name("preview.json")
            if preview_path.exists():
                preview = _read_json(preview_path)
                preview_lane = preview.get("lane")
                if isinstance(preview_lane, str) and preview_lane:
                    migrated = _resolve_lane_id(
                        preview_lane, mapping, global_candidates, preview_path
                    )
                    if migrated != preview_lane:
                        preview["lane"] = migrated
                        updates[preview_path] = preview
    return updates


def _resolve_lane_id(
    lane_id: str,
    project_mapping: dict[str, str],
    global_candidates: dict[str, set[str]],
    source: Path,
) -> str:
    if lane_id in project_mapping:
        return project_mapping[lane_id]
    candidates = global_candidates.get(lane_id, set())
    if len(candidates) == 1:
        return next(iter(candidates))
    if not candidates:
        raise ValueError(f"{source}：找不到 planspace {lane_id!r} 的迁移目标")
    raise ValueError(
        f"{source}：planspace {lane_id!r} 被多个 binding 共享，无法确定项目归属"
    )


def _validate_destinations(moves: list[LaneCopy]) -> None:
    destinations: dict[Path, LaneCopy] = {}
    source_paths = {move.source.resolve() for move in moves}
    for move in moves:
        destination = move.destination.resolve()
        prior = destinations.get(destination)
        if prior is not None and prior.source.resolve() != move.source.resolve():
            raise ValueError(f"多个 planspace 将写入同一路径：{move.destination}")
        destinations[destination] = move
        if (
            destination.exists()
            and destination not in source_paths
            and move.source.resolve() != destination
        ):
            raise ValueError(f"迁移目标已存在：{move.destination}")


def _backup(plan: MigrationPlan, backup_root: Path) -> None:
    projects = plan.home / "projects"
    if projects.exists():
        shutil.copytree(projects, backup_root / "projects")
    bindings = plan.context_root / "bindings"
    if bindings.exists():
        shutil.copytree(bindings, backup_root / "contextspace" / "bindings")
    planspaces = plan.context_root / "plugs" / "planspaces"
    if planspaces.exists():
        shutil.copytree(
            planspaces,
            backup_root / "contextspace" / "plugs" / "planspaces",
        )
    _atomic_write_json(
        backup_root / "locations.json",
        {
            "miniclaw_home": str(plan.home),
            "contextspace_root": str(plan.context_root),
        },
    )


def _plug_id(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        value = item.get("id")
        return value if isinstance(value, str) and value else None
    return None


def _replace_plug_id(item: Any, new_id: str) -> Any:
    if isinstance(item, str):
        return new_id
    replaced = dict(item)
    replaced["id"] = new_id
    return replaced


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


def _unique_slug(base: str, used: set[str]) -> str:
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _required_string(payload: dict[str, Any], key: str, path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}：缺少字符串字段 {key!r}")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}：必须是 JSON object")
    return payload


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}：必须是 YAML mapping")
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _default_home() -> Path:
    configured = os.environ.get("MINICLAW_HOME")
    return (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".miniclaw2"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将旧 planspace ID 迁移为 project-scoped ID"
    )
    parser.add_argument("--home", type=Path, default=_default_home())
    parser.add_argument("--context-home", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入；省略时只做 dry-run",
    )
    args = parser.parse_args()
    context_root = args.context_home
    if context_root is None:
        configured = os.environ.get("MINICLAW_CONTEXT_HOME")
        context_root = (
            Path(configured).expanduser()
            if configured
            else args.home / "contextspace"
        )

    try:
        plan = build_plan(args.home, context_root)
        print(f"planspace ID 变更：{plan.changed_lane_ids}")
        print(f"待改写 JSON 文件：{len(plan.json_updates)}")
        for binding_id, mapping in plan.binding_maps.items():
            for old_id, new_id in mapping.items():
                if old_id != new_id:
                    print(f"  {binding_id}: {old_id} -> {new_id}")
        if not args.apply:
            print("dry-run 完成；停止 MiniClaw2 后加 --apply 执行迁移。")
            return
        backup = apply_plan(plan)
        if backup is None:
            print("数据已经使用 project-scoped planspace ID，无需迁移。")
        else:
            print(f"迁移完成；备份位于 {backup}")
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        parser.exit(1, f"迁移失败：{exc}\n")


if __name__ == "__main__":
    main()
