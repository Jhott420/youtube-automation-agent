"""FastAPI backend: an HTTP chat endpoint (for testing / other front-ends) and
operator admin endpoints that mirror the CLI.

Every route except /health needs `Authorization: Bearer $ADMIN_API_TOKEN`.
If ADMIN_API_TOKEN is not set, those routes refuse to serve at all -- an open
chat endpoint would let anyone spend your Claude credits.

Run:  uvicorn companion.api:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .config import Settings
from .engine import ChatEngine

log = logging.getLogger(__name__)


class ChatIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    display_name: str | None = None


class ChatOut(BaseModel):
    kind: str
    text: str | None
    disclosure: str | None


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=1000)


def create_app(engine: ChatEngine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "engine", None) is None:
            app.state.engine = ChatEngine.from_settings(Settings.from_env())
        yield

    app = FastAPI(title="AI Companion", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine

    def get_engine(request: Request) -> ChatEngine:
        return request.app.state.engine

    def require_admin(request: Request, authorization: str | None = Header(default=None)) -> None:
        expected = request.app.state.engine.settings.admin_api_token
        if not expected:
            raise HTTPException(503, "ADMIN_API_TOKEN is not configured; API routes are disabled")
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(401, "invalid or missing bearer token")

    admin = [Depends(require_admin)]

    # ---- public ----------------------------------------------------------------

    @app.get("/health")
    def health(engine: ChatEngine = Depends(get_engine)):
        persona = engine.personas.get()
        return {
            "status": "ok",
            "model": engine.settings.model,
            "persona": persona.name,
            "persona_reload_error": engine.personas.last_error,
        }

    # ---- chat ------------------------------------------------------------------

    @app.post("/chat", response_model=ChatOut, dependencies=admin)
    def chat(body: ChatIn, background: BackgroundTasks, engine: ChatEngine = Depends(get_engine)):
        uid = f"api:{body.user_id}"
        reply = engine.handle_message(uid, body.text, body.display_name, platform="api")
        if reply.kind == "normal":
            background.add_task(engine.summarize_if_needed, uid)
        return ChatOut(kind=reply.kind, text=reply.text, disclosure=reply.disclosure)

    # ---- admin -----------------------------------------------------------------

    @app.get("/admin/persona", dependencies=admin)
    def persona_show(engine: ChatEngine = Depends(get_engine)):
        return engine.personas.get().model_dump()

    @app.get("/admin/users", dependencies=admin)
    def users(engine: ChatEngine = Depends(get_engine)):
        return [asdict(u) for u in engine.db.list_users()]

    @app.get("/admin/users/{user_id}/memories", dependencies=admin)
    def memory_list(user_id: str, engine: ChatEngine = Depends(get_engine)):
        return [
            {k: v for k, v in asdict(m).items() if k != "embedding"} for m in engine.memory.list(user_id)
        ]

    @app.post("/admin/users/{user_id}/memories", dependencies=admin)
    def memory_add(user_id: str, body: MemoryIn, engine: ChatEngine = Depends(get_engine)):
        return {"id": engine.memory.add(user_id, body.content)}

    @app.patch("/admin/memories/{memory_id}", dependencies=admin)
    def memory_edit(memory_id: int, body: MemoryIn, engine: ChatEngine = Depends(get_engine)):
        if not engine.memory.edit(memory_id, body.content):
            raise HTTPException(404, "memory not found")
        return {"ok": True}

    @app.delete("/admin/memories/{memory_id}", dependencies=admin)
    def memory_delete(memory_id: int, engine: ChatEngine = Depends(get_engine)):
        if not engine.memory.delete(memory_id):
            raise HTTPException(404, "memory not found")
        return {"ok": True}

    @app.delete("/admin/users/{user_id}/memories", dependencies=admin)
    def memory_wipe(user_id: str, engine: ChatEngine = Depends(get_engine)):
        return {"deleted": engine.memory.wipe(user_id)}

    @app.get("/admin/users/{user_id}/conversations", dependencies=admin)
    def conversations(user_id: str, limit: int = 50, engine: ChatEngine = Depends(get_engine)):
        return [asdict(m) for m in engine.db.recent_messages(user_id, limit)]

    @app.post("/admin/users/{user_id}/pause", dependencies=admin)
    def pause(user_id: str, engine: ChatEngine = Depends(get_engine)):
        engine.db.get_or_create_user(user_id)
        engine.db.set_paused(user_id, True)
        return {"user_id": user_id, "paused": True}

    @app.post("/admin/users/{user_id}/resume", dependencies=admin)
    def resume(user_id: str, engine: ChatEngine = Depends(get_engine)):
        if not engine.db.set_paused(user_id, False):
            raise HTTPException(404, "user not found")
        return {"user_id": user_id, "paused": False}

    return app


app = create_app()
