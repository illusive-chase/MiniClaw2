from __future__ import annotations

from ..sdk import Migration, MigrationContext


def upgrade(context: MigrationContext) -> None:
    for relative in context.paths("projects/*/hosts/*/nodes/*/node.json"):
        node = context.read(relative)
        if "diff_review" not in node:
            node["diff_review"] = False
            context.replace(relative, node)


def verify(context: MigrationContext) -> None:
    for relative in context.paths("projects/*/hosts/*/nodes/*/node.json"):
        node = context.read(relative)
        if type(node.get("diff_review")) is not bool:
            raise ValueError(f"节点 diff_review 设置无效：{relative}")


MIGRATION = Migration(
    source=20,
    target=21,
    scopes=("shared",),
    summary="为节点记录增加运行区间 diff review 开关",
    contract="miniclaw2/store/v21:diff-review-v1",
    upgrade=upgrade,
    verify=verify,
)
