"""The analyser reads project code; it must never run it."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2 import code_analysis
from miniclaw2.code_index import CodeIndexService


class AnalyzerIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._root = tempfile.TemporaryDirectory()
        self._home = tempfile.TemporaryDirectory()
        self.root = Path(self._root.name).resolve()
        self.home = Path(self._home.name).resolve()
        self.marker = self.root / "EXECUTED"
        # A project module shadowing the stdlib, and one with a top-level side
        # effect. Importing or executing either would create the marker.
        side_effect = f"open({str(self.marker)!r}, 'w').write('x')\n"
        (self.root / "ast.py").write_text(side_effect, encoding="utf-8")
        (self.root / "tokenize.py").write_text(side_effect, encoding="utf-8")
        (self.root / "boom.py").write_text(side_effect + "def f():\n    pass\n", encoding="utf-8")
        (self.root / "main.py").write_text("import ast\nimport boom\nboom.f()\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._root.cleanup()
        self._home.cleanup()

    def test_service_runs_isolated_and_imports_nothing(self) -> None:
        service = CodeIndexService(self.home)
        spawned: list[tuple[tuple[object, ...], dict[str, object]]] = []
        original = asyncio.create_subprocess_exec

        async def spy(*args: object, **kwargs: object):
            spawned.append((args, kwargs))
            return await original(*args, **kwargs)  # type: ignore[arg-type]

        async def run() -> dict[str, object]:
            with patch("miniclaw2.code_index.asyncio.create_subprocess_exec", spy):
                await service.ensure("p1", self.root)
                for _ in range(200):
                    status = service.status("p1")
                    if status["state"] != "running":
                        return status
                    await asyncio.sleep(0.05)
            return service.status("p1")

        status = asyncio.run(run())
        self.assertEqual(status["state"], "ready", status)
        self.assertFalse(self.marker.exists(), "project code was executed")
        (args, kwargs), = spawned
        self.assertEqual(args[0], sys.executable)
        self.assertEqual(args[1], "-I")
        self.assertEqual(kwargs["cwd"], str(service.cache_dir("p1")))
        self.assertNotEqual(Path(str(kwargs["cwd"])).resolve(), self.root)
        payload = service.read_result("p1", "main.py")
        self.assertIsNotNone(payload)

    def test_script_is_self_contained(self) -> None:
        """Under -I the backend package is not importable, so the script may
        only use the standard library and no relative imports."""
        source = Path(code_analysis.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from .", source)
        self.assertNotIn("import miniclaw2", source)
        cache = self.home / "cache"
        cache.mkdir()
        job = {"root": str(self.root), "cache_dir": str(cache),
               "files": [{"path": "main.py", "mtime_ns": 0, "size": 0}], "reuse": {}, "priority": []}
        result = subprocess.run(
            [sys.executable, "-I", code_analysis.__file__],
            input=json.dumps(job) + "\n",
            capture_output=True,
            text=True,
            cwd=self.root,  # even with the project as cwd, -I keeps ast.py out
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"event": "done"', result.stdout)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
