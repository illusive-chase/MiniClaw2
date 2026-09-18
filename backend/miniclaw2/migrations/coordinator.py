from __future__ import annotations

import os
import threading
import weakref
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator

from .catalog import CURRENT_VERSION, MINIMUM_VERSION, marker, steps, version_of
from .errors import MigrationError
from .inventory import LOCAL_DIRECTORY, context_root, files, safe_path
from .repairs import repaired_contracts
from .sdk import MigrationContext
from .transaction import Transaction, atomic_json, durable_mkdir, hydrated_backup, recover
from .validation import read_object, validate

_COORDINATORS: weakref.WeakValueDictionary[Path, StorageCoordinator] = weakref.WeakValueDictionary()
_LOCK = threading.RLock()


class StorageCoordinator:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.mutex = threading.RLock()
        self.pid = os.getpid()
        self.ready = False
        self.maintaining = False
        self._context: StorageCoordinator | None = None
        path = safe_path(root, LOCAL_DIRECTORY + "/lock")
        durable_mkdir(path.parent)
        self._lock_file = path.open("a+b")
        self.root_identity = (root.stat().st_dev, root.stat().st_ino)
        try:
            if os.name == "nt":
                import msvcrt

                self._lock_file.write(b"\0")
                self._lock_file.flush()
                self._lock_file.seek(0)
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_file.close()
            raise MigrationError("waiting_for_idle", "此数据根仍由另一个进程持有，请先停止该进程后重试", root) from exc

    def assert_current(self) -> None:
        if self.maintaining:
            return
        if not self.root.exists() or (self.root.stat().st_dev, self.root.stat().st_ino) != self.root_identity:
            raise MigrationError("migration_failed", "运行中的数据根已被替换，请重启并重新准入", self.root)
        if (self.root / LOCAL_DIRECTORY / "pending.json").exists():
            raise MigrationError("migration_failed", "迁移事务尚未完成，请先执行 migrations recover", self.root)
        path = self.root / "schema.json"
        if not path.is_file() or version_of(read_object(path), path) != CURRENT_VERSION:
            raise MigrationError("migration_required", "存储尚未完成当前格式迁移", path)

    @contextmanager
    def access(self) -> Iterator[None]:
        with self.mutex:
            context_mutex = self._context.mutex if self._context else threading.RLock()
            with context_mutex:
                self.assert_current()
                yield

    def roots(self) -> list[Path]:
        context = context_root(self.root)
        if context == self.root / "contextspace":
            self._context = None
            return [self.root]
        if self.root.is_relative_to(context) or (context.is_relative_to(self.root) and context.relative_to(self.root).parts[0] in {"projects", "contextspace", ".git", LOCAL_DIRECTORY, "migration-backups"}):
            raise MigrationError("migration_failed", "外部 ContextSpace 不能与受管数据路径重叠", context)
        self._context = coordinator(context)
        return [self.root, context]

    def source_version(self) -> int:
        path = self.root / "schema.json"
        if not path.exists():
            if files(self.root):
                raise MigrationError("migration_failed", "非空存储缺少 schema.json；请先识别基线，不能自动当作新库", path)
            return CURRENT_VERSION
        return version_of(read_object(path), path)

    def receipt_version(self, root: Path, machine_id: str, *, external: bool = False) -> int:
        path = root / LOCAL_DIRECTORY / "state.json"
        if path.exists():
            receipt = read_object(path)
            version = version_of(receipt, path)
            identity = {"device": root.stat().st_dev, "inode": root.stat().st_ino}
            if receipt.get("machine_id") == machine_id and receipt.get("root_identity") == identity:
                return version
        scope = "external_context" if external else "local"
        relevant = [
            relative for relative, domain in files(root, external=external).items()
            if domain == scope and (
                external or not Path(relative).match("projects/*/hosts/*/local.json")
                or Path(relative).parts[3] == machine_id
            )
        ]
        if relevant:
            from .catalog import MINIMUM_VERSION, steps

            if MINIMUM_VERSION > 14:
                if any(
                    scope in migration.scopes
                    for migration in steps(MINIMUM_VERSION)
                ):
                    raise MigrationError(
                        "schema_too_old",
                        "本机数据缺少可信完成凭据，需通过中间版本识别基线",
                        path,
                    )
                # This data domain has not changed anywhere in the retained
                # chain. Structural validation still runs before publication,
                # so a copied current tree can receive a fresh host receipt
                # without pretending that an unknown old format was migrated.
                return CURRENT_VERSION
            return 14
        return CURRENT_VERSION

    def receipt(self, root: Path, machine_id: str) -> dict[str, Any]:
        return {**marker(), "machine_id": machine_id,
                "root_identity": {"device": root.stat().st_dev, "inode": root.stat().st_ino}}

    def apply(self, machine_id: str, *, accept_data_loss: bool = False) -> None:
        with self.mutex:
            roots = self.roots()
            context_mutex = self._context.mutex if self._context else threading.RLock()
            with context_mutex:
                self.maintaining = True
                try:
                    recover(self.root, roots)
                    shared = self.source_version()
                    local = self.receipt_version(self.root, machine_id)
                    if not (self.root / LOCAL_DIRECTORY / "state.json").exists():
                        local = min(local, shared)
                    external = self.receipt_version(roots[1], machine_id, external=True) if len(roots) > 1 else CURRENT_VERSION
                    sources = {"shared": shared, "local": local, "external_context": external}
                    has_receipts = all((root / LOCAL_DIRECTORY / "state.json").exists() for root in roots)
                    receipt_path = self.root / LOCAL_DIRECTORY / "state.json"
                    old_receipt = read_object(receipt_path) if receipt_path.exists() else {}
                    identity = {"device": self.root.stat().st_dev, "inode": self.root.stat().st_ino}
                    receipt_matches = old_receipt.get("machine_id") == machine_id and old_receipt.get("root_identity") == identity
                    accepted = set(old_receipt.get("accepted_migration_contracts", [])) if receipt_matches else set()
                    if accept_data_loss:
                        accepted.update(migration.contract for migration in steps(MINIMUM_VERSION) if migration.destructive)
                    if all(version == CURRENT_VERSION for version in sources.values()) and has_receipts and receipt_matches and not accept_data_loss:
                        self.ready = True
                        return
                    owner_path = self.root / ".runtime-owner.json"
                    if owner_path.is_file():
                        from ..registry import _process_matches_owner

                        owner = read_object(owner_path)
                        owner_pid = owner.get("pid")
                        if isinstance(owner_pid, int) and owner_pid != os.getpid() and _process_matches_owner(owner_pid, owner.get("process_start")):
                            raise MigrationError("waiting_for_idle", "旧运行进程仍在使用此存储，请停止它后再迁移", owner_path)
                    transaction = Transaction(self.root, roots)
                    transaction.journal["accept_data_loss"] = accept_data_loss
                    transaction.journal["accepted_migration_contracts"] = sorted(accepted)
                    # One hydration per root index, and none at all for a
                    # scope with nothing to run. Both shared and local read
                    # index 0, so hydrating per scope would build the same
                    # overlay twice and — under ExitStack — hold both trees
                    # at once, which the preflight's three-times-size budget
                    # does not cover. `apply --accept-data-loss` on an
                    # already-current store has no steps at all and so needs
                    # no snapshot.
                    with ExitStack() as pristine:
                        snapshots: dict[int, Path] = {}
                        for scope, source in sources.items():
                            if scope == "external_context" and len(roots) == 1:
                                continue
                            applicable = [migration for migration in steps(source) if scope in migration.scopes]
                            if not applicable:
                                continue
                            index = 1 if scope == "external_context" else 0
                            if index not in snapshots:
                                snapshots[index] = pristine.enter_context(
                                    hydrated_backup(self.root, transaction.journal, index))
                            context = MigrationContext(transaction.stage(index), scope, machine_id, snapshots[index])
                            for migration in applicable:
                                if (migration.destructive and migration.contract not in accepted
                                        and migration.contract not in repaired_contracts(applicable, context)):
                                    raise MigrationError("migration_required", f"{migration.summary}；请查看 migrations plan 后用 apply --accept-data-loss 确认")
                                migration.upgrade(context)
                                migration.verify(context)
                    for index, root in enumerate(roots):
                        stage = transaction.stage(index)
                        if index == 0:
                            from ..git_layout import discard_transient_git_positions

                            discard_transient_git_positions(stage)
                        validate(stage, external=index > 0)
                        receipt = self.receipt(root, machine_id)
                        if index == 0:
                            receipt["accepted_migration_contracts"] = sorted(accepted)
                        atomic_json(stage / LOCAL_DIRECTORY / "state.json", receipt)
                    atomic_json(transaction.stage(0) / "schema.json", marker())
                    transaction.decide()
                    transaction.publish()
                    self.ready = True
                except MigrationError:
                    raise
                except Exception as exc:
                    raise MigrationError("migration_failed", str(exc), self.root) from exc
                finally:
                    self.maintaining = False


def coordinator(root: Path) -> StorageCoordinator:
    root = root.expanduser().resolve()
    with _LOCK:
        existing = _COORDINATORS.get(root)
        if existing is None or existing.pid != os.getpid():
            existing = StorageCoordinator(root)
            _COORDINATORS[root] = existing
        return existing


def open_storage(root: Path) -> StorageCoordinator:
    from ..sync import ensure_machine_identity

    storage = coordinator(root)
    with storage.mutex:
        configured_context = context_root(storage.root)
        opened_context = storage._context.root if storage._context else storage.root / "contextspace"
        if configured_context != opened_context:
            storage.ready = False
        if storage.ready and not (storage.root / LOCAL_DIRECTORY / "pending.json").exists():
            storage.assert_current()
        else:
            recover(storage.root, storage.roots())
            storage.source_version()
            identity = ensure_machine_identity(storage.root)
            storage.apply(identity.id)
    return storage
