"""Stdio MCP client for HOTEL-CHATBOT-MCP.

Spawns server.py with paths from config.py only — never from user input.
Bookings persist in MongoDB, independent of this process lifetime.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import Settings, get_settings
from guardrails import ALLOWED_TOOLS, validate_tool_call

logger = logging.getLogger(__name__)


class McpBridge:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._stdio_cm = None
        self._session_cm = None
        self._session: Optional[ClientSession] = None
        self._lock = None

    def _ensure_lock(self):
        import asyncio

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def start(self) -> None:
        self._ensure_lock()
        params = StdioServerParameters(
            command=str(self._settings.mcp_server_python),
            args=[str(self._settings.mcp_server_script)],
            cwd=str(self._settings.mcp_server_cwd),
        )
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        logger.info("MCP bridge connected to hotel-booking server")

    async def close(self) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(None, None, None)
            self._session_cm = None
            self._session = None
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(None, None, None)
            self._stdio_cm = None

    async def list_tools(self) -> list[dict[str, Any]]:
        session = self._require_session()
        listed = await session.list_tools()
        tools = []
        for tool in listed.tools:
            if tool.name not in ALLOWED_TOOLS:
                continue
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
                }
            )
        return tools

    async def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict[str, Any]:
        args = validate_tool_call(name, arguments)
        session = self._require_session()
        async with self._ensure_lock():
            logger.info("MCP call_tool name=%s", name)
            result = await session.call_tool(name, args)
        return _parse_tool_result(result)

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("MCP bridge is not started. Call start() first.")
        return self._session


def _parse_tool_result(result: Any) -> dict[str, Any]:
    if getattr(result, "isError", False):
        text = _content_text(result)
        return {"error": text or "MCP tool returned an error"}
    text = _content_text(result)
    if not text:
        return {"error": "Empty MCP tool result"}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"error": "MCP tool did not return JSON", "raw": text[:500]}
    if isinstance(payload, dict):
        return payload
    return {"data": payload}


def _content_text(result: Any) -> str:
    parts = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()
