from __future__ import annotations

from pathlib import Path

from ..sdk import Migration, MigrationContext


DEFAULT_ICON = {"glyph": "code", "hue": "coral"}


def upgrade(context: MigrationContext) -> None:
    projects: dict[str, dict] = {}
    for relative in context.paths("projects/*/project.json"):
        project = context.read(relative)
        if "icon" not in project:
            project["icon"] = dict(DEFAULT_ICON)
            context.replace(relative, project)
        projects[Path(relative).parts[1]] = project
    if any(True for _ in context.paths("home.json")):
        return
    activity: dict[str, float] = {}
    for relative in context.paths("projects/*/hosts/*/nodes/*/node.json"):
        project_id = Path(relative).parts[1]
        node = context.read(relative)
        for key in ("finished_at", "started_at", "created_at"):
            value = node.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                activity[project_id] = max(activity.get(project_id, value), value)
    active = [
        project_id
        for project_id, project in projects.items()
        if project.get("archived_at") is None
    ]

    def last_activity(project_id: str) -> float:
        created = projects[project_id].get("created_at")
        fallback = created if isinstance(created, (int, float)) and not isinstance(created, bool) else 0.0
        return activity.get(project_id, fallback)

    active.sort(key=lambda project_id: (-last_activity(project_id), project_id))
    context.replace(
        "home.json",
        {"version": 1, "items": [{"project": project_id} for project_id in active]},
    )


def verify(context: MigrationContext) -> None:
    from ...home_layout import HomeLayout, check_membership
    from ...project_icons import ProjectIcon

    active: list[str] = []
    for relative in context.paths("projects/*/project.json"):
        project = context.read(relative)
        if "icon" not in project:
            raise ValueError(f"项目缺少图标：{relative}")
        ProjectIcon.model_validate(project["icon"])
        if project.get("archived_at") is None:
            active.append(Path(relative).parts[1])
    layouts = list(context.paths("home.json"))
    if not layouts:
        raise ValueError("缺少主屏排列 home.json")
    check_membership(HomeLayout.model_validate(context.read(layouts[0])), active)


MIGRATION = Migration(
    source=21,
    target=22,
    scopes=("shared",),
    summary="为项目记录写入图标，并按最近活动生成主屏排列 home.json",
    contract="miniclaw2/store/v22:project-home-v1",
    upgrade=upgrade,
    verify=verify,
)
