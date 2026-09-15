from __future__ import annotations

import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .catalog import steps, version_of
from .errors import MigrationError
from .inventory import files, safe_path
from .sdk import MigrationContext
from .steps.v0017_layout_recovery import pristine_hints, recover_layout
from .transaction import backup_payload, file_digest, hydrated_backup
from .validation import read_object


def layout_impact(root: Path) -> list[dict[str, Any]]:
    schema = root / "schema.json"
    if not schema.exists():
        return []
    source = version_of(read_object(schema), schema)
    with tempfile.TemporaryDirectory(prefix="layout-impact-") as temporary:
        stage = Path(temporary)
        for relative, scope in files(root).items():
            if scope == "shared":
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / relative, target)
        context = MigrationContext(stage, "shared", "", root)
        for migration in steps(source):
            if migration.target < 17 and "shared" in migration.scopes:
                migration.upgrade(context)
                migration.verify(context)
        return recover_layout(context, write=False)


def _transaction_reports(
    root: Path, journal: dict[str, Any], identifier: str, inventory: dict[str, Any],
    snapshot: Path, projects: set[str], with_history: set[str],
) -> list[dict[str, Any]]:
    """Report which pre-migration coordinates one transaction could restore."""
    context = MigrationContext(root, "shared", "", snapshot)
    reports: list[dict[str, Any]] = []
    flat_projects: set[str] = set()
    for relative, _payload in pristine_hints(context):
        if inventory.get(relative) is None or file_digest(safe_path(snapshot, relative)) != inventory[relative]:
            raise MigrationError("migration_failed", "历史布局备份摘要不匹配", snapshot / relative)
        with_history.add(relative.split("/")[1])
        if relative.endswith("/project.json"):
            flat_projects.add(relative.split("/")[1])
    per_project: dict[str, dict[str, int]] = {}
    for report in recover_layout(context, write=False):
        counts = per_project.setdefault(report["project_id"], {})
        for kind, count in report["restored"].items():
            counts[kind] = counts.get(kind, 0) + count
    for project_id, counts in sorted(per_project.items()):
        if not any(counts.values()):
            continue
        arguments = ["--root", str(root), "--transaction", identifier]
        commands = []
        message = "本次快照不含这些旧坐标，v17 不会自动读取历史备份；请先运行恢复预览。真实节点的缺失位置需核对备份后通过 node-layout 接口补入。"
        if project_id in flat_projects:
            commands.append(shlex.join(["python", "-m", "miniclaw2", "migrations", "recover", *arguments, "--output", "/path/to/empty-recovery"]))
            message = "旧备份含 v14 未分区项目布局，现有恢复工具只读取 host 分片。请先导出到空隔离目录并核对；v17 不会自动读取历史备份，不能直接对活动存储复制旧记录。"
        elif counts["artifact"]:
            commands.append(shlex.join(["python", "-m", "miniclaw2.restore_artifact_layout", *arguments, "--project", project_id]))
        for kind in ("git", "lane"):
            if counts[kind] and project_id not in flat_projects:
                commands.append(shlex.join(["python", "-m", "miniclaw2.restore_git_layout", *arguments, "--kind", kind]))
        reports.append({"project_id": project_id, "transaction": identifier, "missing": counts,
                        "message": message, "commands": commands})
    return reports


def layout_recovery_guidance(root: Path) -> list[dict[str, Any]]:
    projects = {path.parent.name for path in root.glob("projects/*/project.json")}
    reports: list[dict[str, Any]] = []
    with_history: set[str] = set()
    for journal_path in sorted((root / ".migration-local/transactions").glob("*/journal.json")):
        journal = read_object(journal_path)
        if journal.get("phase") != "ready":
            continue
        identifier = journal_path.parent.name
        inventory = journal["inputs"][0]
        if not any(Path(relative).match("projects/*/hosts/*/layout.json") for relative in inventory):
            if inventory.get("schema.json") is None:
                continue
            marker = backup_payload(root, journal, 0, "schema.json", identifier=identifier)
            if marker.record.get("schema_version", 17) > 15:
                continue
        with hydrated_backup(root, journal, 0, identifier=identifier) as snapshot:
            reports.extend(_transaction_reports(root, journal, identifier, inventory, snapshot, projects, with_history))
    for project_id in sorted(projects - with_history):
        reports.append({"project_id": project_id, "transaction": None,
                        "message": "本次快照不含旧布局，未找到可核对的本机历史布局备份；v17 不会自动找回已丢坐标。若此前丢失布局，请在原升级设备查找事务备份，再运行恢复工具。",
                        "commands": []})
    return reports
