"""FastAPI app: session-shaped REST + WebSocket gateway over ProjectRegistry.

The wire protocol is intentionally unchanged from before the Phase 0
refactor: a "session" id is a project id; each ``user_message`` spawns
a new agent node whose conversation continues from the project's
latest node via SDK ``resume``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .events import InteractionResponse, Interrupt, UserMessage
from .registry import ProjectRegistry

logger = logging.getLogger(__name__)


class CreateSessionRequest(BaseModel):
    cwd: str | None = None
    model: str | None = None
    model_provider: str | None = None
    provider: str | None = None


class SessionInfo(BaseModel):
    id: str
    created_at: float
    turns: int
    provider: str = "claude"


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
                cwd=req.cwd or os.getcwd(),
                model=req.model,
                model_provider=req.model_provider,
                provider=req.provider,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return SessionInfo(
            id=project.id,
            created_at=project.created_at,
            turns=0,
            provider=project.provider,
        )

    @app.get("/sessions", response_model=list[SessionInfo])
    def list_sessions() -> list[SessionInfo]:
        return [
            SessionInfo(
                id=p.id,
                created_at=p.created_at,
                turns=registry.turn_count(p.id),
                provider=p.provider,
            )
            for p in registry.list_projects()
        ]

    @app.delete("/sessions/{sid}")
    def delete_session(sid: str) -> dict[str, bool]:
        if not registry.delete_project(sid):
            raise HTTPException(404, "session not found")
        return {"ok": True}

    @app.websocket("/ws/{sid}")
    async def ws(websocket: WebSocket, sid: str) -> None:
        if registry.get_project(sid) is None:
            await websocket.close(code=4404, reason="session not found")
            return

        await websocket.accept()

        async def on_event(event: dict[str, Any]) -> None:
            await websocket.send_json(event)

        try:
            while True:
                raw = await websocket.receive_json()
                msg_type = raw.get("type")

                if msg_type == "user_message":
                    msg = UserMessage(**raw)
                    runner = registry.start_node(sid, msg.text, on_event)
                    if runner is None:
                        await _send(websocket, {
                            "type": "error",
                            "message": "turn in progress",
                        })
                        continue

                elif msg_type == "interaction_response":
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
                        await _send(websocket, {
                            "type": "error",
                            "message": f"no pending interaction with id {resp.id}",
                        })

                elif msg_type == "interrupt":
                    Interrupt(**raw)  # validate shape
                    registry.interrupt(sid)

                else:
                    await _send(websocket, {
                        "type": "error",
                        "message": f"unknown type: {msg_type}",
                    })

        except WebSocketDisconnect:
            pass
        finally:
            # Don't cancel the runner on WS disconnect — it should finish
            # and persist final state. The next WS connect can resume
            # observation once replay is implemented (Phase 1).
            pass

    return app


async def _send(ws: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await ws.send_json(payload)
    except Exception:  # noqa: BLE001
        pass


app = create_app()
