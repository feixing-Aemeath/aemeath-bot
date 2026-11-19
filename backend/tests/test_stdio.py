import asyncio
import os
import socket
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core import mcp_stdio
from core.api import create_app
from core.lock import InstanceLock
from core.models import MessageInput, PluginPatch, RuntimeSettings
from core.tokens import task_grant
from harness.base import RunResult


@pytest.mark.parametrize(
    "name", ["../../api/v1/jobs/backup/run", "../tools/todos_list", "todos_list?ignored=1"]
)
async def test_stdio_tool_names_cannot_escape_the_internal_route(monkeypatch, name):
    seen, replies = [], []
    client_class = httpx.AsyncClient

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"content": [], "isError": False})

    def client(**kwargs):
        return client_class(**kwargs, transport=httpx.MockTransport(handle))

    class Server:
        def __init__(self, *_args):
            pass

        def list_tools(self):
            return lambda callback: callback

        def call_tool(self):
            def register(callback):
                self.invoke = callback
                return callback

            return register

        def create_initialization_options(self):
            return None

        async def run(self, *_args):
            replies.append(await self.invoke(name, {}))
            replies.append(await self.invoke("todos_list", {}))

    @asynccontextmanager
    async def streams():
        yield None, None

    monkeypatch.setenv("ASSISTANT_TOKEN", "synthetic-token")
    monkeypatch.setenv("ASSISTANT_BASE_URL", "http://127.0.0.1:18765")
    monkeypatch.setattr(mcp_stdio, "Server", Server)
    monkeypatch.setattr(mcp_stdio, "stdio_server", streams)
    monkeypatch.setattr(mcp_stdio.httpx, "AsyncClient", client)
    await mcp_stdio.main()
    assert replies[0].isError and not replies[1].isError
    assert [request.url.path for request in seen] == ["/internal/tools/todos_list"]


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
            assert len((await session.list_tools()).tools) == 14
            result = await session.call_tool("messages_get_unprocessed", {})
            assert result.isError is False
            assert "screenshot_path" not in str(result)
            await session.call_tool(
                "memory_write", {"path": "stdio.md", "content": "跨进程共享记忆"}
            )
            assert seeded.memory.read("stdio.md")["content"] == "跨进程共享记忆"
            result = await session.call_tool("memory_read", {"path": "../../secret.md"})
            assert result.isError is True
        token = seeded.tokens.temporary(task_grant("other_background_task"), 30)
        params.env["ASSISTANT_TOKEN"] = token
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            assert "messages_search" not in {t.name for t in (await session.list_tools()).tools}
            result = await session.call_tool("messages_search", {"query": "报名表"})
            assert result.isError and "tool_not_allowed" in str(result)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


async def test_stdio_enforces_the_actual_extraction_credential(seeded):
    seeded.store.ingest(
        "qq_screen",
        [
            MessageInput(
                external_id="processed-context",
                chat_name="学校",
                sender="老师",
                sent_at=datetime.now(UTC) - timedelta(days=3),
                text="提炼旧通知：宿舍检查14:00开始",
                screenshot_path="private/context.png",
            )
        ],
        ["学校"],
    )
    old = seeded.db.rows("SELECT id FROM messages WHERE external_id='processed-context'")[0]["id"]
    seeded.store.mark_processed([old])
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(create_app(service=seeded), log_level="critical", access_log=False)
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    observed = {}
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.02)
        await seeded.plugins.patch("finance", PluginPatch(enabled=True))
        await seeded.plugins.patch("weekly_review", PluginPatch(enabled=True))
        seeded.knowledge.topic("私人", readers=[])
        (seeded.memory.root / "知识库/私人/秘密.md").write_text("SYNTHETIC_SECRET")
        seeded.memory.write("关于我.md", "合成用户资料")

        class Agent:
            async def run(self, prompt, config, timeout):
                params = StdioServerParameters(
                    command=config["stdio"]["command"],
                    args=config["stdio"]["args"],
                    cwd=config["stdio"]["cwd"],
                    env={
                        **os.environ,
                        **config["environment"],
                        "ASSISTANT_BASE_URL": f"http://127.0.0.1:{sock.getsockname()[1]}",
                    },
                )
                async with (
                    stdio_client(params) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    observed["tools"] = {t.name for t in (await session.list_tools()).tools}
                    observed["history"] = await session.call_tool(
                        "messages_search", {"query": "提炼旧通知"}
                    )
                    observed["finance"] = await session.call_tool(
                        "finance_transactions", {"arguments": {}}
                    )
                    observed["private"] = await session.call_tool(
                        "memory_read", {"path": "知识库/私人/秘密.md"}
                    )
                    observed["plugin"] = await session.call_tool(
                        "weekly_review_write",
                        {"arguments": {"date": "2026-11-05", "text": "不得写入"}},
                    )
                    observed["profile"] = await session.call_tool(
                        "memory_read", {"path": "关于我.md"}
                    )
                    result = await session.call_tool(
                        "messages_mark_processed",
                        {"ids": [r["id"] for r in seeded.store.unprocessed()]},
                    )
                    observed["mark"] = result
                return RunResult(True)

        seeded.jobs.harnesses = {"codex": Agent()}
        seeded.db.put("settings", RuntimeSettings(harness_order=["codex"]).model_dump())
        await seeded.jobs.extract()
        assert "messages_search" in observed["tools"]
        assert not observed["history"].isError
        assert observed["history"].structuredContent["result"][0]["id"] == old
        assert "screenshot_path" not in str(observed["history"])
        assert "finance_transactions" not in observed["tools"]
        assert "weekly_review_write" not in observed["tools"]
        assert all(observed[key].isError for key in ("finance", "private", "plugin"))
        assert "tool_not_allowed" in str(observed["finance"])
        assert "memory_not_allowed" in str(observed["private"])
        assert not observed["profile"].isError and not observed["mark"].isError
        assert "SYNTHETIC_SECRET" not in str(observed)
        assert not list((seeded.memory.root / "周报").glob("*.md"))
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
