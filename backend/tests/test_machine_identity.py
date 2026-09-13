from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Barrier
from pathlib import Path
from typing import Any
from unittest.mock import patch

import miniclaw2.sync as sync_module
from miniclaw2.__main__ import main
from miniclaw2.domain import Project
from miniclaw2.store import Store, StoreReadOnlyError
from miniclaw2.sync import (
    MachineIdentity,
    MachineIdentityMismatchError,
    SyncManager,
    current_device_fingerprint,
    ensure_machine_identity,
    load_machine_identity,
    resolve_machine_copy,
    resolve_machine_rename,
)


def _initialize_in_process(root: str, barrier: Barrier, result: Queue) -> None:
    write_json = sync_module._write_json

    def delayed_write(path: Path, payload: dict[str, Any]) -> None:
        time.sleep(0.05)
        write_json(path, payload)

    with (
        patch.object(sync_module, "current_hostname", return_value="current-host"),
        patch.object(sync_module, "current_device_fingerprint", return_value="device-a"),
        patch.object(sync_module, "_write_json", side_effect=delayed_write),
    ):
        barrier.wait(timeout=15)
        result.put(ensure_machine_identity(Path(root)).payload())


def _crash_before_replace(root: str) -> None:
    def interrupted_write(path: Path, payload: dict[str, Any]) -> None:
        path.with_suffix(".json.tmp").write_text('{"id":', encoding="utf-8")
        os._exit(17)

    with (
        patch.object(sync_module, "current_hostname", return_value="new-host"),
        patch.object(sync_module, "current_device_fingerprint", return_value="device-a"),
        patch.object(sync_module, "_write_json", side_effect=interrupted_write),
    ):
        ensure_machine_identity(Path(root))


class MachineIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "store"
        self.root.mkdir()
        self.hostname = self.enterContext(
            patch.object(sync_module, "current_hostname", return_value="old-host")
        )
        self.fingerprint = self.enterContext(
            patch.object(sync_module, "current_device_fingerprint", return_value="device-a")
        )

    def save(self, identity: MachineIdentity) -> None:
        (self.root / "machine.json").write_text(json.dumps(identity.payload()), encoding="utf-8")

    def seed(self, **changes: Any) -> MachineIdentity:
        identity = replace(
            MachineIdentity(
                id="original-id",
                hostname="old-host",
                label="old-host",
                last_sync_at=123.0,
                last_synced_commit="checkpoint",
                sync_pending=True,
                device_fingerprint="device-a",
            ),
            **changes,
        )
        self.save(identity)
        return identity

    def test_rename_preserves_identity_checkpoint_and_custom_label(self) -> None:
        for label, expected in (("old-host", "new-host"), ("开发机", "开发机")):
            with self.subTest(label=label):
                original = self.seed(label=label)
                self.hostname.return_value = "new-host"
                updated = ensure_machine_identity(self.root)
                self.assertEqual(updated, replace(original, hostname="new-host", label=expected))
                self.assertEqual(ensure_machine_identity(self.root), updated)

    def test_rename_repairs_only_owned_project_and_host_labels(self) -> None:
        self.seed()
        for project_id, owner in (("owned", "original-id"), ("foreign", "other-id")):
            project_dir = self.root / "projects" / project_id
            host_dir = project_dir / "hosts" / "original-id"
            host_dir.mkdir(parents=True)
            (project_dir / "project.json").write_text(json.dumps({
                "machine_id": owner, "machine_label": "old-host", "name": project_id,
            }))
            (host_dir / "host.json").write_text(json.dumps({"label": "old-host", "bound_at": 123}))
        self.hostname.return_value = "new-host"
        with patch.object(sync_module, "_update_owned_project_labels", side_effect=OSError):
            with self.assertRaises(OSError):
                ensure_machine_identity(self.root)
        ensure_machine_identity(self.root)
        for project_id, expected in (("owned", "new-host"), ("foreign", "old-host")):
            project_dir = self.root / "projects" / project_id
            self.assertEqual(json.loads((project_dir / "project.json").read_text())["machine_label"], expected)
            host_file = project_dir / "hosts" / "original-id" / "host.json"
            self.assertEqual(json.loads(host_file.read_text()), {"label": "new-host", "bound_at": 123})
        with patch.object(sync_module, "_write_json") as write_json:
            ensure_machine_identity(self.root)
            write_json.assert_not_called()

    def test_copied_store_gets_new_identity_even_with_same_hostname(self) -> None:
        for hostname in ("old-host", "other-host"):
            with self.subTest(hostname=hostname):
                original = self.seed()
                self.hostname.return_value = hostname
                self.fingerprint.return_value = "device-b"
                updated = ensure_machine_identity(self.root)
                self.assertNotEqual(updated.id, original.id)
                self.assertEqual(updated.device_fingerprint, "device-b")
                self.assertEqual(updated.hostname, hostname)
                self.assertEqual(updated.label, hostname)
                self.assertIsNone(updated.last_sync_at)
                self.assertIsNone(updated.last_synced_commit)
                self.assertFalse(updated.sync_pending)
                self.assertEqual(ensure_machine_identity(self.root), updated)

    def test_copy_does_not_inherit_binding_or_mutate_source_partition(self) -> None:
        source = Store(self.root)
        project = source.create_project(Project(root_path="/old/checkout", name="项目"))
        host_dir = self.root / "projects" / project.id / "hosts" / source.machine.id
        before = {path.relative_to(host_dir): path.read_bytes() for path in host_dir.rglob("*") if path.is_file()}
        destination = self.root.parent / "copy"
        shutil.copytree(self.root, destination)
        self.fingerprint.return_value = "device-b"
        copied = Store(destination)
        self.assertNotEqual(copied.machine.id, source.machine.id)
        self.assertFalse(copied.is_bound_here(project.id))
        self.assertEqual(copied.list_projects()[0].machine_id, source.machine.id)
        copied_host = destination / host_dir.relative_to(self.root)
        self.assertEqual(before, {path.relative_to(copied_host): path.read_bytes() for path in copied_host.rglob("*") if path.is_file()})
        self.assertEqual(load_machine_identity(self.root), source.machine)

    def test_legacy_identity_adopts_fingerprint_without_resetting_checkpoint(self) -> None:
        original = self.seed(device_fingerprint=None)
        self.assertEqual(ensure_machine_identity(self.root), replace(original, device_fingerprint="device-a"))

    def test_ambiguous_legacy_rename_requires_explicit_confirmation(self) -> None:
        original = self.seed(device_fingerprint=None, label="开发机")
        self.hostname.return_value = "new-host"
        with self.assertRaisesRegex(MachineIdentityMismatchError, "machine copy"):
            ensure_machine_identity(self.root)
        self.assertEqual(load_machine_identity(self.root), original)
        updated = resolve_machine_rename(self.root)
        self.assertEqual(updated, replace(original, hostname="new-host", device_fingerprint="device-a"))
        self.assertEqual(ensure_machine_identity(self.root), updated)

    def test_missing_fingerprint_cannot_erase_existing_device_evidence(self) -> None:
        original = self.seed()
        self.fingerprint.return_value = None
        for action in (ensure_machine_identity, resolve_machine_rename):
            with self.subTest(action=action.__name__):
                with self.assertRaises(MachineIdentityMismatchError):
                    action(self.root)
                self.assertEqual(load_machine_identity(self.root), original)
        self.fingerprint.return_value = "device-a"
        self.assertEqual(ensure_machine_identity(self.root), original)

    def test_explicit_rename_cannot_reuse_another_devices_id(self) -> None:
        original = self.seed()
        self.fingerprint.return_value = "device-b"
        with self.assertRaises(MachineIdentityMismatchError):
            resolve_machine_rename(self.root)
        self.assertEqual(load_machine_identity(self.root), original)

    def test_explicit_copy_works_without_fingerprint(self) -> None:
        original = self.seed(device_fingerprint=None)
        self.fingerprint.return_value = None
        updated = resolve_machine_copy(self.root, label="备用机")
        self.assertNotEqual(updated.id, original.id)
        self.assertEqual(updated.label, "备用机")
        self.assertEqual(ensure_machine_identity(self.root), updated)

    def test_sync_checkpoint_write_preserves_a_concurrent_rename(self) -> None:
        original = self.seed()
        manager = SyncManager(self.root, original)
        self.hostname.return_value = "new-host"
        updated = resolve_machine_rename(self.root, label="开发机")
        manager._record_failure()
        self.assertEqual(load_machine_identity(self.root), updated)
        with patch.object(manager, "_head", return_value="new-checkpoint"):
            manager._record_success()
        saved = load_machine_identity(self.root)
        self.assertEqual(saved.hostname, updated.hostname)
        self.assertEqual(saved.label, updated.label)
        self.assertEqual(saved.device_fingerprint, original.device_fingerprint)
        self.assertEqual(saved.last_synced_commit, "new-checkpoint")
        self.assertFalse(saved.sync_pending)

    def test_hostname_change_does_not_make_store_read_only(self) -> None:
        store = Store(self.root)
        self.hostname.return_value = "new-host"
        self.assertTrue(store.sync.status()["hostname_mismatch"])
        self.assertIsNone(store.read_only_reason)
        updated = Store(self.root)
        self.assertEqual(updated.machine.id, store.machine.id)
        self.assertEqual(updated.sync.status()["machine_label"], "new-host")
        self.assertFalse(updated.sync.status()["hostname_mismatch"])

    def test_sync_failure_from_stale_manager_does_not_erase_newer_checkpoint(self) -> None:
        original = self.seed()
        manager = SyncManager(self.root, original)
        updated = replace(original, last_sync_at=456.0, last_synced_commit="new-checkpoint", sync_pending=False)
        self.save(updated)
        manager._record_failure()
        self.assertEqual(load_machine_identity(self.root), replace(updated, sync_pending=True))

    def test_stale_store_and_sync_manager_cannot_restore_old_identity(self) -> None:
        store = Store(self.root)
        updated = resolve_machine_copy(self.root)
        with self.assertRaises(StoreReadOnlyError):
            store.create_project(Project(root_path="/old/checkout", name="项目"))
        for action in (store.sync._record_failure, store.sync._record_success):
            with self.assertRaises(MachineIdentityMismatchError):
                action()
        reopened = Store(self.root)
        self.assertEqual(reopened.machine, updated)
        self.assertEqual(reopened.sync.identity, updated)
        self.assertIsNone(reopened.read_only_reason)

    def test_parallel_initialization_and_copy_choose_one_identity(self) -> None:
        context = multiprocessing.get_context("spawn")
        for fingerprint in (None, "device-a", "device-b"):
            with self.subTest(fingerprint=fingerprint):
                root = self.root / str(fingerprint)
                root.mkdir()
                if fingerprint is not None:
                    identity = MachineIdentity("original-id", "old-host", "old-host", device_fingerprint=fingerprint)
                    (root / "machine.json").write_text(json.dumps(identity.payload()))
                barrier = context.Barrier(2)
                result = context.Queue()
                processes = [context.Process(target=_initialize_in_process, args=(str(root), barrier, result)) for _ in range(2)]
                try:
                    for process in processes:
                        process.start()
                    identities = [result.get(timeout=20) for _ in processes]
                    for process in processes:
                        process.join(timeout=20)
                        self.assertEqual(process.exitcode, 0)
                    self.assertEqual(identities[0], identities[1])
                    self.assertEqual(load_machine_identity(root).payload(), identities[0])
                finally:
                    for process in processes:
                        if process.is_alive():
                            process.terminate()
                            process.join(timeout=5)
                    result.close()

    def test_crashed_writer_releases_lock_and_preserves_previous_identity(self) -> None:
        original = self.seed()
        context = multiprocessing.get_context("spawn")
        process = context.Process(target=_crash_before_replace, args=(str(self.root),))
        process.start()
        try:
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 17)
            self.assertEqual(load_machine_identity(self.root), original)
            self.hostname.return_value = "new-host"
            updated = ensure_machine_identity(self.root)
            self.assertEqual(updated, replace(original, hostname="new-host", label="new-host"))
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    def test_cli_copy_and_rename_are_noninteractive(self) -> None:
        original = self.seed(device_fingerprint=None)
        self.hostname.return_value = "new-host"
        with (
            patch.dict(os.environ, {"MINICLAW_HOME": str(self.root)}),
            patch("builtins.input", side_effect=AssertionError("不得询问交互输入")),
            patch("builtins.print"),
        ):
            with patch.object(sys, "argv", ["miniclaw2", "machine", "rename", "--label", "开发机"]):
                main()
            renamed = load_machine_identity(self.root)
            self.assertEqual(renamed.id, original.id)
            self.assertEqual(renamed.label, "开发机")
            with patch.object(sys, "argv", ["miniclaw2", "machine", "copy"]):
                main()
            self.assertNotEqual(load_machine_identity(self.root).id, original.id)

    def test_cli_rejects_ambiguous_start_before_spawning_servers(self) -> None:
        self.seed(device_fingerprint=None)
        self.hostname.return_value = "new-host"
        with (
            patch.dict(os.environ, {"MINICLAW_HOME": str(self.root)}),
            patch.object(sys, "argv", ["miniclaw2", "--dev"]),
            patch("miniclaw2.__main__.uvicorn.run") as run,
            patch("miniclaw2.__main__.subprocess.Popen") as spawn,
            patch("sys.stderr"),
        ):
            with self.assertRaises(SystemExit) as raised:
                main()
            self.assertEqual(raised.exception.code, 1)
            run.assert_not_called()
            spawn.assert_not_called()

    def test_cli_sync_init_existing_store_and_renamed_store(self) -> None:
        remote = self.root.parent / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        store = Store(self.root)
        self.hostname.return_value = "new-host"
        with (
            patch.dict(os.environ, {"MINICLAW_HOME": str(self.root)}),
            patch.object(sys, "argv", ["miniclaw2", "sync", "init", str(remote)]),
            patch("builtins.print"),
        ):
            main()
        updated = load_machine_identity(self.root)
        self.assertEqual(updated.id, store.machine.id)
        self.assertEqual(updated.hostname, "new-host")
        self.assertEqual(updated.device_fingerprint, "device-a")
        self.assertIsNotNone(updated.last_synced_commit)
        self.assertFalse(updated.sync_pending)
        tracked = subprocess.run(["git", "-C", str(self.root), "ls-files"], check=True, capture_output=True, text=True).stdout.splitlines()
        self.assertNotIn("machine.json", tracked)
        self.assertNotIn("machine.lock", tracked)


class DeviceFingerprintTests(unittest.TestCase):
    def test_macos_identifier_is_hashed_and_read_failures_are_retried(self) -> None:
        result = subprocess.CompletedProcess([], 0, plistlib.dumps([{"IOPlatformUUID": "device-uuid"}]))
        with (
            patch.object(sys, "platform", "darwin"),
            patch.object(sync_module.subprocess, "run", side_effect=[OSError, result]),
        ):
            self.assertIsNone(current_device_fingerprint())
            self.assertEqual(current_device_fingerprint(), hashlib.sha256(b"device-uuid").hexdigest())

    def test_linux_empty_machine_id_uses_dbus_fallback(self) -> None:
        with (
            patch.object(sys, "platform", "linux"),
            patch.object(Path, "read_text", side_effect=["", "dbus-id\n"]),
        ):
            self.assertEqual(current_device_fingerprint(), hashlib.sha256(b"dbus-id").hexdigest())
