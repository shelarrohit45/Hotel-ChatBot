"""Load Mongo user helpers from the MCP package."""

from __future__ import annotations

import sys
from typing import Optional

from config import get_settings


def _catalog_db():
    root = str(get_settings().mcp_server_cwd)
    if root not in sys.path:
        sys.path.insert(0, root)
    import db as catalog_db

    catalog_db.init_db()
    return catalog_db


def upsert_guest(name: str, email: str, phone: str) -> dict:
    return _catalog_db().upsert_guest(name, email, phone)


def public_user(doc) -> Optional[dict]:
    return _catalog_db().public_user(doc)


def load_login_session(session_id: str) -> Optional[dict]:
    return _catalog_db().load_login_session(session_id)


def save_login_session(session_id: str, user: dict) -> None:
    _catalog_db().save_login_session(session_id, user)


def delete_login_session(session_id: str) -> None:
    _catalog_db().delete_login_session(session_id)


def attach_razorpay_order(booking_id: str, order_id: str, amount_paise: int):
    return _catalog_db().attach_razorpay_order(booking_id, order_id, amount_paise)


def confirm_booking_payment(
    booking_id: str, phone: str, email: str, payment_id: str, order_id: str, signature: str = ""
):
    return _catalog_db().confirm_booking_payment(
        booking_id, phone, email, payment_id, order_id, signature
    )


def fail_booking_payment(
    booking_id: str,
    phone: str,
    email: str,
    reason: str = "",
    payment_id: str = "",
    order_id: str = "",
):
    return _catalog_db().fail_booking_payment(
        booking_id, phone, email, reason, payment_id, order_id
    )


def booking_for_payment(booking_id: str, phone: str, email: str):
    return _catalog_db().booking_for_payment(booking_id, phone, email)


def unpaid_booking_for(phone: str, email: str, booking_id: str = ""):
    return _catalog_db().unpaid_booking_for(phone, email, booking_id)


def list_receipts_for(phone: str, email: str, booking_id: str = ""):
    return _catalog_db().list_receipts_for(phone, email, booking_id)


def get_receipt_for_guest(booking_id: str, phone: str, email: str):
    return _catalog_db().get_receipt_for_guest(booking_id, phone, email)
