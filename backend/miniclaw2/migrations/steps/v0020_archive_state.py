from __future__ import annotations

from ..sdk import Migration, MigrationContext


def upgrade(context: MigrationContext) -> None:
    for relative in context.paths("projects/*/project.json"):
        project = context.read(relative)
        if "archived_at" not in project:
            project["archived_at"] = None
            context.replace(relative, project)


def verify(context: MigrationContext) -> None:
    for relative in context.paths("projects/*/project.json"):
        project = context.read(relative)
        archived_at = project.get("archived_at")
        if archived_at is not None and (
            isinstance(archived_at, bool)
            or not isinstance(archived_at, (int, float))
        ):
            raise ValueError(f"项目归档时间无效：{relative}")


MIGRATION = Migration(
    source=19,
    target=20,
    scopes=("shared",),
    summary="为项目记录增加可逆的归档时间",
    contract="miniclaw2/store/v20:archive-state-v1",
    upgrade=upgrade,
    verify=verify,
)
