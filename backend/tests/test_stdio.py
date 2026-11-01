import asyncio
import os
import socket
import sys
from pathlib import Path

import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core.api import create_app
from core.lock import InstanceLock


async def test_stdio_process_uses_same_core_database(seeded):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(service=seeded), log_level="critical", access_log=False)
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.02)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "core.mcp_stdio"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env={
                **os.environ,
                "ASSISTANT_TOKEN": seeded.settings.token.get_secret_value(),
                "ASSISTANT_BASE_URL": f"http://127.0.0.1:{port}",
            },
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            assert len((await session.list_tools()).tools) == 12
            result = await session.call_tool("messages_get_unprocessed", {})
            assert result.isError is False
            assert "screenshot_path" not in str(result)
            await session.call_tool(
                "memory_write", {"path": "stdio.md", "content": "跨进程共享记忆"}
            )
            assert seeded.memory.read("stdio.md")["content"] == "跨进程共享记忆"
            result = await session.call_tool("memory_read", {"path": "../../secret.md"})
            assert result.isError is True
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


def test_second_core_cannot_acquire_lock(tmp_path):
    first, second = InstanceLock(tmp_path / "lock"), InstanceLock(tmp_path / "lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="another_backend"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
