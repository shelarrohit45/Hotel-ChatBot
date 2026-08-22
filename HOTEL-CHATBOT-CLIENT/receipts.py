"""Payment receipt HTML for download. Data lives on the booking document."""

from __future__ import annotations

import html
import re
from typing import Any

_RECEIPT_ASK = re.compile(
    r"\b(receipt|invoice|payment\s+proof|payment\s+slip)\b",
    re.I,
)
_BOOKING_ID = re.compile(r"BK-\d+", re.I)


def wants_receipt(text: str) -> bool:
    return bool(_RECEIPT_ASK.search(text or ""))


def booking_id_in(text: str) -> str:
    match = _BOOKING_ID.search(text or "")
    return match.group(0).upper() if match else ""


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def receipt_html(receipt: dict[str, Any]) -> str:
    rows = [
        ("Receipt", receipt.get("receipt_id")),
        ("Booking", receipt.get("booking_id")),
        ("Payment ID", receipt.get("payment_id")),
        ("Order ID", receipt.get("order_id")),
        ("Hotel", receipt.get("hotel_name")),
        ("Room", receipt.get("room_name")),
        ("Check-in", receipt.get("check_in")),
        ("Check-out", receipt.get("check_out")),
        ("Nights", receipt.get("nights")),
        ("Guests", receipt.get("guests")),
        ("Guest", receipt.get("guest_name")),
        ("Email", receipt.get("guest_email")),
        ("Mobile", receipt.get("guest_phone")),
        ("Paid at", receipt.get("paid_at")),
        ("Provider", receipt.get("provider") or "Razorpay"),
        ("Amount", f"INR {receipt.get('total_inr') or 0}"),
        ("Status", "Paid"),
    ]
    body = "".join(
        f"<tr><th>{_e(label)}</th><td>{_e(value)}</td></tr>" for label, value in rows if value not in (None, "")
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Receipt {_e(receipt.get("receipt_id"))}</title>
  <style>
    body {{ font-family: Georgia, serif; background: #f6f1e8; color: #0e0d0b; margin: 0; padding: 32px; }}
    .card {{ max-width: 560px; margin: 0 auto; background: #fff; padding: 28px; border: 1px solid #e0d8cc; }}
    h1 {{ margin: 0 0 6px; font-size: 22px; font-weight: 500; }}
    .mark {{ letter-spacing: 0.2em; text-transform: uppercase; font-size: 11px; color: #7a7168; margin: 0 0 20px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ text-align: left; color: #7a7168; font-weight: 400; padding: 8px 8px 8px 0; width: 38%; }}
    td {{ padding: 8px 0; }}
    .total {{ font-size: 20px; margin-top: 18px; }}
  </style>
</head>
<body>
  <div class="card">
    <p class="mark">The Desk</p>
    <h1>Payment receipt</h1>
    <table>{body}</table>
    <p class="total">Paid INR {_e(receipt.get("total_inr") or 0)}</p>
  </div>
</body>
</html>
"""
