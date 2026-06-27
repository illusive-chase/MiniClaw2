from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app


class SessionFileApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._cwd = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        self._cwd.cleanup()
        self._home.cleanup()

    def _create_session(self) -> str:
        res = self.client.post(
            "/sessions",
            json={"cwd": self._cwd.name, "provider": "claude"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["id"]

    def test_context_file_response_includes_hand_writer(self) -> None:
        (Path(self._cwd.name) / "CONTEXT.md").write_text(
            "# Context\n",
            encoding="utf-8",
        )
        sid = self._create_session()

        res = self.client.get(f"/sessions/{sid}/files?role=context")

        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["role"], "context")
        self.assertEqual(body["text"], "# Context\n")
        self.assertEqual(body["last_writer"], {"kind": "hand"})

    def test_context_file_response_uses_context_refresh_meta(self) -> None:
        root = Path(self._cwd.name)
        (root / "CONTEXT.md").write_text("# Context\n", encoding="utf-8")
        time.sleep(0.01)
        meta = root / ".miniclaw2" / "context.meta.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(
            json.dumps(
                {
                    "updated_at": 123.5,
                    "source": "refresh",
                    "rewritten": True,
                },
            ),
            encoding="utf-8",
        )
        sid = self._create_session()

        res = self.client.get(f"/sessions/{sid}/files?role=context")

        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(
            res.json()["last_writer"],
            {
                "kind": "context-refresh",
                "updated_at": 123.5,
                "source": "refresh",
            },
        )


if __name__ == "__main__":
    unittest.main()
