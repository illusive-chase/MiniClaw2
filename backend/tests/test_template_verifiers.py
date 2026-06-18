from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _write_node(project_dir: Path, node_id: str, payload: dict) -> None:
    node_dir = project_dir / "nodes" / node_id
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "node.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


class TemplateVerifierTests(unittest.TestCase):
    def test_interrupt_midstream_verifier_targets_interrupted_template_node(self) -> None:
        script = (
            BACKEND_ROOT
            / "miniclaw2"
            / "templates"
            / "bundled"
            / "interrupt-midstream"
            / "scripts"
            / "verify.sh"
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project_dir = home / "projects" / "p1"
            _write_node(
                project_dir,
                "turn1-node",
                {
                    "id": "turn1-node",
                    "project_id": "p1",
                    "kind": "agent",
                    "category": "regular",
                    "state": "cancelled",
                    "planspace_id": "lane-A",
                    "provider": "claude",
                    "prompt": "for i in $(seq 1 60); do echo line; done",
                    "scheduled_deps": [],
                    "proposed_by": "template:interrupt-midstream",
                    "created_at": 1.0,
                    "started_at": 1.0,
                    "finished_at": 2.0,
                },
            )
            events_path = project_dir / "nodes" / "turn1-node" / "events.jsonl"
            events_path.write_text(
                json.dumps({"seq": 1, "event": {"type": "text_delta"}}) + "\n",
                encoding="utf-8",
            )
            _write_node(
                project_dir,
                "accept-node",
                {
                    "id": "accept-node",
                    "project_id": "p1",
                    "kind": "agent",
                    "category": "review",
                    "subtype": "human_interact_review",
                    "state": "virtual",
                    "planspace_id": "lane-A",
                    "provider": "claude",
                    "prompt": "",
                    "prompt_draft": "accept",
                    "scheduled_deps": ["turn1-node", "verify-node"],
                    "proposed_by": "template:interrupt-midstream",
                    "created_at": 3.0,
                },
            )

            env = dict(os.environ)
            env["MINICLAW_HOME"] = str(home)
            env["MINICLAW_PROJECT_ID"] = "p1"
            completed = subprocess.run(
                ["bash", str(script)],
                cwd=str(project_dir),
                env=env,
                check=False,
                text=True,
                capture_output=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
