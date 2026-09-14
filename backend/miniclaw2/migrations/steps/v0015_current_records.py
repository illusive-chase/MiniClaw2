from __future__ import annotations

from typing import Any

from miniclaw2.migrations.sdk import Migration, MigrationContext


def event_v2(record: dict[str, Any]) -> dict[str, Any]:
    version = record.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("不支持的历史事件版本")
    updated = dict(record)
    event = dict(record["event"])
    if version == 1 and event.get("type") == "interaction_request" and event.get("interaction_type") == "checkpoint_review":
        event["interaction_type"] = "human_review_prose"
        event["tool_name"] = "human_review_prose"
    updated.update(schema_version=2, event=event)
    return updated


def upgrade(context: MigrationContext) -> None:
    if context.scope == "local":
        for relative, payload in context.input_records("projects/*/project.json"):
            if payload.get("machine_id") != context.machine_id:
                continue
            root_path = payload.get("root_path")
            local = relative.removesuffix("project.json") + f"hosts/{context.machine_id}/local.json"
            if isinstance(root_path, str) and root_path and not context.path(local).exists():
                context.replace(local, {"root_path": root_path})
        for relative in context.paths("projects/*/hosts/*/local.json"):
            payload = context.read(relative)
            if not isinstance(payload.get("root_path"), str):
                raise ValueError(f"{relative} 缺少本机路径，不能从远端推断")
        return
    for relative in context.paths("projects/*/project.json"):
        payload = context.read(relative)
        project_dir = relative.removesuffix("project.json")
        flat_files = [source for source in context.paths("**") if source.startswith(project_dir + "nodes/")]
        if "root_path" in payload or flat_files:
            owner = payload.get("machine_id")
            if not isinstance(owner, str) or not owner:
                raise ValueError(f"{relative} 缺少历史节点归属，不能猜测")
            host_dir = project_dir + f"hosts/{owner}/"
            for source in flat_files:
                context.move(source, host_dir + source.removeprefix(project_dir))
            for source in list(context.paths(project_dir + "git_aliases.json")):
                context.move(source, host_dir + "git_aliases.json")
            if not context.path(host_dir + "host.json").exists():
                context.replace(host_dir + "host.json", {"label": payload.get("machine_label", owner), "bound_at": payload["created_at"], "repo": {}})
            if not context.path(host_dir + "layout.json").exists():
                context.replace(host_dir + "layout.json", {"layout_hints": payload.get("layout_hints", {}), "layout_viewport": payload.get("layout_viewport")})
            for key in ("root_path", "layout_hints", "layout_viewport"):
                payload.pop(key, None)
        for key in ("active_planspace_id", "planspace_selection_explicit", "sharing", "identity"):
            payload.pop(key, None)
        context.replace(relative, payload)
    for relative in context.paths("projects/*/hosts/*/host.json"):
        payload = context.read(relative)
        payload.pop("identity", None)
        payload.pop("attestation", None)
        context.replace(relative, payload)
    for relative in context.paths("projects/*/hosts/*/nodes/*/node.json"):
        payload = context.read(relative)
        payload.pop("promoted_from", None)
        context.replace(relative, payload)
    for relative in context.paths("projects/*/hosts/*/claims/*"):
        context.delete(relative)
    for relative in context.paths("projects/*/hosts/*/nodes/*/events.jsonl"):
        context.transform_jsonl(relative, event_v2)
    prefix = "" if context.scope == "external_context" else "contextspace/"
    for relative in context.paths(prefix + "bindings/projects/*.yaml"):
        payload = context.read(relative)
        project = payload.get("project")
        if isinstance(project, dict):
            project.pop("local_paths", None)
        context.replace(relative, payload)
    for relative in context.paths(prefix + "contextspace.yaml"):
        payload = context.read(relative)
        payload.pop("git", None)
        context.replace(relative, payload)
    for relative in context.paths("config.json"):
        payload = context.read(relative)
        payload.pop("updates", None)
        if "code_review" not in payload:
            active = {preset["id"] for preset in payload["model_presets"] if preset.get("status", "active") == "active"}
            payload["code_review"] = {"model_preset_id": "gpt-5.6" if "gpt-5.6" in active else payload["defaults"]["default_model_preset_id"]}
        context.replace(relative, payload)


def verify(context: MigrationContext) -> None:
    for relative in context.paths("projects/*/project.json"):
        if any(key in context.read(relative) for key in ("active_planspace_id", "planspace_selection_explicit", "sharing", "identity")):
            raise ValueError(f"{relative} 仍包含退休字段")


MIGRATION = Migration(
    source=14,
    target=15,
    scopes=("shared", "local", "external_context"),
    summary="一次性收敛 v14 项目与配置字段、事件载体，建立独立本机完成凭据",
    contract="miniclaw2/store/v15:host-partitions;strict-project-config;event-v2;domain-receipts-v1",
    upgrade=upgrade,
    verify=verify,
)
