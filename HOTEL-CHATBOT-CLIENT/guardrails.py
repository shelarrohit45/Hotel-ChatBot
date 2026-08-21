"""Security guardrails for the hotel chatbot client.

Layers: input sanitization, jailbreak/injection, topic gate, MCP tool allowlist,
output redaction, in-memory rate limit, HTTP security headers.

The browser never sees secrets. This module must not log ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import html
import re
import time
import unicodedata
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

MAX_MESSAGE_CHARS = 4000
MAX_TOOL_ARG_CHARS = 2000
RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_MAX_REQUESTS = 20

ALLOWED_TOOLS = frozenset(
    {
        "search_hotels",
        "get_hotel_details",
        "get_room_details",
        "check_hotel_availability",
        "get_hotel_rates",
        "create_booking",
        "get_booking",
        "list_bookings",
        "modify_booking",
        "cancel_booking",
        "get_cancellation_policy",
        "get_booking_price",
        "get_booking_status",
        "get_hotel_reviews",
        "search_hotels_by_location",
        "get_hotel_amenities",
    }
)

_INJECTION_PATTERNS = (
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"disregard (your|the) (system )?prompt",
    r"you are now (dan|jailbroken|unfiltered)",
    r"jailbreak",
    r"do not follow (your|the) rules",
    r"reveal (your )?(system prompt|hidden instructions|chain of thought)",
    r"print (your )?(system prompt|api key|\.env)",
    r"show me (the )?(api key|anthropic key|\.env)",
    r"<(script|iframe|img|object|embed)\b",
    r"javascript:",
    r"\beval\s*\(",
    r"\bos\.system\b",
    r"\bsubprocess\b",
    r"rm\s+-rf\b",
)

_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"ANTHROPIC_API_KEY\s*=\s*\S+"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*sk-[^\s]+"),
)

_HOTEL_HINTS = (
    "hotel",
    "room",
    "book",
    "booking",
    "reserv",
    "stay",
    "check-in",
    "check in",
    "check-out",
    "checkout",
    "guest",
    "pune",
    "hinjewadi",
    "koregaon",
    "kharadi",
    "baner",
    "viman",
    "shivaji",
    "amenit",
    "cancel",
    "rate",
    "price",
    "inr",
    "photo",
    "picture",
    "image",
    "gallery",
    "htl-pun",
    "bk-",
    "night",
    "suite",
    "deluxe",
    "review",
    "availab",
    "mobile",
    "phone",
    "marriott",
    "courtyard",
    "westin",
    "conrad",
    "hyatt",
    "ritz",
    "ibis",
    "novotel",
    "radisson",
    "sheraton",
    "hilton",
    "taj",
    "lemon tree",
    "fairfield",
    "residence inn",
)

_GREETING_OR_FOLLOWUP = re.compile(
    r"^(hi|hello|hey|yo|ok|okay|yes|no|thanks|thank you|please|sure|book it|"
    r"that one|this one|the first|the second|first one|second one|"
    r"go ahead|continue|more|details|confirm|proceed|done)\b",
    re.I,
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DATEISH = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?|"
    r"\d{4}-\d{2}-\d{2}|"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)\b",
    re.I,
)

_HTML_TAG = re.compile(r"<[^>]+>")
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.I)


class GuardrailReject(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def sanitize_input(text: str) -> str:
    if text is None:
        return ""
    cleaned = unicodedata.normalize("NFKC", str(text))
    cleaned = cleaned.replace("\x00", "")
    cleaned = _HTML_TAG.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:MAX_MESSAGE_CHARS]


def inspect_user_message(text: str, in_session: bool = False) -> Tuple[str, Optional[str]]:
    """Return (sanitized_text, error_message). error_message is None if allowed."""
    cleaned = sanitize_input(text)
    if not cleaned:
        return cleaned, "Please enter a hotel booking question."
    if _INJECTION_RE.search(cleaned):
        return cleaned, "That request is blocked. I can only help with hotel booking in Pune."
    lowered = cleaned.lower()
    if "sk-ant-" in lowered or (".env" in lowered and "key" in lowered):
        return cleaned, "Do not send secrets in chat. I can only help with hotel booking."
    # Follow-ups (name, email, "that one") are allowed once a hotel thread exists.
    if in_session or _is_on_topic(cleaned):
        return cleaned, None
    return cleaned, "I only help with hotel search and booking in Pune."


def _is_on_topic(text: str) -> bool:
    lowered = text.lower()
    if len(text) <= 120 and _GREETING_OR_FOLLOWUP.search(lowered):
        return True
    if re.search(r"\bHTL-PUN-\d{3}\b", text, re.I) or re.search(r"\bBK-\d+\b", text, re.I):
        return True
    if _EMAIL.search(text) or _DATEISH.search(text):
        return True
    return any(hint in lowered for hint in _HOTEL_HINTS)


def validate_tool_call(name: str, arguments: Optional[dict]) -> dict:
    """Allow only the 16 hotel MCP tools. Reject oversized or non-object args."""
    if name not in ALLOWED_TOOLS:
        raise GuardrailReject(f"Tool not allowed: {name}", "tool_blocked")
    args = arguments or {}
    if not isinstance(args, dict):
        raise GuardrailReject("Tool arguments must be an object.", "bad_args")
    blob = str(args)
    if len(blob) > MAX_TOOL_ARG_CHARS:
        raise GuardrailReject("Tool arguments too large.", "args_too_large")
    if _INJECTION_RE.search(blob):
        raise GuardrailReject("Tool arguments failed security checks.", "args_blocked")
    return args


_TOOL_NAME_LINE = re.compile(
    r"^\s*(?:[-*`•]+\s*)?(?:"
    + "|".join(re.escape(name) for name in sorted(ALLOWED_TOOLS, key=len, reverse=True))
    + r")\s*`?\s*$"
)
_TOOL_NAME_TOKEN = re.compile(
    r"`?("
    + "|".join(re.escape(name) for name in sorted(ALLOWED_TOOLS, key=len, reverse=True))
    + r")`?"
)


def _strip_tool_names(text: str) -> str:
    kept = []
    for line in text.splitlines():
        tokens = [t.strip("`,.") for t in line.split() if t.strip("`,.")]
        if tokens and all(t in ALLOWED_TOOLS for t in tokens):
            continue
        if _TOOL_NAME_LINE.match(line):
            continue
        kept.append(_TOOL_NAME_TOKEN.sub("", line))
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def redact_output(text: str) -> str:
    if not text:
        return text
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    redacted = redacted.replace("/HOTEL-CHATBOT-CLIENT/.env", "[REDACTED_PATH]")
    return _strip_tool_names(redacted)


class RateLimiter:
    """Sliding window, per session. Used by FastAPI in Step 6."""

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
        window_sec: int = RATE_LIMIT_WINDOW_SEC,
    ) -> None:
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, session_id: str) -> bool:
        now = time.monotonic()
        bucket = self._hits[session_id]
        cutoff = now - self.window_sec
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            return False
        bucket.append(now)
        return True


def security_headers() -> Dict[str, str]:
    return {
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://images.unsplash.com; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'"
        ),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cache-Control": "no-store",
    }
