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
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .contextspace import (
    add_planspace_to_binding,
    apply_planspace_status_ops,
    bootstrap_project_contextspace,
    describe_project_contextspace,
    ensure_project_binding,
    load_context_bundle_for_node,
    load_node_status_delta,
    load_planspace_view,
    read_project_file_role,
)
from .context_refresh import context_refresh_status, start_context_task
from .domain import Node
from .events import (
    InteractionResponse,
    Interrupt,
    ReplayRequest,
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
    preferred_language: str | None = None
    temporary: bool = False
    scenario_name: str | None = None
    name: str | None = None
    project_context_binding_id: str | None = None


class RenameSessionRequest(BaseModel):
    name: str


class UpdateSessionPreferencesRequest(BaseModel):
    preferred_language: str | None = None


class UpdateSessionContextRequest(BaseModel):
    project_context_binding_id: str | None = None
    active_planspace_id: str | None = None


class BootstrapSessionContextRequest(BaseModel):
    title: str | None = None
    planspace_slug: str | None = None
    binding_slug: str | None = None


class CreatePlanspaceRequest(BaseModel):
    user_seed: str
    needs_review: bool = False


class CreatePlanspaceResponse(BaseModel):
    planspace_id: str
    binding_id: str
    node_id: str


class SessionInfo(BaseModel):
    id: str
    created_at: float
    turns: int
    provider: str = "claude"
    preferred_language: str | None = None
    temporary: bool = False
    scenario_name: str | None = None
    name: str = ""
    project_context_binding_id: str | None = None
    # Opaque per-node canvas positions persisted from the frontend (PRD §5.1).
    layout_hints: dict[str, dict[str, float]] = Field(default_factory=dict)
    planspace_view: dict[str, dict[str, bool]] = Field(default_factory=dict)


class UpdateLayoutHintsRequest(BaseModel):
    # Merge semantics: `updates` overwrites per-id; `remove` deletes ids.
    updates: dict[str, dict[str, float]] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)


class UpdatePlanspaceViewRequest(BaseModel):
    planspaces: dict[str, dict[str, bool]] = Field(default_factory=dict)


class EventRecord(BaseModel):
    seq: int
    event: dict[str, Any]


class NodeDiffResponse(BaseModel):
    kind: str
    text: str
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


