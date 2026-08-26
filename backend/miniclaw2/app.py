"""FastAPI app: session-shaped REST + WebSocket gateway over ProjectRegistry.

A "session" id is a project id; each ``user_message`` spawns a new
agent node; resume continuation is explicit via an optional resume
source.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from .active_nodes import ACTIVE_STATES, ActiveNodesIndex, collect_active_entries
from .artifacts import INLINE_TEXT_CAP, stored_artifact_path
from .contextspace import (
    delete_principle,
    describe_project_contextspace,
    get_principle,
    list_principles,
    load_context_bundle_for_node,
    read_project_context,
    read_template_instances,
)
from .context_refresh import cancel_context_task, context_refresh_status, start_context_task
from .domain import Node
from .events import (
    ContextRefreshUpdated,
    InteractionResponse,
    Interrupt,
    ReplayRequest,
    UserMessage,
)
from .file_manager import RevealError, RevealUnsupportedError, reveal_directory
from .git_state import commit_graph, node_diff
from .global_config import (
    CodeReviewSettings,
    ModelPreset,
    SyncSettings,
    ToolRequestSettings,
    global_config_path,
    load_global_config,
    save_global_config,
)
from .language import normalize_preferred_language, project_preferred_language
from .model_catalog import list_model_presets
from .providers.claude_native import hook_runtime
from .providers.claude_native.hook_installer import install_hooks
from .registry import NonNativeNodeError, NonNativeProjectError, ProjectRegistry
from .replay import LiveReplayBuffer
from .skills import (
    SkillError,
    delete_agent_skill,
    get_agent_skill,
    import_agent_skill,
    list_agent_skills,
)
from .store import StoreReadOnlyError
from .self_update import (
    UpdateChecker,
    UpdateError,
    consume_pending_exit,
)
from .sync import SyncError
from .tags import Tag
from .templates import (
    SCHEMA_VERSION as TEMPLATE_SCHEMA_VERSION,
    SerializerError,
    Template,
    TemplateError,
    apply_user_template,
    delete_user_template,
    embedded_session_slug,
    launch_template,
    list_templates,
    list_user_templates,
    load_template,
    load_user_template,
    materialize_embedded_session,
    rewrite_user_template,
    serialize_embedded_session,
    serialize_selection,
)

logger = logging.getLogger(__name__)

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
    concurrency: StrictInt | None = Field(default=None, ge=1)


class UpdateGlobalDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model_preset_id: str | None = None
    auto_commit: bool | None = None
    preferred_language: str | None = None
    concurrency: StrictInt | None = Field(default=None, ge=1)


class UpdateCodeReviewSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_preset_id: str | None = None


class UpdateToolRequestSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: StrictInt | None = Field(default=None, ge=1)
    timeout_action: Literal["accept", "reject"] | None = None


class SetupSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote_url: str
    privacy_acknowledged: bool = False


class RenameSessionRequest(BaseModel):
    name: str


class CreateTagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    color: str | None = None


class UpdateTagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    color: str | None = None


class UpdateSessionTagsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_ids: list[str]


class UpdateSessionPreferencesRequest(BaseModel):
    preferred_language: str | None = None
    concurrency: StrictInt | None = Field(default=None, ge=1)


class UpdateSessionContextRequest(BaseModel):
    project_context_binding_id: str | None = None
    active_planspace_id: str | None = None


class SessionInfo(BaseModel):
    id: str
    created_at: float
    turns: int
    model_preset_id: str
    provider: str = "codex"
    concurrency: int = 1
    active_count: int = 0
    queued_count: int = 0
    preferred_language: str | None = None
    temporary: bool = False
    template_id: str | None = None
    tag_ids: list[str] = Field(default_factory=list)
    last_activity_at: float | None = None
    name: str = ""
    machine_id: str = ""
    local_machine_id: str = ""
    created_on_machine_label: str = ""
    bound_here: bool = True
    read_only: bool = False
    can_delete: bool = True
    can_bind_here: bool = False
    # Empty unless this host has a local binding: an unbound project's
    # `root_path` is a sentinel, not a directory anyone could open.
    root_path: str = ""
    hosts: list[dict[str, Any]] = Field(default_factory=list)
    last_sync_at: float | None = None
    project_context_binding_id: str | None = None
    layout_hints: dict[str, dict[str, float]] = Field(default_factory=dict)
    layout_viewport: dict[str, float] | None = None


class ActiveNodeGate(BaseModel):
    id: str
    subtype: str
    tool_name: str
    summary: str


class ActiveNodeEntry(BaseModel):
    project_id: str
    project_name: str
    node_id: str
    state: str
    category: str | None = None
    kind: str
    op_kind: str | None = None
    planspace_id: str | None = None
    planspace_title: str | None = None
    is_active_planspace: bool = False
    label: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    gate: ActiveNodeGate | None = None


class ActiveNodesResponse(BaseModel):
    generated_at: float
    entries: list[ActiveNodeEntry]


class UpdateLayoutHintsRequest(BaseModel):
    updates: dict[str, dict[str, float]] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)
    layout_viewport: dict[str, float] | None = None


class BindProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_path: str
    unverified_acknowledged: bool = False


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
    review_target: dict[str, Any] | None = None
    motivation: str | None = None
    scheduled_deps: list[str] | None = None
    pending_extra_principles: list[str] | None = None
    pending_extra_skills: list[str | dict[str, Any]] | None = None
    qa_mode: bool | None = None
    artifact_mode: str | None = None
    artifact_spec: str | None = None
    agent_op_kind: str | None = None
    model_preset_id: str | None = None
    obsolete_reason: str | None = None


class CreateVirtualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_draft: str
    category: str | None = None
    subtype: str | None = None
    brief: dict[str, Any] | None = None
    review_target: dict[str, Any] | None = None
    motivation: str | None = None
    scheduled_deps: list[str] | None = None
    pending_extra_principles: list[str] | None = None
    pending_extra_skills: list[str | dict[str, Any]] | None = None
    qa_mode: bool | None = None
    artifact_mode: str | None = None
    artifact_spec: str | None = None
    agent_op_kind: str | None = None
    model_preset_id: str | None = None
    planspace_id: str | None = None
    parent_node_id: str | None = None
    resume_from_node_id: str | None = None


class ImportSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    slug: str | None = None


class EventRecord(BaseModel):
    seq: int
    event: dict[str, Any]


class NodeDiffResponse(BaseModel):
    kind: str
    text: str
    error: str | None = None


class GitCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = ""


class TemplateArgumentMeta(BaseModel):
    """A template argument as rendered in the instantiation dialog."""

    name: str
    description: str = ""
    default: str | None = None
    required: bool = True
    declared: bool = True


class TemplateInputMeta(BaseModel):
    """A named upstream port the caller binds to an existing node."""

    name: str
    description: str = ""


class TemplateWarningMeta(BaseModel):
    """Non-fatal schema complaint the template editor surfaces."""

    code: str
    name: str = ""
    message: str = ""


class TemplateSummary(BaseModel):
    slug: str
    name: str
    brief: str
    allowed_model_preset_ids: list[str]
    auto_commit: bool
    node_count: int
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    schema_version: int = TEMPLATE_SCHEMA_VERSION
    arguments: list[TemplateArgumentMeta] = Field(default_factory=list)
    inputs: list[TemplateInputMeta] = Field(default_factory=list)
    warnings: list[TemplateWarningMeta] = Field(default_factory=list)


class TemplateDetail(BaseModel):
    slug: str
    name: str
    brief: str
    allowed_model_preset_ids: list[str]
    auto_commit: bool
    node_count: int
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    schema_version: int = TEMPLATE_SCHEMA_VERSION
    arguments: list[TemplateArgumentMeta] = Field(default_factory=list)
    inputs: list[TemplateInputMeta] = Field(default_factory=list)
    warnings: list[TemplateWarningMeta] = Field(default_factory=list)


class SkillSummary(BaseModel):
    id: str
    kind: Literal["skill"]
    slug: str
    name: str
    title: str
    description: str
    path: str
    files: list[str]
    body: str | None = None
    content_hash: str
    version: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    import_source: str | None = None
    import_kind: str | None = None
    imported_at: float | None = None
    package_id: str | None = None
    package_members: list[str] | None = None
    auto_attach_package: bool | None = None


class SkillDetail(SkillSummary):
    body: str


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


class UserTemplateArgumentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    # Missing and explicit null both mean required; "" is an optional value.
    default: str | None = None


class UserTemplateInputWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""


class UserTemplateNodeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    category: str
    subtype: str | None = None
    brief: dict[str, Any] | None = None
    prompt: str
    motivation: str = ""
    scheduled_deps: list[str] = Field(default_factory=list)
    resume_from: str | None = None
    #: Model this node runs on. ``None`` inherits the project preset at apply.
    model_preset_id: str | None = None
    #: Deliverable shape this node must publish. ``None`` means default.
    artifact_mode: str | None = None
    artifact_spec: str | None = None


class RewriteUserTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    brief: str = ""
    nodes: list[UserTemplateNodeWrite] = Field(default_factory=list)
    arguments: list[UserTemplateArgumentWrite] = Field(default_factory=list)
    inputs: list[UserTemplateInputWrite] = Field(default_factory=list)


class ApplyUserTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_node_id: str | None = None
    arguments: dict[str, str] = Field(default_factory=dict)
    input_bindings: dict[str, str] = Field(default_factory=dict)


class ApplyUserTemplateResponse(BaseModel):
    node_ids: list[str]
    instance_id: str


def _template_detail_payload(template: Template) -> dict[str, Any]:
    """Add editable prompt source only to detail responses.

    ``Template.metadata()`` also powers both list endpoints, which refresh in
    the library UI. Keeping full prompt text in this detail-only branch avoids
    making every library refresh download every template's source files.
    """
    payload = template.metadata()
    payload["nodes"] = [
        {
            **node.metadata(),
            "prompt": node.prompt,
            "motivation": node.summary,
        }
        for node in template.nodes
    ]
    return payload


def create_app(
    registry: ProjectRegistry | None = None,
    update_checker: UpdateChecker | None = None,
) -> FastAPI:
    from contextlib import asynccontextmanager

    registry = registry if registry is not None else ProjectRegistry(initialize=False)
    update_checker = update_checker if update_checker is not None else UpdateChecker()

    # Per-app so tests do not inherit one another's cached node facts.
    active_nodes_index = ActiveNodesIndex()

    def initialize_registry() -> None:
        initialize = getattr(registry, "initialize", None)
        if initialize is not None:
            initialize()

    def project_is_native(sid: str) -> bool:
        checker = getattr(registry, "is_native", None)
        return bool(checker(sid)) if checker is not None else True

    def require_native_project(sid: str) -> None:
        guard = getattr(registry, "require_native", None)
        if guard is not None:
            guard(sid)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        initialize_registry()
        if consume_pending_exit(registry.store.root):
            yield
            return
        schedule_all = getattr(registry, "schedule_all", None)
        if schedule_all is not None:
            schedule_all()
        # Generate the shared token before spawning any claude PTYs, and
        # merge the AskUserQuestion / SessionStart / Stop hooks into the user's
        # ~/.claude/settings.json. Both are idempotent.
        hook_runtime.ensure_token()
        _set_hook_port_from_env()
        try:
            install_hooks()
        except Exception:  # noqa: BLE001
            logger.exception("failed to install claude hooks")
        yield

    app = FastAPI(title="MiniClaw2", lifespan=lifespan)
    app.state.update_checker = update_checker
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(NonNativeProjectError)
    async def non_native_project_error(
        _request: Request, exc: NonNativeProjectError
    ) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(NonNativeNodeError)
    async def non_native_node_error(
        _request: Request, exc: NonNativeNodeError
    ) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(StoreReadOnlyError)
    async def read_only_store_error(
        _request: Request, exc: StoreReadOnlyError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/model-presets", response_model=list[dict[str, Any]])
    def list_model_presets_endpoint() -> list[dict[str, Any]]:
        return [
            preset.metadata()
            for preset in list_model_presets(store_root=registry.store.root)
        ]

    @app.get("/global-state", response_model=dict[str, Any])
    def get_global_state() -> dict[str, Any]:
        return _global_state_payload(registry.store.root)

    @app.post("/global-state/sync/setup", response_model=dict[str, Any])
    def setup_sync(req: SetupSyncRequest) -> dict[str, Any]:
        registry.store.assert_writable()
        if not req.privacy_acknowledged:
            raise HTTPException(
                400,
                "confirm that the private remote will contain full agent transcripts and tool output",
            )
        remote_url = req.remote_url.strip()
        if not remote_url:
            raise HTTPException(400, "git remote URL is required")
        try:
            registry.store.sync.setup_existing_store(remote_url)
            config = load_global_config(registry.store.root)
            save_global_config(
                config.model_copy(update={"sync": SyncSettings(remote_url=remote_url)}),
                registry.store.root,
            )
            registry.store.sync.schedule_commit("configure metadata sync")
            registry.store.sync.sync_now()
        except SyncError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _global_state_payload(registry.store.root)

    @app.post("/global-state/sync", response_model=dict[str, Any])
    def sync_now() -> dict[str, Any]:
        try:
            registry.store.sync.sync_now()
            registry.reload_from_store()
        except SyncError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _global_state_payload(registry.store.root)

    @app.post("/global-state/sync/check", response_model=dict[str, Any])
    def check_sync_remote() -> dict[str, Any]:
        try:
            registry.store.sync.check_remote()
        except SyncError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _global_state_payload(registry.store.root)

    @app.patch("/global-state/defaults", response_model=dict[str, Any])
    def update_global_defaults(
        req: UpdateGlobalDefaultsRequest,
    ) -> dict[str, Any]:
        registry.store.assert_writable()
        config = load_global_config(registry.store.root)
        updates: dict[str, Any] = {}
        if "default_model_preset_id" in req.model_fields_set:
            if req.default_model_preset_id is None:
                raise HTTPException(422, "default_model_preset_id cannot be null")
            updates["default_model_preset_id"] = req.default_model_preset_id.strip()
        if "auto_commit" in req.model_fields_set:
            if req.auto_commit is None:
                raise HTTPException(422, "auto_commit cannot be null")
            updates["auto_commit"] = req.auto_commit
        if "preferred_language" in req.model_fields_set:
            try:
                updates["preferred_language"] = normalize_preferred_language(
                    req.preferred_language
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        if "concurrency" in req.model_fields_set:
            if req.concurrency is None:
                raise HTTPException(422, "concurrency cannot be null")
            updates["concurrency"] = req.concurrency
        try:
            save_global_config(
                config.model_copy(
                    update={"defaults": config.defaults.model_copy(update=updates)}
                ),
                registry.store.root,
            )
            registry.store.sync.schedule_commit("update global defaults")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _global_state_payload(registry.store.root)

    @app.patch("/global-state/code-review", response_model=dict[str, Any])
    def update_code_review_settings(
        req: UpdateCodeReviewSettingsRequest,
    ) -> dict[str, Any]:
        registry.store.assert_writable()
        if req.model_preset_id is None:
            raise HTTPException(422, "model_preset_id cannot be null")
        config = load_global_config(registry.store.root)
        try:
            save_global_config(
                config.model_copy(
                    update={
                        "code_review": CodeReviewSettings(
                            model_preset_id=req.model_preset_id.strip()
                        )
                    }
                ),
                registry.store.root,
            )
            registry.store.sync.schedule_commit("update code review settings")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _global_state_payload(registry.store.root)

    @app.patch("/global-state/tool-requests", response_model=dict[str, Any])
    def update_tool_request_settings(
        req: UpdateToolRequestSettingsRequest,
    ) -> dict[str, Any]:
        registry.store.assert_writable()
        config = load_global_config(registry.store.root)
        updates = req.model_dump(exclude_unset=True)
        if any(value is None for value in updates.values()):
            raise HTTPException(422, "tool request settings cannot be null")
        try:
            tool_requests = ToolRequestSettings.model_validate(
                {**config.tool_requests.model_dump(), **updates}
            )
            save_global_config(
                config.model_copy(update={"tool_requests": tool_requests}),
                registry.store.root,
            )
            registry.store.sync.schedule_commit("update tool request settings")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _global_state_payload(registry.store.root)

    @app.post("/global-state/model-presets", response_model=dict[str, Any], status_code=201)
    def create_model_preset(preset: ModelPreset) -> dict[str, Any]:
        registry.store.assert_writable()
        config = load_global_config(registry.store.root)
        if any(item.id == preset.id for item in config.model_presets):
            raise HTTPException(409, f"model preset already exists: {preset.id}")
        try:
            save_global_config(
                config.model_copy(
                    update={"model_presets": [*config.model_presets, preset]}
                ),
                registry.store.root,
            )
            registry.store.sync.schedule_commit(f"create model preset {preset.id}")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _global_state_payload(registry.store.root)

    @app.put("/global-state/model-presets/{preset_id}", response_model=dict[str, Any])
    def replace_model_preset(
        preset_id: str,
        preset: ModelPreset,
    ) -> dict[str, Any]:
        registry.store.assert_writable()
        if preset.id != preset_id:
            raise HTTPException(400, "preset id in path and body must match")
        config = load_global_config(registry.store.root)
        if not any(item.id == preset_id for item in config.model_presets):
            raise HTTPException(404, f"model preset not found: {preset_id}")
        try:
            save_global_config(
                config.model_copy(
                    update={
                        "model_presets": [
                            preset if item.id == preset_id else item
                            for item in config.model_presets
                        ]
                    }
                ),
                registry.store.root,
            )
            registry.store.sync.schedule_commit(f"update model preset {preset.id}")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _global_state_payload(registry.store.root)

    @app.delete("/global-state/model-presets/{preset_id}", status_code=204)
    def delete_model_preset(preset_id: str) -> Response:
        registry.store.assert_writable()
        config = load_global_config(registry.store.root)
        if not any(item.id == preset_id for item in config.model_presets):
            raise HTTPException(404, f"model preset not found: {preset_id}")
        if config.defaults.default_model_preset_id == preset_id:
            raise HTTPException(409, "cannot delete the default model preset")
        if config.code_review.model_preset_id == preset_id:
            raise HTTPException(409, "cannot delete the code review model preset")
        for project in registry.list_projects():
            nodes = registry.list_nodes(project.id) or []
            if project.model_preset_id == preset_id or any(
                node.model_preset_id == preset_id for node in nodes
            ):
                raise HTTPException(409, "cannot delete a model preset used by a project")
        bundled_templates = list_templates(registry.store.root)
        user_templates = list_user_templates(registry.store.root)
        # Only bundled templates use the template-level list as a live run
        # matrix. User templates may still carry a legacy list, but apply-time
        # model resolution deliberately ignores it in favor of per-node data.
        matrix_reference = any(
            preset_id in template.allowed_model_preset_ids
            for template in bundled_templates
        )
        node_reference = any(
            node.model_preset_id == preset_id
            for template in [*bundled_templates, *user_templates]
            for node in template.nodes
        )
        if matrix_reference or node_reference:
            raise HTTPException(409, "cannot delete a model preset used by a template")
        save_global_config(
            config.model_copy(
                update={
                    "model_presets": [
                        item for item in config.model_presets if item.id != preset_id
                    ]
                }
            ),
            registry.store.root,
        )
        registry.store.sync.schedule_commit(f"delete model preset {preset_id}")
        return Response(status_code=204)

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
            directive = await dispatcher(payload)
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

    @app.post("/hook/turn-complete")
    async def hook_turn_complete(request: Request) -> JSONResponse:
        _require_hook_token(request)
        body = await request.json()
        node_id = body.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise HTTPException(400, "node_id required")
        # node_id is inherited by every descendant of the PTY child, so it
        # names the node but proves nothing. The session id comes from the
        # Stop payload of the process that is actually stopping; without a
        # match the signal is dropped rather than trusted.
        session_id = body.get("session_id")
        accepted = hook_runtime.signal_turn_complete(
            node_id,
            session_id if isinstance(session_id, str) and session_id else None,
        )
        if not accepted:
            # Expected and harmless for a nested CLI. Logged at warning
            # because the same line is the only symptom if a CLI upgrade
            # ever stops sending session_id: the check is fail-closed, so
            # every turn would then run to the stall timeout instead.
            logger.warning(
                "refused turn-complete for node %s from unowned session %r",
                node_id,
                session_id,
            )
        return JSONResponse({"ok": True, "accepted": accepted})

    @app.post("/sessions", response_model=SessionInfo)
    def create_session(req: CreateSessionRequest) -> SessionInfo:
        defaults = load_global_config(registry.store.root).defaults
        try:
            project = registry.create_project(
                cwd=None if req.temporary else (req.cwd or os.getcwd()),
                model_preset_id=(
                    req.model_preset_id
                    if "model_preset_id" in req.model_fields_set
                    else defaults.default_model_preset_id
                ),
                auto_commit=(
                    req.auto_commit
                    if "auto_commit" in req.model_fields_set
                    else defaults.auto_commit
                ),
                preferred_language=(
                    req.preferred_language
                    if "preferred_language" in req.model_fields_set
                    else defaults.preferred_language
                ),
                temporary=req.temporary,
                name=req.name or "",
                project_context_binding_id=req.project_context_binding_id,
                create_missing_cwd=req.create_missing_cwd,
                concurrency=(
                    req.concurrency
                    if req.concurrency is not None
                    else defaults.concurrency
                ),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc
        return _session_info(registry, project)

    @app.get("/tags", response_model=list[Tag])
    def list_tags() -> list[Tag]:
        return registry.store.list_tags()

    @app.post("/tags", response_model=Tag)
    def create_tag(req: CreateTagRequest) -> Tag:
        try:
            return registry.store.create_tag(req.name, req.color)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.patch("/tags/{tag_id}", response_model=Tag)
    def update_tag(tag_id: str, req: UpdateTagRequest) -> Tag:
        try:
            tag = registry.store.update_tag(
                tag_id,
                name=req.name if "name" in req.model_fields_set else None,
                color=req.color if "color" in req.model_fields_set else None,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if tag is None:
            raise HTTPException(404, "tag not found")
        return tag

    @app.delete("/tags/{tag_id}", status_code=204)
    def delete_tag(tag_id: str) -> Response:
        if not registry.delete_tag(tag_id):
            raise HTTPException(404, "tag not found")
        return Response(status_code=204)

    @app.get("/active-nodes", response_model=ActiveNodesResponse)
    def list_active_nodes() -> ActiveNodesResponse:
        """Nodes running, needing a human, or recently finished, workspace-wide.

        This is the initial/reconciliation snapshot for the workspace stream.
        """
        entries = collect_active_entries(registry, active_nodes_index)
        return ActiveNodesResponse(
            generated_at=time.time(),
            entries=[
                ActiveNodeEntry(
                    project_id=entry.project_id,
                    project_name=entry.project_name,
                    node_id=entry.node_id,
                    state=entry.state,
                    category=entry.category,
                    kind=entry.kind,
                    op_kind=entry.op_kind,
                    planspace_id=entry.planspace_id,
                    planspace_title=entry.planspace_title,
                    is_active_planspace=entry.is_active_planspace,
                    label=entry.label,
                    started_at=entry.started_at,
                    finished_at=entry.finished_at,
                    gate=ActiveNodeGate(**entry.gate) if entry.gate else None,
                )
                for entry in entries
            ],
        )

    def self_update_blockers() -> list[dict[str, str]]:
        # Only genuinely-busy nodes block an update. The sweep also reports
        # recently-finished nodes so the notification bell can list them, and
        # a done node must not hold the update back.
        blockers = {
            (entry.project_id, entry.node_id): {
                "project_id": entry.project_id,
                "project_name": entry.project_name,
                "node_id": entry.node_id,
                "state": entry.state,
            }
            for entry in collect_active_entries(registry, active_nodes_index)
            if entry.state in ACTIVE_STATES
        }
        for project, node_id in registry.finalizing_runner_nodes():
            blockers.setdefault(
                (project.id, node_id),
                {
                    "project_id": project.id,
                    "project_name": project.name,
                    "node_id": node_id,
                    "state": "finalizing",
                },
            )
        return list(blockers.values())

    def self_update_payload() -> dict[str, Any]:
        return {
            **asdict(update_checker.state()),
            "blockers": self_update_blockers(),
        }

    @app.get("/self-update", response_model=dict[str, Any])
    def get_self_update() -> dict[str, Any]:
        return self_update_payload()

    @app.post("/self-update/check", response_model=dict[str, Any])
    async def check_self_update() -> dict[str, Any]:
        """Fetch the source remote. Only ever reached by an explicit user action."""
        try:
            await asyncio.to_thread(update_checker.check_remote)
        except UpdateError as exc:
            raise HTTPException(409, str(exc)) from exc
        return await asyncio.to_thread(self_update_payload)

    @app.post("/self-update/apply", response_model=dict[str, Any])
    async def apply_self_update() -> dict[str, Any]:
        if not registry.prepare_self_update():
            raise HTTPException(409, "已有自更新正在进行")
        applied = False
        try:
            blockers = await asyncio.to_thread(self_update_blockers)
            if blockers:
                names = ", ".join(
                    f"{entry['project_name']}/{entry['node_id'][:8]} "
                    f"({entry['state']})"
                    for entry in blockers
                )
                raise HTTPException(409, f"仍有活跃节点，无法更新：{names}")
            try:
                result = await asyncio.to_thread(
                    update_checker.apply, registry.store.root
                )
            except UpdateError as exc:
                raise HTTPException(409, str(exc)) from exc
            applied = True
        finally:
            if not applied:
                registry.cancel_self_update()
        print("MiniClaw2 已快进更新，将退出进程。", flush=True)
        if result.restart_commands:
            print("重启前请执行：", flush=True)
            for command in result.restart_commands:
                print(f"  {command}", flush=True)
        else:
            print("无需额外安装或构建步骤，可直接重启。", flush=True)
        update_checker.schedule_exit(registry.store.root)
        return {
            "ok": True,
            "old_head": result.old_head,
            "new_head": result.new_head,
            "changed_paths": result.changed_paths,
            "restart_commands": result.restart_commands,
            "message": "更新已完成，MiniClaw2 即将退出",
        }

    @app.get("/sessions", response_model=list[SessionInfo])
    def list_sessions() -> list[SessionInfo]:
        return [_session_info(registry, p) for p in registry.list_projects()]

    @app.get("/sessions/{sid}", response_model=SessionInfo)
    def get_session(sid: str) -> SessionInfo:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        return _session_info(registry, project)

    @app.post("/sessions/{sid}/hosts", response_model=SessionInfo)
    def bind_session_host(sid: str, req: BindProjectRequest) -> SessionInfo:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        # This is the sole mutation allowed before this host has a local path.
        try:
            project = registry.bind_project_here(
                sid,
                req.root_path,
                unverified_acknowledged=req.unverified_acknowledged,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if project is None:
            raise HTTPException(404, "session not found")
        return _session_info(registry, project)

    @app.delete("/sessions/{sid}/hosts/{mid}", response_model=SessionInfo)
    def unbind_session_host(sid: str, mid: str) -> SessionInfo:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if mid != registry.store.machine.id:
            raise HTTPException(400, "only this device's binding can be removed")
        try:
            project = registry.unbind_project_here(sid)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        if project is None:
            raise HTTPException(404, "session not found")
        return _session_info(registry, project)

    @app.post("/sessions/{sid}/reveal", response_model=dict[str, Any])
    def reveal_session_root(sid: str) -> dict[str, Any]:
        """Open the project directory in this machine's file manager.

        Deliberately not gated on ``read_only``: a read-only store still lets
        the human look at the tree, and a store-level write lock has nothing to
        do with whether a folder may be opened. What it *is* gated on is a local
        binding, because without one there is no directory on this machine.
        """
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if not registry.is_native_project(project):
            raise HTTPException(409, "此设备尚未配置项目路径")
        try:
            reveal_directory(project.root_path)
        except RevealUnsupportedError as exc:
            raise HTTPException(501, str(exc)) from exc
        except RevealError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"root_path": project.root_path}

    @app.get("/sessions/{sid}/git", response_model=dict[str, Any])
    def get_git_state(sid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        status = registry.git_status(sid)
        if status is None:
            raise HTTPException(404, "session not found")
        nodes = registry.list_nodes(sid) or []
        refs = {
            sha
            for node in nodes
            for sha in (node.commit_before, node.commit_after)
            if sha
        }
        ref_timestamps: dict[str, float] = {}
        for node in nodes:
            for sha in (node.commit_before, node.commit_after):
                if sha and (sha not in ref_timestamps or node.created_at < ref_timestamps[sha]):
                    ref_timestamps[sha] = node.created_at
        commits = commit_graph(
            project.root_path,
            refs,
            registry.store.read_git_aliases(sid),
            ref_timestamps,
            status,
            {
                mid: payload["head"]
                for mid, payload in registry.store.read_host_heads(sid).items()
                if mid != registry.store.machine.id
            },
        )
        return {"status": asdict(status), "commits": [asdict(item) for item in commits]}

    @app.post("/sessions/{sid}/git/commit", response_model=dict[str, Any])
    async def git_commit(sid: str, req: GitCommitRequest) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        try:
            node = registry.spawn_git_op(sid, "commit", message=req.message)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if node is None:
            raise HTTPException(409, "project unavailable")
        return {"node": node.model_dump()}

    @app.post("/sessions/{sid}/git/review", response_model=dict[str, Any])
    async def git_review(sid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        try:
            node = await registry.spawn_code_review(sid)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if node is None:
            raise HTTPException(409, "project unavailable")
        return {"node": node.model_dump()}

    @app.post("/sessions/{sid}/git/pull", response_model=dict[str, Any])
    async def git_pull(sid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        require_native_project(sid)
        if not registry.quiescent(sid):
            raise HTTPException(409, "project must be idle before pulling")
        node = registry.spawn_git_op(sid, "pull")
        if node is None:
            raise HTTPException(409, "project unavailable")
        return {"node": node.model_dump()}

    @app.post("/sessions/{sid}/git/push", response_model=dict[str, Any])
    async def git_push(sid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        require_native_project(sid)
        result = await registry.git_push(sid)
        if result is None:
            raise HTTPException(404, "session not found")
        status, error = result
        if error is not None:
            raise HTTPException(409, error)
        return {"status": asdict(status)}

    @app.patch("/sessions/{sid}", response_model=SessionInfo)
    def rename_session(sid: str, req: RenameSessionRequest) -> SessionInfo:
        project = registry.rename_project(sid, req.name)
        if project is None:
            raise HTTPException(404, "session not found")
        return _session_info(registry, project)

    @app.patch("/sessions/{sid}/tags", response_model=SessionInfo)
    def update_session_tags(
        sid: str,
        req: UpdateSessionTagsRequest,
    ) -> SessionInfo:
        project = registry.update_project_tags(sid, req.tag_ids)
        if project is None:
            raise HTTPException(404, "session not found")
        return _session_info(registry, project)

    @app.patch("/sessions/{sid}/preferences", response_model=SessionInfo)
    async def update_session_preferences(
        sid: str,
        req: UpdateSessionPreferencesRequest,
    ) -> SessionInfo:
        kwargs: dict[str, Any] = {}
        if "preferred_language" in req.model_fields_set:
            kwargs["preferred_language"] = req.preferred_language
        if "concurrency" in req.model_fields_set:
            if req.concurrency is None:
                raise HTTPException(422, "concurrency must be a positive integer")
            kwargs["concurrency"] = req.concurrency
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
    async def update_session_contextspace(
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
        require_native_project(sid)
        result = read_project_context(project)
        if result is None:
            raise HTTPException(404, "file not found")
        return result

    @app.post("/sessions/{sid}/context/init", response_model=dict[str, Any])
    async def init_project_context(sid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        require_native_project(sid)
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        try:
            start_context_task(
                project,
                mode="init",
                on_status=lambda status: registry.broadcast_project(
                    project.id,
                    ContextRefreshUpdated(
                        project_id=project.id,
                        context_refresh=status,
                    ).model_dump(),
                ),
            )
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
        require_native_project(sid)
        if registry.is_running(sid):
            raise HTTPException(409, "turn in progress")
        try:
            start_context_task(
                project,
                mode="refresh",
                on_status=lambda status: registry.broadcast_project(
                    project.id,
                    ContextRefreshUpdated(
                        project_id=project.id,
                        context_refresh=status,
                    ).model_dump(),
                ),
            )
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
            result = registry.create_planspace_and_launch_concierge(
                sid,
                title=req.title.strip(),
                seed=seed,
                mode=req.mode,
                model_preset_id=req.model_preset_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if result is None:
            raise HTTPException(409, "project runtime is unavailable")
        node = result.node
        contextspace = describe_project_contextspace(
            project, store_root=registry.store.root
        )
        contextspace["node_id"] = node.id
        contextspace["planspace_id"] = node.planspace_id
        contextspace["binding_id"] = contextspace.get("resolved_binding_id")
        contextspace["activated"] = result.activated
        return contextspace

    @app.post("/sessions/{sid}/planspaces/blank", response_model=dict[str, Any])
    async def create_blank_planspace(
        sid: str,
        req: CreateBlankPlanspaceRequest,
    ) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        if not req.seed.strip():
            raise HTTPException(400, "seed must be non-empty")
        try:
            result = registry.create_blank_planspace(
                sid,
                title=(req.title or "").strip(),
                seed=req.seed,
                mode=req.mode,
                model_preset_id=req.model_preset_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if result is None:
            raise HTTPException(409, "project runtime is unavailable")
        node = result.node
        contextspace = describe_project_contextspace(
            project, store_root=registry.store.root
        )
        contextspace["node_id"] = node.id
        contextspace["planspace_id"] = node.planspace_id
        contextspace["binding_id"] = contextspace.get("resolved_binding_id")
        contextspace["activated"] = result.activated
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

    @app.delete("/sessions/{sid}/planspaces/{planspace_id}", response_model=dict[str, Any])
    async def delete_planspace(sid: str, planspace_id: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        try:
            deleted, busy = registry.delete_planspace(sid, planspace_id)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if busy:
            raise HTTPException(409, {"busy": busy})
        if not deleted:
            raise HTTPException(404, "planspace not found")
        return describe_project_contextspace(project, store_root=registry.store.root)

    @app.get(
        "/sessions/{sid}/planspaces/{planspace_id}/template-instances",
        response_model=list[dict[str, Any]],
    )
    def list_template_instances_endpoint(
        sid: str,
        planspace_id: str,
    ) -> list[dict[str, Any]]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        try:
            return read_template_instances(
                project,
                planspace_id,
                store_root=registry.store.root,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.delete(
        "/sessions/{sid}/planspaces/{planspace_id}/template-instances/{instance_id}",
        response_model=dict[str, Any],
    )
    async def delete_template_instance_endpoint(
        sid: str,
        planspace_id: str,
        instance_id: str,
    ) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        try:
            deleted, blockers, removed = registry.delete_template_instance(
                sid,
                planspace_id,
                instance_id,
            )
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if blockers:
            raise HTTPException(409, {"blockers": blockers})
        if not deleted:
            raise HTTPException(404, "template instance not found")
        return {"ok": True, "removed_node_ids": removed}

    @app.post("/sessions/{sid}/virtuals", response_model=dict[str, Any])
    async def create_virtual(
        sid: str,
        req: CreateVirtualRequest,
    ) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        try:
            node = registry.create_virtual(
                sid,
                prompt_draft=req.prompt_draft,
                category=req.category,
                subtype=req.subtype,
                brief=req.brief,
                review_target=req.review_target,
                motivation=req.motivation,
                scheduled_deps=req.scheduled_deps,
                pending_extra_principles=req.pending_extra_principles,
                pending_extra_skills=req.pending_extra_skills,
                qa_mode=bool(req.qa_mode),
                artifact_mode=req.artifact_mode,
                artifact_spec=req.artifact_spec,
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
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        result = registry.promote_virtual_result(sid, vid)
        node = result.node
        if node is None:
            detail: dict[str, Any] = {
                "code": result.code or "promotion_conflict",
                "message": result.message or "Virtual node cannot be promoted.",
            }
            if result.blockers:
                detail["blockers"] = list(result.blockers)
            raise HTTPException(409, detail)
        return {
            "ok": True,
            "node_id": node.id,
            "node": node.model_dump(),
            "already_promoted": result.code == "already_promoted",
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
        require_native_project(sid)
        await cancel_context_task(project.id)
        return describe_project_contextspace(project, store_root=registry.store.root)

    @app.get("/sessions/{sid}/nodes", response_model=list[dict[str, Any]])
    def list_nodes(sid: str) -> list[dict[str, Any]]:
        nodes = registry.list_nodes(sid)
        if nodes is None:
            raise HTTPException(404, "session not found")
        return [node.model_dump() for node in nodes]

    @app.get("/sessions/{sid}/nodes/{nid}", response_model=dict[str, Any])
    def get_node(sid: str, nid: str) -> dict[str, Any]:
        if registry.get_project(sid) is None:
            raise HTTPException(404, "session not found")
        node = registry.get_node(sid, nid)
        if node is None:
            raise HTTPException(404, "node not found")
        return node.model_dump()

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

    @app.post("/sessions/{sid}/nodes/{nid}/dequeue", response_model=dict[str, Any])
    async def dequeue_node(sid: str, nid: str) -> dict[str, Any]:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        node = registry.dequeue_node(sid, nid)
        if node is None:
            if registry.get_node(sid, nid) is None:
                raise HTTPException(404, "node not found")
            raise HTTPException(
                409,
                "node must be unscheduled, queued, and in a manual planspace",
            )
        return {"ok": True, "node_id": node.id, "node": node.model_dump()}

    @app.get("/sessions/{sid}/nodes/{nid}/diff", response_model=NodeDiffResponse)
    def get_node_diff(sid: str, nid: str) -> NodeDiffResponse:
        project = registry.get_project(sid)
        if project is None:
            raise HTTPException(404, "session not found")
        require_native_project(sid)
        node = registry.get_node(sid, nid)
        if node is None:
            raise HTTPException(404, "node not found")
        diff = node_diff(project.root_path, node.commit_before, node.commit_after)
        return NodeDiffResponse(kind=diff.kind, text=diff.text, error=diff.error)

    @app.get(
        "/sessions/{sid}/nodes/{nid}/reviewed-diff",
        response_model=NodeDiffResponse,
    )
    def get_reviewed_diff(sid: str, nid: str) -> NodeDiffResponse:
        if registry.get_project(sid) is None:
            raise HTTPException(404, "session not found")
        node = registry.get_node(sid, nid)
        if node is None:
            raise HTTPException(404, "node not found")
        path = registry.store.node_dir(sid, nid) / "reviewed-diff.patch"
        if not path.exists():
            raise HTTPException(404, "review snapshot not yet written")
        return NodeDiffResponse(kind="patch", text=path.read_text(encoding="utf-8"))

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

    @app.get(
        "/sessions/{sid}/nodes/{nid}/artifacts/{name}",
        response_model=None,
    )
    def get_node_artifact(
        sid: str,
        nid: str,
        name: str,
        raw: bool = False,
    ) -> Response | dict[str, Any]:
        if registry.get_project(sid) is None:
            raise HTTPException(404, "session not found")
        node = registry.get_node(sid, nid)
        if node is None:
            raise HTTPException(404, "node not found")
        artifact = next(
            (
                ref
                for ref in node.artifacts
                if ref.status == "published" and ref.name == name
            ),
            None,
        )
        if artifact is None:
            raise HTTPException(404, "published artifact not found")
        path = stored_artifact_path(registry.store, sid, nid, artifact.name)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise HTTPException(404, "artifact content not found") from exc

        if not raw:
            inline = content[:INLINE_TEXT_CAP]
            return {
                "name": artifact.name,
                "text": inline.decode("utf-8", errors="replace"),
                "bytes": artifact.bytes,
                "mtime": artifact.mtime,
                "sha256": artifact.sha256,
                "truncated": len(content) > INLINE_TEXT_CAP,
            }

        suffix = Path(artifact.name).suffix
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".md": "text/plain; charset=utf-8",
            ".json": "application/json",
        }.get(suffix)
        if content_type is None:
            raise HTTPException(404, "published artifact type is not supported")
        headers = {"X-Content-Type-Options": "nosniff"}
        if suffix == ".html":
            headers["Content-Security-Policy"] = (
                "sandbox allow-scripts; connect-src 'none'"
            )
        headers["Content-Type"] = content_type
        return Response(content=content, headers=headers)

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
        return [
            TemplateSummary(**s.metadata())
            for s in list_templates(registry.store.root)
        ]

    @app.get("/templates/{name}", response_model=TemplateDetail)
    def get_template(name: str) -> TemplateDetail:
        try:
            template = load_template(name, store_root=registry.store.root)
        except TemplateError as exc:
            raise HTTPException(404, str(exc)) from exc
        return TemplateDetail(**_template_detail_payload(template))

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
        return TemplateDetail(**_template_detail_payload(template))

    @app.put("/user-templates/{slug}", response_model=TemplateDetail)
    def rewrite_user_template_endpoint(
        slug: str,
        req: RewriteUserTemplateRequest,
    ) -> TemplateDetail:
        registry.store.assert_writable()
        try:
            template = rewrite_user_template(
                slug,
                name=req.name,
                brief=req.brief,
                nodes=[node.model_dump() for node in req.nodes],
                arguments=[argument.model_dump() for argument in req.arguments],
                inputs=[input_spec.model_dump() for input_spec in req.inputs],
                store_root=registry.store.root,
            )
        except (SerializerError, TemplateError) as exc:
            raise HTTPException(400, str(exc)) from exc
        registry.store.sync.schedule_commit(f"update template {slug}")
        return TemplateDetail(**_template_detail_payload(template))

    @app.delete("/user-templates/{slug}", status_code=204)
    def delete_user_template_endpoint(slug: str) -> Response:
        registry.store.assert_writable()
        if not delete_user_template(slug, registry.store.root):
            raise HTTPException(404, f"user template not found: {slug}")
        registry.store.sync.schedule_commit(f"delete template {slug}")
        return Response(status_code=204)

    @app.post("/user-templates/{slug}/session", response_model=SessionInfo)
    async def open_user_template_session(slug: str) -> SessionInfo:
        """Open (or re-attach to) the embedded editing session for a template.

        The session is an ordinary temporary project whose lane holds the
        template's own nodes with placeholders intact, so the shared project
        canvas can edit it. Re-opening returns the existing session rather than
        stamping a second copy — otherwise the unsaved edits in the first one
        would become unreachable.
        """
        registry.store.assert_writable()
        try:
            template = load_user_template(slug, registry.store.root)
        except TemplateError as exc:
            raise HTTPException(404, str(exc)) from exc

        existing = _embedded_session_for(registry, slug)
        if existing is not None:
            return _session_info(registry, existing)

        try:
            project, _lane = materialize_embedded_session(template, registry)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        registry.store.sync.schedule_commit(f"open template session {slug}")
        return _session_info(registry, project)

    @app.post(
        "/user-templates/{slug}/session/commit",
        response_model=TemplateDetail,
    )
    async def commit_user_template_session(slug: str) -> TemplateDetail:
        """Write an embedded session's graph back onto its template.

        Explicit commit, never automatic: the session is a scratch space, and
        an author who edits without saving must be able to walk away.
        """
        registry.store.assert_writable()
        project = _embedded_session_for(registry, slug)
        if project is None:
            raise HTTPException(404, f"no open session for template: {slug}")
        if _context_task_running(project.id):
            raise HTTPException(409, "context refresh in progress")
        try:
            template = serialize_embedded_session(registry, project, slug)
        except SerializerError as exc:
            raise HTTPException(400, str(exc)) from exc
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
        registry.store.sync.schedule_commit(f"commit template session {slug}")
        return TemplateDetail(**_template_detail_payload(template))

    @app.delete("/user-templates/{slug}/session", status_code=204)
    def discard_user_template_session(slug: str) -> Response:
        """Throw the session away. Uncommitted graph edits go with it."""
        project = _embedded_session_for(registry, slug)
        if project is None:
            raise HTTPException(404, f"no open session for template: {slug}")
        # `delete_project` removes the temporary workspace for a temporary
        # project, so the scratch worktree does not outlive the session.
        if not registry.delete_project(project.id):
            raise HTTPException(404, f"no open session for template: {slug}")
        return Response(status_code=204)

    @app.get("/principles", response_model=list[dict[str, Any]])
    def list_principles_endpoint() -> list[dict[str, Any]]:
        return list_principles(store_root=registry.store.root)

    @app.get("/principles/{slug}", response_model=dict[str, Any])
    def get_principle_endpoint(slug: str) -> dict[str, Any]:
        try:
            principle = get_principle(slug, store_root=registry.store.root)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if principle is None:
            raise HTTPException(404, f"未找到 principle: {slug}")
        return principle

    @app.delete("/principles/{slug}", status_code=204)
    def delete_principle_endpoint(slug: str) -> Response:
        registry.store.assert_writable()
        try:
            removed = delete_principle(slug, store_root=registry.store.root)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not removed:
            raise HTTPException(404, f"principle not found: {slug}")
        registry.store.sync.schedule_commit(f"delete principle {slug}")
        return Response(status_code=204)

    @app.get(
        "/skills",
        response_model=list[SkillSummary],
        response_model_exclude_none=True,
    )
    def list_agent_skills_endpoint() -> list[dict[str, Any]]:
        return list_agent_skills(store_root=registry.store.root)

    @app.get(
        "/skills/{slug}",
        response_model=SkillDetail,
        response_model_exclude_none=True,
    )
    def get_agent_skill_endpoint(slug: str) -> dict[str, Any]:
        try:
            skill = get_agent_skill(slug, store_root=registry.store.root)
        except SkillError as exc:
            raise HTTPException(400, str(exc)) from exc
        if skill is None:
            raise HTTPException(404, f"未找到 skill: {slug}")
        return skill

    @app.post("/skills/import", response_model=dict[str, Any])
    def import_agent_skill_endpoint(req: ImportSkillRequest) -> dict[str, Any]:
        registry.store.assert_writable()
        try:
            imported = import_agent_skill(
                req.source,
                slug=req.slug,
                store_root=registry.store.root,
            )
        except SkillError as exc:
            raise HTTPException(400, str(exc)) from exc
        if imported.get("kind") == "skill-pack":
            registry.store.sync.schedule_commit(
                f'import skill pack {imported["package_id"]} ({imported["count"]} skills)'
            )
        else:
            registry.store.sync.schedule_commit(f'import skill {imported["slug"]}')
        return imported

    @app.delete("/skills/{slug}", status_code=204)
    def delete_agent_skill_endpoint(slug: str) -> Response:
        registry.store.assert_writable()
        try:
            removed = delete_agent_skill(slug, store_root=registry.store.root)
        except SkillError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not removed:
            raise HTTPException(404, f"skill not found: {slug}")
        registry.store.sync.schedule_commit(f"delete skill {slug}")
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
        require_native_project(sid)
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
        registry.store.sync.schedule_commit(f"save template {slug}")
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
        require_native_project(sid)
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
                arguments=req.arguments,
                input_bindings=req.input_bindings,
            )
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
        registry.promote_next_virtual(sid)
        instance_id = created[0].template_instance_id if created else None
        if instance_id is None:
            raise HTTPException(500, "stamped template has no instance id")
        return ApplyUserTemplateResponse(
            node_ids=[n.id for n in created],
            instance_id=instance_id,
        )

    @app.websocket("/ws/{sid}")
    async def ws(websocket: WebSocket, sid: str) -> None:
        initialize_registry()
        _record_hook_port_from_scope(websocket.scope)
        if sid == "-":
            await websocket.accept()
            send_lock = asyncio.Lock()

            async def send_workspace_event(event: dict[str, Any]) -> None:
                async with send_lock:
                    await websocket.send_json(event)

            observer_token = registry.attach_workspace_observer(send_workspace_event)
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
            except WebSocketDisconnect:
                pass
            finally:
                registry.detach_workspace_observer(observer_token)
            return

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

                if msg_type in {"user_message", "interaction_response", "interrupt"} and not project_is_native(sid):
                    await mark_live_ready()
                    await _send(send_now, {
                        "type": "error",
                        "message": str(NonNativeProjectError(project)),
                    })
                    continue

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
                            extra_principles=msg.extra_principles,
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
                            "message": "invalid resume source or project",
                        })
                        continue

                elif msg_type == "interaction_response":
                    await mark_live_ready()
                    resp = InteractionResponse(**raw)
                    ok = registry.resolve_gate(
                        sid,
                        resp.id,
                        node_id=resp.node_id,
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
                    interrupt = Interrupt(**raw)
                    registry.interrupt(sid, interrupt.node_id)

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


def _global_state_payload(store_root: Path) -> dict[str, Any]:
    config = load_global_config(store_root)
    # The manager is keyed by the same store root and performs no network IO.
    from .sync import get_sync_manager

    sync_status = get_sync_manager(store_root).status()
    default_id = config.defaults.default_model_preset_id
    return {
        "config_path": str(global_config_path(store_root)),
        "defaults": config.defaults.model_dump(),
        "code_review": config.code_review.model_dump(),
        "tool_requests": config.tool_requests.model_dump(),
        "model_presets": [
            preset.model_copy(
                update={"is_default": preset.id == default_id}
            ).metadata()
            for preset in config.model_presets
        ],
        "sync": {
            **sync_status,
            "remote_url": sync_status["remote_url"] or config.sync.remote_url,
            "privacy_notice": (
                "The remote contains full agent transcripts, prompts, tool output, and code. "
                "Use a private remote."
            ),
        },
    }


def _embedded_session_for(registry: ProjectRegistry, slug: str) -> Any:
    """Find the open embedded editing session for a user template, if any.

    Matched on the ``embedded:<slug>`` marker rather than a bare template name:
    ``launch_template`` tags its bundled test runs with the template's display
    name, and every bundled template's display name is also its directory name,
    so a bare comparison would let a test run be committed over a user template
    it never came from.
    """
    if not slug:
        return None
    for project in registry.list_projects():
        if not project.temporary:
            continue
        if embedded_session_slug(project.template_id) == slug:
            return project
    return None


def _session_info(registry: ProjectRegistry, project: Any) -> SessionInfo:
    bound_here = registry.is_native_project(project)
    node_summary = registry.node_summary(project)
    return SessionInfo(
        id=project.id,
        created_at=project.created_at,
        turns=node_summary.turns,
        model_preset_id=project.model_preset_id,
        provider=project.provider,
        concurrency=project.concurrency,
        active_count=registry.active_count(project.id),
        queued_count=node_summary.queued_count,
        preferred_language=project_preferred_language(project),
        temporary=project.temporary,
        template_id=project.template_id,
        tag_ids=project.tag_ids,
        last_activity_at=node_summary.last_activity_at,
        name=project.name,
        machine_id=project.machine_id,
        local_machine_id=registry.store.machine.id,
        created_on_machine_label=project.machine_label or project.machine_id,
        bound_here=bound_here,
        read_only=not bound_here or registry.store.read_only_reason is not None,
        can_delete=(
            registry.store.read_only_reason is None
            and bound_here
        ),
        can_bind_here=(
            not bound_here
            and not project.temporary
            and registry.store.read_only_reason is None
        ),
        root_path=project.root_path if bound_here else "",
        hosts=registry.store.list_hosts(project.id),
        last_sync_at=registry.store.sync.identity.last_sync_at,
        project_context_binding_id=project.project_context_binding_id,
        layout_hints=project.layout_hints,
        layout_viewport=project.layout_viewport,
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
