from __future__ import annotations

from collections.abc import Sequence

from .sdk import Migration, MigrationContext
from .steps.v0017_layout_recovery import recover_layout


V16 = "miniclaw2/store/v16:owner-node-layout-v1;explicit-coordinate-space;browser-viewport"
V17 = "miniclaw2/store/v17:layout-recovery-v1"

# 修复关系必须显式声明，不能从步骤读写的文件推导，也不能改动已发布脚本。
REPAIRS: dict[str, tuple[str, ...]] = {V17: (V16,)}


def repaired_contracts(migrations: Sequence[Migration], context: MigrationContext) -> frozenset[str]:
    contracts = {migration.contract: index for index, migration in enumerate(migrations)}
    # 修复者和被修复者都必须在本条链内，且修复者在后；窗口裁剪不能扩大授权。
    declared = {
        (repair.contract, contract)
        for repair in migrations
        for contract in REPAIRS.get(repair.contract, ())
        if contract in contracts and contracts[contract] < contracts[repair.contract]
    }
    if context.scope != "shared" or (V17, V16) not in declared:
        return frozenset()
    reports = recover_layout(context, write=False)
    # 必须有本次输入可补回的坐标；缺失来源或空影响不代表净无损，不读取历史备份兜底。
    if any(report["source"] == "missing" for report in reports):
        return frozenset()
    if not any(sum(report["restored"].values()) for report in reports):
        return frozenset()
    return frozenset({V16})
