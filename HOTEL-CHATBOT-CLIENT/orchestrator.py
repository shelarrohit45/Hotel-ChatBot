"""Claude Messages API + MCP tool loop.

System prompt is CLAUDE.md + skills.md (loaded once). Tool calls go through
McpBridge after guardrails.allowlist. Final text is redacted before return.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from anthropic import AsyncAnthropic

from config import CLIENT_ROOT, Settings, get_settings
from guardrails import inspect_user_message, redact_output, validate_tool_call
from mcp_bridge import McpBridge

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
MAX_HISTORY_MESSAGES = 24
MAX_TOOL_RESULT_CHARS = 8000
MAX_PHOTOS = 8
_PHOTO_ASK = re.compile(
    r"\b(photos?|pictures?|images?|pics?|gallery|looks?\s+like)\b",
    re.I,
)
_HOTEL_ID = re.compile(r"HTL-PUN-\d{3}", re.I)


def load_system_prompt(root: Path = CLIENT_ROOT) -> str:
    claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
    skills = (root / "skills.md").read_text(encoding="utf-8")
    return claude.strip() + "\n\n---\n\n" + skills.strip()


def to_anthropic_tools(mcp_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools = []
    for tool in mcp_tools:
        tools.append(
            {
                "name": tool["name"],
                "description": tool.get("description") or "",
                "input_schema": tool.get("input_schema")
                or {"type": "object", "properties": {}},
            }
        )
    return tools


class HotelOrchestrator:
    def __init__(self, bridge: McpBridge, settings: Optional[Settings] = None) -> None:
        self.bridge = bridge
        self.settings = settings or get_settings()
        self.system_prompt = load_system_prompt()
        self._client: Optional[AsyncAnthropic] = None
        self._tools: list[dict[str, Any]] = []

    async def prepare(self) -> None:
        mcp_tools = await self.bridge.list_tools()
        self._tools = to_anthropic_tools(mcp_tools)
        if not self._tools:
            raise RuntimeError("MCP server returned no allowed tools")
        logger.info("Orchestrator ready with %s tools", len(self._tools))

    def _client_or_raise(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(api_key=self.settings.require_api_key())
        return self._client

    async def reply(
        self,
        user_text: str,
        history: Optional[list[dict[str, Any]]] = None,
        guest: Optional[dict[str, Any]] = None,
        cached_images: Optional[list[dict[str, str]]] = None,
    ) -> dict[str, Any]:
        history = list(history or [])
        cleaned, blocked = inspect_user_message(user_text, in_session=bool(history))
        if blocked:
            return {
                "text": blocked,
                "history": history,
                "tools_called": [],
                "images": [],
                "blocked": True,
            }

        messages = _trim_history(history) + [{"role": "user", "content": cleaned}]
        tools_called: list[str] = []
        photos: list[dict[str, str]] = []
        client = self._client_or_raise()
        retried_fresh = False
        system = _system_with_guest(self.system_prompt, guest)

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response = await client.messages.create(
                    model=self.settings.anthropic_model,
                    max_tokens=2048,
                    system=system,
                    tools=self._tools,
                    messages=messages,
                )
            except Exception as exc:
                if retried_fresh or "tool_result" not in str(exc):
                    raise
                logger.warning("history reset after tool_result mismatch")
                retried_fresh = True
                messages = [{"role": "user", "content": cleaned}]
                continue
            assistant_blocks = _blocks_to_dicts(response.content)
            messages.append({"role": "assistant", "content": assistant_blocks})

            tool_uses = [b for b in assistant_blocks if b.get("type") == "tool_use"]
            if not tool_uses:
                text = _text_from_blocks(assistant_blocks)
                photos = await self._photos_for_reply(cleaned, messages, photos, cached_images)
                return {
                    "text": redact_output(text),
                    "history": _trim_history(messages),
                    "tools_called": tools_called,
                    "images": photos,
                    "blocked": False,
                }

            results = []
            for block in tool_uses:
                name = block["name"]
                args = _apply_guest(name, block.get("input") or {}, guest)
                tools_called.append(name)
                try:
                    validate_tool_call(name, args)
                    payload = await self.bridge.call_tool(name, args)
                except Exception as exc:
                    logger.warning("tool %s failed: %s", name, type(exc).__name__)
                    payload = {"error": "Tool call was blocked or failed."}
                _collect_hotel_photos(payload, photos)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": json.dumps(payload, ensure_ascii=False)[:MAX_TOOL_RESULT_CHARS],
                    }
                )
            messages.append({"role": "user", "content": results})

        photos = await self._photos_for_reply(cleaned, messages, photos, cached_images)
        return {
            "text": "I could not finish that booking request. Please try a shorter question.",
            "history": _trim_history(messages),
            "tools_called": tools_called,
            "images": photos,
            "blocked": False,
        }

    async def _photos_for_reply(
        self,
        user_text: str,
        messages: list[dict[str, Any]],
        photos: list[dict[str, str]],
        cached_images: Optional[list[dict[str, str]]],
    ) -> list[dict[str, str]]:
        wants_photos = bool(_PHOTO_ASK.search(user_text or ""))
        ids = _hotel_ids_from(user_text) or _hotel_ids_from_history(messages)
        if wants_photos and ids:
            focused: list[dict[str, str]] = []
            for hotel_id in ids[:2]:
                try:
                    payload = await self.bridge.call_tool("get_hotel_details", {"hotel_id": hotel_id})
                except Exception as exc:
                    logger.warning("photo lookup %s failed: %s", hotel_id, type(exc).__name__)
                    continue
                _collect_hotel_photos(payload, focused, limit=4)
            if focused:
                return focused
        if photos:
            return photos[:MAX_PHOTOS]
        if wants_photos and cached_images:
            return list(cached_images)[:MAX_PHOTOS]
        return photos


def _hotel_ids_from(text: str) -> list[str]:
    found = [match.group(0).upper() for match in _HOTEL_ID.finditer(text or "")]
    return list(dict.fromkeys(found))


def _hotel_ids_from_history(messages: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for message in reversed(messages[-12:]):
        content = message.get("content")
        if isinstance(content, str):
            blob = content
        else:
            try:
                blob = json.dumps(content, ensure_ascii=False)
            except TypeError:
                blob = str(content)
        ids.extend(_hotel_ids_from(blob))
        if ids:
            break
    return list(dict.fromkeys(ids))


def _ok_photo_url(url: str) -> bool:
    return url.startswith("https://") or url.startswith("/static/")


def _collect_hotel_photos(payload: Any, photos: list[dict[str, str]], limit: int = MAX_PHOTOS) -> None:
    if len(photos) >= limit:
        return
    if isinstance(payload, list):
        for item in payload:
            _collect_hotel_photos(item, photos, limit)
        return
    if not isinstance(payload, dict):
        return
    caption = str(payload.get("name") or payload.get("hotel_name") or "").strip()
    urls: list[str] = []
    hero = payload.get("image_url")
    if isinstance(hero, str):
        urls.append(hero)
    extra = payload.get("images")
    if isinstance(extra, list):
        urls.extend(str(item) for item in extra if isinstance(item, str))
    seen = {item["url"] for item in photos}
    for url in urls:
        if len(photos) >= limit:
            return
        if not _ok_photo_url(url) or url in seen:
            continue
        photos.append({"url": url, "caption": caption})
        seen.add(url)
    for value in payload.values():
        if isinstance(value, (dict, list)):
            _collect_hotel_photos(value, photos, limit)


def _blocks_to_dicts(content: Any) -> list[dict[str, Any]]:
    blocks = []
    for block in content:
        kind = getattr(block, "type", None)
        if kind == "text":
            blocks.append({"type": "text", "text": block.text or ""})
        elif kind == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": dict(block.input or {}),
                }
            )
    return blocks


def _text_from_blocks(blocks: list[dict[str, Any]]) -> str:
    parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    return "\n".join(p for p in parts if p).strip()


def _is_plain_user(message: dict[str, Any]) -> bool:
    """True for a user turn that is chat text, not a tool_result payload."""
    if message.get("role") != "user":
        return False
    content = message.get("content")
    return isinstance(content, str)


def _trim_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep recent turns. Never start on assistant or orphan tool_result blocks."""
    trimmed = messages[-MAX_HISTORY_MESSAGES:] if len(messages) > MAX_HISTORY_MESSAGES else list(messages)
    while trimmed and not _is_plain_user(trimmed[0]):
        trimmed = trimmed[1:]
    return trimmed


