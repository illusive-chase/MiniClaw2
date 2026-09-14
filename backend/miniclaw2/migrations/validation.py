from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .errors import MigrationError
from .inventory import files


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) if path.suffix in {".yaml", ".yml"} else json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("记录必须是对象")
        return value
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise MigrationError("migration_failed", str(exc), path) from exc


def validate(root: Path, *, external: bool = False) -> None:
    from ..domain import HumanGate, Node, Project, UNBOUND_ROOT_PATH
    from ..global_config import GlobalConfig
    from ..templates.loader import TemplateError, _load_from_root

    nodes: dict[tuple[str, str], Path] = {}
    for relative in files(root, external=external):
        path = root / relative
        parts = Path(relative).parts
        host_record = not external and len(parts) == 5 and parts[0] == "projects" and parts[2] == "hosts"
        node_record = not external and len(parts) == 7 and parts[0] == "projects" and parts[2] == "hosts" and parts[4] == "nodes"
        try:
            if not external and path.name == "project.json" and len(parts) == 3 and parts[0] == "projects":
                payload = read_object(path)
                if any(key in payload for key in ("root_path", "layout_hints", "layout_viewport")):
                    raise ValueError("共享项目不能包含本机字段；请先通过中间版本完成分区")
                if not payload.get("model_preset_id") or payload.get("id") != parts[1]:
                    raise ValueError("项目 id 或模型预设无效")
                Project.model_validate({**payload, "root_path": UNBOUND_ROOT_PATH})
                if any(candidate.is_file() for candidate in (path.parent / "nodes").rglob("*")):
                    raise ValueError("发现未分区节点，不能猜测归属")
            elif path.name == "node.json" and node_record:
                node = Node.model_validate(read_object(path))
                if node.id != parts[5] or node.project_id != parts[1]:
                    raise ValueError("节点 id 或项目归属与路径不一致")
                key = (node.project_id, node.id)
                if key in nodes:
                    raise ValueError(f"节点同时出现在多个 host 分区：{nodes[key]}")
                if not (root / "projects" / node.project_id / "project.json").is_file():
                    raise ValueError("节点所属项目不存在")
                nodes[key] = path
            elif relative == "config.json" and not external:
                GlobalConfig.model_validate(read_object(path))
            elif host_record and path.name in {"local.json", "layout.json", "host.json", "head.json", "git_aliases.json"}:
                payload = read_object(path)
                if path.name == "local.json" and (set(payload) != {"root_path"} or not isinstance(payload["root_path"], str)):
                    raise ValueError("本机绑定必须且只能包含 root_path")
                if path.name == "host.json" and any(key in payload for key in ("identity", "attestation")):
                    raise ValueError("host 观察仍包含退休权限字段")
            elif node_record and path.name in {"events.jsonl", "gates.jsonl"}:
                with path.open(encoding="utf-8") as stream:
                    for number, line in enumerate(stream, 1):
                        if not line.endswith("\n"):
                            raise ValueError(f"JSONL 第 {number} 行未完整落盘")
                        record = json.loads(line)
                        if path.name == "events.jsonl":
                            if type(record.get("schema_version")) is not int or record["schema_version"] != 2 or not isinstance(record.get("event"), dict) or type(record.get("seq")) is not int:
                                raise ValueError(f"第 {number} 行不是当前事件载体")
                        else:
                            HumanGate.model_validate(record["gate"])
            elif path.suffix in {".yaml", ".yml"} and (external or parts[0] == "contextspace"):
                payload = read_object(path)
                if "bindings/projects/" in relative and "local_paths" in payload.get("project", {}):
                    raise ValueError("共享绑定仍包含本机路径")
                if path.name == "template.yaml":
                    _load_from_root(path.parent, path.parent.name, store_root=root)
            elif relative == "tags.json" and not external:
                from ..tags import load_tags

                load_tags(root)
        except MigrationError:
            raise
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, TemplateError) as exc:
            raise MigrationError("migration_failed", str(exc), path) from exc
