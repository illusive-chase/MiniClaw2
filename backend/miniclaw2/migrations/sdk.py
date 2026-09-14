from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import MigrationError
from .inventory import Scope, files, safe_path, scope_for


@dataclass(frozen=True)
class Migration:
    source: int
    target: int
    scopes: tuple[Scope, ...]
    summary: str
    contract: str
    upgrade: Callable[["MigrationContext"], None]
    verify: Callable[["MigrationContext"], None]
    destructive: bool = False


@dataclass
class MigrationContext:
    root: Path
    scope: Scope
    machine_id: str
    source_root: Path | None = None

    def input_records(self, pattern: str) -> Iterator[tuple[str, dict[str, Any]]]:
        root = self.source_root or self.root
        for relative in files(root):
            if Path(relative).match(pattern):
                payload = json.loads(safe_path(root, relative).read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise MigrationError("migration_failed", "输入记录必须是对象", root / relative)
                yield relative, payload

    def paths(self, pattern: str) -> Iterator[str]:
        for relative, scope in files(self.root, external=self.scope == "external_context").items():
            if scope == self.scope and Path(relative).match(pattern):
                yield relative

    def path(self, relative: str) -> Path:
        path = safe_path(self.root, relative)
        if scope_for(Path(relative), external=self.scope == "external_context") != self.scope:
            raise MigrationError("migration_failed", "迁移脚本不能跨数据域写入", path)
        return path

    def read(self, relative: str) -> dict[str, Any]:
        from .validation import read_object

        return read_object(self.path(relative))

    def replace(self, relative: str, payload: dict[str, Any]) -> None:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False) if path.suffix in {".yaml", ".yml"} else json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        path.write_text(text, encoding="utf-8")

    def delete(self, relative: str) -> None:
        self.path(relative).unlink()

    def move(self, source: str, target: str) -> None:
        target_path = self.path(target)
        if target_path.exists():
            raise MigrationError("migration_failed", "迁移目标已经存在", target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        self.path(source).replace(target_path)

    def transform_jsonl(self, relative: str, transform: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        path = self.path(relative)
        temporary = path.with_suffix(".tmp")
        changed = False
        with path.open(encoding="utf-8") as source, temporary.open("w", encoding="utf-8") as output:
            for number, line in enumerate(source, 1):
                if not line.endswith("\n"):
                    raise MigrationError("migration_failed", f"JSONL 第 {number} 行未完整落盘", path)
                record = json.loads(line)
                updated = transform(record)
                changed |= updated != record
                output.write(json.dumps(updated, ensure_ascii=False) + "\n")
        if changed:
            evidence = self.path(relative + ".original")
            if evidence.exists():
                raise MigrationError("migration_failed", "原始审计副本已存在，需人工核对", evidence)
            shutil.copyfile(path, evidence)
            temporary.replace(path)
        else:
            temporary.unlink()
