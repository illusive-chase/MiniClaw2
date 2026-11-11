from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Category, Node, NodeKind, NodeState, Project
from miniclaw2.node_search import NodeSearchIndex, split_terms
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[ProjectRegistry, Project]:
    os.environ["MINICLAW_CONTEXT_HOME"] = str(tmp_path / "context")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = Store(tmp_path / "store")
    project = store.create_project(Project(root_path=str(workspace), name="search"))
    return ProjectRegistry(store=store), project


def _executed(store: Store, project: Project, nid: str, **fields: object) -> Node:
    return store.create_node(
        Node(
            id=nid,
            project_id=project.id,
            model_preset_id="opus-4-8",
            state=NodeState.DONE,
            category=Category.REGULAR,
            prompt=f"prompt {nid}",
            finished_at=fields.pop("finished_at", 100.0),  # type: ignore[arg-type]
            **fields,  # type: ignore[arg-type]
        )
    )


def _preview(store: Store, project: Project, nid: str, **fields: str) -> None:
    payload = {
        "id": nid,
        "kind": "agent",
        "category": "regular",
        "state": "done",
        "ran_at": "2026-11-11T00:00:00Z",
        "lane": "",
        "motivation": fields.get("motivation", ""),
        "summary": fields.get("summary", ""),
        "next_implications": fields.get("next_implications", ""),
        "artifacts": [],
    }
    store.write_node_preview(project.id, nid, json.dumps(payload, ensure_ascii=False))


def _events(store: Store, project: Project, nid: str, events: list[dict]) -> None:
    seq = len(store.replay_events(project.id, nid))
    for event in events:
        seq += 1
        store.append_event(project.id, nid, seq, {**event, "node_id": nid, "seq": seq})


def _text(text: str) -> dict:
    return {"type": "text_delta", "text": text}


def _tool() -> dict:
    return {"type": "activity", "kind": "tool", "status": "finish", "id": "t", "name": "Read"}


def test_split_terms_uses_whitespace_and_cjk_punctuation() -> None:
    assert split_terms("  代码阅读，Preview。缓存  preview ;x ") == [
        "代码阅读",
        "preview",
        "缓存",
        "x",
    ]
    assert split_terms(" ，。 ") == []


def test_matches_preview_fields_and_requires_every_term(
    seeded: tuple[ProjectRegistry, Project],
) -> None:
    registry, project = seeded
    store = registry.store
    _executed(store, project, "a1", finished_at=200.0)
    _preview(store, project, "a1", summary="实现了 Feishu 卡片信箱", motivation="远程回答")
    _executed(store, project, "b2")
    _preview(store, project, "b2", summary="Feishu 扫码绑定")
    index = NodeSearchIndex()
    nodes = store.list_nodes(project.id)

    both = index.search(store, project.id, nodes, "feishu")
    assert [r["node_id"] for r in both["results"]] == ["a1", "b2"]

    narrowed = index.search(store, project.id, nodes, "FEISHU，信箱")
    assert [r["node_id"] for r in narrowed["results"]] == ["a1"]
    assert narrowed["terms"] == ["feishu", "信箱"]
    assert narrowed["results"][0]["matches"] == [
        {"field": "summary", "snippet": "实现了 Feishu 卡片信箱"}
    ]

    # Terms may be spread across preview and response text.
    _events(store, project, "b2", [_text("这里提到信箱")])
    spread = index.search(store, project.id, store.list_nodes(project.id), "feishu 信箱")
    assert {r["node_id"] for r in spread["results"]} == {"a1", "b2"}


def test_text_blocks_follow_transcript_boundaries_and_resume(
    seeded: tuple[ProjectRegistry, Project],
) -> None:
    registry, project = seeded
    store = registry.store
    _executed(store, project, "n1")
    _events(
        store,
        project,
        "n1",
        [
            {"type": "node_started", "node": {}, "prompt": "find needle in prompt"},
            _text("first "),
            {"type": "usage", "input_tokens": 1},
            _text("block"),
            _tool(),
            _text("second nee"),
            _text("dle block"),
            {"type": "thinking", "text": "needle in thinking only"},
        ],
    )
    index = NodeSearchIndex()

    def run(query: str) -> dict:
        return index.search(store, project.id, store.list_nodes(project.id), query)

    # Deltas join across `usage`, which the transcript also ignores.
    assert run("first block")["total"] == 1
    hit = run("needle")["results"][0]
    assert hit["matches"] == [{"field": "text", "snippet": "second needle block"}]
    assert hit["text_match_count"] == 1
    # Neither the prompt nor thinking is a plain-text response.
    assert run("prompt")["total"] == 0
    assert run("thinking")["total"] == 0

    # Appended deltas extend the still-open block on the next query.
    _events(store, project, "n1", [_text(" tail")])
    _events(store, project, "n1", [_text("more")])
    assert run("thinking")["total"] == 0
    state = index._text[project.id]["n1"]
    assert state.blocks == ["first block", "second needle block", " tailmore"]
    _events(store, project, "n1", [{"type": "turn_done", "node": {}}, _text("fresh")])
    run("x")
    assert state.blocks[-1] == "fresh"

    # A partial trailing line is left for the next query.
    events_file = store.node_dir(project.id, "n1") / "events.jsonl"
    with events_file.open("a", encoding="utf-8") as handle:
        handle.write('{"schema_version": 2, "seq": 99, "event": {"type": "text_delta", "te')
    assert run("fresh")["total"] == 1
    with events_file.open("a", encoding="utf-8") as handle:
        handle.write('xt": " partialword", "node_id": "n1", "seq": 99}}\n')
    assert run("partialword")["total"] == 1


