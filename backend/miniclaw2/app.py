"""FastAPI app: session-shaped REST + WebSocket gateway over ProjectRegistry.

A "session" id is a project id; each ``user_message`` spawns a new
agent node; resume continuation is explicit via an optional resume
source.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contextspace import (
    delete_skill,
    describe_project_contextspace,
    list_skills,
    load_context_bundle_for_node,
    read_project_context,
)
from .context_refresh import cancel_context_task, context_refresh_status, start_context_task
from .domain import Node
from .events import (
    InteractionResponse,
    Interrupt,
    ReplayRequest,
    UserMessage,
)
from .git_state import node_diff
from .language import project_preferred_language
from .model_catalog import list_model_presets
from .providers import GateTimeoutError
from .providers.claude_native import hook_runtime
from .providers.claude_native.hook_installer import install_hooks
from .registry import ProjectRegistry
from .replay import LiveReplayBuffer
from .templates import (
    SerializerError,
    TemplateError,
    apply_user_template,
    delete_user_template,
    launch_template,
    list_templates,
    list_user_templates,
    load_template,
    load_user_template,
    serialize_selection,
)

logger = logging.getLogger(__name__)

# Outer safety net for a hung ask dispatcher. Must respond before the hook
# bridge's 600s HTTP timeout so the bridge sees a structured failure instead
# of a dead socket. Slow humans are handled one layer down: the runner-side
# ask-gate supervision (providers/claude._ASK_GATE_TIMEOUT_SECONDS, 570s)
# fires first and interrupts the session with an honest error.
_HOOK_ASK_TIMEOUT_SECONDS = 590.0


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cwd: str | None = None
    model_preset_id: str | None = None
    auto_commit: bool | None = None
    preferred_language: str | None = None
    temporary: bool = False
    name: str | None = None
    project_context_binding_id: str | None = None
    create_missing_cwd: bool = False


class RenameSessionRequest(BaseModel):
    name: str


class UpdateSessionPreferencesRequest(BaseModel):
    preferred_language: str | None = None


class UpdateSessionContextRequest(BaseModel):
    project_context_binding_id: str | None = None
    active_planspace_id: str | None = None


class SessionInfo(BaseModel):
    id: str
    created_at: float
    turns: int
    model_preset_id: str
    provider: str = "codex"
    preferred_language: str | None = None
    temporary: bool = False
    template_id: str | None = None
    name: str = ""
    project_context_binding_id: str | None = None
    layout_hints: dict[str, dict[str, float]] = Field(default_factory=dict)
    layout_viewport: dict[str, float] | None = None
    planspace_view: dict[str, dict[str, bool]] = Field(default_factory=dict)


class UpdateLayoutHintsRequest(BaseModel):
    updates: dict[str, dict[str, float]] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)
    layout_viewport: dict[str, float] | None = None


class UpdatePlanspaceViewRequest(BaseModel):
    planspaces: dict[str, dict[str, bool]] = Field(default_factory=dict)


class CreatePlanspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    seed: str | None = None
    user_seed: str | None = None
    mode: str | None = None
    model_preset_id: str | None = None


class CreateBlankPlanspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    seed: str
    mode: str | None = None
    model_preset_id: str | None = None


class UpdatePlanspaceModeRequest(BaseModel):
    mode: str


class UpdateVirtualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_draft: str | None = None
    category: str | None = None
    subtype: str | None = None
    brief: dict[str, Any] | None = None
    motivation: str | None = None
    scheduled_deps: list[str] | None = None
    pending_extra_skills: list[str] | None = None
    model_preset_id: str | None = None
    obsolete_reason: str | None = None


class CreateVirtualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_draft: str
    category: str | None = None
    subtype: str | None = None
    brief: dict[str, Any] | None = None
    motivation: str | None = None
    scheduled_deps: list[str] | None = None
    pending_extra_skills: list[str] | None = None
    agent_op_kind: str | None = None
    model_preset_id: str | None = None
    planspace_id: str | None = None
    parent_node_id: str | None = None
    resume_from_node_id: str | None = None


class EventRecord(BaseModel):
    seq: int
    event: dict[str, Any]


class NodeDiffResponse(BaseModel):
    kind: str
    text: str
    error: str | None = None


class TemplateSummary(BaseModel):
    name: str
    brief: str
    allowed_model_preset_ids: list[str]
    auto_commit: bool
    node_count: int
    nodes: list[dict[str, Any]] = Field(default_factory=list)


class TemplateDetail(BaseModel):
    name: str
    brief: str
    allowed_model_preset_ids: list[str]
    auto_commit: bool
    node_count: int
    nodes: list[dict[str, Any]] = Field(default_factory=list)


class TemplateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_preset_id: str


class SaveUserTemplateRequest(BaseModel):
    name: str
    brief: str = ""
    node_ids: list[str] = Field(default_factory=list)


class SaveUserTemplateResponse(BaseModel):
    slug: str
    name: str
    brief: str
    node_count: int


class ApplyUserTemplateRequest(BaseModel):
    anchor_node_id: str | None = None


class ApplyUserTemplateResponse(BaseModel):
    node_ids: list[str]


def create_app(registry: ProjectRegistry | None = None) -> FastAPI:
    from contextlib import asynccontextmanager

    registry = registry if registry is not None else ProjectRegistry(initialize=False)

    def initialize_registry() -> None:
        initialize = getattr(registry, "initialize", None)
        if initialize is not None:
            initialize()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        initialize_registry()
        # Generate the shared token before spawning any claude PTYs, and
        # merge the AskUserQuestion / SessionStart hooks into the user's
        # ~/.claude/settings.json. Both are idempotent.
        hook_runtime.ensure_token()
        _set_hook_port_from_env()
        try:
            install_hooks()
        except Exception:  # noqa: BLE001
            logger.exception("failed to install claude hooks")
        yield

    app = FastAPI(title="MiniClaw2", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/model-presets", response_model=list[dict[str, Any]])
    def list_model_presets_endpoint() -> list[dict[str, Any]]:
        return [preset.metadata() for preset in list_model_presets()]

    @app.middleware("http")
    async def record_hook_port(request: Request, call_next):
        initialize_registry()
        _record_hook_port_from_scope(request.scope)
        return await call_next(request)

    def _require_hook_token(request: Request) -> None:
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {hook_runtime.token()}"
        if auth != expected:
            raise HTTPException(status_code=403, detail="invalid hook token")

    @app.post("/hook/ask")
    async def hook_ask(request: Request) -> JSONResponse:
        _require_hook_token(request)
        body = await request.json()
        node_id = body.get("node_id")
        payload = body.get("payload")
        if not isinstance(node_id, str) or not isinstance(payload, dict):
            raise HTTPException(400, "invalid hook payload")
        dispatcher = hook_runtime.get_ask_dispatcher(node_id)
        if dispatcher is None:
            raise HTTPException(404, f"no active session for node {node_id!r}")
        try:
            directive = await asyncio.wait_for(
                dispatcher(payload),
                timeout=_HOOK_ASK_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("hook_ask dispatcher timed out for node %s", node_id)
            return JSONResponse(
                status_code=504,
                content={"error": "ask dispatch timed out"},
            )
        except GateTimeoutError as exc:
            logger.warning("hook_ask gate timed out for node %s: %s", node_id, exc)
            return JSONResponse(status_code=504, content={"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("hook_ask dispatcher failed")
            raise HTTPException(500, f"ask dispatch failed: {exc}") from exc
        return JSONResponse(content=directive)

    @app.post("/hook/session-ready")
    async def hook_session_ready(request: Request) -> JSONResponse:
        _require_hook_token(request)
        body = await request.json()
        session_id = body.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise HTTPException(400, "session_id required")
        hook_runtime.signal_session_ready(session_id)
        return JSONResponse({"ok": True})

    @app.post("/sessions", response_model=SessionInfo)
    def create_session(req: CreateSessionRequest) -> SessionInfo:
        try:
            project = registry.create_project(
                cwd=None if req.temporary else (req.cwd or os.getcwd()),
                model_preset_id=req.model_preset_id,
                auto_commit=req.auto_commit,
                preferred_language=req.preferred_language,
                temporary=req.temporary,
                name=req.name or "",
                project_context_binding_id=req.project_context_binding_id,
                create_missing_cwd=req.create_missing_cwd,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc
        return _session_info(registry, project)

    @app.get("/sessions", response_model=list[SessionInfo])
    def list_sessions() -> list[SessionInfo]:
        return [_session_info(registry, p) for p in registry.list_projects()]

    @app.get("/sessions/{sid}", response_model=SessionInfo)
    def get_session(sid: str) -> SessionInfo:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        return _session_info(registry, project)

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
        project = registry.update_layout_hints(
            sid,
            req.updates,
            remove=req.remove,
            layout_viewport=req.layout_viewport,
        )
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

    @app.get("/sessions/{sid}/files", response_model=dict[str, Any])
    def get_session_file(sid: str, role: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if role != "context":
            raise HTTPException(400, "role must be 'context'")
        result = read_project_context(project)
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

    @app.post("/sessions/{sid}/planspaces", response_model=dict[str, Any])
    async def create_planspace(
        sid: str,
        req: CreatePlanspaceRequest,
        response: Response,
    ) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        if (
            req.seed is not None
            and req.user_seed is not None
            and req.seed != req.user_seed
        ):
            raise HTTPException(400, "seed and deprecated user_seed disagree")
        seed = req.seed if req.seed is not None else req.user_seed
        if req.user_seed is not None:
            response.headers["Deprecation"] = "true"
            response.headers["Warning"] = (
                '299 MiniClaw2 "user_seed is deprecated; use seed"'
            )
        if seed is None or not seed.strip():
            raise HTTPException(400, "seed must be non-empty")
        try:
            runner = registry.create_planspace_and_launch_concierge(
                sid,
                title=req.title.strip(),
                seed=seed,
                mode=req.mode,
                model_preset_id=req.model_preset_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if runner is None:
            raise HTTPException(409, "failed to launch concierge")
        contextspace = describe_project_contextspace(
            project, store_root=registry.store.root
        )
        contextspace["node_id"] = runner.node.id
        contextspace["planspace_id"] = runner.node.planspace_id
        contextspace["binding_id"] = contextspace.get("resolved_binding_id")
        return contextspace

    @app.post("/sessions/{sid}/planspaces/blank", response_model=dict[str, Any])
    async def create_blank_planspace(
        sid: str,
        req: CreateBlankPlanspaceRequest,
    ) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        if not req.seed.strip():
            raise HTTPException(400, "seed must be non-empty")
        try:
            node = registry.create_blank_planspace(
                sid,
                title=(req.title or "").strip(),
                seed=req.seed,
                mode=req.mode,
                model_preset_id=req.model_preset_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if node is None:
            raise HTTPException(409, "project is busy")
        contextspace = describe_project_contextspace(
            project, store_root=registry.store.root
        )
        contextspace["node_id"] = node.id
        contextspace["planspace_id"] = node.planspace_id
        contextspace["binding_id"] = contextspace.get("resolved_binding_id")
        return contextspace

    @app.patch("/sessions/{sid}/planspaces/{planspace_id}/mode", response_model=dict[str, Any])
    async def update_planspace_mode(
        sid: str,
        planspace_id: str,
        req: UpdatePlanspaceModeRequest,
    ) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        try:
            mode = registry.update_planspace_mode(sid, planspace_id, req.mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if mode is None:
            raise HTTPException(404, "session not found")
        return describe_project_contextspace(project, store_root=registry.store.root)

    @app.post("/sessions/{sid}/virtuals", response_model=dict[str, Any])
    async def create_virtual(
        sid: str,
        req: CreateVirtualRequest,
    ) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        try:
            node = registry.create_virtual(
                sid,
                prompt_draft=req.prompt_draft,
                category=req.category,
                subtype=req.subtype,
                brief=req.brief,
                motivation=req.motivation,
                scheduled_deps=req.scheduled_deps,
                pending_extra_skills=req.pending_extra_skills,
                agent_op_kind=req.agent_op_kind,
                model_preset_id=req.model_preset_id,
                planspace_id=req.planspace_id,
                parent_node_id=req.parent_node_id,
                resume_from_node_id=req.resume_from_node_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if node is None:
            raise HTTPException(409, "project is busy")
        return {"ok": True, "node_id": node.id, "node": node.model_dump()}

    @app.delete("/sessions/{sid}/virtuals/{vid}", status_code=204)
    async def delete_virtual(sid: str, vid: str) -> Response:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        try:
            deleted, blockers = registry.delete_virtual(sid, vid)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if blockers:
            raise HTTPException(409, {"blockers": blockers})
        if not deleted:
            raise HTTPException(404, "virtual not found")
        return Response(status_code=204)

    @app.post(
        "/sessions/{sid}/virtuals/{vid}/promote", response_model=dict[str, Any]
    )
    async def promote_virtual(sid: str, vid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        runner = registry.promote_virtual(sid, vid)
        if runner is None:
            raise HTTPException(
                409,
                "virtual cannot be promoted (missing, obsolete, deps not "
                "terminal, or project busy)",
            )
        return {
            "ok": True,
            "node_id": runner.node.id,
            "node": runner.node.model_dump(),
        }

    @app.patch(
        "/sessions/{sid}/virtuals/{vid}", response_model=dict[str, Any]
    )
    async def update_virtual(
        sid: str,
        vid: str,
        req: UpdateVirtualRequest,
    ) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        kwargs: dict[str, Any] = {}
        for field in req.model_fields_set:
            kwargs[field] = getattr(req, field)
        try:
            node = registry.update_virtual(sid, vid, **kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if node is None:
            if registry.get_node(sid, vid) is None:
                raise HTTPException(404, "virtual not found")
            raise HTTPException(409, "node is not an editable virtual or project is busy")
        return {"ok": True, "node_id": node.id, "node": node.model_dump()}

    @app.post("/sessions/{sid}/context/cancel", response_model=dict[str, Any])
    async def cancel_project_context(sid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        await cancel_context_task(project.id)
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

    @app.post("/sessions/{sid}/nodes/{nid}/rerun", response_model=dict[str, Any])
    async def rerun_node(sid: str, nid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        try:
            node = registry.rerun_node(sid, nid)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if node is None:
            if registry.get_node(sid, nid) is None:
                raise HTTPException(404, "node not found")
            raise HTTPException(409, "cannot rerun this node")
        return {"ok": True, "node_id": node.id, "node": node.model_dump()}

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

    @app.get("/sessions/{sid}/nodes/{nid}/preview", response_model=dict[str, Any])
    def get_node_preview(sid: str, nid: str) -> dict[str, Any]:
        if registry.get_project(sid) is None:
            raise HTTPException(404, "session not found")
        if registry.get_node(sid, nid) is None:
            raise HTTPException(404, "node not found")
        text = registry.store.read_node_preview(sid, nid)
        if text is None:
            raise HTTPException(404, "preview not yet written")
        return {"text": text}

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

    @app.get("/templates", response_model=list[TemplateSummary])
    def list_templates_endpoint() -> list[TemplateSummary]:
        return [TemplateSummary(**s.metadata()) for s in list_templates()]

    @app.get("/templates/{name}", response_model=TemplateDetail)
    def get_template(name: str) -> TemplateDetail:
        try:
            template = load_template(name)
        except TemplateError as exc:
            raise HTTPException(404, str(exc)) from exc
        return TemplateDetail(**template.metadata())

    @app.post("/templates/{name}/run", response_model=SessionInfo)
    async def run_template(name: str, req: TemplateRunRequest) -> SessionInfo:
        try:
            project, _ = launch_template(name, req.model_preset_id, registry)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc
        return _session_info(registry, project)

    @app.get("/user-templates", response_model=list[TemplateSummary])
    def list_user_templates_endpoint() -> list[TemplateSummary]:
        return [
            TemplateSummary(**tpl.metadata())
            for tpl in list_user_templates(registry.store.root)
        ]

    @app.get("/user-templates/{slug}", response_model=TemplateDetail)
    def get_user_template(slug: str) -> TemplateDetail:
        try:
            template = load_user_template(slug, registry.store.root)
        except TemplateError as exc:
            raise HTTPException(404, str(exc)) from exc
        return TemplateDetail(**template.metadata())

    @app.delete("/user-templates/{slug}", status_code=204)
    def delete_user_template_endpoint(slug: str) -> Response:
        if not delete_user_template(slug, registry.store.root):
            raise HTTPException(404, f"user template not found: {slug}")
        return Response(status_code=204)

    @app.get("/skills", response_model=list[dict[str, Any]])
    def list_skills_endpoint() -> list[dict[str, Any]]:
        return list_skills(store_root=registry.store.root)

    @app.delete("/skills/{slug}", status_code=204)
    def delete_skill_endpoint(slug: str) -> Response:
        try:
            removed = delete_skill(slug, store_root=registry.store.root)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not removed:
            raise HTTPException(404, f"skill not found: {slug}")
        return Response(status_code=204)

    @app.post(
        "/sessions/{sid}/user-templates",
        response_model=SaveUserTemplateResponse,
    )
    async def save_user_template(
        sid: str,
        req: SaveUserTemplateRequest,
    ) -> SaveUserTemplateResponse:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        try:
            template = serialize_selection(
                registry.store,
                sid,
                req.node_ids,
                name=req.name,
                brief=req.brief,
                store_root=registry.store.root,
            )
        except SerializerError as exc:
            raise HTTPException(400, str(exc)) from exc
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
        # ``template.root`` is the on-disk directory; its basename is the slug.
        slug = template.root.name
        return SaveUserTemplateResponse(
            slug=slug,
            name=template.name,
            brief=template.brief,
            node_count=len(template.nodes),
        )

    @app.post(
        "/sessions/{sid}/user-templates/{slug}/apply",
        response_model=ApplyUserTemplateResponse,
    )
    async def apply_user_template_endpoint(
        sid: str,
        slug: str,
        req: ApplyUserTemplateRequest,
    ) -> ApplyUserTemplateResponse:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        try:
            template = load_user_template(slug, registry.store.root)
        except TemplateError as exc:
            raise HTTPException(404, str(exc)) from exc
        try:
            created = apply_user_template(
                template,
                project,
                registry,
                anchor_node_id=req.anchor_node_id,
            )
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
        registry.promote_next_virtual(sid)
        return ApplyUserTemplateResponse(node_ids=[n.id for n in created])

    @app.websocket("/ws/{sid}")
    async def ws(websocket: WebSocket, sid: str) -> None:
        initialize_registry()
        _record_hook_port_from_scope(websocket.scope)
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
                    try:
                        msg = UserMessage(**raw)
                    except ValidationError as exc:
                        await _send(send_now, {
                            "type": "error",
                            "message": str(exc),
                        })
                        continue
                    if _context_task_running(project.id):
                        await _send(send_now, {
                            "type": "error",
                            "message": "context refresh in progress",
                        })
                        continue
                    try:
                        runner = registry.start_node(
                            sid,
                            msg.text,
                            resume_from_node_id=msg.resume_from_node_id,
                            extra_skills=msg.extra_skills,
                            agent_op_kind=msg.agent_op_kind,
                            model_preset_id=msg.model_preset_id,
                        )
                    except ValueError as exc:
                        await _send(send_now, {
                            "type": "error",
                            "message": str(exc),
                        })
                        continue
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
                    Interrupt(**raw)
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
            registry.detach_observer(sid, observer_token)

    # Keep last — the /* mount below shadows any unmatched paths.
    dist_env = os.environ.get("MINICLAW_FRONTEND_DIST")
    if dist_env:
        dist_path = Path(dist_env)
        if not (dist_path / "index.html").is_file():
            raise RuntimeError(
                f"frontend dist not built at {dist_path}; "
                "run `npm run build` in frontend/"
            )
        app.mount(
            "/",
            StaticFiles(directory=str(dist_path), html=True),
            name="frontend",
        )

    return app


def _session_info(registry: ProjectRegistry, project: Any) -> SessionInfo:
    return SessionInfo(
        id=project.id,
        created_at=project.created_at,
        turns=registry.turn_count(project.id),
        model_preset_id=project.model_preset_id,
        provider=project.provider,
        preferred_language=project_preferred_language(project),
        temporary=project.temporary,
        template_id=project.template_id,
        name=project.name,
        project_context_binding_id=project.project_context_binding_id,
        layout_hints=project.layout_hints,
        layout_viewport=project.layout_viewport,
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


def _set_hook_port_from_env() -> None:
    raw = os.environ.get("MINICLAW2_HOOK_PORT") or os.environ.get("MINICLAW2_PORT")
    if not raw:
        return
    try:
        hook_runtime.set_port(int(raw))
    except ValueError:
        logger.debug("ignoring invalid hook port value %r", raw)


def _record_hook_port_from_scope(scope: dict[str, Any]) -> None:
    if os.environ.get("MINICLAW2_HOOK_PORT") or os.environ.get("MINICLAW2_PORT"):
        return
    server = scope.get("server")
    if (
        isinstance(server, (tuple, list))
        and len(server) >= 2
        and isinstance(server[1], int)
        and server[1] > 0
    ):
        hook_runtime.set_port(server[1])


app = create_app()
