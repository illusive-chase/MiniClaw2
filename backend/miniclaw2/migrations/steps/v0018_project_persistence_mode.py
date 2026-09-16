from __future__ import annotations

from ..sdk import Migration, MigrationContext


def upgrade(context: MigrationContext) -> None:
    for relative in context.paths("projects/*/project.json"):
        project = context.read(relative)
        if "persistence_mode" in project:
            continue
        project["persistence_mode"] = (
            "ephemeral" if project.get("temporary") is True else "durable"
        )
        context.replace(relative, project)


def verify(context: MigrationContext) -> None:
    for relative in context.paths("projects/*/project.json"):
        project = context.read(relative)
        mode = project.get("persistence_mode")
        if mode not in {"durable", "ephemeral", "remote"}:
            raise ValueError(f"项目持久化模式无效：{relative}")
        if (project.get("temporary") is True) != (mode == "ephemeral"):
            raise ValueError(f"项目 temporary 与持久化模式冲突：{relative}")
        if (mode == "remote") != isinstance(project.get("remote"), dict):
            raise ValueError(f"远端项目身份与持久化模式冲突：{relative}")


MIGRATION = Migration(
    source=17,
    target=18,
    scopes=("shared",),
    summary="为项目写入 durable、ephemeral 或 remote 持久化模式；旧 temporary 项目映射为 ephemeral",
    contract="miniclaw2/store/v18:project-persistence-mode-v1",
    upgrade=upgrade,
    verify=verify,
)
