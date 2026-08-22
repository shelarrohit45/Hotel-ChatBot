"""Razorpay test/live orders. Key secret never leaves this process."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any, Optional

import httpx

from config import Settings, get_settings

logger = logging.getLogger(__name__)

RAZORPAY_ORDERS = "https://api.razorpay.com/v1/orders"
_PAY_ASK = re.compile(
    r"\b("
    r"pay(\s+now|\s+again)?|"
    r"payment\s+(screen|window|page|again|gateway)|"
    r"open\s+(the\s+)?(payment|pay|checkout)|"
    r"(complete|retry|make|do)\s+(the\s+)?payment|"
    r"try\s+(to\s+)?pay|"
    r"razorpay|"
    r"checkout"
    r")\b",
    re.I,
)
_RECEIPT_ASK = re.compile(r"\b(receipt|invoice|payment\s+proof|payment\s+slip)\b", re.I)


def wants_payment(text: str) -> bool:
    message = text or ""
    if _RECEIPT_ASK.search(message):
        return False
    return bool(_PAY_ASK.search(message))


def keys_ready(settings: Optional[Settings] = None) -> bool:
    cfg = settings or get_settings()
    return bool((cfg.razorpay_key_id or "").strip() and (cfg.razorpay_key_secret or "").strip())


def public_key_id(settings: Optional[Settings] = None) -> str:
    return ((settings or get_settings()).razorpay_key_id or "").strip()


async def create_checkout(booking: dict[str, Any], guest: dict[str, Any]) -> dict[str, Any]:
    cfg = get_settings()
    key_id = (cfg.razorpay_key_id or "").strip()
    secret = (cfg.razorpay_key_secret or "").strip()
    if not key_id or not secret:
        raise RuntimeError("Razorpay test keys are missing.")
    total = booking.get("total_inr") or 0
    amount = int(round(float(total) * 100))
    if amount < 100:
        raise RuntimeError("Booking total is too small to charge.")
    booking_id = str(booking.get("booking_id") or "")
    hotel = str(booking.get("hotel_name") or "Hotel stay")
    payload = {
        "amount": amount,
        "currency": "INR",
        "receipt": booking_id[:40],
        "notes": {
            "booking_id": booking_id,
            "hotel_name": hotel[:100],
        },
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(RAZORPAY_ORDERS, auth=(key_id, secret), json=payload)
    if response.status_code >= 400:
        logger.warning("razorpay order failed status=%s", response.status_code)
        raise RuntimeError("Could not start Razorpay checkout.")
    order = response.json()
    order_id = str(order.get("id") or "")
    if not order_id:
        raise RuntimeError("Razorpay did not return an order id.")
    check_in = booking.get("check_in") or ""
    check_out = booking.get("check_out") or ""
    return {
        "key_id": key_id,
        "order_id": order_id,
        "amount": amount,
        "currency": "INR",
        "booking_id": booking_id,
        "description": f"{hotel} · {check_in} to {check_out}",
        "prefill": {
            "name": guest.get("name") or "",
            "email": guest.get("email") or "",
            "contact": guest.get("phone") or "",
        },
    }


def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    secret = (get_settings().razorpay_key_secret or "").strip()
    if not secret or not order_id or not payment_id or not signature:
        return False
    body = f"{order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())
