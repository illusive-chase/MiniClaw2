from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import CURRENT_VERSION, MINIMUM_VERSION, steps, version_of
from .errors import MigrationError
from .impact import layout_impact, layout_recovery_guidance
from .inventory import files
from .sdk import Migration
from .validation import read_object


def describe_step(migration: Migration) -> dict[str, Any]:
    return {"source": migration.source, "target": migration.target, "summary": migration.summary,
            "contract": migration.contract, "destructive": migration.destructive}


def migration_plan(root: Path) -> dict[str, Any]:
    schema = root / "schema.json"
    if schema.exists():
        source = version_of(read_object(schema), schema)
    elif files(root):
        raise MigrationError(
            "migration_failed",
            "非空存储缺少 schema.json；请先识别基线，不能自动当作新库",
            schema,
        )
    else:
        source = CURRENT_VERSION
    applicable = steps(source)
    confirmation = [item for item in steps(MINIMUM_VERSION) if item.destructive]
    machine_path = root / "machine.json"
    machine = read_object(machine_path) if machine_path.exists() else {}
    hosts: dict[str, str] = {}
    for path in sorted(root.glob("projects/*/hosts/*/host.json")):
        payload = read_object(path)
        hosts[path.parent.name] = payload.get("label") or path.parent.name
    for path in sorted(root.glob("projects/*/project.json")):
        payload = read_object(path)
        if payload.get("machine_id"):
            hosts.setdefault(payload["machine_id"], payload.get("machine_label") or payload["machine_id"])
    return {
        "source": source, "target": CURRENT_VERSION, "minimum": MINIMUM_VERSION,
        "steps": [describe_step(item) for item in applicable],
        "local_destructive_steps": [describe_step(item) for item in applicable if item.destructive],
        "sync_confirmation": [item.summary for item in confirmation],
        "sync_confirmation_contracts": [describe_step(item) for item in confirmation],
        "sync_confirmation_note": "apply --accept-data-loss 同时授权当前发行窗口内的全部有损契约，用于规范化远端旧快照；即使本机无待执行步骤也会补登。凭据绑定本机和物理存储根，不随同步传播。",
        "confirmation_hosts": [{"host_id": host, "label": label, "local": host == machine.get("id")}
                               for host, label in sorted(hosts.items())],
        "confirmation_hosts_note": "确认也覆盖本机快照中以下设备的记录。列表仅反映已知 host，不会联系远端，也不代表这些设备自身已确认；尚未获取的远端快照也受上述契约授权约束。",
        "layout_impact": layout_impact(root) if source < 17 else [],
        "layout_recovery": layout_recovery_guidance(root) if source >= 16 else [],
        "layout_note": "v17 显式修复 v16：两步同在执行链且本次原始输入可补回有效坐标时，可自动升级。缺失来源或空影响不自动放行。commit:ghost 属于单机未提交工作区，不作为共享坐标恢复，改由浏览器本地保存；其他合成图元与旧 viewport 按设计不恢复。已过 v16 且输入已丢失时，须核对历史备份，不能宣称自动找回。",
        "note": "只读预览，不记录确认；本机及外部 ContextSpace 游标在 apply 中独立核验。执行前请重新核对计划。",
    }
