"""FastAPI app: session-shaped REST + WebSocket gateway over ProjectRegistry.

The wire protocol is intentionally unchanged from before the Phase 0
refactor: a "session" id is a project id; each ``user_message`` spawns
a new agent node. Conversation continuation is explicit via an
optional resume source, not inherited from timeline adjacency.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .artifacts import load_node_artifact
from .domain import Node
from .events import (
    InteractionResponse,
    Interrupt,
    ReplayRequest,
    StartGateNode,
    UserMessage,
)
from .git_state import node_diff
from .registry import ProjectRegistry
from .replay import LiveReplayBuffer
from .scenarios import (
    ScenarioError,
    launch_scenario,
    list_scenarios,
    load_scenario,
    run_verify,
)

logger = logging.getLogger(__name__)


class CreateSessionRequest(BaseModel):
    cwd: str | None = None
    model: str | None = None
    model_provider: str | None = None
    provider: str | None = None
    auto_commit: bool | None = None
    temporary: bool = False
    scenario_name: str | None = None
    name: str | None = None


class RenameSessionRequest(BaseModel):
    name: str


class SessionInfo(BaseModel):
    id: str
    created_at: float
    turns: int
    provider: str = "claude"
    temporary: bool = False
    scenario_name: str | None = None
    name: str = ""


class EventRecord(BaseModel):
    seq: int
    event: dict[str, Any]


class NodeDiffResponse(BaseModel):
    kind: str
    text: str
    error: str | None = None


class NodeArtifactResponse(BaseModel):
    kind: str
    path: str | None
    exists: bool
    content: str | None = None
    data: Any | None = None
    error: str | None = None


class ScenarioSummary(BaseModel):
    name: str
    brief: str
    providers: list[str]
    auto_commit: bool
    node_count: int


class ScenarioDetail(BaseModel):
    name: str
    brief: str
    providers: list[str]
    auto_commit: bool
    node_count: int
    acceptance: str


class ScenarioRunRequest(BaseModel):
    provider: str


class VerifyResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool


def create_app() -> FastAPI:
    app = FastAPI(title="MiniClaw2")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    registry = ProjectRegistry()

    @app.post("/sessions", response_model=SessionInfo)
    def create_session(req: CreateSessionRequest) -> SessionInfo:
        try:
            project = registry.create_project(
                cwd=None if req.temporary else (req.cwd or os.getcwd()),
                model=req.model,
                model_provider=req.model_provider,
                provider=req.provider,
                auto_commit=req.auto_commit,
                temporary=req.temporary,
                scenario_name=req.scenario_name,
                name=req.name or "",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc
        return SessionInfo(
            id=project.id,
            created_at=project.created_at,
            turns=0,
            provider=project.provider,
            temporary=project.temporary,
            scenario_name=project.scenario_name,
            name=project.name,
        )

    @app.get("/sessions", response_model=list[SessionInfo])
    def list_sessions() -> list[SessionInfo]:
        return [
            SessionInfo(
                id=p.id,
                created_at=p.created_at,
                turns=registry.turn_count(p.id),
                provider=p.provider,
                temporary=p.temporary,
                scenario_name=p.scenario_name,
                name=p.name,
            )
            for p in registry.list_projects()
        ]

    @app.patch("/sessions/{sid}", response_model=SessionInfo)
    def rename_session(sid: str, req: RenameSessionRequest) -> SessionInfo:
        project = registry.rename_project(sid, req.name)
        if project is None:
            raise HTTPException(404, "session not found")
        return SessionInfo(
            id=project.id,
            created_at=project.created_at,
            turns=registry.turn_count(project.id),
            provider=project.provider,
            temporary=project.temporary,
            scenario_name=project.scenario_name,
            name=project.name,
        )

    @app.delete("/sessions/{sid}")
    def delete_session(sid: str) -> dict[str, bool]:
        if not registry.delete_project(sid):
            raise HTTPException(404, "session not found")
        return {"ok": True}

    @app.get("/sessions/{sid}/nodes", response_model=list[Node])
    def list_nodes(sid: str) -> list[Node]:
        nodes = registry.list_nodes(sid)
        if nodes is None:
            raise HTTPException(404, "session not found")
        return nodes

    @app.get("/sessions/{sid}/nodes/{nid}", response_model=Node)
    def get_node(sid: str, nid: str) -> Node:
        if registry.get_project(sid) is None:
            raise HTTPException(404, "session not found")
        node = registry.get_node(sid, nid)
        if node is None:
            raise HTTPException(404, "node not found")
        return node

    @app.get("/sessions/{sid}/nodes/{nid}/events", response_model=list[EventRecord])
    def get_node_events(sid: str, nid: str, since_seq: int = 0) -> list[EventRecord]:
        records = registry.replay_node_events(sid, nid, since_seq)
        if records is None:
            if registry.get_project(sid) is None:
                raise HTTPException(404, "session not found")
            raise HTTPException(404, "node not found")
        return [EventRecord(**record) for record in records]

    @app.get("/sessions/{sid}/nodes/{nid}/diff", response_model=NodeDiffResponse)
    def get_node_diff(sid: str, nid: str) -> NodeDiffResponse:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        node = registry.get_node(sid, nid)
        if node is None:
            raise HTTPException(404, "node not found")
        diff = node_diff(project.root_path, node.commit_before, node.commit_after)
        return NodeDiffResponse(kind=diff.kind, text=diff.text, error=diff.error)

    @app.get("/sessions/{sid}/nodes/{nid}/artifact", response_model=NodeArtifactResponse)
    def get_node_artifact(sid: str, nid: str) -> NodeArtifactResponse:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        node = registry.get_node(sid, nid)
        if node is None:
            raise HTTPException(404, "node not found")
        artifact = load_node_artifact(project.root_path, node)
        return NodeArtifactResponse(
            kind=artifact.kind,
            path=artifact.path,
            exists=artifact.exists,
            content=artifact.content,
            data=artifact.data,
            error=artifact.error,
        )

    @app.get("/scenarios", response_model=list[ScenarioSummary])
    def list_scenarios_endpoint() -> list[ScenarioSummary]:
        return [ScenarioSummary(**s.metadata()) for s in list_scenarios()]

    @app.get("/scenarios/{name}", response_model=ScenarioDetail)
    def get_scenario(name: str) -> ScenarioDetail:
        try:
            scenario = load_scenario(name)
        except ScenarioError as exc:
            raise HTTPException(404, str(exc)) from exc
        meta = scenario.metadata()
        return ScenarioDetail(**meta, acceptance=scenario.acceptance)

    @app.post("/scenarios/{name}/run", response_model=SessionInfo)
    async def run_scenario(name: str, req: ScenarioRunRequest) -> SessionInfo:
        # async so that registry.start_node's asyncio.create_task sees an
        # active event loop (the runner runs concurrently with the request).
        try:
            project, _ = launch_scenario(name, req.provider, registry)
        except ScenarioError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc
        return SessionInfo(
            id=project.id,
            created_at=project.created_at,
            turns=0,
            provider=project.provider,
            temporary=project.temporary,
            scenario_name=project.scenario_name,
            name=project.name,
        )

    @app.post("/sessions/{sid}/verify", response_model=VerifyResponse)
    async def verify_session(sid: str) -> VerifyResponse:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if not project.scenario_name:
            raise HTTPException(400, "project has no associated scenario")
        try:
            # run_verify blocks on subprocess.run; offload so the event loop
            # stays responsive for WS observers.
            result = await asyncio.to_thread(run_verify, project)
        except ScenarioError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return VerifyResponse(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
        )

    @app.websocket("/ws/{sid}")
    async def ws(websocket: WebSocket, sid: str) -> None:
        if registry.get_project(sid) is None:
            await websocket.close(code=4404, reason="session not found")
            return

        await websocket.accept()
        send_lock = asyncio.Lock()
        replay_buffer = LiveReplayBuffer()

        async def send_now(event: dict[str, Any]) -> None:
            async with send_lock:
                await websocket.send_json(event)

        async def on_event(event: dict[str, Any]) -> None:
            ready_event = replay_buffer.push_live(event)
            if ready_event is not None:
                await send_now(ready_event)

        async def mark_live_ready(
            *,
            replay_node_id: str | None = None,
            replayed_through_seq: int | None = None,
        ) -> None:
            for event in replay_buffer.mark_live_ready(
                replay_node_id=replay_node_id,
                replayed_through_seq=replayed_through_seq,
            ):
                await send_now(event)

        observer_token = registry.attach_observer(sid, on_event)
        if observer_token is None:
            await websocket.close(code=4404, reason="session not found")
            return

        try:
            while True:
                raw = await websocket.receive_json()
                msg_type = raw.get("type")

                if msg_type == "user_message":
                    await mark_live_ready()
                    msg = UserMessage(**raw)
                    runner = registry.start_node(
                        sid,
                        msg.text,
                        resume_from_node_id=msg.resume_from_node_id,
                        output_kind=msg.output_kind,
                        output_path=msg.output_path,
                    )
                    if runner is None:
                        await _send(send_now, {
                            "type": "error",
                            "message": "turn in progress or invalid resume source",
                        })
                        continue

                elif msg_type == "start_gate_node":
                    await mark_live_ready()
                    gmsg = StartGateNode(**raw)
                    runner = registry.start_gate_node(sid, gmsg.prompt, gmsg.contract)
                    if runner is None:
                        await _send(send_now, {
                            "type": "error",
                            "message": "turn in progress",
                        })
                        continue

                elif msg_type == "interaction_response":
                    await mark_live_ready()
                    resp = InteractionResponse(**raw)
                    ok = registry.resolve_gate(
                        sid,
                        resp.id,
                        allow=resp.allow,
                        message=resp.message,
                        updated_input=resp.updated_input,
                        decision=resp.decision,
                        response=resp.response,
                        scope=resp.scope,
                        interrupt=resp.interrupt,
                        permission_mode=resp.permission_mode,
                        clear_context=resp.clear_context,
                    )
                    if not ok:
                        await _send(send_now, {
                            "type": "error",
                            "message": f"no pending interaction with id {resp.id}",
                        })

                elif msg_type == "interrupt":
                    await mark_live_ready()
                    Interrupt(**raw)  # validate shape
                    registry.interrupt(sid)

                elif msg_type == "replay_request":
                    req = ReplayRequest(**raw)
                    replay_node_id = req.node_id
                    if not replay_node_id:
                        latest = registry.store.latest_node(sid)
                        replay_node_id = latest.id if latest is not None else ""
                    records = registry.replay_node_events(
                        sid, replay_node_id, req.since_seq
                    )
                    if records is None:
                        records = []
                    replayed_through_seq = req.since_seq
                    for rec in records:
                        replayed_through_seq = max(replayed_through_seq, rec["seq"])
                        await _send(send_now, rec["event"])
                    await mark_live_ready(
                        replay_node_id=replay_node_id or None,
                        replayed_through_seq=replayed_through_seq,
                    )

                else:
                    await mark_live_ready()
                    await _send(send_now, {
                        "type": "error",
                        "message": f"unknown type: {msg_type}",
                    })

        except WebSocketDisconnect:
            pass
        finally:
            # Don't cancel the runner on WS disconnect — it should finish
            # and persist final state. A reconnect attaches a fresh observer
            # and uses replay_request to fill any gap from the JSONL log.
            registry.detach_observer(sid, observer_token)

    return app


async def _send(
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
    payload: dict[str, Any],
) -> None:
    try:
        await on_event(payload)
    except Exception:  # noqa: BLE001
        pass


app = create_app()
