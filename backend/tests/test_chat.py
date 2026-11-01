"""Chatting with a Bot: POST a line, an Agent answers through chat_reply."""

import asyncio
import json
import re
import threading
import time

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from core.mcp_server import create_mcp
from core.models import PluginPatch
from harness.base import RunResult
from tests.test_api import client_for


class Agent:
    """A scripted harness: reads the prompt like a CLI would and calls MCP tools."""

    def __init__(self, mcp, act=None, result=None):
        self.mcp, self.act, self.prompts, self.models = mcp, act, [], []
        self.result = result or RunResult(True)

    async def run(self, prompt, mcp_config, timeout, **chosen):
        self.prompts.append(prompt)
        self.models.append(chosen.get("model"))
        if self.act:
            await self.act(self.mcp, prompt)
        return self.result


def ids(prompt):
    message = re.search(r"消息 ID ([0-9a-f-]+)", prompt).group(1)
    reply = re.search(r'reply_id 填 "([0-9a-f-]+)"', prompt).group(1)
    return message, reply


async def tool(mcp, name, arguments):
    result = await mcp.call_tool(name, arguments)
    return json.loads(result[0].text)


async def finish(service, bot_id="assistant"):
    await asyncio.gather(*service.chat.tasks.values())
    return service.chat.page(bot_id)["items"]


async def test_a_chat_turn_answers_and_can_make_a_todo(service):
    mcp = create_mcp(service)

    async def act(mcp, prompt):
        message, reply = ids(prompt)
        await tool(
            mcp,
            "todos_add",
            {
                "todo": {
                    "title": "交实验报告",
                    "due_at": "2026-10-30T18:00:00+08:00",
                    "has_time": True,
                    "source_plugin": "chat",
                    "source_refs": [message],
                    "source_excerpt": "周五六点前交实验报告",
                    "confidence": 0.95,
                }
            },
        )
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "记好了，周五 18:00 截止。"})

    agent = Agent(mcp, act)
    service.jobs.harnesses = {"claude_code": agent, "codex": Agent(mcp)}
    await service.start()
    try:
        accepted = await service.chat.send("assistant", "提醒我周五六点前交实验报告")
        assert accepted["message"]["author"] == "me"
        assert accepted["reply"]["status"] == "pending" and accepted["reply"]["text"] == ""
        reply, line = await finish(service)
        assert reply["status"] == "ready" and reply["text"] == "记好了，周五 18:00 截止。"
        assert line["text"] == "提醒我周五六点前交实验报告"
        prompt = agent.prompts[0]
        assert "「助理Bot」" in prompt and "（这是第一句）" in prompt
        [todo] = service.store.todos()["items"]
        assert todo["source_plugin"] == "chat"
        assert todo["sources"][0]["chat_name"] == "和助理Bot的对话"
        # The line is a message for provenance only; extraction never sees it.
        assert service.store.unprocessed() == []
        runs = service.store.runs()["items"]
        assert runs[0]["job"] == "chat:assistant" and runs[0]["status"] == "succeeded"
    finally:
        await service.close()


async def test_plugin_bots_use_their_role_and_the_conversation(service):
    mcp = create_mcp(service)

    async def act(mcp, prompt):
        await tool(mcp, "chat_reply", {"reply_id": ids(prompt)[1], "text": "好的"})

    agent = Agent(mcp, act)
    service.jobs.harnesses = {"claude_code": agent, "codex": agent}
    await service.start()
    try:
        await service.plugins.patch("homework", PluginPatch(enabled=True))
        await service.chat.send("homework", "我还有什么作业？", bot_name="作业Bot")
        await finish(service, "homework")
        await service.chat.send("homework", "那明天的呢", bot_name="作业Bot")
        await finish(service, "homework")
        prompt = agent.prompts[1]
        assert "「作业Bot」" in prompt and "从课程群的待办里整理作业" in prompt
        assert "我：我还有什么作业？\n作业Bot：好的" in prompt
        assert service.chat.page("assistant")["total"] == 0
        assert service.chat.page("homework")["total"] == 4
    finally:
        await service.close()


async def test_a_line_can_pick_the_agent_and_its_model(service):
    mcp = create_mcp(service)

    async def act(mcp, prompt):
        await tool(mcp, "chat_reply", {"reply_id": ids(prompt)[1], "text": "好的"})

    claude, codex = Agent(mcp, act), Agent(mcp, act)
    service.jobs.harnesses = {"claude_code": claude, "codex": codex}
    await service.start()
    try:
        await service.chat.send("assistant", "在吗", harness="codex", model="gpt-6-luna")
        await finish(service)
        assert (len(claude.prompts), codex.models) == (0, ["gpt-6-luna"])
        await service.chat.send("assistant", "再说一次")  # no choice: the usual order
        await finish(service)
        assert claude.models == [None]
        runs = [r["harness"] for r in service.store.runs()["items"]]
        assert runs == ["claude_code", "codex"]  # newest first
    finally:
        await service.close()


