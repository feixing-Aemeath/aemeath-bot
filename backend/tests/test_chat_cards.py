"""A Bot attaches plugin cards to a chat reply: named by the Agent, copied by the core."""

import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from core.mcp_server import create_mcp
from core.models import ChatMessage, PluginPatch
from tests.test_chat import Agent, finish, ids, tool


def weather_card(day="多云"):
    return {"type": "stat", "title": "上海今天", "data": {"value": "9–21", "label": day}}


@pytest.fixture
async def running(service, monkeypatch):
    """The service with weather and finance on, weather's card fixed, finance's invented."""
    await service.start()
    await service.plugins.patch("weather", PluginPatch(enabled=True))
    await service.plugins.patch("finance", PluginPatch(enabled=True))
    cards = {
        "weather": [weather_card()],
        "finance": [{"type": "stat", "title": "本月还能花", "data": {"value": 1}}],
    }

    async def fixed(identifier):
        return [{**card, "plugin": identifier} for card in cards.get(identifier, [])]

    monkeypatch.setattr(service.plugins, "cards", fixed)
    service.cards = cards
    try:
        yield service
    finally:
        await service.close()


async def test_the_assistant_attaches_a_card_as_it_is_now(running):
    service = running
    mcp = create_mcp(service)
    seen = {}

    async def act(mcp, prompt):
        reply = ids(prompt)[1]
        seen["choices"] = (await tool(mcp, "chat_cards", {"reply_id": reply}))["cards"]
        await tool(
            mcp,
            "chat_reply",
            {
                "reply_id": reply,
                "text": "今天多云，9 到 21 度。",
                "cards": [
                    {"plugin": "weather", "title": "上海今天"},
                    {"plugin": "weather", "title": "上海今天"},  # twice: once
                    {"plugin": "finance", "title": "本月还能花"},  # not hers
                    {"plugin": "weather", "title": "没有这张"},
                    "乱写",
                ],
            },
        )

    service.jobs.harnesses = {"claude_code": Agent(mcp, act), "codex": Agent(mcp)}
    await service.chat.send("assistant", "今天天气怎么样")
    reply, _ = await finish(service)
    # She is offered every running plugin's cards but the accounts, by name only.
    assert {"plugin": "weather", "type": "stat", "title": "上海今天"} in seen["choices"]
    assert all(choice["plugin"] != "finance" for choice in seen["choices"])
    assert all(set(choice) == {"plugin", "type", "title"} for choice in seen["choices"])
    assert reply["text"] == "今天多云，9 到 21 度。"
    [card] = reply["cards"]
    assert card["plugin"] == "weather" and card["data"] == weather_card()["data"]
    assert card["taken_at"]
    ChatMessage.model_validate(reply)

    # The card changes; the reply keeps what it showed.
    service.cards["weather"] = [weather_card("小雨")]
    assert service.chat.page("assistant")["items"][0]["cards"][0]["data"]["label"] == "多云"

    # The next turn's history says a card went with the reply, not what was on it.
    prompts = []

    async def look(mcp, prompt):
        prompts.append(prompt)
        await tool(mcp, "chat_reply", {"reply_id": ids(prompt)[1], "text": "嗯"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, look), "codex": Agent(mcp)}
    await service.chat.send("assistant", "明天呢")
    await finish(service)
    assert "（附了卡片：上海今天）" in prompts[0]
    assert "9–21" not in prompts[0]


async def test_only_carlotta_attaches_the_accounts(running):
    service = running
    assert "finance" not in service.chat.attachable("assistant")
    assert "weather" in service.chat.attachable("assistant")
    assert service.chat.attachable("finance") == ["finance"]
    assert "finance" not in service.chat.attachable("knowledge")


async def test_a_turn_lists_and_attaches_only_for_itself(running):
    service = running
    mcp = create_mcp(service)
    gate = asyncio.Event()
    denied = []

    async def act(mcp, prompt):
        await gate.wait()
        reply = ids(prompt)[1]
        other = next(t["reply"] for t in service.chat.turns.values() if t["reply"] != reply)
        for name, arguments in (
            ("chat_cards", {"reply_id": other}),
            (
                "chat_reply",
                {
                    "reply_id": other,
                    "text": "串台",
                    "cards": [{"plugin": "weather", "title": "上海今天"}],
                },
            ),
        ):
            try:
                await tool(mcp, name, arguments)
            except ToolError:
                denied.append(name)
        await asyncio.sleep(0.05)
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "本轮"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, act), "codex": Agent(mcp)}
    await service.chat.send("assistant", "一")
    await service.chat.send("weather", "二")
    gate.set()
    await finish(service)
    assert sorted(denied) == ["chat_cards", "chat_cards", "chat_reply", "chat_reply"]
    for bot_id in ("assistant", "weather"):
        reply = service.chat.page(bot_id)["items"][0]
        assert reply["text"] == "本轮" and reply["cards"] is None


async def test_cards_must_be_a_list(running):
    service = running
    service.chat.answer = lambda *args: asyncio.sleep(0)
    accepted = await service.chat.send("assistant", "在吗")
    await asyncio.gather(*service.chat.tasks.values())
    mcp = create_mcp(service)
    reply = await tool(
        mcp,
        "chat_reply",
        {"reply_id": accepted["reply"]["id"], "text": "在", "cards": {"plugin": "weather"}},
    )
    assert reply["text"] == "在" and reply["cards"] is None
