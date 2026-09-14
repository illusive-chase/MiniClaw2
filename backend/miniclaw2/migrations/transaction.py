from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import MigrationError
from .inventory import LOCAL_DIRECTORY, files, safe_path
from .validation import read_object


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_mkdir(path: Path) -> None:
    if path.is_dir():
        return
    durable_mkdir(path.parent)
    path.mkdir(exist_ok=True)
    fsync_directory(path.parent)


def atomic_bytes(path: Path, data: bytes) -> None:
    durable_mkdir(path.parent)
    temporary = path.with_name(path.name + ".migration.tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    fsync_directory(path.parent)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())


def file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    import hashlib

    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return f"{path.stat().st_mode & 0o777:03o}:{checksum.hexdigest()}"


def durable_copy(source: Path, destination: Path) -> None:
    durable_mkdir(destination.parent)
    temporary = destination.with_name(destination.name + ".migration.tmp")
    with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream)
        temporary.chmod(source.stat().st_mode & 0o777)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    temporary.replace(destination)
    fsync_directory(destination.parent)


def snapshot(root: Path, *, external: bool = False) -> dict[str, str | None]:
    paths = set(files(root, external=external))
    paths.update({"schema.json", f"{LOCAL_DIRECTORY}/state.json"})
    return {relative: file_digest(safe_path(root, relative)) for relative in sorted(paths)}


