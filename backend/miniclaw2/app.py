"""FastAPI app: session CRUD + WebSocket gateway."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .events import InteractionResponse, Interrupt, UserMessage
from .session import SessionRegistry

logger = logging.getLogger(__name__)


class CreateSessionRequest(BaseModel):
    cwd: str | None = None
    model: str | None = None


class SessionInfo(BaseModel):
    id: str
    created_at: float
    turns: int


def create_app() -> FastAPI:
    app = FastAPI(title="MiniClaw2")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    registry = SessionRegistry()

    @app.post("/sessions", response_model=SessionInfo)
    def create_session(req: CreateSessionRequest) -> SessionInfo:
        s = registry.create(cwd=req.cwd, model=req.model)
        return SessionInfo(id=s.id, created_at=s.created_at, turns=s.turns)

    @app.get("/sessions", response_model=list[SessionInfo])
    def list_sessions() -> list[SessionInfo]:
        return [
            SessionInfo(id=s.id, created_at=s.created_at, turns=s.turns)
            for s in registry.list()
        ]

    @app.delete("/sessions/{sid}")
    def delete_session(sid: str) -> dict[str, bool]:
        if not registry.delete(sid):
            raise HTTPException(404, "session not found")
        return {"ok": True}

    @app.websocket("/ws/{sid}")
    async def ws(websocket: WebSocket, sid: str) -> None:
        session = registry.get(sid)
        if session is None:
            await websocket.close(code=4404, reason="session not found")
            return

        await websocket.accept()
        turn_task: asyncio.Task | None = None

        async def run_turn(text: str) -> None:
            session.turns += 1
            try:
                async for event in session.agent.run_turn(text):
                    await websocket.send_json(event.model_dump())
            except Exception:  # noqa: BLE001
                logger.exception("turn failed")
                await _send(websocket, {"type": "error", "message": "internal error"})

        try:
            while True:
                raw = await websocket.receive_json()
                msg_type = raw.get("type")

                if msg_type == "user_message":
                    msg = UserMessage(**raw)
                    if turn_task and not turn_task.done():
                        await _send(websocket, {"type": "error", "message": "turn in progress"})
                        continue
                    turn_task = asyncio.create_task(run_turn(msg.text))

                elif msg_type == "interaction_response":
                    resp = InteractionResponse(**raw)
                    ok = session.agent.resolve_interaction(
                        resp.id,
                        allow=resp.allow,
                        message=resp.message,
                        updated_input=resp.updated_input,
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
                    if turn_task and not turn_task.done():
                        turn_task.cancel()

                else:
                    await _send(websocket, {"type": "error", "message": f"unknown type: {msg_type}"})

        except WebSocketDisconnect:
            pass
        finally:
            if turn_task and not turn_task.done():
                turn_task.cancel()

    return app


async def _send(ws: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await ws.send_json(payload)
    except Exception:  # noqa: BLE001
        pass


app = create_app()
