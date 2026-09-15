from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Iterator
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


def _git_output(root: Path, *arguments: str) -> bytes | None:
    if not (root / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return None if result.returncode else result.stdout


def head_blobs(root: Path) -> tuple[str, dict[str, tuple[int, str]]]:
    """The commit at HEAD and the (mode, blob) pair for each path in it.

    Returns no commit when the store keeps no Git history, which is the case
    until metadata sync is configured. Callers must then treat every file as
    unavailable from Git and copy it instead.
    """
    commit = _git_output(root, "rev-parse", "HEAD")
    output = _git_output(root, "ls-tree", "-r", "-z", "HEAD")
    if not commit or not output:
        return "", {}
    entries: dict[str, tuple[int, str]] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        header, _, path = record.partition(b"\t")
        fields = header.split(b" ")
        if len(fields) != 3 or fields[1] != b"blob":
            continue
        entries[path.decode()] = (int(fields[0], 8) & 0o777, fields[2].decode())
    return commit.decode().strip(), entries


def pin_backup_commit(root: Path, identifier: str, commit: str) -> bool:
    """Anchor the commit a backup references so Git cannot garbage-collect it.

    Without a ref of its own, a referenced blob is only as durable as the
    branch that happened to contain it: a later rewrite or prune could drop
    the very bytes the backup promises. The ref makes the backup outlive
    history edits, and is removed when the backup itself is removed.
    """
    return _git_output(root, "update-ref", f"refs/miniclaw2/migration-backups/{identifier}", commit) is not None


def blob_names(root: Path, paths: list[Path]) -> dict[Path, str]:
    """Name each file by the hash of its raw bytes, as Git stores them.

    `--no-filters` is what makes the answer usable as a byte-identity test.
    Without it, a clean filter or newline normalization is applied first, so
    a CRLF working file under `core.autocrlf=true` hashes to the committed
    LF blob — and a backup that recorded that reference would hand back
    bytes the store never had. Filtered files simply fail to match here and
    are copied instead.
    """
    names: dict[Path, str] = {}
    for start in range(0, len(paths), 400):
        batch = paths[start:start + 400]
        output = _git_output(root, "hash-object", "--no-filters", "--", *[str(path) for path in batch])
        if output is None:
            return {}
        hashes = output.decode().split()
        if len(hashes) != len(batch):
            return {}
        names.update(zip(batch, hashes))
    return names


@contextmanager
def blob_stream(root: Path, blob: str) -> Iterator[IO[bytes]]:
    """One Git object, open for reading rather than buffered whole.

    Backups reference blobs of any size — transcripts and event logs among
    them — so every read of one is incremental. Consumers read to EOF: the
    exit status is what catches an object that turns out to be truncated,
    and abandoning the stream early is indistinguishable from that.
    """
    def lost() -> MigrationError:
        return MigrationError("migration_failed", f"Git 不再持有备份引用的对象 {blob}", root)

    if not (root / ".git").exists():
        raise lost()
    process = subprocess.Popen(
        ["git", "-C", str(root), "cat-file", "blob", blob], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if process.stdout is None:
        process.wait()
        raise lost()
    try:
        yield process.stdout
    finally:
        process.stdout.close()
        process.wait()
    if process.returncode:
        raise lost()


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
            "inputs": [], "changes": [], "backup_refs": [],
        }
        self.save()
        atomic_json(root / LOCAL_DIRECTORY / "pending.json", {"id": self.identifier})
        for index, source in enumerate(roots):
            inputs = snapshot(source, external=index > 0)
            self.journal["inputs"].append(inputs)
            present = [relative for relative, checksum in inputs.items() if checksum is not None]
            # The store is itself a Git repository, so any file already committed
            # at HEAD with identical bytes needs no second copy on disk: the
            # journal records the blob and restore reads it back from Git. Only
            # what Git does not hold (uncommitted edits, gitignored per-host
            # files, or a store with no history yet) is copied. The commit is
            # pinned first, so a reference is never recorded against bytes that
            # a later history rewrite could collect.
            commit, committed = head_blobs(source) if index == 0 else ("", {})
            if commit and not pin_backup_commit(source, self.identifier, commit):
                commit, committed = "", {}
            names = blob_names(source, [safe_path(source, relative) for relative in present]) if committed else {}
            references: dict[str, str] = {}
            for relative in present:
                original = safe_path(source, relative)
                entry = committed.get(relative)
                if entry is not None and names.get(original) == entry[1] and original.stat().st_mode & 0o777 == entry[0]:
                    references[relative] = entry[1]
                else:
                    durable_copy(original, self.backup / str(index) / relative)
                durable_copy(original, self.stage(index) / relative)
            self.journal["backup_refs"].append(references)
            if commit:
                self.journal["backup_commit"] = commit
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

    def discard_stage(self) -> None:
        """Drop the staged tree once it has been published.

        Nothing reads a stage after `publish` — the backup is the audit
        record and the live store is the result — so keeping it only
        doubles the migration's disk cost.
        """
        stage = self.directory / "stage"
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
            fsync_directory(self.directory)

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
            if change["before"] is not None and backup_digest(
                self.root, self.journal, index, relative, identifier=self.identifier,
            ) != change["before"]:
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
        self.journal["stage_discarded"] = True
        self.save()
        self.discard_stage()

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


def _reference(journal: dict[str, Any], index: int, relative: str) -> str | None:
    references = journal.get("backup_refs") or []
    return references[index].get(relative) if index < len(references) else None


def backup_origin(
    root: Path, journal: dict[str, Any], index: int, relative: str, *, identifier: str | None = None,
) -> str:
    """Where one backup entry's bytes live, for error messages."""
    blob = _reference(journal, index, relative)
    if blob is None:
        return str(safe_path(root / "migration-backups", f"{identifier or journal['id']}/{index}/{relative}"))
    return f"git:{blob} ({relative})"


@contextmanager
def backup_stream(
    root: Path, journal: dict[str, Any], index: int, relative: str, *, identifier: str | None = None,
) -> Iterator[tuple[IO[bytes], str, str]]:
    """One backup entry open for incremental reading.

    A backup entry is either a copied file or a reference to the Git blob
    that already held those exact bytes at the pre-migration commit. Both
    are addressed by path here, so callers need not know which they got.
    Yielded alongside the stream are the permission prefix the entry's
    digest carries and an origin label for error messages.
    """
    blob = _reference(journal, index, relative)
    origin = backup_origin(root, journal, index, relative, identifier=identifier)
    if blob is None:
        path = Path(origin)
        if not path.is_file():
            raise MigrationError("migration_failed", "迁移备份缺失", path)
        with path.open("rb") as stream:
            yield stream, f"{path.stat().st_mode & 0o777:03o}", origin
        return
    recorded = (journal["inputs"][index] or {}).get(relative) or ""
    with blob_stream(root, blob) as stream:
        yield stream, recorded.split(":")[0], origin


def backup_digest(
    root: Path, journal: dict[str, Any], index: int, relative: str, *, identifier: str | None = None,
) -> str:
    """Checksum one backup entry without holding it in memory."""
    checksum = hashlib.sha256()
    with backup_stream(root, journal, index, relative, identifier=identifier) as (stream, mode, _origin):
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return f"{mode}:{checksum.hexdigest()}"


def backup_extract(
    root: Path, journal: dict[str, Any], index: int, relative: str, target: Path,
    *, identifier: str | None = None,
) -> str:
    """Write one backup entry to `target`, and return the digest of what was written.

    Copying and checksumming share the one pass, so restoring a store costs
    a buffer rather than its largest transcript.
    """
    checksum = hashlib.sha256()
    target.parent.mkdir(parents=True, exist_ok=True)
    with backup_stream(root, journal, index, relative, identifier=identifier) as (stream, mode, _origin):
        with target.open("wb") as output:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(block)
                output.write(block)
    return f"{mode}:{checksum.hexdigest()}"


@dataclass(frozen=True)
class BackupPayload:
    """One pre-migration metadata record, wherever its bytes actually live."""

    content: bytes
    digest: str
    origin: str

    @property
    def record(self) -> dict[str, Any]:
        payload = json.loads(self.content.decode("utf-8"))
        if not isinstance(payload, dict):
            raise MigrationError("migration_failed", f"备份记录不是对象：{self.origin}")
        return payload


def backup_payload(
    root: Path, journal: dict[str, Any], index: int, relative: str, *, identifier: str | None = None,
) -> BackupPayload:
    """Read one backup entry whole, for callers that parse it as a record.

    Only metadata records are read this way. Anything whose size is not
    bounded by its shape — a transcript, an event log — is checksummed with
    `backup_digest` or copied with `backup_extract` instead, so that neither
    verification nor restore scales with the largest file in the store.
    """
    with backup_stream(root, journal, index, relative, identifier=identifier) as (stream, mode, origin):
        content = stream.read()
    return BackupPayload(content, f"{mode}:{hashlib.sha256(content).hexdigest()}", origin)


@contextmanager
def hydrated_backup(
    root: Path, journal: dict[str, Any], index: int, *, identifier: str | None = None,
) -> Iterator[Path]:
    """Present a transaction's backup as a complete tree on disk.

    Migration steps and impact reports read the pre-migration snapshot by
    walking it, so every file has to exist. Referenced entries are written
    into a temporary overlay that lives only for the duration of the call —
    transient disk, unlike the permanent second copy it replaces.
    """
    identifier = identifier or journal["id"]
    backup = safe_path(root / "migration-backups", f"{identifier}/{index}")
    references = (journal.get("backup_refs") or [])[index:index + 1]
    if not references or not references[0]:
        yield backup
        return
    with tempfile.TemporaryDirectory(prefix="migration-pristine-") as temporary:
        overlay = Path(temporary)
        for relative, checksum in (journal["inputs"][index] or {}).items():
            if checksum is None:
                continue
            target = safe_path(overlay, relative)
            digest = backup_extract(root, journal, index, relative, target, identifier=identifier)
            if digest != checksum:
                origin = backup_origin(root, journal, index, relative, identifier=identifier)
                raise MigrationError("migration_failed", f"备份摘要不一致：{origin}", root)
            target.chmod(int(checksum.split(":")[0], 8))
        yield overlay


def prune_transactions(root: Path, *, keep: int = 2, protected: set[str] | None = None) -> dict[str, Any]:
    """Reclaim finished transactions beyond the most recent `keep`.

    A finished transaction's stage is dead weight in every case, so every
    finished stage goes. Its backup is the audit record for one upgrade:
    the newest few are kept, and so is any backup named in `protected` —
    recency is not the only thing that makes a backup load-bearing, since
    an older one can be the only remaining source of pre-migration
    coordinates. An unfinished transaction is never touched: `recover`
    still needs both its stage and its backup.
    """
    protected = protected or set()
    journals = sorted((root / LOCAL_DIRECTORY / "transactions").glob("*/journal.json"),
                      key=lambda path: path.stat().st_mtime)
    finished = [path for path in journals if read_object(path).get("phase") in {"ready", "aborted"}]
    removed: list[str] = []
    kept_protected: list[str] = []
    reclaimed = 0
    candidates = finished[:max(0, len(finished) - keep)]
    for path in candidates:
        identifier = path.parent.name
        if identifier in protected:
            kept_protected.append(identifier)
            continue
        for directory in (path.parent, safe_path(root / "migration-backups", identifier)):
            if directory.exists():
                reclaimed += sum(item.stat().st_size for item in directory.rglob("*") if item.is_file())
                shutil.rmtree(directory, ignore_errors=True)
        _git_output(root, "update-ref", "-d", f"refs/miniclaw2/migration-backups/{identifier}")
        removed.append(identifier)
    for path in journals:
        if path.exists() and read_object(path).get("phase") in {"ready", "aborted"}:
            stage = path.parent / "stage"
            if stage.exists():
                reclaimed += sum(item.stat().st_size for item in stage.rglob("*") if item.is_file())
                shutil.rmtree(stage, ignore_errors=True)
                fsync_directory(path.parent)
    return {"removed": removed, "kept": keep, "kept_protected": kept_protected,
            "reclaimed_bytes": reclaimed}


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
            transaction.journal["stage_discarded"] = True
            transaction.save()
            transaction.discard_stage()
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
