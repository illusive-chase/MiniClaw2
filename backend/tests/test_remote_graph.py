from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniclaw2.domain import Node, Project
from miniclaw2.remote_graph import RemoteGraphTools, remote_launch_block


def tools(tmp_path: Path, category="regular") -> RemoteGraphTools:
    return RemoteGraphTools(Project(root_path=str(tmp_path)), Node(
        project_id="test", model_preset_id="gpt-5.6", category=category, planspace_id="lane",
    ))


def preview(tool: RemoteGraphTools) -> dict:
    return {"id": tool.node.id, "kind": "agent", "category": tool.node.category.value,
            "state": "done", "ran_at": "2026-09-18T00:00:00Z", "lane": "lane",
            "motivation": "测试", "summary": "完成", "next_implications": "通过", "artifacts": []}


def test_publish_then_read_preview(tmp_path: Path) -> None:
    tool = tools(tmp_path)
    assert tool.call("publish_preview", {"preview": preview(tool)})["success"]
    listing = json.loads(tool.call("lane_list", {})["contentItems"][0]["text"])
    path = listing["files"][0]
    response = tool.call("lane_read", {"path": path})
    text = json.loads(response["contentItems"][0]["text"])["text"]
    assert json.loads(text) == preview(tool)
    assert "publish_preview" in remote_launch_block(tool.node)


@pytest.mark.parametrize("change", [{"id": "../other"}, {"lane": "other"}, {"unknown": True}, {"id": "other"}])
def test_publish_rejects_identity_schema_and_ownership(tmp_path: Path, change: dict) -> None:
    tool = tools(tmp_path)
    assert not tool.call("publish_preview", {"preview": {**preview(tool), **change}})["success"]


def test_virtual_writes_only_planning_review(tmp_path: Path) -> None:
    tool = tools(tmp_path)
    virtual = {"id": "future", "kind": "agent", "category": "regular", "state": "virtual",
               "lane": "lane", "proposed_by": tool.node.id, "motivation": "后续", "prompt_draft": "执行"}
    assert not tool.call("publish_preview", {"preview": virtual})["success"]
    planner = tools(tmp_path, "planning")
    assert planner.call("publish_preview", {"preview": virtual})["success"]


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "nodes/../../../secret", "a\x00b", "a\\b"])
def test_read_rejects_path_escape(tmp_path: Path, path: str) -> None:
    assert not tools(tmp_path).call("lane_read", {"path": path})["success"]


def test_symlink_artifact_and_preview_escape_rejected(tmp_path: Path) -> None:
    tool = tools(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    tool.outputs.parent.mkdir(parents=True)
    tool.outputs.symlink_to(outside, target_is_directory=True)
    assert not tool.call("publish_artifact", {"name": "bad.md", "content": "bad"})["success"]
    tool.lane.mkdir(parents=True)
    (tool.lane / "link").symlink_to(outside, target_is_directory=True)
    assert not tool.call("lane_read", {"path": "link/secret"})["success"]
    assert not list(outside.iterdir())


def test_chunked_artifact_size_and_suffix_limits(tmp_path: Path) -> None:
    tool = tools(tmp_path)
    assert tool.call("publish_artifact", {"name": "result.md", "content": "第一段"})["success"]
    assert tool.call("publish_artifact", {"name": "result.md", "content": "第二段", "append": True})["success"]
    assert (tool.outputs / "result.md").read_text() == "第一段第二段"
    assert not tool.call("publish_artifact", {"name": "bad.py", "content": "no"})["success"]
    assert not tool.call("publish_artifact", {"name": "big.md", "content": "x" * (2 * 1024 * 1024 + 1)})["success"]
    assert not tool.call("publish_artifact", {"name": "../bad.md", "content": "no"})["success"]
