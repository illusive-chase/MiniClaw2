from __future__ import annotations

import gzip
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2 import code_index as code_index_module
from miniclaw2.app import _code_reading_available, create_app
from miniclaw2.domain import ProjectPersistenceMode
from miniclaw2.migrations import inventory


class CodeIndexApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._project = tempfile.TemporaryDirectory()
        self.root = Path(self._project.name).resolve()
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "pkg" / "core.py").write_text(
            "class Engine:\n    def start(self) -> int:\n        return 1\n", encoding="utf-8"
        )
        (self.root / "pkg" / "other.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "main.py").write_text(
            "from pkg.core import Engine\nEngine().start()\n", encoding="utf-8"
        )
        (self.root / "notes.md").write_text("# notes\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("secret.py\n", encoding="utf-8")
        (self.root / "secret.py").write_text("TOKEN = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        self._hooks = patch("miniclaw2.app.install_hooks")
        self._hooks.start()
        self.app = create_app()
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.sid = self._create_session()
        self.service = self.app.state.code_index

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._hooks.stop()
        self._project.cleanup()

    def _create_session(self) -> str:
        res = self.client.post("/sessions", json={"cwd": str(self.root), "model_preset_id": "opus-4-8"})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["id"]

    def _index(self, prioritize: list[str] | None = None) -> dict:
        res = self.client.post(f"/sessions/{self.sid}/code/index", json={"prioritize": prioritize or []})
        self.assertEqual(res.status_code, 200, res.text)
        status = res.json()
        deadline = time.time() + 60
        while status["state"] == "running" and time.time() < deadline:
            time.sleep(0.05)
            status = self.client.get(f"/sessions/{self.sid}/code/index").json()
        return status

    def _analysis(self, path: str):
        return self.client.get(f"/sessions/{self.sid}/code/analysis", params={"path": path})

    def _cache(self) -> Path:
        return self.service.cache_dir(self.sid)

    def test_capability_for_each_project_form(self) -> None:
        self.assertTrue(self.client.get(f"/sessions/{self.sid}").json()["capabilities"]["code_reading"])
        temp = self.client.post("/sessions", json={"temporary": True, "model_preset_id": "opus-4-8"})
        self.assertEqual(temp.status_code, 200, temp.text)
        self.assertFalse(temp.json()["capabilities"]["code_reading"])
        self.assertEqual(self.client.get(f"/sessions/{temp.json()['id']}/code/tree").status_code, 400)
        registry = SimpleNamespace(is_native_project=lambda _project: True)
        remote = SimpleNamespace(
            persistence_mode=ProjectPersistenceMode.REMOTE, temporary=False, archived_at=None
        )
        self.assertFalse(_code_reading_available(registry, remote))  # type: ignore[arg-type]
        unbound = SimpleNamespace(
            persistence_mode=ProjectPersistenceMode.DURABLE, temporary=False, archived_at=None
        )
        self.assertFalse(_code_reading_available(  # type: ignore[arg-type]
            SimpleNamespace(is_native_project=lambda _project: False), unbound
        ))

    def test_tree_and_file_follow_git_visibility(self) -> None:
        tree = self.client.get(f"/sessions/{self.sid}/code/tree").json()
        paths = [entry["path"] for entry in tree["files"]]
        self.assertTrue(tree["is_git"])
        self.assertIn("main.py", paths)
        self.assertIn("notes.md", paths)
        self.assertNotIn("secret.py", paths)
        body = self.client.get(f"/sessions/{self.sid}/code/file", params={"path": "notes.md"}).json()
        self.assertEqual(body["language"], "other")
        self.assertEqual(
            self.client.get(f"/sessions/{self.sid}/code/file", params={"path": "secret.py"}).status_code, 403
        )
        self.assertEqual(self._analysis("secret.py").status_code, 403)
        self.assertEqual(self._analysis("notes.md").status_code, 404)

    def test_index_then_analysis_is_pregzipped_and_hash_aligned(self) -> None:
        self.assertEqual(self._analysis("main.py").status_code, 202)
        status = self._index(["main.py"])
        self.assertEqual(status["state"], "ready", status)
        res = self._analysis("main.py")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.headers["content-encoding"], "gzip")
        self.assertTrue(res.headers["etag"].startswith('"'))
        payload = res.json()
        file_body = self.client.get(f"/sessions/{self.sid}/code/file", params={"path": "main.py"}).json()
        self.assertEqual(payload["sha256"], file_body["sha256"])
        targets = [s["target"] for s in payload["symbols"] if s["target"]]
        self.assertIn({"path": "pkg/core.py", "line": 2, "col": 8, "end_col": 13}, targets)
        not_modified = self.client.get(
            f"/sessions/{self.sid}/code/analysis",
            params={"path": "main.py"},
            headers={"If-None-Match": res.headers["etag"]},
        )
        self.assertEqual(not_modified.status_code, 304)

    def test_etag_follows_the_result_not_the_source_hash(self) -> None:
        self._index()
        first = self._analysis("main.py")
        self.assertEqual(first.status_code, 200, first.text)
        etag = first.headers["etag"]

        # A rerun that rewrites main.py's result with the same content keeps
        # its ETag. The pause puts the rewrite in another second, which is
        # what a gzip header's timestamp would otherwise record.
        from miniclaw2.code_analysis import cache_file_for

        cached = cache_file_for(self._cache(), "main.py")
        written = cached.stat().st_mtime_ns
        time.sleep(1.1)
        (self.root / "pkg" / "other.py").write_text("VALUE = 2\n", encoding="utf-8")
        self._index()
        self.assertNotEqual(cached.stat().st_mtime_ns, written)
        self.assertEqual(self._analysis("main.py").headers["etag"], etag)

        # Moving a definition main.py imports rewrites its targets, not its
        # sha256: a revalidating client must get the new body, not a 304.
        (self.root / "pkg" / "core.py").write_text(
            "\n\nclass Engine:\n    def start(self) -> int:\n        return 1\n", encoding="utf-8"
        )
        self._index()
        res = self.client.get(
            f"/sessions/{self.sid}/code/analysis",
            params={"path": "main.py"},
            headers={"If-None-Match": etag},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotEqual(res.headers["etag"], etag)
        self.assertEqual(res.json()["sha256"], first.json()["sha256"])
        lines = [s["target"]["line"] for s in res.json()["symbols"] if s["target"] and s["target"]["path"] == "pkg/core.py"]
        self.assertIn(4, lines)

    def test_unchanged_project_spawns_nothing(self) -> None:
        self._index()
        with patch.object(code_index_module.asyncio, "create_subprocess_exec") as spawn:
            status = self._index()
        spawn.assert_not_called()
        self.assertEqual(status["state"], "ready")

    def test_incremental_run_rewrites_only_changed_files(self) -> None:
        self._index()
        from miniclaw2.code_analysis import cache_file_for

        other = cache_file_for(self._cache(), "pkg/other.py")
        main = cache_file_for(self._cache(), "main.py")
        before = {path: path.stat().st_mtime_ns for path in (other, main)}
        time.sleep(0.01)
        # Same bytes, new stat (what `git checkout` does): the manifest is
        # refreshed but no result is recomputed.
        os.utime(self.root / "pkg" / "other.py", None)
        (self.root / "main.py").write_text(
            "from pkg.core import Engine\nEngine().start()\n", encoding="utf-8"
        )
        os.utime(self.root / "main.py", (time.time() + 5, time.time() + 5))
        self._index()
        self.assertEqual(other.stat().st_mtime_ns, before[other])
        self.assertEqual(main.stat().st_mtime_ns, before[main])

        # A real edit that moves a definition: its dependants pick up the new line.
        (self.root / "pkg" / "core.py").write_text(
            "\nclass Engine:\n    def start(self) -> int:\n        return 1\n", encoding="utf-8"
        )
        self._index()
        payload = self._analysis("main.py").json()
        targets = [s["target"]["line"] for s in payload["symbols"] if s["target"] and s["target"]["path"] == "pkg/core.py"]
        self.assertIn(3, targets)

    def test_version_or_root_change_invalidates_the_cache(self) -> None:
        self._index()
        marker = self._cache() / "files" / "stale.json.gz"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(gzip.compress(b"{}"))
        with patch.object(code_index_module, "ANALYZER_VERSION", 999):
            self._index()
        self.assertFalse(marker.exists())
        meta = json.loads((self._cache() / "meta.json").read_text())
        self.assertEqual(meta["analyzer_version"], 999)

    def test_prioritize_orders_phase_two(self) -> None:
        from miniclaw2.code_analysis import run_job

        cache = Path(tempfile.mkdtemp())
        events: list[dict] = []
        files = [{"path": p, "mtime_ns": 0, "size": 0} for p in ("main.py", "pkg/__init__.py", "pkg/core.py", "pkg/other.py")]
        run_job({"root": str(self.root), "cache_dir": str(cache), "files": files, "reuse": {},
                 "priority": ["pkg/other.py"]}, emit=events.append)
        order = [e["path"] for e in events if e["event"] == "file"]
        self.assertEqual(order[0], "pkg/other.py")
        self.assertEqual(sorted(order), sorted(f["path"] for f in files))

    def test_stalled_child_is_killed(self) -> None:
        self.service.stall_timeout = 0.3
        hang = Path(tempfile.mkdtemp()) / "hang.py"
        hang.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        with patch.object(code_index_module.code_analysis, "__file__", str(hang)):
            status = self._index()
        self.assertEqual(status["state"], "error", status)
        self.assertIsNotNone(status["error"])

    def test_delete_and_unbind_remove_the_cache(self) -> None:
        self._index()
        self.assertTrue(self._cache().exists())
        machine = self.client.get(f"/sessions/{self.sid}").json()["local_machine_id"]
        res = self.client.delete(f"/sessions/{self.sid}/hosts/{machine}")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertFalse(self._cache().exists())

        self.sid = self._create_session()
        self._index()
        self.assertTrue(self._cache().exists())
        self.assertEqual(self.client.delete(f"/sessions/{self.sid}").status_code, 200)
        self.assertFalse(self._cache().exists())

    def test_symlink_out_of_the_project_is_not_analysed(self) -> None:
        outside = Path(tempfile.mkdtemp()) / "leak.py"
        outside.write_text('def leaked():\n    """SECRET DOC"""\n', encoding="utf-8")
        (self.root / "link.py").symlink_to(outside)
        (self.root / "use.py").write_text("from link import leaked\nleaked()\n", encoding="utf-8")
        self._index(["use.py"])
        payload = self._analysis("use.py").json()
        self.assertNotIn("SECRET DOC", json.dumps(payload))
        self.assertEqual(self._analysis("link.py").status_code, 403)

    def test_cache_is_outside_managed_inventory(self) -> None:
        self._index()
        store_root = self.app.state.code_index.home()
        self.assertTrue((store_root / "cache").exists())
        managed = inventory.files(store_root)
        self.assertFalse([path for path in managed if path.startswith("cache/")])
        self.assertIn("cache", inventory.EXCLUDED_ROOTS)
        self.assertIn("/cache/", inventory.IGNORED_PATTERNS)


if __name__ == "__main__":
    unittest.main()
