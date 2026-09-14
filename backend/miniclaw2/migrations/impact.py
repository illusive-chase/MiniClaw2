from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .errors import MigrationError
from .inventory import files
from .validation import read_object


def layout_impact(root: Path) -> list[dict[str, Any]]:
    inventory = files(root)
    owners: dict[tuple[str, str], str] = {}
    hosts: set[tuple[str, str]] = set()
    for relative in inventory:
        parts = Path(relative).parts
        if len(parts) == 5 and parts[0] == "projects" and parts[2] == "hosts":
            hosts.add((parts[1], parts[3]))
        if len(parts) != 7 or parts[0] != "projects" or parts[2] != "hosts" or parts[4] != "nodes" or parts[6] != "node.json":
            continue
        payload = read_object(root / relative)
        key = (parts[1], parts[5])
        if payload.get("id") != parts[5] or payload.get("project_id") != parts[1] or key in owners:
            raise MigrationError("migration_failed", "节点路径或跨 host 唯一性无效", root / relative)
        owners[key] = parts[3]
        hosts.add((parts[1], parts[3]))
    result = []
    for project_id, host_id in sorted(hosts):
        path = root / "projects" / project_id / "hosts" / host_id / "layout.json"
        payload = read_object(path) if path.exists() else {}
        hints = payload.get("layout_hints", {})
        if not isinstance(hints, dict):
            raise MigrationError("migration_failed", "布局必须是对象", path)
        retained = foreign = other = 0
        for node_id, position in hints.items():
            owner = owners.get((project_id, node_id))
            if owner == host_id:
                if not isinstance(position, dict) or any(
                    type(position.get(axis)) not in (int, float) or not math.isfinite(position[axis])
                    for axis in ("x", "y")
                ):
                    raise MigrationError("migration_failed", f"节点坐标不是有限数值：{node_id}", path)
                retained += 1
            elif owner is not None:
                foreign += 1
            else:
                other += 1
        result.append({"project_id": project_id, "host_id": host_id,
                       "source": "missing" if not path.exists() else "present" if hints else "empty",
                       "retained": retained, "discarded_foreign": foreign,
                       "discarded_synthetic_or_missing": other,
                       "viewport_discarded": payload.get("layout_viewport") is not None})
    return result