async def test_a_picked_agent_that_fails_falls_back_without_its_model(service):
    mcp = create_mcp(service)

    async def act(mcp, prompt):
        await tool(mcp, "chat_reply", {"reply_id": ids(prompt)[1], "text": "好的"})

    claude = Agent(mcp, act)
    codex = Agent(mcp, result=RunResult(False, "quota_exhausted"))
    service.jobs.harnesses = {"claude_code": claude, "codex": codex}
    await service.start()
    try:
        await service.chat.send("assistant", "在吗", harness="codex", model="gpt-6-luna")
        reply, _ = await finish(service)
        assert reply["status"] == "ready"
        assert (codex.models, claude.models) == (["gpt-6-luna"], [None])
    finally:
        await service.close()


async def test_failed_or_silent_agents_fail_the_reply(service):
    mcp = create_mcp(service)
    service.jobs.harnesses = {
        "claude_code": Agent(mcp, result=RunResult(False, "quota_exhausted")),
        "codex": Agent(mcp),  # exits 0 without calling chat_reply
    }
    await service.start()
    try:
        await service.chat.send("assistant", "在吗")
        reply, _ = await finish(service)
        assert reply["status"] == "failed" and reply["error"] == "all_harnesses_failed"
        errors = {r["harness"]: r["error"] for r in service.store.runs()["items"]}
        assert errors == {"claude_code": "quota_exhausted", "codex": "reply_not_written"}
    finally:
        await service.close()


def test_chat_routes_bots_and_one_reply_at_a_time(service):
    gate = threading.Event()  # the app runs in TestClient's own thread and event loop

    async def act(mcp, prompt):
        while not gate.is_set():
            await asyncio.sleep(0.01)
        await tool(mcp, "chat_reply", {"reply_id": ids(prompt)[1], "text": "到"})

    with client_for(service) as client:
        agent = Agent(client.app.state.mcp, act)
        service.jobs.harnesses = {"claude_code": agent, "codex": agent}
        sent = client.post("/api/v1/bots/assistant/messages", json={"text": "  在吗  "})
        assert sent.status_code == 202
        assert sent.json()["message"]["text"] == "在吗"
        assert sent.json()["reply"]["status"] == "pending"
        busy = client.post("/api/v1/bots/assistant/messages", json={"text": "在吗"})
        assert busy.status_code == 409
        for bot in (
            "qq_screen",  # a message source, not a Bot
            "homework",  # not enabled
            "nobody",
        ):
            assert (
                client.post(f"/api/v1/bots/{bot}/messages", json={"text": "hi"}).status_code == 404
            )
        for body in (
            {"text": " "},
            {"text": "x" * 2001},
            {"text": "hi", "extra": 1},
            {"text": "hi", "harness": "gemini"},
            {"text": "hi", "harness": "codex", "model": "rm -rf /"},
            {"text": "hi", "model": "opus"},  # a model needs its Agent
        ):
            assert client.post("/api/v1/bots/assistant/messages", json=body).status_code == 422
        gate.set()
        for _ in range(200):
            page = client.get("/api/v1/bots/assistant/messages").json()
            if page["items"][0]["status"] == "ready":
                break
            time.sleep(0.01)
        assert page["total"] == 2 and page["items"][0]["text"] == "到"
        newest = client.get("/api/v1/bots/assistant/messages?limit=1").json()["items"][0]
        assert newest["author"] == "bot"


async def test_replies_cut_off_by_a_restart_fail(service):
    await service.start()
    await service.plugins.patch("campus", PluginPatch(enabled=True))
    service.jobs.harnesses = {}  # no Agent at all: the reply stays pending

    async def never(*args):
        await asyncio.Event().wait()

    service.chat.answer = never
    await service.chat.send("campus", "我明天请病假")
    service.chat.tasks.clear()  # as if the process died
    service.chat.recover()
    reply = service.chat.page("campus")["items"][0]
    assert reply["status"] == "failed" and reply["error"] == "interrupted"
    await service.close()


async def test_chat_reply_rejects_replies_that_are_not_pending(service):
    mcp = create_mcp(service)
    await service.start()
    try:
        service.chat.answer = lambda *args: asyncio.sleep(0)
        accepted = await service.chat.send("assistant", "在吗")
        await asyncio.gather(*service.chat.tasks.values())
        reply_id = accepted["reply"]["id"]
        assert (await tool(mcp, "chat_reply", {"reply_id": reply_id, "text": "在"}))["text"] == "在"
        for arguments in (
            {"reply_id": reply_id, "text": "又一次"},
            {"reply_id": accepted["message"]["id"], "text": "冒充用户"},
            {"reply_id": "missing", "text": "在"},
        ):
            with pytest.raises(ToolError):
                await mcp.call_tool("chat_reply", arguments)
    finally:
        await service.close()
