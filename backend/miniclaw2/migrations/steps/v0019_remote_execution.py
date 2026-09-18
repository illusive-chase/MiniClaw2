from __future__ import annotations

from ..sdk import Migration, MigrationContext


def upgrade(context: MigrationContext) -> None:
    for relative in context.paths("projects/*/hosts/*/local.json"):
        binding = context.read(relative)
        remote = binding.get("remote")
        if isinstance(remote, dict):
            remote.setdefault("codex_remote_experimental", False)
            remote.setdefault("codex_path", "codex")
            remote.setdefault("sandbox", "workspaceWrite")
            context.replace(relative, binding)


def verify(context: MigrationContext) -> None:
    from ...domain import RemoteProjectBinding
    for relative in context.paths("projects/*/hosts/*/local.json"):
        binding = context.read(relative)
        if "remote" in binding:
            RemoteProjectBinding.model_validate(binding)


MIGRATION = Migration(
    source=18, target=19, scopes=("local",),
    summary="为本机远端接入配置增加默认关闭的 Codex 实验开关、执行器路径及沙箱策略",
    contract="miniclaw2/store/v19:remote-execution-v1",
    upgrade=upgrade, verify=verify,
)