def _system_with_guest(base: str, guest: Optional[dict[str, Any]]) -> str:
    if not guest:
        return base
    name = guest.get("name") or "guest"
    email = guest.get("email") or ""
    phone = guest.get("phone") or ""
    return (
        f"{base}\n\n---\n\nSigned-in guest: {name}, email {email}, mobile {phone}. "
        "Always use these exact values for create_booking, list_bookings, get_booking, "
        "get_booking_status, modify_booking, and cancel_booking. "
        "Never use another person's name, email, or mobile. "
        "This guest can only view, change, or cancel reservations booked with this mobile and email. "
        "Do not ask for name, email, or mobile again."
    )


_OWNED_TOOLS = frozenset(
    {
        "create_booking",
        "list_bookings",
        "get_booking",
        "get_booking_status",
        "modify_booking",
        "cancel_booking",
    }
)


def _apply_guest(tool_name: str, arguments: dict[str, Any], guest: Optional[dict[str, Any]]) -> dict[str, Any]:
    args = dict(arguments or {})
    if not guest:
        return args
    if tool_name not in _OWNED_TOOLS:
        return args
    if guest.get("phone"):
        args["guest_phone"] = guest["phone"]
    if guest.get("email"):
        args["guest_email"] = guest["email"]
        args["customer_id"] = guest["email"]
    if tool_name == "create_booking" and guest.get("name"):
        args["guest_name"] = guest["name"]
    return args

