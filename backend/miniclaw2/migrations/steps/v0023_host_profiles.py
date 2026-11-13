from __future__ import annotations

from pathlib import Path

from ..sdk import Migration, MigrationContext


def upgrade(context: MigrationContext) -> None:
    # The new ``hosts/<machine-id>/profile.json`` directory starts empty and
    # each host writes its own profile at runtime. Creating one here would
    # make the shared chain host-dependent: sync normalizes remote and
    # ancestor snapshots with this same step, and every host must turn the
    # same input into the same output.
    return


def verify(context: MigrationContext) -> None:
    from ...host_profile import validate_profile_file

    for relative in context.paths("hosts/*/profile.json"):
        validate_profile_file(context.path(relative), Path(relative).parts[1])


MIGRATION = Migration(
    source=22,
    target=23,
    scopes=("shared",),
    summary="新增单写者的 host 资料目录 hosts/<machine-id>/profile.json（名称、图标、颜色）",
    contract="miniclaw2/store/v23:host-profiles-v1",
    upgrade=upgrade,
    verify=verify,
)
