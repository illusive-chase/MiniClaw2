"""Frozen v16 input transformation used to exercise retained v17 recovery.

Not a production migration: the released store no longer accepts v15.
"""
from __future__ import annotations

from miniclaw2.migrations.sdk import Migration, MigrationContext
from miniclaw2.restore_artifact_layout import coordinate_space


def upgrade(context: MigrationContext) -> None:
    for project_file in context.paths("projects/*/project.json"):
        directory = project_file.removesuffix("project.json")
        nodes = {context.read(path)["id"]: context.read(path)
                 for path in context.paths(directory + "hosts/*/nodes/*/node.json")}
        owners = {context.read(path)["id"]: path.split("/")[3]
                  for path in context.paths(directory + "hosts/*/nodes/*/node.json")}
        for relative in context.paths(directory + "hosts/*/layout.json"):
            hints = context.read(relative).get("layout_hints", {})
            owner = relative.split("/")[3]
            positions = {key: {**value, "space": coordinate_space(nodes[key], nodes)}
                         for key, value in hints.items() if owners.get(key) == owner}
            context.replace(relative.removesuffix("layout.json") + "node-layout.json",
                            {"schema_version": 1, "nodes": positions})
            context.delete(relative)


MIGRATION = Migration(16, 17, ("shared",), "冻结的旧布局测试输入", "miniclaw2/store/v16:owner-node-layout-v1;explicit-coordinate-space;browser-viewport", upgrade, lambda context: None, True)
