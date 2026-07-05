#!/usr/bin/env python3
"""Smoke test: drive the stdio MCP server as a client, list + call tools.

Run with the sim container up (docker compose up -d) so calls return live data.
"""
import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))


async def main() -> None:
    params = StdioServerParameters(
        command=os.path.join(HERE, ".venv", "bin", "python"),
        args=[os.path.join(HERE, "ros_steering_mcp.py")],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            health = await session.call_tool("get_steering_health", {})
            print("get_steering_health ->", health.content[0].text)

            nodes = await session.call_tool("get_node_list", {})
            print("get_node_list ->", nodes.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
