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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from config import CLIENT_ROOT, get_settings
from guardrails import RateLimiter, redact_output, security_headers
from mcp_bridge import McpBridge
from orchestrator import HotelOrchestrator
from payments import create_checkout, keys_ready, verify_signature
from receipts import booking_id_in, receipt_html, wants_receipt
from user_store import (
    attach_razorpay_order,
    booking_for_payment,
    confirm_booking_payment,
    delete_login_session,
    fail_booking_payment,
    get_receipt_for_guest,
    list_receipts_for,
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


class PayVerifyIn(BaseModel):
    booking_id: str = Field(min_length=3, max_length=40)
    razorpay_order_id: str = Field(min_length=5, max_length=80)
    razorpay_payment_id: str = Field(min_length=5, max_length=80)
    razorpay_signature: str = Field(min_length=10, max_length=200)


class PayFailIn(BaseModel):
    booking_id: str = Field(min_length=3, max_length=40)
    reason: str = Field(default="", max_length=200)
    razorpay_order_id: str = Field(default="", max_length=80)
    razorpay_payment_id: str = Field(default="", max_length=80)


class PayStartIn(BaseModel):
    booking_id: str = Field(min_length=3, max_length=40)


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
    bag["images"] = []
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
        result = await orchestrator.reply(
            payload.message,
            bag.get("history") or [],
            guest=guest,
            cached_images=bag.get("images") or [],
        )
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
    if result.get("images"):
        bag["images"] = result["images"]
    receipts = []
    if guest and not result.get("blocked") and wants_receipt(payload.message):
        receipts = list_receipts_for(
            guest["phone"], guest["email"], booking_id_in(payload.message)
        )
        if not receipts:
            result["text"] = (
                redact_output(result["text"])
                if result.get("text")
                else "No paid receipt yet. Book a stay and complete Razorpay payment first."
            )
        elif not result.get("text"):
            result["text"] = "Here is your payment receipt."
    body = JSONResponse(
        {
            "text": result["text"],
            "blocked": result["blocked"],
            "images": result.get("images") or [],
            "payment": result.get("payment"),
            "pay_booking_id": result.get("pay_booking_id"),
            "pay_amount_inr": result.get("pay_amount_inr"),
            "receipts": receipts,
        }
    )
    _set_session_cookie(body, session_id)
    return body


@app.post("/pay/start")
async def pay_start(payload: PayStartIn, request: Request):
    session_id = _sid(request)
    bag = _bag(session_id)
    guest = _guest_from_session(bag)
    if not guest:
        body = JSONResponse({"error": "Enter your details first."}, status_code=401)
        _set_session_cookie(body, session_id)
        return body
    if not keys_ready():
        body = JSONResponse(
            {
                "error": "Razorpay test keys are missing. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to HOTEL-CHATBOT-CLIENT/.env and restart the server."
            },
            status_code=503,
        )
        _set_session_cookie(body, session_id)
        return body
    booking, error = booking_for_payment(payload.booking_id, guest["phone"], guest["email"])
    if error or not booking:
        body = JSONResponse({"error": error or "Booking not found."}, status_code=400)
        _set_session_cookie(body, session_id)
        return body
    try:
        checkout = await create_checkout(booking, guest)
        attach_razorpay_order(checkout["booking_id"], checkout["order_id"], checkout["amount"])
    except Exception:
        logger.warning("razorpay start failed")
        body = JSONResponse(
            {"error": "Could not open Razorpay. Check the test Key Id and Key Secret, then restart."},
            status_code=503,
        )
        _set_session_cookie(body, session_id)
        return body
    body = JSONResponse({"ok": True, "payment": checkout})
    _set_session_cookie(body, session_id)
    return body


@app.post("/pay/verify")
async def pay_verify(payload: PayVerifyIn, request: Request):
    session_id = _sid(request)
    bag = _bag(session_id)
    guest = _guest_from_session(bag)
    if not guest:
        body = JSONResponse({"error": "Enter your details first."}, status_code=401)
        _set_session_cookie(body, session_id)
        return body
    if not verify_signature(
        payload.razorpay_order_id,
        payload.razorpay_payment_id,
        payload.razorpay_signature,
    ):
        fail_booking_payment(
            payload.booking_id,
            guest["phone"],
            guest["email"],
            "Signature check failed",
            payload.razorpay_payment_id,
            payload.razorpay_order_id,
        )
        body = JSONResponse({"ok": False, "error": "Payment could not be verified."}, status_code=400)
        _set_session_cookie(body, session_id)
        return body
    booking, error = confirm_booking_payment(
        payload.booking_id,
        guest["phone"],
        guest["email"],
        payload.razorpay_payment_id,
        payload.razorpay_order_id,
        payload.razorpay_signature,
    )
    if error or not booking:
        body = JSONResponse({"ok": False, "error": error or "Payment could not be saved."}, status_code=400)
        _set_session_cookie(body, session_id)
        return body
    body = JSONResponse(
        {
            "ok": True,
            "booking_id": booking.get("booking_id"),
            "status": booking.get("status"),
            "total_inr": booking.get("total_inr"),
            "hotel_name": booking.get("hotel_name"),
            "receipt": (booking or {}).get("receipt"),
        }
    )
    _set_session_cookie(body, session_id)
    return body


@app.get("/receipt/{booking_id}")
async def download_receipt(booking_id: str, request: Request):
    session_id = _sid(request)
    bag = _bag(session_id)
    guest = _guest_from_session(bag)
    if not guest:
        body = JSONResponse({"error": "Enter your details first."}, status_code=401)
        _set_session_cookie(body, session_id)
        return body
    receipt, error = get_receipt_for_guest(booking_id, guest["phone"], guest["email"])
    if error or not receipt:
        body = JSONResponse({"error": error or "Receipt not found."}, status_code=404)
        _set_session_cookie(body, session_id)
        return body
    filename = f"receipt-{receipt.get('booking_id') or booking_id}.html"
    body = HTMLResponse(receipt_html(receipt))
    body.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    _set_session_cookie(body, session_id)
    return body


@app.post("/pay/fail")
async def pay_fail(payload: PayFailIn, request: Request):
    session_id = _sid(request)
    bag = _bag(session_id)
    guest = _guest_from_session(bag)
    if not guest:
        body = JSONResponse({"error": "Enter your details first."}, status_code=401)
        _set_session_cookie(body, session_id)
        return body
    booking, error = fail_booking_payment(
        payload.booking_id,
        guest["phone"],
        guest["email"],
        payload.reason or "Payment failed or cancelled",
        payload.razorpay_payment_id,
        payload.razorpay_order_id,
    )
    if error and not booking:
        body = JSONResponse({"ok": False, "error": error}, status_code=400)
        _set_session_cookie(body, session_id)
        return body
    body = JSONResponse({"ok": True, "status": (booking or {}).get("status") or "payment_failed"})
    _set_session_cookie(body, session_id)
    return body


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