def test_virtual_nodes_search_motivation_and_prompt_draft(
    seeded: tuple[ProjectRegistry, Project],
) -> None:
    registry, project = seeded
    store = registry.store
    store.create_node(
        Node(
            id="v1",
            project_id=project.id,
            model_preset_id="opus-4-8",
            state=NodeState.VIRTUAL,
            category=Category.REGULAR,
            summary="why: 搜索侧栏",
            prompt_draft="draft mentions sidebar",
        )
    )
    store.create_node(
        Node(id="op1", project_id=project.id, kind=NodeKind.OP, op_kind="noop", summary="sidebar")
    )
    index = NodeSearchIndex()
    nodes = store.list_nodes(project.id)

    result = index.search(store, project.id, nodes, "sidebar")
    assert [r["node_id"] for r in result["results"]] == ["v1"]
    assert result["results"][0]["matches"] == [
        {"field": "prompt_draft", "snippet": "draft mentions sidebar"}
    ]
    assert index.search(store, project.id, nodes, "搜索侧栏")["results"][0]["matches"][0][
        "field"
    ] == "motivation"


def test_results_sort_newest_first_and_truncate(
    seeded: tuple[ProjectRegistry, Project],
) -> None:
    registry, project = seeded
    store = registry.store
    for i in range(5):
        _executed(store, project, f"n{i}", finished_at=float(10 + i))
        _preview(store, project, f"n{i}", summary="shared keyword")
    result = NodeSearchIndex().search(
        store, project.id, store.list_nodes(project.id), "keyword", limit=3
    )
    assert [r["node_id"] for r in result["results"]] == ["n4", "n3", "n2"]
    assert result["total"] == 5
    assert result["truncated"] is True


def test_long_text_snippet_is_centred_on_the_first_hit(
    seeded: tuple[ProjectRegistry, Project],
) -> None:
    registry, project = seeded
    store = registry.store
    _executed(store, project, "n1")
    _events(store, project, "n1", [_text("a" * 500 + " target " + "b" * 500)])
    snippet = NodeSearchIndex().search(
        store, project.id, store.list_nodes(project.id), "target"
    )["results"][0]["matches"][0]["snippet"]
    assert snippet.startswith("…") and snippet.endswith("…")
    assert "target" in snippet
    assert len(snippet) < 200


def test_search_endpoint(tmp_path: Path) -> None:
    os.environ["MINICLAW_HOME"] = str(tmp_path / "home")
    os.environ["MINICLAW_CONTEXT_HOME"] = str(tmp_path / "context")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with TestClient(create_app()) as client:
        res = client.post(
            "/sessions", json={"cwd": str(workspace), "model_preset_id": "opus-4-8"}
        )
        assert res.status_code == 200, res.text
        sid = res.json()["id"]
        res = client.post(
            f"/sessions/{sid}/planspaces/blank",
            json={"title": "Lane", "seed": "lane"},
        )
        assert res.status_code == 200, res.text

        empty = client.get(f"/sessions/{sid}/search", params={"q": "  "})
        assert empty.status_code == 200
        assert empty.json() == {"terms": [], "results": [], "total": 0, "truncated": False}

        nodes = client.get(f"/sessions/{sid}/nodes").json()
        assert nodes, "blank planspace seeds one virtual"
        node_id = nodes[0]["id"]
        res = client.patch(
            f"/sessions/{sid}/virtuals/{node_id}",
            json={"prompt_draft": "look for the Lantern"},
        )
        assert res.status_code == 200, res.text
        hit = client.get(f"/sessions/{sid}/search", params={"q": "lantern"}).json()
        assert [r["node_id"] for r in hit["results"]] == [node_id]
        assert hit["results"][0]["planspace_title"] == "Lane"

        assert client.get("/sessions/missing/search", params={"q": "x"}).status_code == 404
