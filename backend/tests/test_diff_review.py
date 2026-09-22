from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from miniclaw2.diff_review import DIFF_ARTIFACT_KIND, build_diff_artifact
from miniclaw2.git_state import write_tree_snapshot


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "old.txt").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)


def test_build_diff_artifact_inlines_text_and_marks_binary(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    base = write_tree_snapshot(str(tmp_path))
    (tmp_path / "old.txt").write_text("new\n", encoding="utf-8")
    (tmp_path / "binary.dat").write_bytes(b"a\x00b")
    head = write_tree_snapshot(str(tmp_path))

    payload = build_diff_artifact(
        str(tmp_path),
        base_tree=base or "",
        head_tree=head or "",
        started_at=1,
        ended_at=2,
        concurrent_node_ids=["node-b", "node-b"],
    )

    assert payload["kind"] == DIFF_ARTIFACT_KIND
    assert payload["totals"]["files"] == 2
    assert payload["concurrent_node_ids"] == ["node-b"]
    by_path = {item["path"]: item for item in payload["files"]}
    assert by_path["old.txt"]["before"] == "old\n"
    assert by_path["old.txt"]["after"] == "new\n"
    assert by_path["binary.dat"]["binary"] is True
    assert by_path["binary.dat"]["inlined"] is False
    json.dumps(payload)


def test_build_diff_artifact_keeps_complete_inventory_when_truncated(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    base = write_tree_snapshot(str(tmp_path))
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    head = write_tree_snapshot(str(tmp_path))

    with patch("miniclaw2.diff_review.INLINE_CONTENT_BUDGET", 30):
        payload = build_diff_artifact(
            str(tmp_path),
            base_tree=base or "",
            head_tree=head or "",
            started_at=1,
            ended_at=2,
            concurrent_node_ids=[],
        )

    assert payload["truncated"] is True
    assert [item["path"] for item in payload["files"]] == ["a.txt", "b.txt"]
    assert all(not item["inlined"] for item in payload["files"])