class PlanspaceStatusOpsRequest(BaseModel):
    operations: list[dict[str, Any]] = Field(default_factory=list)


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
                preferred_language=req.preferred_language,
                temporary=req.temporary,
                scenario_name=req.scenario_name,
                name=req.name or "",
                project_context_binding_id=req.project_context_binding_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc
        return _session_info(registry, project)

    @app.get("/sessions", response_model=list[SessionInfo])
    def list_sessions() -> list[SessionInfo]:
        return [
            _session_info(registry, p)
            for p in registry.list_projects()
        ]

    @app.patch("/sessions/{sid}", response_model=SessionInfo)
    def rename_session(sid: str, req: RenameSessionRequest) -> SessionInfo:
        project = registry.rename_project(sid, req.name)
        if project is None:
            raise HTTPException(404, "session not found")
        return _session_info(registry, project)

    @app.patch("/sessions/{sid}/preferences", response_model=SessionInfo)
    def update_session_preferences(
        sid: str,
        req: UpdateSessionPreferencesRequest,
    ) -> SessionInfo:
        kwargs: dict[str, Any] = {}
        if "preferred_language" in req.model_fields_set:
            kwargs["preferred_language"] = req.preferred_language
        try:
            project = registry.update_project_preferences(sid, **kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if project is None:
            raise HTTPException(404, "session not found")
        return _session_info(registry, project)

    @app.patch("/sessions/{sid}/layout-hints", response_model=SessionInfo)
    def update_layout_hints(
        sid: str,
        req: UpdateLayoutHintsRequest,
    ) -> SessionInfo:
        project = registry.update_layout_hints(sid, req.updates, remove=req.remove)
        if project is None:
            raise HTTPException(404, "session not found")
        return _session_info(registry, project)

    @app.patch("/sessions/{sid}/planspace-view", response_model=dict[str, Any])
    def update_planspace_view(
        sid: str,
        req: UpdatePlanspaceViewRequest,
    ) -> dict[str, Any]:
        project = registry.update_planspace_view(sid, req.planspaces)
        if project is None:
            raise HTTPException(404, "session not found")
        return describe_project_contextspace(project, store_root=registry.store.root)

    @app.delete("/sessions/{sid}")
    def delete_session(sid: str) -> dict[str, bool]:
        if not registry.delete_project(sid):
            raise HTTPException(404, "session not found")
        return {"ok": True}

    @app.get("/sessions/{sid}/contextspace", response_model=dict[str, Any])
    def get_session_contextspace(sid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        return describe_project_contextspace(project, store_root=registry.store.root)

    @app.patch("/sessions/{sid}/contextspace", response_model=dict[str, Any])
    def update_session_contextspace(
        sid: str,
        req: UpdateSessionContextRequest,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if "project_context_binding_id" in req.model_fields_set:
            kwargs["project_context_binding_id"] = req.project_context_binding_id
        if "active_planspace_id" in req.model_fields_set:
            kwargs["active_planspace_id"] = req.active_planspace_id
        project = registry.update_project_context(sid, **kwargs)
        if project is None:
            raise HTTPException(404, "session not found")
        return describe_project_contextspace(project, store_root=registry.store.root)

    @app.post("/sessions/{sid}/contextspace/bootstrap", response_model=dict[str, Any])
    def bootstrap_session_contextspace(
        sid: str,
        req: BootstrapSessionContextRequest,
    ) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        result = bootstrap_project_contextspace(
            project,
            store_root=registry.store.root,
            title=req.title,
            planspace_slug=req.planspace_slug,
            binding_slug=req.binding_slug,
        )
        project = registry.update_project_context(
            sid,
            project_context_binding_id=result["binding_id"],
            active_planspace_id=result["planspace_id"],
        )
        if project is None:
            raise HTTPException(404, "session not found")
        response = describe_project_contextspace(project, store_root=registry.store.root)
        response["bootstrap"] = result
        return response

    @app.post("/sessions/{sid}/planspaces", response_model=CreatePlanspaceResponse)
    async def create_planspace(
        sid: str,
        req: CreatePlanspaceRequest,
    ) -> CreatePlanspaceResponse:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        user_seed = req.user_seed.strip()
        if not user_seed:
            raise HTTPException(400, "user_seed is required")
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        created: list[str] = []
        try:
            binding = ensure_project_binding(
                project,
                store_root=registry.store.root,
                created=created,
            )
            title = _direction_title_from_seed(user_seed)
            planspace_id = add_planspace_to_binding(
                binding.id,
                title=title,
                planspace_slug=title,
                store_root=registry.store.root,
                created=created,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc

        updated = registry.update_project_context(
            sid,
            project_context_binding_id=binding.id,
            active_planspace_id=planspace_id,
        )
        if updated is None:
            raise HTTPException(404, "session not found")

        runner = registry.start_node(
            sid,
            _concierge_bootstrap_prompt(user_seed),
            needs_review=req.needs_review,
        )
        if runner is None:
            raise HTTPException(409, "turn in progress or invalid project")
        return CreatePlanspaceResponse(
            planspace_id=planspace_id,
            binding_id=binding.id,
            node_id=runner.node.id,
        )

    @app.get("/sessions/{sid}/files", response_model=dict[str, Any])
    def get_session_file(
        sid: str,
        role: str,
        planspace_id: str | None = None,
    ) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if role not in {"status", "plan", "context"}:
            raise HTTPException(400, "role must be status, plan, or context")
        if role in {"status", "plan"} and not planspace_id:
            raise HTTPException(400, "planspace_id is required for this role")
        result = read_project_file_role(
            project,
            role=role,
            planspace_id=planspace_id,
            store_root=registry.store.root,
        )
        if result is None:
            raise HTTPException(404, "file not found")
        return result

    @app.post("/sessions/{sid}/context/init", response_model=dict[str, Any])
    async def init_project_context(sid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        try:
            start_context_task(project, mode="init")
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return describe_project_contextspace(project, store_root=registry.store.root)

    @app.post("/sessions/{sid}/context/refresh", response_model=dict[str, Any])
    async def refresh_project_context(sid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        try:
            start_context_task(project, mode="refresh")
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return describe_project_contextspace(project, store_root=registry.store.root)

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

    @app.get("/sessions/{sid}/nodes/{nid}/status-delta", response_model=dict[str, Any])
    def get_node_status_delta(sid: str, nid: str) -> dict[str, Any]:
        if registry.get_project(sid) is None:
            raise HTTPException(404, "session not found")
        if registry.get_node(sid, nid) is None:
            raise HTTPException(404, "node not found")
        delta = load_node_status_delta(sid, nid, store_root=registry.store.root)
        if delta is None:
            raise HTTPException(404, "status delta not found")
        return delta

    @app.get(
        "/sessions/{sid}/planspaces/{planspace_id}/status",
        response_model=dict[str, Any],
    )
    def get_planspace_status(sid: str, planspace_id: str) -> dict[str, Any]:
        if registry.get_project(sid) is None:
            raise HTTPException(404, "session not found")
        view = load_planspace_view(planspace_id, store_root=registry.store.root)
        if view is None:
            raise HTTPException(404, "planspace not found")
        return view

    @app.patch(
        "/sessions/{sid}/planspaces/{planspace_id}/status",
        response_model=dict[str, Any],
    )
    def patch_planspace_status(
        sid: str,
        planspace_id: str,
        req: PlanspaceStatusOpsRequest,
    ) -> dict[str, Any]:
        if registry.get_project(sid) is None:
            raise HTTPException(404, "session not found")
        view = apply_planspace_status_ops(
            planspace_id,
            req.operations,
            store_root=registry.store.root,
        )
        if view is None:
            raise HTTPException(404, "planspace not found")
        if view.get("errors"):
            raise HTTPException(400, "; ".join(view["errors"]))
        return view

    @app.get("/sessions/{sid}/nodes/{nid}/context-bundle", response_model=dict[str, Any])
    def get_node_context_bundle(sid: str, nid: str) -> dict[str, Any]:
        if registry.get_project(sid) is None:
            raise HTTPException(404, "session not found")
        node = registry.get_node(sid, nid)
        if node is None:
            raise HTTPException(404, "node not found")
        bundle = load_context_bundle_for_node(node, store_root=registry.store.root)
        if bundle is None:
            raise HTTPException(404, "context bundle not found")
        return bundle

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
        return _session_info(registry, project)

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
        project = registry.get_project(sid)
        if project is None:
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
                    if _context_task_running(project.id):
                        await _send(send_now, {
                            "type": "error",
                            "message": "context refresh in progress",
                        })
                        continue
                    runner = registry.start_node(
                        sid,
                        msg.text,
                        resume_from_node_id=msg.resume_from_node_id,
                        needs_review=msg.needs_review,
                        extra_planspace_loads=msg.extra_planspace_loads,
                    )
                    if runner is None:
                        await _send(send_now, {
                            "type": "error",
                            "message": "turn in progress or invalid resume source",
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


def _session_info(registry: ProjectRegistry, project: Any) -> SessionInfo:
    return SessionInfo(
        id=project.id,
        created_at=project.created_at,
        turns=registry.turn_count(project.id),
        provider=project.provider,
        preferred_language=project.preferred_language,
        temporary=project.temporary,
        scenario_name=project.scenario_name,
        name=project.name,
        project_context_binding_id=project.project_context_binding_id,
        layout_hints=project.layout_hints,
        planspace_view=project.planspace_view,
    )


async def _send(
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
    payload: dict[str, Any],
) -> None:
    try:
        await on_event(payload)
    except Exception:  # noqa: BLE001
        pass


def _context_task_running(project_id: str) -> bool:
    return bool(context_refresh_status(project_id).get("running"))


def _direction_title_from_seed(user_seed: str) -> str:
    first_line = next((line.strip() for line in user_seed.splitlines() if line.strip()), "")
    text = re.sub(r"\s+", " ", first_line).strip()
    if not text:
        return "New direction"
    words = text.split(" ")
    title = " ".join(words[:8]).strip(" .,:;")
    return title[:72] or "New direction"


def _concierge_bootstrap_prompt(user_seed: str) -> str:
    prompt_path = Path(__file__).with_name("prompts") / "concierge_bootstrap.md"
    try:
        template = prompt_path.read_text(encoding="utf-8")
    except OSError:
        template = (
            "# Direction concierge bootstrap\n\n"
            "The user is starting a new direction. Read <user_seed>, infer the "
            "goal, current state, initial open questions, and any decisions. "
            "Use the standard ask-user inline gate only for load-bearing facts "
            "you cannot infer. Before finishing, write the required "
            "planspace-update JSON artifact with STATUS.md operations: "
            "rewrite_current_state, add_open_question, add_decision, "
            "add_out_of_scope, or append_body.\n\n"
            "<user_seed>\n{user_seed}\n</user_seed>\n"
        )
    return template.replace("{user_seed}", user_seed.strip())


app = create_app()
