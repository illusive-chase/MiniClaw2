from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .catalog import CURRENT_VERSION, MINIMUM_VERSION, check_manifest, generate, read_manifest, steps
from .coordinator import coordinator, open_storage
from .errors import MigrationError
from .inventory import LOCAL_DIRECTORY
from .impact import layout_impact, layout_recovery_guidance
from .validation import read_object
from .inventory import safe_path
from .transaction import backup_digest, backup_extract, backup_origin, prune_transactions


def status(root: Path) -> dict[str, Any]:
    schema = root / "schema.json"
    return {"target": CURRENT_VERSION, "minimum": MINIMUM_VERSION,
            "schema": read_object(schema) if schema.exists() else None,
            "local": read_object(root / LOCAL_DIRECTORY / "state.json") if (root / LOCAL_DIRECTORY / "state.json").exists() else None,
            "transactions": [read_object(path) for path in sorted((root / LOCAL_DIRECTORY / "transactions").glob("*/journal.json"))],
            "backup_directory": str(root / "migration-backups")}


def main(arguments: list[str]) -> None:
    from ..global_config import miniclaw_home

    parser = argparse.ArgumentParser(prog="miniclaw2 migrations", description="格式迁移诊断与恢复；不会下载或执行远端脚本")
    parser.add_argument("action", choices=["status", "plan", "apply", "recover", "release", "check", "prune"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--prune", action="store_true", help="发布时删除迁移目录内三步窗口之外的脚本")
    parser.add_argument("--keep", type=int, default=2, help="仅 prune：保留最近多少个已完结事务的备份（默认 2）")
    parser.add_argument("--accept-data-loss", action="store_true", help="仅 apply：明确接受迁移脚本声明的语义损失，并记录在事务日志")
    parser.add_argument("--transaction", help="恢复到隔离目录时指定事务 id")
    parser.add_argument("--output", type=Path, help="仅 recover：把事务源备份导出到一个空目录，不回滚活动存储")
    args = parser.parse_args(arguments)
    if args.accept_data_loss and args.action != "apply":
        parser.error("--accept-data-loss 仅用于 apply")
    if args.output is not None and args.action != "recover":
        parser.error("--output 仅用于 recover")
    if args.keep < 0:
        parser.error("--keep 不能为负")
    root = (args.root or miniclaw_home()).expanduser().resolve()
    try:
        if args.action == "release":
            result = generate(prune=args.prune)
        elif args.action == "check":
            check_manifest()
            result = {"state": "ready", "target": CURRENT_VERSION, "minimum": MINIMUM_VERSION}
        elif args.action == "status":
            result = status(root)
        elif args.action == "prune":
            # Reclaiming a transaction has to take the storage lock, which
            # constructing the coordinator does: `publish` writes its ready
            # journal and only then fsyncs the transaction directory, so a
            # delete landing between those two steps fails a publication
            # whose live data has already changed. Under a running backend
            # this now refuses outright instead of deleting some of what
            # that backend still depends on.
            storage = coordinator(root)
            with storage.mutex:
                # A backup that is still the only source of restorable
                # coordinates is load-bearing regardless of age, so it is
                # excluded here rather than left to whoever picks --keep.
                protected = {report["transaction"] for report in layout_recovery_guidance(root) if report.get("transaction")}
                result = {"state": "ready", **prune_transactions(root, keep=args.keep, protected=protected),
                          "detail": "已回收已完结事务的暂存树；保留窗口之外、且不再被布局恢复引用的备份已清理。未完结事务与仍可恢复坐标的备份保持原样"}
        elif args.action == "plan":
            storage = coordinator(root)
            source = storage.source_version()
            result = {"source": source, "target": CURRENT_VERSION, "minimum": MINIMUM_VERSION,
                      "steps": [{"source": item.source, "target": item.target, "summary": item.summary,
                                 "destructive": item.destructive} for item in steps(source)],
                      "sync_confirmation": [item.summary for item in steps(MINIMUM_VERSION) if item.destructive],
                      "layout_impact": layout_impact(root) if source < 17 else [],
                      "layout_recovery": layout_recovery_guidance(root) if source >= 16 else [],
                      "layout_note": "v14/v15 升级包含 v16 有损中间步骤，仍需 --accept-data-loss 确认；v17 在同一事务内从原始快照补回有效节点、产物、Git 和方向坐标，只补缺。合成图元与旧 viewport 按设计不恢复；坏项及悬空条目见 layout_impact。已是 v16 的存储需另行核对历史备份。",
                      "note": "本机及外部 ContextSpace 游标在 apply 中独立核验"}
        elif args.action == "recover" and args.output is not None:
            if not args.transaction:
                parser.error("--output 必须同时指定 --transaction")
            restore_isolated(root, args.transaction, args.output)
            result = {"state": "ready", "output": str(args.output), "detail": "已导出原始备份；活动存储未回滚，导出数据再次打开时仍需通过格式准入"}
        else:
            if args.accept_data_loss:
                from ..sync import ensure_machine_identity

                storage = coordinator(root)
                storage.source_version()
                storage.apply(ensure_machine_identity(root).id, accept_data_loss=True)
            else:
                open_storage(root)
            result = {"state": "ready", **status(root)}
    except (MigrationError, OSError, ValueError) as exc:
        parser.exit(1, f"迁移操作未完成：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def restore_isolated(root: Path, identifier: str, output: Path) -> None:
    journal = read_object(safe_path(root / LOCAL_DIRECTORY / "transactions", identifier + "/journal.json"))
    if output.exists() and any(output.iterdir()):
        raise MigrationError("migration_failed", "隔离恢复目录必须为空", output)
    for index, inventory in enumerate(journal["inputs"]):
        for relative, checksum in inventory.items():
            if checksum is None:
                continue
            if backup_digest(root, journal, index, relative, identifier=identifier) != checksum:
                origin = backup_origin(root, journal, index, relative, identifier=identifier)
                raise MigrationError("migration_failed", f"原始备份缺失或摘要不一致：{origin}", root)
    for index, inventory in enumerate(journal["inputs"]):
        destination = output / ("store" if index == 0 else "external_context")
        for relative, checksum in inventory.items():
            if checksum is not None:
                target = safe_path(destination, relative)
                if backup_extract(root, journal, index, relative, target, identifier=identifier) != checksum:
                    origin = backup_origin(root, journal, index, relative, identifier=identifier)
                    raise MigrationError("migration_failed", f"导出期间原始备份发生变化：{origin}", root)
                target.chmod(int(checksum.split(":")[0], 8))
