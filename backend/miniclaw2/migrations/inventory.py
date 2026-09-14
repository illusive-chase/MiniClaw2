from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from .errors import MigrationError

Scope = Literal["shared", "local", "external_context"]
LOCAL_DIRECTORY = ".migration-local"
EXCLUDED_ROOTS = {
    ".git", LOCAL_DIRECTORY, "migration-backups", "machine.json", "machine.lock",
    ".runtime-owner.json", ".update-exit-pending", "workspaces",
}
IGNORED_PATTERNS = [
    "machine.json", "machine.lock", "migration-backups/", ".migration-local/",
    ".update-exit-pending", ".runtime-owner.json", "*.tmp",
    "projects/*/hosts/*/local.json", "workspaces/",
]


def context_root(root: Path) -> Path:
    override = os.environ.get("MINICLAW_CONTEXT_HOME")
    return Path(override).expanduser().resolve() if override else root / "contextspace"


def scope_for(relative: Path, *, external: bool = False) -> Scope | None:
    if not relative.parts or relative.parts[0] in EXCLUDED_ROOTS:
        return None
    if relative.suffix == ".tmp" or relative.as_posix() == "schema.json":
        return None
    if external:
        return "external_context"
    if relative.match("projects/*/hosts/*/local.json"):
        return "local"
    if relative.parts[0] in {"projects", "contextspace"} or relative.name in {
        "config.json", "tags.json", ".gitignore",
    }:
        return "shared"
    return None


def files(root: Path, *, external: bool = False) -> dict[str, Scope]:
    result: dict[str, Scope] = {}
    if not root.exists():
        return result
    for directory, directories, filenames in os.walk(root, followlinks=False):
        parent = Path(directory)
        directories[:] = sorted(
            name for name in directories
            if name not in {".git", LOCAL_DIRECTORY, "migration-backups"}
            and not (parent == root and name in EXCLUDED_ROOTS)
        )
        for name in [*directories, *filenames]:
            path = parent / name
            if path.is_symlink():
                raise MigrationError("migration_failed", "受管数据不能包含符号链接", path)
        for name in sorted(filenames):
            if not (parent / name).is_file():
                raise MigrationError("migration_failed", "受管数据不能包含特殊文件", parent / name)
            relative = (parent / name).relative_to(root)
            scope = scope_for(relative, external=external)
            if scope is not None:
                result[relative.as_posix()] = scope
    return result


def safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise MigrationError("migration_failed", "迁移路径越界", path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise MigrationError("migration_failed", "迁移路径越界", path)
    ancestor = root
    for part in Path(relative).parts:
        ancestor = ancestor / part
        if ancestor.is_symlink():
            raise MigrationError("migration_failed", "迁移路径不能经过符号链接", ancestor)
    return path