class Transaction:
    def __init__(self, root: Path, roots: list[Path]) -> None:
        self.root = root
        self.identifier = uuid4().hex
        self.directory = root / LOCAL_DIRECTORY / "transactions" / self.identifier
        self.backup = safe_path(root, "migration-backups/" + self.identifier)
        if (root / LOCAL_DIRECTORY / "pending.json").exists():
            raise MigrationError("migration_failed", "存在未恢复事务，请先执行 migrations recover", root)
        total = sum((source / relative).stat().st_size for index, source in enumerate(roots) for relative in files(source, external=index > 0))
        if shutil.disk_usage(root).free < total * 3:
            raise MigrationError("migration_failed", "可用空间不足以保存备份、暂存和发布临时文件；不会删除已有备份", root)
        self.journal: dict[str, Any] = {
            "id": self.identifier, "phase": "prepare", "roots": [str(path) for path in roots],
            "inputs": [], "changes": [],
        }
        self.save()
        atomic_json(root / LOCAL_DIRECTORY / "pending.json", {"id": self.identifier})
        for index, source in enumerate(roots):
            inputs = snapshot(source, external=index > 0)
            self.journal["inputs"].append(inputs)
            for relative, checksum in inputs.items():
                if checksum is not None:
                    original = safe_path(source, relative)
                    durable_copy(original, self.backup / str(index) / relative)
                    durable_copy(original, self.stage(index) / relative)
            self.save()

    @classmethod
    def load(cls, root: Path, path: Path, roots: list[Path]) -> "Transaction":
        instance = cls.__new__(cls)
        instance.root = root
        instance.directory = path.parent
        instance.identifier = path.parent.name
        instance.backup = root / "migration-backups" / instance.identifier
        instance.journal = read_object(path)
        if instance.journal.get("roots") != [str(item) for item in roots]:
            raise MigrationError("migration_failed", "事务的数据根与当前配置不同，请恢复原配置后重试", path)
        return instance

    def stage(self, index: int) -> Path:
        path = self.directory / "stage" / str(index)
        durable_mkdir(path)
        return path

    def save(self) -> None:
        atomic_json(self.directory / "journal.json", self.journal)
        pending = self.root / LOCAL_DIRECTORY / "pending.json"
        if self.journal["phase"] in {"ready", "aborted"} and pending.exists():
            if read_object(pending).get("id") == self.identifier:
                pending.unlink()
                fsync_directory(pending.parent)

    def decide(self) -> None:
        self.check_git()
        changes = []
        for index, root_name in enumerate(self.journal["roots"]):
            root = Path(root_name)
            before = self.journal["inputs"][index]
            if snapshot(root, external=index > 0) != before:
                raise MigrationError("migration_failed", "迁移期间源数据发生外部修改，未发布任何转换", root)
            after = snapshot(self.stage(index), external=index > 0)
            for relative in sorted(set(before) | set(after)):
                old, new = before.get(relative), after.get(relative)
                if old == new:
                    continue
                if new is not None:
                    staged = self.stage(index) / relative
                    with staged.open("rb") as stream:
                        os.fsync(stream.fileno())
                    fsync_directory(staged.parent)
                changes.append({"root": index, "path": relative, "before": old, "after": new})
            for directory, _directories, _filenames in os.walk(self.stage(index), topdown=False):
                fsync_directory(Path(directory))
        changes.sort(key=lambda item: item["path"] in {"schema.json", f"{LOCAL_DIRECTORY}/state.json"})
        self.journal.update(phase="decided", changes=changes)
        self.save()

    def publish(self) -> None:
        if self.journal["phase"] != "decided":
            raise MigrationError("migration_failed", "事务尚未作出提交决定", self.directory)
        self.check_git()
        self.check_inputs()
        for change in self.journal["changes"]:
            index, relative = change["root"], change["path"]
            target = safe_path(Path(self.journal["roots"][index]), relative)
            current = file_digest(target)
            if current not in (change["before"], change["after"]):
                raise MigrationError("migration_failed", "发布目标被外部修改，请在隔离目录恢复备份", target)
            if change["before"] is not None and file_digest(self.backup / str(index) / relative) != change["before"]:
                raise MigrationError("migration_failed", "迁移备份摘要不一致", target)
            if change["after"] is not None:
                source = safe_path(self.stage(index), relative)
                if file_digest(source) != change["after"]:
                    raise MigrationError("migration_failed", "迁移暂存摘要不一致", source)
                if current != change["after"]:
                    durable_copy(source, target)
            elif target.exists():
                target.unlink()
                fsync_directory(target.parent)
        git_state = self.journal.get("git")
        for index, root_name in enumerate(self.journal["roots"]):
            if snapshot(Path(root_name), external=index > 0) != snapshot(self.stage(index), external=index > 0):
                raise MigrationError("migration_failed", "发布后数据与已验证暂存不一致", Path(root_name))
        if git_state:
            from .sync_tree import git

            current = git(self.root, "rev-parse", "HEAD")
            if current == git_state["before"]:
                git(self.root, "update-ref", "HEAD", git_state["after"], git_state["before"])
            elif current != git_state["after"]:
                raise MigrationError("migration_failed", "发布期间 Git HEAD 被外部修改", self.root)
            git(self.root, "read-tree", git_state["after"])
        self.journal["phase"] = "ready"
        self.save()

    def check_git(self) -> None:
        state = self.journal.get("git")
        if state:
            from .sync_tree import git

            if git(self.root, "rev-parse", "HEAD") not in (state["before"], state["after"]):
                raise MigrationError("migration_failed", "Git HEAD 不再匹配事务输入", self.root)

    def check_inputs(self) -> None:
        for index, root_name in enumerate(self.journal["roots"]):
            before = self.journal["inputs"][index]
            changes = {change["path"]: change["after"] for change in self.journal["changes"] if change["root"] == index}
            actual = snapshot(Path(root_name), external=index > 0)
            if set(actual) - set(before) - set(changes):
                raise MigrationError("migration_failed", "发布期间出现未经验证的新文件", Path(root_name))
            for relative in set(before) | set(changes):
                allowed = (before.get(relative), changes.get(relative, before.get(relative)))
                if actual.get(relative) not in allowed:
                    raise MigrationError("migration_failed", "发布输入被外部修改", Path(root_name) / relative)


def recover(root: Path, roots: list[Path]) -> None:
    for path in sorted((root / LOCAL_DIRECTORY / "transactions").glob("*/journal.json")):
        if read_object(path).get("phase") in {"ready", "aborted"}:
            continue
        transaction = Transaction.load(root, path, roots)
        phase = transaction.journal["phase"]
        if phase == "decided":
            transaction.publish()
        elif phase == "prepare":
            transaction.journal["phase"] = "aborted"
            transaction.save()
        elif phase not in {"ready", "aborted"}:
            raise MigrationError("migration_failed", "未知事务阶段", path)
    pending = root / LOCAL_DIRECTORY / "pending.json"
    if pending.exists():
        identifier = read_object(pending).get("id")
        journal = safe_path(root / LOCAL_DIRECTORY / "transactions", f"{identifier}/journal.json")
        if read_object(journal).get("phase") not in {"ready", "aborted"}:
            raise MigrationError("migration_failed", "未找到可恢复的完整事务日志", pending)
        pending.unlink()
        fsync_directory(pending.parent)
