"""MCP stdio server for the hotel booking tools.

Protocol layer only. Tool names, descriptions, and input_schema come from
tools.TOOL_DEFINITIONS. Calls go to tools.execute_tool, which reads
MongoDB (hotels, bookings, users).

Uses the official SDK Server (not FastMCP decorators) so the JSON Schema
on each tool stays exactly as defined — including date format and minimums.
"""

import asyncio
import json
import logging

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from tools import TOOL_DEFINITIONS, execute_tool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hotel-booking")

mcp = Server("hotel-booking")


@mcp.list_tools()
async def list_tools() -> list[Tool]:
    """Expose TOOL_DEFINITIONS to the MCP host (input_schema → inputSchema)."""
    return [
        Tool(
            name=definition["name"],
            description=definition["description"],
            inputSchema=definition["input_schema"],
        )
        for definition in TOOL_DEFINITIONS
    ]


@mcp.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    """Run execute_tool and return JSON text to the host."""
    logger.info("tool %s args=%s", name, arguments)
    result = execute_tool(name, arguments or {})
    return [
        TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False, default=str),
        )
    ]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await mcp.run(
            read_stream,
            write_stream,
            mcp.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
