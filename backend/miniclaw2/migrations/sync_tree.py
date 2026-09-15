from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .catalog import marker, steps, version_of
from .errors import MigrationError
from .inventory import files, safe_path, scope_for
from .sdk import MigrationContext
from .transaction import Transaction, atomic_json, durable_copy
from .validation import read_object, validate


def git(root: Path, *arguments: str, input_text: str | None = None, index: Path | None = None, worktree: Path | None = None) -> str:
    environment = {**os.environ, "GIT_AUTHOR_NAME": "MiniClaw2", "GIT_COMMITTER_NAME": "MiniClaw2",
                   "GIT_AUTHOR_EMAIL": "miniclaw2@localhost", "GIT_COMMITTER_EMAIL": "miniclaw2@localhost",
                   "GIT_TERMINAL_PROMPT": "0"}
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    command = ["git", "-C", str(root)]
    if worktree is not None:
        command.extend(["--work-tree", str(worktree)])
    result = subprocess.run([*command, *arguments], input=input_text, capture_output=True, text=True, env=environment)
    if result.returncode:
        raise MigrationError("schema_conflict", (result.stderr or result.stdout).strip() or "Git 合并失败")
    return result.stdout.strip()


def extract(root: Path, revision: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as archive:
        result = subprocess.run(["git", "-C", str(root), "archive", revision], stdout=archive, stderr=subprocess.PIPE)
        if result.returncode:
            raise MigrationError("schema_conflict", result.stderr.decode(errors="replace"))
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r|") as stream:
            for member in stream:
                path = safe_path(destination, member.name)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise MigrationError("schema_conflict", "同步快照包含链接或特殊文件", path)
                if member.name != "schema.json" and scope_for(Path(member.name)) != "shared":
                    raise MigrationError("schema_conflict", "同步快照包含本机私有或非受管路径", path)
                source = stream.extractfile(member)
                if source is None:
                    raise MigrationError("schema_conflict", "无法读取同步快照", path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("wb") as output:
                    shutil.copyfileobj(source, output)
                path.chmod(member.mode & 0o777)


def normalize(root: Path, accepted: frozenset[str] = frozenset()) -> None:
    path = root / "schema.json"
    version = version_of(read_object(path), path)
    migrations = [migration for migration in steps(version) if "shared" in migration.scopes]
    for migration in migrations:
        if migration.destructive and migration.contract not in accepted:
            raise MigrationError("migration_required", f"同步需要确认：{migration.summary}；请运行 migrations apply --accept-data-loss")
    if migrations:
        with tempfile.TemporaryDirectory(prefix="migration-source-") as temporary:
            source = Path(temporary)
            for relative, scope in files(root).items():
                if scope == "shared":
                    durable_copy(root / relative, source / relative)
            context = MigrationContext(root, "shared", "", source)
            for migration in migrations:
                migration.upgrade(context)
                migration.verify(context)
    validate(root)
    atomic_json(path, marker())


def tree(root: Path, directory: Path, index: Path) -> str:
    git(root, "read-tree", "--empty", index=index)
    git(root, "add", "--all", "--force", ".", index=index, worktree=directory)
    return git(root, "write-tree", index=index)


def merge_remote(root: Path, remote_ref: str) -> bool:
    receipt = root / ".migration-local" / "state.json"
    accepted = frozenset(read_object(receipt).get("accepted_migration_contracts", [])) if receipt.exists() else frozenset()
    local_head = git(root, "rev-parse", "HEAD")
    remote_head = git(root, "rev-parse", remote_ref)
    with tempfile.TemporaryDirectory(prefix="sync-", dir=root / ".migration-local") as temporary:
        directory = Path(temporary)
        local, remote, base, result = (directory / name for name in ("local", "remote", "base", "result"))
        extract(root, remote_head, remote)
        normalize(remote, accepted)
        extract(root, local_head, local)
        local_files = files(local)
        normalize(local, accepted)
        if local_head == remote_head:
            return False
        ancestor_commit = git(root, "merge-base", local_head, remote_head)
        remote_tree = tree(root, remote, directory / "remote.index")
        if ancestor_commit == local_head:
            merged_tree = remote_tree
        elif ancestor_commit == remote_head:
            return False
        else:
            extract(root, ancestor_commit, base)
            normalize(base, accepted)
            from ..git_layout import check_git_layout_conflicts, check_lane_layout_conflicts

            check_git_layout_conflicts(base, local, remote)
            check_lane_layout_conflicts(base, local, remote)
            base_commit = git(root, "commit-tree", tree(root, base, directory / "base.index"), input_text="迁移规范化共同祖先\n")
            local_commit = git(root, "commit-tree", tree(root, local, directory / "local.index"), "-p", base_commit, input_text="迁移规范化本地\n")
            remote_commit = git(root, "commit-tree", remote_tree, "-p", base_commit, input_text="迁移规范化远端\n")
            merged_tree = git(root, "merge-tree", "--write-tree", local_commit, remote_commit).splitlines()[0]
        extract(root, merged_tree, result)
        validate(result)
        remote_original_tree = git(root, "rev-parse", f"{remote_head}^{{tree}}")
        target = remote_head if ancestor_commit == local_head and merged_tree == remote_original_tree else git(
            root, "commit-tree", merged_tree, "-p", local_head, "-p", remote_head, input_text="规范化并合并元数据\n",
        )
        result_files = files(result)
        for relative, scope in result_files.items():
            if scope != "shared" or relative in local_files:
                continue
            for ancestor in Path(relative).parents:
                ancestor_path = root / ancestor
                if ancestor.as_posix() not in local_files and ancestor_path.exists() and not ancestor_path.is_dir():
                    raise MigrationError("schema_conflict", "远端文件与本机未跟踪文件冲突", ancestor_path)
            destination = root / relative
            if destination.exists():
                if not destination.is_file() or destination.read_bytes() != (result / relative).read_bytes():
                    raise MigrationError("schema_conflict", "远端文件与本机未跟踪文件冲突", destination)
        transaction = Transaction(root, [root])
        stage = transaction.stage(0)
        for relative, scope in local_files.items():
            if scope == "shared" and relative not in result_files:
                (stage / relative).unlink(missing_ok=True)
        for relative, scope in result_files.items():
            if scope == "shared":
                durable_copy(result / relative, stage / relative)
        durable_copy(result / "schema.json", stage / "schema.json")
        validate(stage)
        transaction.journal["git"] = {"before": local_head, "after": target}
        transaction.journal["migration_inputs"] = {"local": local_head, "remote": remote_head, "base": ancestor_commit}
        transaction.journal["accepted_migration_contracts"] = sorted(accepted)
        transaction.decide()
        transaction.publish()
        return True
