"""Morning briefing / weekly report: computed inputs, number validation, Agent flow (mocked)."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from core.api import create_app
from core.mcp_server import create_mcp
from core.models import HealthBatch, PluginPatch, RuntimeSettings
from harness.base import RunResult
from plugins.health.reports import (
    ReportConfig,
    build_input,
    check_report,
    hours_minutes,
    week_summary,
)
from tests.test_api import client_for
from tests.test_health_engine import END, build
from tests.test_health_plugin import night_samples
from tests.test_knowledge import as_reader, bot

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 10, 12)
MORNING = datetime(2026, 10, 12, 7, 30, tzinfo=SHANGHAI)
MANIFEST = Path(__file__).resolve().parents[1] / "plugins" / "health" / "manifest.yaml"


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    engine, _ = build(tmp_path_factory.mktemp("reports"))
    return engine


def numbers_bundle():
    return {
        "date": "2026-10-12",
        "sleep": {"nights": [{"onset": "23:20", "wake": "07:05", "asleep_minutes": 460}]},
        "daily": {"resting_hr": {"value": 55.4, "vs_30d": {"delta": -2.25, "median": 57.65}}},
        "display": {"last_night_asleep": "7小时40分"},
        "baselines": {"windows": {"7d": ["2026-10-05", "2026-10-11"]}, "steps": 8432},
        "goals": {"text": "目标：每周跑步 3 次"},
    }


def test_check_report_accepts_only_numbers_from_the_input():
    bundle = numbers_bundle()
    good = (
        "10月12日：昨晚 23:20 入睡、07:05 醒来，睡了 7小时40分（460 分钟）。"
        "静息心率 55 bpm，比 30 天基线中位数 57.7 低 2.25；近 7 天步数 8,432 步。"
        "按每周 3 次跑步的目标，今天可以正常训练。"
    )
    assert check_report("10月12日晨间健康简报", good, bundle) == []
    bad = "昨晚睡了 7.7 小时，比平时多 12%，连续三天心率偏高，疑似过度训练，建议服用褪黑素。"
    errors = {e["code"]: e["values"] for e in check_report("简报", bad, bundle)}
    assert errors["unsupported_numbers"] == ["12", "7.7"]
    assert errors["chinese_numerals"] == ["三天"]
    assert errors["medical_terms"] == ["疑似", "服用"]
    # Full-width digits and a lone 一 ("这一周") are handled.
    assert check_report("简报", "这一周平均 ５５ bpm", bundle) == []
    assert check_report("简报", "多睡了十分钟", bundle)[0]["code"] == "chinese_numerals"
    # Rounding to fewer decimals is allowed, inventing precision is not.
    assert check_report("简报", "相差 2.3", bundle) == []
    assert check_report("简报", "相差 2.251", bundle)[0]["values"] == ["2.251"]


def test_hours_minutes():
    assert [hours_minutes(m) for m in (460, 45, 60, -25, 89.6)] == [
        "7小时40分",
        "45分",
        "1小时0分",
        "25分",
        "1小时30分",
    ]


def test_morning_input_is_computed_and_self_consistent(synthetic):
    bundle = build_input(synthetic, "morning", END, END, {"path": "g.md", "text": "每周 3 次"})
    assert bundle["kind"] == "morning" and bundle["date"] == END.isoformat()
    assert [d["date"] for d in bundle["daily"]["days"]] == ["2026-10-11", "2026-10-12"]
    night = bundle["sleep"]["nights"][-1]
    assert night["date"] == "2026-10-12" and night["quality"] == "ok"
    assert bundle["display"]["last_night_asleep"] == hours_minutes(night["total_minutes"])
    assert set(bundle["baselines"]["metrics"]) >= {"resting_hr", "sleep_minutes"}
    assert bundle["goals"]["text"] == "每周 3 次"
    # A report quoting the bundle's own values passes validation.
    text = (
        f"{bundle['title_hint']}：{night['onset']} 入睡，{night['wake']} 醒来，"
        f"共 {bundle['display']['last_night_asleep']}。"
    )
    assert check_report(bundle["title_hint"], text, bundle) == []
    text = json.dumps(bundle, ensure_ascii=False)
    assert "Apple Watch" not in text and "示例用户" not in text  # no device or owner names


def test_weekly_summary_compares_with_the_week_before(synthetic):
    week = week_summary(synthetic, END, END)
    assert week["range"] == ["2026-10-06", "2026-10-12"]
    assert week["previous_range"] == ["2026-09-29", "2026-10-05"]
    sleep = week["metrics"]["sleep_minutes"]
    assert 0 < sleep["this_week"]["days"] <= 7
    assert sleep["this_week"]["mean_text"] == hours_minutes(sleep["this_week"]["mean"])
    assert sleep["change"] == sleep["this_week"]["mean"] - sleep["last_week"]["mean"]
    hrv = week["metrics"]["hrv_ln_sdnn"]
    assert hrv["unit"] == "ms" and hrv["this_week"]["mean"] > 10  # SDNN in ms, not ln
    assert week["workouts"]["this_week"]["count"] >= 0
    assert ":" in week["sleep_timing"]["this_week"]["onset_mean"]
    bundle = build_input(synthetic, "weekly", END, END)
    assert "week" in bundle and "daily" not in bundle


def test_manifest_report_defaults_match_code():
    schema = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["config_schema"]
    properties = schema["properties"]["reports"]["properties"]
    defaults = ReportConfig()
    for key, spec in properties.items():
        assert spec["default"] == getattr(defaults, key), key
    with pytest.raises(ValueError):
        ReportConfig.from_dict({"morning_earliest_hour": 11, "morning_deadline_hour": 10})


class ScriptedAgent:
    """Stands in for a model: reads the input over MCP and submits mocked report texts."""

    def __init__(self, mcp, texts):
        self.mcp, self.texts, self.replies, self.prompts = mcp, list(texts), [], []

    async def call(self, name, arguments):
        result = await self.mcp.call_tool(name, {"arguments": arguments})
        content = result[0] if isinstance(result, tuple) else result
        return json.loads(content[0].text)

    async def run(self, prompt, mcp_config, timeout):
        self.prompts.append(prompt)
        kind = "weekly" if "周报" in prompt.splitlines()[0] else "morning"
        day = prompt.split("health_report_input，参数")[1].split('"date": "')[1][:10]
        bundle = await self.call("health_report_input", {"kind": kind, "date": day})
        for text in self.texts:
            body = text(bundle) if callable(text) else text
            reply = await self.call(
                "health_report_write",
                {"kind": kind, "date": day, "title": bundle["title_hint"], "body": body},
            )
            self.replies.append(reply)
            if reply["accepted"]:
                break
        return RunResult(True)


def good_morning(bundle):
    night = bundle["sleep"]["nights"][-1]
    return (
        "## 睡得怎么样\n"
        f"- 昨晚 {night['onset']} 入睡，{night['wake']} 醒来，共睡了 "
        f"{bundle['display']['last_night_asleep']}。\n"
        "## 身体恢复情况\n- 个人基线的数据还不足，暂不判断。\n"
        "## 今天的训练强度建议\n- 中等强度：先观察几天数据再加量。"
    )


BAD = "昨晚睡了 7.7 小时，比平时多 12%，连续三天心率偏高，疑似过度训练。"


async def enable_health(service):
    mcp = create_mcp(service)
    await service.start()
    await service.plugins.patch("health", PluginPatch(enabled=True))
    service.store.health(HealthBatch.model_validate({"samples": night_samples()}))
    await service.jobs.submit("plugin:health:refresh")
    service.memory.write("健康/训练目标.md", "# 训练目标\n\n- 每周跑步 3 次，每次 30 分钟\n")
    plugin = service.plugins.instances["health"]
    plugin.now = lambda: MORNING
    return mcp, plugin


async def test_morning_report_rejects_then_accepts_a_rewrite(service):
    mcp, plugin = await enable_health(service)
    try:
        agent = ScriptedAgent(mcp, [BAD, good_morning])
        service.jobs.harnesses = {"claude_code": agent}
        service.db.put("settings", RuntimeSettings(harness_order=["claude_code"]).model_dump())
        tools = {tool.name for tool in await mcp.list_tools()}
        assert {"health_report_input", "health_report_write"} <= tools
        assert "plugin:health:morning_report" in service.jobs.callbacks

        await service.jobs.submit("plugin:health:morning_report")
        first, second = agent.replies
        assert first["accepted"] is False
        codes = {e["code"] for e in first["errors"]}
        assert codes == {"unsupported_numbers", "chinese_numerals", "medical_terms"}
        assert second["accepted"] is True
        assert "{" not in agent.prompts[0].split("参数")[0]  # placeholders were filled
        assert "2026-10-12" in agent.prompts[0]

        row = plugin.reports.row("morning", DAY)
        assert (row["status"], row["harness"], row["rejections"]) == ("ready", "claude_code", 1)
        bundle = json.loads(row["input_json"])
        assert bundle["goals"]["text"].startswith("# 训练目标")
        note = service.memory.read("健康/晨间简报/2026-10-12.md")["content"]
        assert note.startswith("# 10月12日晨间健康简报") and "共睡了 7小时40分" in note
        cards = await service.plugins.cards("health")
        reports = [card for card in cards if card["type"] == "text"]  # after the metric cards
        assert cards[-1] == reports[0] and reports[0]["data"]["kind"] == "morning"
        assert reports[0]["data"]["text"] == row["body"]
        # The service is already running, so no second lifespan (and instance lock) here.
        client = TestClient(create_app(service=service), headers=client_for(service).headers)
        page = client.get("/api/v1/health/reports?date=2026-10-12&kind=morning").json()
        assert page["total"] == 1 and page["items"][0]["status"] == "ready"
        assert page["items"][0]["body"].startswith("## 睡得怎么样")
        # Already ready: the next tick does nothing.
        await service.jobs.submit("plugin:health:morning_report")
        assert len(agent.prompts) == 1
    finally:
        await service.close()


@pytest.mark.parametrize("kind,folder", [("morning", "晨间简报"), ("weekly", "周报")])
async def test_bot_can_write_health_report_but_memory_write_stays_restricted(service, kind, folder):
    mcp, _ = await enable_health(service)
    try:
        with as_reader(bot("健康")):
            result = await mcp.call_tool(
                "health_report_write",
                {
                    "arguments": {
                        "kind": kind,
                        "date": DAY.isoformat(),
                        "title": "健康简报",
                        "body": "个人基线的数据还不足，暂不判断。",
                    }
                },
            )
            assert json.loads(result[0].text)["accepted"]
            path = f"健康/{folder}/{DAY.isoformat()}.md"
            assert "个人基线的数据还不足" in service.memory.read(path)["content"]
            for other in (path, "健康/训练目标.md", "周报/其他.md", "系统/其他.md"):
                with pytest.raises(ToolError, match="只能写自己的角色记忆和每日笔记"):
                    await mcp.call_tool("memory_write", {"path": other, "content": "不能写"})
    finally:
        await service.close()


async def test_report_that_never_validates_fails_after_both_harnesses(service):
    mcp, plugin = await enable_health(service)
    try:
        service.jobs.harnesses = {
            "claude_code": ScriptedAgent(mcp, [BAD, BAD]),
            "codex": ScriptedAgent(mcp, [BAD]),
        }
        service.db.put("settings", RuntimeSettings(retry_attempts=1).model_dump())
        await service.jobs.submit("plugin:health:morning_report")
        row = plugin.reports.row("morning", DAY)
        assert (row["status"], row["error"], row["body"]) == ("failed", "report_rejected", None)
        runs = service.store.runs(limit=10)["items"]
        attempts = [r for r in runs if r["job"] == "plugin:health:morning_report" and r["harness"]]
        assert {(r["harness"], r["error"]) for r in attempts} == {
            ("claude_code", "report_not_written"),
            ("codex", "report_not_written"),
        }
        # One more generation is allowed (max_attempts=2), then the day is given up.
        await service.jobs.submit("plugin:health:morning_report")
        await service.jobs.submit("plugin:health:morning_report")
        assert len(service.jobs.harnesses["codex"].prompts) == 2
        # Rejections are capped per report (max_rejections=5; this generation used 3).
        replies = [
            await service.jobs.harnesses["codex"].call(
                "health_report_write",
                {"kind": "morning", "date": "2026-10-12", "title": "t", "body": BAD},
            )
            for _ in range(3)
        ]
        assert [r["errors"][0]["code"] for r in replies] == [
            "unsupported_numbers",
            "unsupported_numbers",
            "too_many_attempts",
        ]
        assert replies[1]["attempts_left"] == 0
    finally:
        await service.close()


async def test_morning_waits_for_sleep_until_the_deadline(service):
    mcp, plugin = await enable_health(service)
    try:
        agent = ScriptedAgent(mcp, [good_morning])
        service.jobs.harnesses = {"claude_code": agent, "codex": agent}
        later = DAY + timedelta(days=1)  # no sleep uploaded for this night
        plugin.now = lambda: datetime(2026, 10, 13, 8, 0, tzinfo=SHANGHAI)
        await service.jobs.submit("plugin:health:morning_report")
        assert agent.prompts == [] and plugin.reports.row("morning", later) is None
        plugin.now = lambda: datetime(2026, 10, 13, 10, 0, tzinfo=SHANGHAI)

        def missing_night(bundle):
            assert bundle["display"]["last_night_quality"] == "missing"
            return (
                "## 睡得怎么样\n- 昨晚没有睡眠数据，暂不判断。\n## 今天的训练强度建议\n- 低强度。"
            )

        agent.texts = [missing_night]
        await service.jobs.submit("plugin:health:morning_report")
        assert plugin.reports.status("morning", later) == "ready"
    finally:
        await service.close()


async def test_weekly_report_and_tool_argument_checks(service):
    mcp, plugin = await enable_health(service)
    try:
        sunday = datetime(2026, 10, 18, 21, 0, tzinfo=SHANGHAI)
        plugin.now = lambda: sunday

        def weekly(bundle):
            week = bundle["week"]
            return (
                f"## 本周睡眠\n- 本周有 {week['sleep_timing']['this_week']['nights']} 晚有数据。\n"
                "## 训练\n- 数据不足。\n## 恢复与趋势\n- 数据不足。\n## 下周建议\n- 规律作息。"
            )

        agent = ScriptedAgent(mcp, [weekly])
        service.jobs.harnesses = {"claude_code": agent, "codex": agent}
        await service.jobs.submit("plugin:health:weekly_report")
        assert "健康周报" in agent.prompts[0].splitlines()[0]
        assert plugin.reports.status("weekly", sunday.date()) == "ready"
        assert service.memory.read("健康/周报/2026-10-18.md")["content"].startswith("# 10月18日")
        assert service.jobs.scheduler.get_job("plugin:health:weekly_report") is not None
        for bad in (
            {"kind": "daily", "date": "2026-10-18"},
            {"kind": "weekly", "date": "yesterday"},
            {"kind": "weekly", "date": "2026-10-18", "extra": 1},
        ):
            with pytest.raises(ToolError):
                await agent.call("health_report_input", bad)
        with pytest.raises(ToolError):
            await agent.call(
                "health_report_write",
                {"kind": "weekly", "date": "2026-10-18", "title": "", "body": "x"},
            )
    finally:
        await service.close()
