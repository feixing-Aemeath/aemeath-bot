"""Charts drawn on request for a chat reply: presets with parameters, numbers from the plugins."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core import charts
from core.mcp_server import create_mcp
from core.models import ChatMessage, PluginPatch
from plugins.health.cards import trend_card
from plugins.homework.plugin import Plugin as Homework
from plugins.token_usage.plugin import Plugin as TokenUsage
from plugins.token_usage.usage import Ledger
from tests.conftest import candidate
from tests.test_chat import Agent, finish, ids, tool
from tests.test_finance import finance  # noqa: F401  (fixture)
from tests.test_token_usage import TODAY, ZONE, seed

DAYS = {"type": "integer", "minimum": 7, "maximum": 31, "default": 14}


async def build(values):
    return None


def test_presets_need_typed_params_with_defaults_in_range():
    good = {"title": "图", "description": "说明", "params": {"days": DAYS}, "build": build}
    assert charts.valid("sleep", good)
    assert charts.public("sleep", good) == {
        "chart": "sleep",
        "title": "图",
        "description": "说明",
        "params": {"days": DAYS},
    }
    for broken in (
        {**good, "build": lambda values: None},  # not async
        {**good, "params": {"days": {**DAYS, "default": 40}}},
        {**good, "params": {"days": {**DAYS, "minimum": True}}},
        {**good, "params": {"kind": {"type": "string", "enum": [], "default": "a"}}},
        {**good, "params": {"Bad-Name": DAYS}},
        {**good, "title": None},
    ):
        assert not charts.valid("sleep", broken)
    assert not charts.valid("Sleep", good)


def test_params_are_checked_and_defaulted():
    spec = {
        "params": {
            "days": DAYS,
            "metric": {"type": "string", "enum": ["sleep", "steps"], "default": "sleep"},
        }
    }
    assert charts.values(spec, None) == {"days": 14, "metric": "sleep"}
    assert charts.values(spec, {"days": 30, "metric": "steps"}) == {"days": 30, "metric": "steps"}
    for bad in ({"days": 32}, {"days": True}, {"days": "30"}, {"metric": "hrv"}, {"other": 1}, []):
        with pytest.raises(ValueError):
            charts.values(spec, bad)


async def test_every_plugins_presets_are_offered(service, finance):  # noqa: F811
    await service.plugins.discover()
    for plugin in (Homework(), TokenUsage(), finance):
        presets = plugin.charts()
        assert presets and all(charts.valid(name, spec) for name, spec in presets.items())
    await service.plugins.patch("health", PluginPatch(enabled=True))
    assert set(service.plugins.charts("health")) == {"sleep_stages", "trend"}
    # No data yet: nothing to draw, and no error.
    assert await service.plugins.chart("health", "trend", {"metric": "steps"}) is None
    with pytest.raises(KeyError):
        await service.plugins.chart("health", "missing", {})
    with pytest.raises(ValueError):
        await service.plugins.chart("health", "trend", {"days": 99})


def test_trend_skips_days_without_a_good_value():
    def day(text, minutes, quality="ok"):
        view = {"value": minutes, "quality": quality}
        return {"date": text, "metrics": {"sleep_minutes": view}, "hrv_preferred": None}

    days = [day("2026-11-01", 420), day("2026-11-02", None, "missing"), day("2026-11-03", 390)]
    card = trend_card(days, "sleep")
    assert card["title"] == "最近 3 天总睡眠"
    assert card["data"] == {
        "kind": "line",
        "unit": "小时",
        "points": [{"x": "11/1", "y": 7.0}, {"x": "11/3", "y": 6.5}],
        "zero": False,
    }
    steps = [{**d, "metrics": {"steps": {"value": 5000, "quality": "ok"}}} for d in days]
    assert "zero" not in trend_card(steps, "steps")["data"]
    assert trend_card(days[:2], "sleep") is None
    hrv = [{**d, "hrv_preferred": "hrv_ln_rmssd"} for d in days]
    for d, ms in zip(hrv, (60, None, 52), strict=True):
        d["metrics"] = {"hrv_ln_rmssd": {"quality": "ok" if ms else "missing", "rmssd_ms": ms}}
    assert trend_card(hrv, "hrv")["title"] == "最近 3 天 HRV"
    assert trend_card(days, "hrv") is None


def test_token_presets_take_their_days(service):
    seed(service)
    ledger = Ledger(service.db, ZONE, TODAY, {})
    chart = ledger.chart_card(30)
    assert chart["title"] == "最近 30 天消耗" and len(chart["data"]["points"]) == 30
    agents = ledger.agents_card(30)
    assert agents["title"] == "最近 30 天各 Agent 的用量"
    assert [p["x"] for p in agents["data"]["points"]] == ["Claude Code", "Codex"]
    assert Ledger(service.db, ZONE, TODAY + timedelta(days=60), {}).agents_card(1) is None


async def test_finance_presets_draw_from_the_ledger(finance):  # noqa: F811
    await finance.finance_add(
        {"amount": 30, "category": "吃饭", "occurred_at": "2026-10-25T12:00:00+08:00"}
    )
    month = await finance.month_chart({"months_ago": 0})
    assert month["data"]["kind"] == "donut" and month["data"]["value"] == "30"
    assert await finance.month_chart({"months_ago": 3}) is None


async def test_a_reply_attaches_a_homework_chart_with_its_params(seeded):
    service = seeded
    await service.start()
    try:
        await service.plugins.patch("homework", PluginPatch(enabled=True))
        zone = ZoneInfo(service.store.settings.timezone)
        tomorrow = (datetime.now(zone) + timedelta(days=1)).replace(hour=18, minute=0, second=0)
        service.store.add_todo(
            candidate(service, title="交数据结构作业", due_at=tomorrow.isoformat())
        )
        mcp = create_mcp(service)
        seen = {}

        async def act(mcp, prompt):
            reply = ids(prompt)[1]
            seen["choices"] = await tool(mcp, "chat_cards", {"reply_id": reply})
            await tool(
                mcp,
                "chat_reply",
                {
                    "reply_id": reply,
                    "text": "明天要交数据结构作业。",
                    "cards": [
                        {"plugin": "homework", "chart": "upcoming", "params": {"days": 99}},
                        {"plugin": "homework", "chart": "nothing"},
                        {"plugin": "homework", "chart": "upcoming", "params": {"days": 3}},
                        {"plugin": "homework", "chart": "weekly"},
                    ],
                },
            )

        service.jobs.harnesses = {"claude_code": Agent(mcp, act), "codex": Agent(mcp)}
        await service.chat.send("assistant", "这几天有什么作业")
        reply, _ = await finish(service)
        offered = {(c["plugin"], c["chart"]) for c in seen["choices"]["charts"]}
        assert {("homework", "upcoming"), ("homework", "weekly")} <= offered
        assert all(c["plugin"] != "finance" for c in seen["choices"]["charts"])
        upcoming, weekly = reply["cards"]
        assert upcoming["title"] == "接下来 3 天要交的作业"
        assert upcoming["data"]["items"][0]["title"] == "交数据结构作业"
        assert weekly["title"] == "最近 8 周的作业" and len(weekly["data"]["points"]) == 8
        assert all(card["plugin"] == "homework" and card["taken_at"] for card in reply["cards"])
        ChatMessage.model_validate(reply)
    finally:
        await service.close()
