"""Official SDK stdio server. A bridge to the single running core, never a new core."""

import asyncio
import os

import httpx
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server


async def main():
    token = os.environ["ASSISTANT_TOKEN"]
    url = os.environ.get("ASSISTANT_BASE_URL", "http://127.0.0.1:8765")
    from urllib.parse import urlsplit

    from core.config import safe_bind

    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("invalid backend URL")
    safe_bind(parsed.hostname)
    server = Server("assistant")
    async with httpx.AsyncClient(
        base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=120, trust_env=False
    ) as client:

        @server.list_tools()
        async def list_tools():
            response = await client.get("/internal/tools")
            response.raise_for_status()
            return [types.Tool.model_validate(tool) for tool in response.json()]

        @server.call_tool()
        async def call_tool(name, arguments):
            try:
                response = await client.post(f"/internal/tools/{name}", json=arguments or {})
                response.raise_for_status()
                return types.CallToolResult.model_validate(response.json())
            except Exception:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="backend_unavailable")],
                    isError=True,
                )

        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
