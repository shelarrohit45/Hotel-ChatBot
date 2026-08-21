"""FastAPI chat API for the hotel MCP client.

GET / serves the chat page. POST /chat runs guardrails + Claude + MCP.
First visit collects name, email, and mobile; they are reused until logout.
The Anthropic key stays on the server.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from config import CLIENT_ROOT, get_settings
from guardrails import RateLimiter, redact_output, security_headers
from mcp_bridge import McpBridge
from orchestrator import HotelOrchestrator
from user_store import (
    delete_login_session,
    load_login_session,
    public_user,
    save_login_session,
    upsert_guest,
)

logger = logging.getLogger("hotel-client")
logging.basicConfig(level=logging.INFO)

STATIC_DIR = CLIENT_ROOT / "static"
COOKIE_NAME = "hotel_sid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365

bridge: Optional[McpBridge] = None
orchestrator: Optional[HotelOrchestrator] = None
limiter = RateLimiter()
sessions: dict[str, dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global bridge, orchestrator
    settings = get_settings()
    bridge = McpBridge(settings)
    await bridge.start()
    orchestrator = HotelOrchestrator(bridge, settings)
    await orchestrator.prepare()
    logger.info("Hotel chat API ready")
    try:
        yield
    finally:
        await bridge.close()
        bridge = None
        orchestrator = None


app = FastAPI(title="Hotel ChatBot Client", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

_STATIC_TYPES = {
    "styles.css": "text/css; charset=utf-8",
    "chat.js": "application/javascript; charset=utf-8",
    "index.html": "text/html; charset=utf-8",
}


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class GuestIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=120)
    phone: str = Field(min_length=10, max_length=20)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for key, value in security_headers().items():
        response.headers[key] = value
    return response


def _bag(session_id: str) -> dict[str, Any]:
    data = sessions.get(session_id)
    if data is None:
        stored = load_login_session(session_id) or {}
        data = {"history": [], "user": stored.get("user")}
        sessions[session_id] = data
    elif isinstance(data, list):
        data = {"history": data, "user": None}
        sessions[session_id] = data
    return data


def _sid(request: Request) -> str:
    return request.cookies.get(COOKIE_NAME) or str(uuid.uuid4())


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        session_id,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
        max_age=COOKIE_MAX_AGE,
        path="/",
    )


def _guest_from_session(bag: dict[str, Any]) -> Optional[dict[str, Any]]:
    user = bag.get("user") or {}
    phone = user.get("phone") or ""
    if not user.get("name") or not user.get("email") or len(phone) != 10:
        return None
    return {
        "name": user.get("name") or "",
        "email": user.get("email") or "",
        "phone": phone,
    }


@app.get("/")
async def index(request: Request):
    page = STATIC_DIR / "index.html"
    if not page.exists():
        return JSONResponse({"error": "Chat UI not found."}, status_code=503)
    session_id = _sid(request)
    _bag(session_id)
    body = FileResponse(page, media_type="text/html; charset=utf-8")
    _set_session_cookie(body, session_id)
    return body


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/static/{filename}")
async def static_asset(filename: str):
    media = _STATIC_TYPES.get(filename)
    path = STATIC_DIR / filename
    if not media or not path.is_file():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(path, media_type=media)


@app.get("/auth/me")
async def auth_me(request: Request):
    session_id = _sid(request)
    bag = _bag(session_id)
    body = JSONResponse({"user": bag.get("user")})
    _set_session_cookie(body, session_id)
    return body


@app.post("/auth/guest")
async def auth_guest(payload: GuestIn, request: Request):
    session_id = _sid(request)
    bag = _bag(session_id)
    try:
        stored = upsert_guest(payload.name, payload.email, payload.phone)
    except ValueError as exc:
        body = JSONResponse({"error": str(exc)}, status_code=400)
        _set_session_cookie(body, session_id)
        return body
    bag["user"] = public_user(stored)
    bag["history"] = []
    save_login_session(session_id, bag["user"])
    body = JSONResponse({"user": bag["user"]})
    _set_session_cookie(body, session_id)
    return body


@app.post("/auth/logout")
async def auth_logout(request: Request):
    old = request.cookies.get(COOKIE_NAME)
    if old:
        sessions.pop(old, None)
        delete_login_session(old)
    session_id = str(uuid.uuid4())
    _bag(session_id)
    body = JSONResponse({"ok": True, "user": None})
    _set_session_cookie(body, session_id)
    return body


@app.post("/chat")
async def chat(payload: ChatIn, request: Request):
    if orchestrator is None:
        return JSONResponse({"error": "Chat is not ready."}, status_code=503)

    session_id = _sid(request)
    bag = _bag(session_id)
    guest = _guest_from_session(bag)
    if not guest:
        body = JSONResponse(
            {"error": "Enter your name, email, and mobile first."},
            status_code=401,
        )
        _set_session_cookie(body, session_id)
        return body

    if not limiter.allow(session_id):
        body = JSONResponse(
            {"error": "Too many requests. Please wait a minute."},
            status_code=429,
        )
        _set_session_cookie(body, session_id)
        return body

    try:
        result = await orchestrator.reply(payload.message, bag.get("history") or [], guest=guest)
    except RuntimeError as exc:
        logger.warning("chat unavailable: %s", type(exc).__name__)
        body = JSONResponse({"error": redact_output(str(exc))}, status_code=503)
        _set_session_cookie(body, session_id)
        return body
    except Exception as exc:
        logger.warning("chat failed: %s", type(exc).__name__)
        bag["history"] = []
        body = JSONResponse(
            {"error": "The concierge hit an error. Please send the message again."},
            status_code=503,
        )
        _set_session_cookie(body, session_id)
        return body

    bag["history"] = result["history"]
    body = JSONResponse(
        {
            "text": result["text"],
            "blocked": result["blocked"],
        }
    )
    _set_session_cookie(body, session_id)
    return body


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
