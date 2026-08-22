"""MongoDB access for the hotel catalog, bookings, and users.

Connection string comes from HOTEL-CHATBOT-MCP/.env — never from chat input.
Collections: hotels, bookings, users, counters.
Hotel records are read from MongoDB only.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

logger = logging.getLogger(__name__)

MCP_ROOT = Path(__file__).resolve().parent

load_dotenv(MCP_ROOT / ".env")

PHONE_RE = re.compile(r"\d+")


def normalize_phone(value: Optional[str]) -> str:
    """Keep the last 10 digits so +91 / 0 prefixes still match."""
    digits = "".join(PHONE_RE.findall(value or ""))
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def looks_like_phone(value: Optional[str]) -> bool:
    digits = normalize_phone(value)
    return len(digits) == 10


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = dict(doc)
    out.pop("_id", None)
    return out


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    uri = (os.getenv("MONGODB_URI") or "").strip()
    if not uri:
        raise RuntimeError(
            "MONGODB_URI is missing. Add it to HOTEL-CHATBOT-MCP/.env"
        )
    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    client.admin.command("ping")
    return client


def get_db() -> Database:
    name = (os.getenv("MONGODB_DB") or "hotel_chatbot").strip()
    return get_client()[name]


def hotels_col() -> Collection:
    return get_db()["hotels"]


def bookings_col() -> Collection:
    return get_db()["bookings"]


def users_col() -> Collection:
    return get_db()["users"]


def counters_col() -> Collection:
    return get_db()["counters"]


def sessions_col() -> Collection:
    return get_db()["sessions"]


def ensure_indexes() -> None:
    hotels_col().create_index("hotel_id", unique=True)
    hotels_col().create_index("city")
    bookings_col().create_index("booking_id", unique=True)
    bookings_col().create_index("guest_phone")
    bookings_col().create_index("customer_id")
    bookings_col().create_index("razorpay_order_id")
    bookings_col().create_index("razorpay_payment_id")
    bookings_col().create_index(
        [("hotel_id", ASCENDING), ("room_id", ASCENDING), ("status", ASCENDING)]
    )
    users_col().create_index("email")
    try:
        users_col().drop_index("mobile_1")
    except Exception:
        pass
    # Atlas partial indexes cannot use $regex. $gt:"" = non-empty string only.
    users_col().create_index(
        "mobile",
        unique=True,
        name="mobile_unique_nonempty",
        partialFilterExpression={"mobile": {"$gt": ""}},
    )


_initialized = False


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    ensure_indexes()
    count = hotels_col().count_documents({})
    if count == 0:
        raise RuntimeError(
            "MongoDB hotels collection is empty. Expected documents in hotel_chatbot.hotels."
        )
    logger.info("MongoDB hotels collection has %s documents", count)
    _initialized = True


def list_hotels() -> list[dict]:
    return [_clean(doc) for doc in hotels_col().find({})]


def hotels_by_city(city: str) -> list[dict]:
    return [
        _clean(doc)
        for doc in hotels_col().find({"city": {"$regex": f"^{re.escape(city)}$", "$options": "i"}})
    ]


def find_hotel(hotel_id: str) -> Optional[dict]:
    return _clean(hotels_col().find_one({"hotel_id": hotel_id}))


def next_booking_id() -> str:
    counters_col().update_one(
        {"_id": "booking_seq"},
        {"$setOnInsert": {"seq": 1000}},
        upsert=True,
    )
    doc = counters_col().find_one_and_update(
        {"_id": "booking_seq"},
        {"$inc": {"seq": 1}},
        return_document=ReturnDocument.AFTER,
    )
    return f"BK-{doc['seq']}"


def insert_booking(booking: dict) -> dict:
    doc = dict(booking)
    doc["_id"] = booking["booking_id"]
    bookings_col().insert_one(doc)
    return _clean(doc)


def get_booking(booking_id: str) -> Optional[dict]:
    return _clean(bookings_col().find_one({"booking_id": booking_id}))


def save_booking(booking: dict) -> dict:
    booking_id = booking["booking_id"]
    doc = dict(booking)
    doc["_id"] = booking_id
    bookings_col().replace_one({"_id": booking_id}, doc, upsert=True)
    return _clean(doc)


def list_bookings_for(customer_id: str = "", guest_phone: str = "") -> list[dict]:
    phone = normalize_phone(guest_phone or (customer_id if looks_like_phone(customer_id) else ""))
    email = ""
    if customer_id and not looks_like_phone(customer_id):
        email = customer_id.strip().lower()

    if phone and email:
        query: dict[str, Any] = {
            "guest_phone": phone,
            "$or": [
                {"customer_id": email},
                {"guest_email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}},
            ],
        }
    elif phone:
        query = {"guest_phone": phone}
    elif email:
        query = {
            "$or": [
                {"customer_id": email},
                {"guest_email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}},
            ]
        }
    else:
        return []

    return [_clean(doc) for doc in bookings_col().find(query).sort("created_at", ASCENDING)]


def overlapping_count(
    hotel_id: str,
    room_id: str,
    check_in: str,
    check_out: str,
    skip_booking_id: Optional[str] = None,
) -> int:
    query: dict[str, Any] = {
        "hotel_id": hotel_id,
        "room_id": room_id,
        "status": {"$in": ["confirmed", "pending_payment", "modified"]},
        "check_in": {"$lt": check_out},
        "check_out": {"$gt": check_in},
    }
    if skip_booking_id:
        query["booking_id"] = {"$ne": skip_booking_id}
    return bookings_col().count_documents(query)


def upsert_user(name: str, email: str, phone: str, booking_id: str) -> dict:
    mobile = normalize_phone(phone)
    if not mobile:
        raise ValueError("Mobile number is required")
    now = _now()
    email_n = (email or "").strip().lower()
    existing = users_col().find_one({"mobile": mobile}) or (
        users_col().find_one({"email": email_n}) if email_n else None
    )
    if existing:
        users_col().update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "name": name or existing.get("name"),
                    "email": email_n or existing.get("email"),
                    "mobile": mobile,
                    "updated_at": now,
                },
                "$addToSet": {"booking_ids": booking_id},
            },
        )
        return _clean(users_col().find_one({"_id": existing["_id"]}))
    users_col().update_one(
        {"mobile": mobile},
        {
            "$set": {
                "name": name,
                "email": email_n,
                "mobile": mobile,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now, "_id": mobile},
            "$addToSet": {"booking_ids": booking_id},
        },
        upsert=True,
    )
    return _clean(users_col().find_one({"mobile": mobile}))


def public_user(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    phone = normalize_phone(doc.get("mobile") or doc.get("phone"))
    return {
        "name": doc.get("name") or "",
        "email": doc.get("email") or "",
        "phone": phone,
    }


def upsert_guest(name: str, email: str, phone: str) -> dict:
    """Create or refresh a guest profile from the first-visit form."""
    name_n = (name or "").strip()
    email_n = (email or "").strip().lower()
    mobile = normalize_phone(phone)
    if len(name_n) < 2:
        raise ValueError("Enter your full name")
    if "@" not in email_n or "." not in email_n.split("@")[-1]:
        raise ValueError("Enter a valid email")
    if len(mobile) != 10:
        raise ValueError("Enter a 10-digit mobile number")
    now = _now()
    existing = users_col().find_one({"mobile": mobile}) or users_col().find_one({"email": email_n})
    fields = {
        "name": name_n,
        "email": email_n,
        "mobile": mobile,
        "updated_at": now,
    }
    if existing:
        users_col().update_one({"_id": existing["_id"]}, {"$set": fields})
        return _clean(users_col().find_one({"_id": existing["_id"]}))
    users_col().insert_one(
        {
            "_id": mobile,
            "name": name_n,
            "email": email_n,
            "mobile": mobile,
            "booking_ids": [],
            "created_at": now,
            "updated_at": now,
        }
    )
    return _clean(users_col().find_one({"mobile": mobile}))


def load_login_session(session_id: str) -> Optional[dict]:
    if not session_id:
        return None
    return _clean(sessions_col().find_one({"_id": session_id}))


def save_login_session(session_id: str, user: dict) -> None:
    sessions_col().replace_one(
        {"_id": session_id},
        {"_id": session_id, "user": user, "updated_at": _now()},
        upsert=True,
    )


def delete_login_session(session_id: str) -> None:
    if session_id:
        sessions_col().delete_one({"_id": session_id})


def attach_razorpay_order(booking_id: str, order_id: str, amount_paise: int) -> Optional[dict]:
    now = _now()
    bookings_col().update_one(
        {"booking_id": booking_id},
        {
            "$set": {
                "razorpay_order_id": order_id,
                "razorpay_amount_paise": amount_paise,
                "payment.provider": "razorpay",
                "payment.status": "pending",
                "payment.order_id": order_id,
                "payment.amount_paise": amount_paise,
                "payment.currency": "INR",
                "updated_at": now,
            }
        },
    )
    return get_booking(booking_id)


def confirm_booking_payment(
    booking_id: str,
    phone: str,
    email: str,
    payment_id: str,
    order_id: str,
    signature: str = "",
) -> tuple[Optional[dict], Optional[str]]:
    booking = get_booking(booking_id)
    if not booking:
        return None, "Booking not found."
    booked_phone = normalize_phone(booking.get("guest_phone"))
    booked_email = str(booking.get("guest_email") or booking.get("customer_id") or "").strip().lower()
    if booked_phone != normalize_phone(phone) or booked_email != (email or "").strip().lower():
        return None, "This booking does not match the signed-in guest."
    stored_order = booking.get("razorpay_order_id") or ""
    if stored_order and stored_order != order_id:
        return None, "Payment does not match this booking."
    if booking.get("status") == "confirmed" and booking.get("razorpay_payment_id") == payment_id:
        rec = booking.get("receipt") if isinstance(booking.get("receipt"), dict) else None
        if not rec:
            rec = build_receipt(booking)
            if rec:
                bookings_col().update_one({"booking_id": booking_id}, {"$set": {"receipt": rec}})
                booking = get_booking(booking_id)
        return booking, None
    if booking.get("status") not in {"pending_payment", "confirmed"}:
        return None, "This booking cannot be paid now."
    now = _now()
    receipt = build_receipt(
        {
            **booking,
            "status": "confirmed",
            "razorpay_payment_id": payment_id,
            "razorpay_order_id": order_id,
            "paid_at": now,
        }
    )
    bookings_col().update_one(
        {"booking_id": booking_id},
        {
            "$set": {
                "status": "confirmed",
                "razorpay_payment_id": payment_id,
                "razorpay_order_id": order_id,
                "razorpay_signature": signature or booking.get("razorpay_signature") or "",
                "paid_at": now,
                "updated_at": now,
                "receipt": receipt,
                "payment.provider": "razorpay",
                "payment.status": "captured",
                "payment.order_id": order_id,
                "payment.payment_id": payment_id,
                "payment.signature": signature or "",
                "payment.paid_at": now,
            }
        },
    )
    return get_booking(booking_id), None


def fail_booking_payment(
    booking_id: str,
    phone: str,
    email: str,
    reason: str = "",
    payment_id: str = "",
    order_id: str = "",
) -> tuple[Optional[dict], Optional[str]]:
    booking = get_booking(booking_id)
    if not booking:
        return None, "Booking not found."
    booked_phone = normalize_phone(booking.get("guest_phone"))
    booked_email = str(booking.get("guest_email") or booking.get("customer_id") or "").strip().lower()
    if booked_phone != normalize_phone(phone) or booked_email != (email or "").strip().lower():
        return None, "This booking does not match the signed-in guest."
    if booking.get("status") == "confirmed":
        return booking, None
    if booking.get("status") != "pending_payment":
        return None, "This booking is not awaiting payment."
    now = _now()
    fields: dict[str, Any] = {
        "status": "payment_failed",
        "payment_error": (reason or "Payment failed")[:200],
        "updated_at": now,
        "payment.provider": "razorpay",
        "payment.status": "failed",
        "payment.error": (reason or "Payment failed")[:200],
        "payment.failed_at": now,
    }
    if order_id:
        fields["razorpay_order_id"] = order_id
        fields["payment.order_id"] = order_id
    if payment_id:
        fields["razorpay_payment_id"] = payment_id
        fields["payment.payment_id"] = payment_id
    bookings_col().update_one({"booking_id": booking_id}, {"$set": fields})
    return get_booking(booking_id), None


def booking_for_payment(booking_id: str, phone: str, email: str) -> tuple[Optional[dict], Optional[str]]:
    booking = get_booking(booking_id)
    if not booking:
        return None, "Booking not found."
    booked_phone = normalize_phone(booking.get("guest_phone"))
    booked_email = str(booking.get("guest_email") or booking.get("customer_id") or "").strip().lower()
    if booked_phone != normalize_phone(phone) or booked_email != (email or "").strip().lower():
        return None, "This booking does not match the signed-in guest."
    if booking.get("status") == "confirmed":
        return None, "This stay is already paid."
    if booking.get("status") == "cancelled":
        return None, "This booking is cancelled."
    if booking.get("status") not in {"pending_payment", "payment_failed"}:
        return None, "This booking cannot be paid now."
    if booking.get("status") == "payment_failed":
        bookings_col().update_one(
            {"booking_id": booking_id},
            {"$set": {"status": "pending_payment", "updated_at": _now()}},
        )
        booking = get_booking(booking_id)
    return booking, None


def build_receipt(booking: dict[str, Any]) -> Optional[dict]:
    booking_id = str(booking.get("booking_id") or "")
    payment_id = str(
        booking.get("razorpay_payment_id")
        or (booking.get("payment") or {}).get("payment_id")
        or ""
    )
    if not booking_id or not payment_id:
        return None
    order_id = str(
        booking.get("razorpay_order_id")
        or (booking.get("payment") or {}).get("order_id")
        or ""
    )
    paid_at = str(booking.get("paid_at") or (booking.get("payment") or {}).get("paid_at") or "")
    return {
        "receipt_id": f"RCPT-{booking_id}",
        "booking_id": booking_id,
        "payment_id": payment_id,
        "order_id": order_id,
        "status": "paid",
        "hotel_name": booking.get("hotel_name") or "",
        "hotel_area": booking.get("hotel_area") or "",
        "room_name": booking.get("room_name") or "",
        "check_in": booking.get("check_in") or "",
        "check_out": booking.get("check_out") or "",
        "nights": booking.get("nights") or 0,
        "guests": booking.get("guests") or 0,
        "guest_name": booking.get("guest_name") or "",
        "guest_email": booking.get("guest_email") or "",
        "guest_phone": booking.get("guest_phone") or "",
        "total_inr": booking.get("total_inr") or 0,
        "currency": booking.get("currency") or "INR",
        "paid_at": paid_at,
        "provider": "Razorpay",
    }


def _owned_by(booking: dict, phone: str, email: str) -> bool:
    booked_phone = normalize_phone(booking.get("guest_phone"))
    booked_email = str(booking.get("guest_email") or booking.get("customer_id") or "").strip().lower()
    return booked_phone == normalize_phone(phone) and booked_email == (email or "").strip().lower()


def list_receipts_for(phone: str, email: str, booking_id: str = "") -> list[dict]:
    wanted = (booking_id or "").strip().upper()
    rows = list_bookings_for(email, phone)
    receipts: list[dict] = []
    for booking in rows:
        if booking.get("status") != "confirmed":
            continue
        if wanted and str(booking.get("booking_id") or "").upper() != wanted:
            continue
        rec = booking.get("receipt") if isinstance(booking.get("receipt"), dict) else None
        if not rec or not rec.get("receipt_id"):
            rec = build_receipt(booking)
            if rec:
                bookings_col().update_one(
                    {"booking_id": booking["booking_id"]},
                    {"$set": {"receipt": rec, "updated_at": _now()}},
                )
        if rec:
            receipts.append(rec)
    return receipts


def get_receipt_for_guest(booking_id: str, phone: str, email: str) -> tuple[Optional[dict], Optional[str]]:
    booking = get_booking(booking_id)
    if not booking:
        return None, "Booking not found."
    if not _owned_by(booking, phone, email):
        return None, "This receipt does not match the signed-in guest."
    if booking.get("status") != "confirmed":
        return None, "This stay is not paid yet."
    rec = booking.get("receipt") if isinstance(booking.get("receipt"), dict) else None
    if not rec or not rec.get("receipt_id"):
        rec = build_receipt(booking)
        if rec:
            bookings_col().update_one(
                {"booking_id": booking_id},
                {"$set": {"receipt": rec, "updated_at": _now()}},
            )
    if not rec:
        return None, "No payment receipt for this booking."
    return rec, None
