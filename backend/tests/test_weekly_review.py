"""Weekly aggregates and review delivery, entirely with temporary data and fake Agents."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from mcp.server.fastmcp.exceptions import ToolError

from core.db import uid
from core.mcp_server import create_mcp
from core.models import HealthBatch, PluginPatch, RuntimeSettings
from harness.base import RunResult, Usage
from plugins.timetable import page
from plugins.weekly_review.plugin import review_schedule, week_id
from tests.test_health_plugin import night_samples
from tests.test_knowledge import as_reader, bot
from tests.test_school import add_todo, call, ingest

ZONE = ZoneInfo("Asia/Shanghai")
MONDAY = date(2026, 10, 26)
NOW = datetime(2026, 11, 1, 23, 59, 59, 500000, tzinfo=ZONE)
TOOLS = {"weekly_review_input", "weekly_review_write"}


@pytest.fixture
async def weekly(service):
    mcp = create_mcp(service)
    await service.start()
    await service.plugins.patch("weekly_review", PluginPatch(enabled=True))
    plugin = service.plugins.instances["weekly_review"]
    plugin.now = lambda: NOW
    try:
        yield service, mcp, plugin
    finally:
        await service.close()


def todo(
    service, title, status="pending", due=None, updated="2026-10-27T12:00:00+08:00", has_time=True
):
    message = ingest(service, "示例群", "原始聊天只留本地", uid())
    item = add_todo(
        service, message, title, "原始聊天只留本地", due, has_time=bool(due) and has_time
    )
    service.db.execute(
        "UPDATE todos SET status=?,created_at=?,updated_at=?,notes=? WHERE id=?",
        (status, "2026-10-20T12:00:00+00:00", updated, "私密原文笔记", item["id"]),
    )
    return item


async def enable(service, identifier, config=None):
    await service.plugins.patch(identifier, PluginPatch(enabled=True, config=config or {}))
    return service.plugins.instances[identifier]


async def timetable(service, monkeypatch):
    async def offline(*_args):
        raise page.PageError("screen_page_not_open")

    monkeypatch.setattr(page, "read_window", offline)
    service.plugins.platform = "macos"
    # Absolute on every system; on Windows a path without a drive is not.
    helper = str(Path("/fake/screen-helper").resolve())
    plugin = await enable(service, "timetable", {"helper": helper})
    await plugin.reading
    return plugin


async def test_default_disabled_and_lifecycle(service):
    mcp = create_mcp(service)
    await service.start()
    try:
        view = service.plugins.view("weekly_review")
        assert view["state"] == "disabled" and view["platforms"] == ["any"]
        assert view["mascot"]["sticker"] == "aemeath"
        assert not TOOLS & {t.name for t in await mcp.list_tools()}
        await enable(service, "weekly_review")
        assert TOOLS <= {t.name for t in await mcp.list_tools()}
        assert "plugin:weekly_review:review" in service.jobs.callbacks
        await service.plugins.patch("weekly_review", PluginPatch(enabled=False))
        assert not TOOLS & {t.name for t in await mcp.list_tools()}
        assert "plugin:weekly_review:review" not in service.jobs.callbacks
    finally:
        await service.close()


async def test_missing_and_disabled_plugins(weekly):
    service, mcp, _ = weekly
    data = await call(mcp, "weekly_review_input", {})
    assert (data["from"], data["to"], data["week"]) == ("2026-10-26", "2026-11-01", "2026-W44")
    for key in ("todos", "homework", "timetable", "activities", "health", "tokens"):
        assert data[key]["status"] == "没有数据"
    assert data["next_week"]["todos"] == "没有数据"
    todo(service, "数学作业")
    await enable(service, "homework")
    assert (await call(mcp, "weekly_review_input", {}))["homework"]["status"] == "ok"
    await service.plugins.patch("homework", PluginPatch(enabled=False))
    assert (await call(mcp, "weekly_review_input", {}))["homework"] == {"status": "没有数据"}


async def test_all_weekly_numbers_and_only_aggregate_data(weekly, monkeypatch):
    service, mcp, _ = weekly
    todo(service, "周一做完", "completed", updated="2026-10-26T00:00:00+08:00")
    done = todo(service, "高数练习", "completed", updated="2026-11-01T23:59:59+08:00")
    todo(service, "前周完成", "completed", updated="2026-10-25T23:59:59+08:00")
    todo(service, "下周完成", "completed", updated="2026-11-02T00:00:00+08:00")
    todo(service, "已经删除", "deleted", due="2026-10-28T12:00:00+08:00")
    todo(service, "错题作业", due="2026-10-28T12:00:00+08:00")
    todo(service, "体测考试", due="2026-11-02T00:00:00+08:00")
    todo(service, "下周作业", due="2026-11-08T23:59:59+08:00")
    todo(service, "再下一周", due="2026-11-09T00:00:00+08:00")
    homework = await enable(service, "homework")
    await homework.homework_mark({"todo_id": done["id"], "course": "高数"})
    classes = await timetable(service, monkeypatch)
    classes.store_week(
        page.Week(
            MONDAY,
            (
                page.Entry(MONDAY, "08:00", "09:35", "高数", "课程"),
                page.Entry(MONDAY + timedelta(days=6), "14:00", "15:35", "英语", "课程"),
                page.Entry(MONDAY, "10:00", "11:00", "自己的日程", "日程"),
            ),
        ),
        NOW,
    )
    campus = await enable(service, "campus")
    ref = ingest(service, "活动群", "活动通知原文", uid())
    await campus.campus_event_add(
        {
            "title": "志愿服务",
            "kind": "志愿",
            "starts_at": "2026-10-31T10:00:00+08:00",
            "source_refs": [ref],
        }
    )
    health = await enable(service, "health")

    async def daily(arguments):
        assert arguments == {"date": "2026-11-01", "days": 7}
        return {
            "days": [
                {"date": "2026-10-26", "readiness": {"score": 4.5}},
                {"date": "2026-11-01", "readiness": {"score": 6.0}},
            ]
        }

    async def sleep(arguments):
        assert arguments == {"date": "2026-11-01", "days": 7}
        return {
            "nights": [
                {"date": "2026-10-26", "quality": "ok", "total_minutes": 420},
                {"date": "2026-10-27", "quality": "partial", "total_minutes": 100},
                {"date": "2026-11-01", "quality": "ok", "total_minutes": 480},
            ]
        }

    monkeypatch.setattr(health, "health_get_daily", daily)
    monkeypatch.setattr(health, "health_get_sleep", sleep)
    tokens = await enable(service, "token_usage", {"read_sessions": False, "read_limits": False})
    await tokens.importing
    for at in (
        "2026-10-25T23:59:59+08:00",
        "2026-10-26T00:00:00+08:00",
        "2026-11-01T23:59:59+08:00",
        "2026-11-02T00:00:00+08:00",
    ):
        run = service.db.start_run("fixture", "codex")
        service.db.record_usage(run, "fixture", "codex", Usage(100, 80, 20))
        service.db.execute(
            "UPDATE run_usage SET recorded_at=? WHERE run_id=?",
            (
                datetime.fromisoformat(at).astimezone(UTC).isoformat(),
                run,
            ),
        )
    service.db.execute(
        "INSERT INTO local_usage VALUES(?,?,?,?,?,?)",
        (
            "own",
            "codex",
            "2026-10-30T12:00:00+00:00",
            20,
            10,
            10,
        ),
    )

    data = await call(mcp, "weekly_review_input", {})
    assert data["todos"]["completed"] == 2
    assert data["todos"]["overdue"] == ["错题作业"]
    assert data["homework"]["completed"] == 1
    assert {t["title"] for t in data["homework"]["pending"]} == {"错题作业", "下周作业"}
    assert data["timetable"]["classes"] == 2
    assert data["activities"]["recorded_titles"] == ["志愿服务"]
    assert data["activities"]["attended_or_registered"] == "没有数据"
    assert data["health"]["average_sleep_minutes"] == 450
    assert data["health"]["sleep_days"] == 2 and data["health"]["readiness_change"] == 1.5
    assert (data["tokens"]["total"], data["tokens"]["assistant"], data["tokens"]["own"]) == (
        270,
        240,
        30,
    )
    assert [t["title"] for t in data["next_week"]["todos"]] == ["体测考试", "下周作业"]
    assert [t["title"] for t in data["next_week"]["homework"]] == ["下周作业"]
    assert [t["title"] for t in data["next_week"]["exams"]] == ["体测考试"]
    encoded = json.dumps(data, ensure_ascii=False)
    assert all(
        s not in encoded
        for s in ("原始聊天", "私密原文", "source_refs", "source_excerpt", "sender")
    )


async def test_empty_enabled_plugins_and_timetable_estimates(weekly, monkeypatch):
    service, mcp, plugin = weekly
    for identifier in ("homework", "campus", "health"):
        await enable(service, identifier)
    tokens = await enable(service, "token_usage", {"read_sessions": False, "read_limits": False})
    await tokens.importing
    classes = await timetable(service, monkeypatch)
    data = await plugin.review_input({})
    assert all(
        data[k]["status"] == "没有数据"
        for k in ("homework", "activities", "health", "tokens", "timetable")
    )
    prior = MONDAY - timedelta(days=7)
    classes.store_week(
        page.Week(prior, (page.Entry(prior, "08:00", "09:35", "高数", "课程"),)), NOW
    )
    data = await call(mcp, "weekly_review_input", {})
    assert data["timetable"]["classes"] == 1
    assert data["timetable"]["days"][0]["estimated_from"] == prior.isoformat()


async def test_health_uses_the_existing_public_tools_with_synthetic_uploads(weekly):
    service, _, plugin = weekly
    health = await enable(service, "health")
    service.store.health(HealthBatch.model_validate({"samples": night_samples()}))
    result = await plugin.review_input({"date": "2026-10-12"})
    sleep = await health.health_get_sleep({"date": "2026-10-18", "days": 7})
    night = next(n for n in sleep["nights"] if n["date"] == "2026-10-12")
    assert result["health"]["average_sleep_minutes"] == night["total_minutes"] == 460
    assert result["health"]["sleep_days"] == 1


async def test_date_only_deadline_lasts_until_midnight_and_next_week_boundaries(weekly):
    service, _, plugin = weekly
    todo(service, "周日交表", due="2026-11-01T00:00:00+08:00", has_time=False)
    todo(service, "周日限时", due="2026-11-01T23:59:00+08:00")
    data = await plugin.review_input({})
    assert data["todos"]["overdue"] == ["周日限时"]
    plugin.now = lambda: datetime(2026, 11, 2, 0, tzinfo=ZONE)
    data = await plugin.review_input({"date": "2026-11-01"})
    assert set(data["todos"]["overdue"]) == {"周日交表", "周日限时"}
    current = await plugin.review_input({})
    assert current["from"] == "2026-11-02"


async def test_one_plugin_failure_does_not_hide_other_sections(weekly, monkeypatch):
    service, _, plugin = weekly
    todo(service, "待交作业", due="2026-10-28T12:00:00+08:00")
    homework = await enable(service, "homework")

    def unavailable():
        raise RuntimeError("plugin_unavailable")

    monkeypatch.setattr(homework, "homework", unavailable)
    data = await plugin.review_input({})
    assert data["homework"] == {"status": "没有数据"}
    assert data["next_week"]["homework"] == "没有数据"
    assert data["todos"]["overdue"] == ["待交作业"]


async def test_exact_monday_midnight_includes_a_new_todo(weekly):
    service, _, plugin = weekly
    item = todo(service, "周一新事项")
    midnight = "2026-10-26T00:00:00+08:00"
    service.db.execute("UPDATE todos SET created_at=? WHERE id=?", (midnight, item["id"]))
    plugin.now = lambda: datetime.fromisoformat(midnight)
    data = await plugin.review_input({})
    assert data["from"] == "2026-10-26"
    assert [t["title"] for t in data["todos"]["pending"]] == ["周一新事项"]


async def test_write_cards_memory_daily_notes_and_expiry(weekly):
    service, mcp, plugin = weekly
    assert await plugin.cards() == []
    written = await call(mcp, "weekly_review_write", {"date": "2026-10-28", "text": "这周做得好。"})
    assert written == {"accepted": True, "week": "2026-W44", "path": "周报/2026-W44.md"}
    assert (await service.plugins.cards("weekly_review"))[0]["data"]["text"] == "这周做得好。"
    original = service.memory.read(written["path"])["content"]
    service.memory.write(written["path"], original + "\n自己补充的笔记。\n")
    await plugin.review_write({"date": "2026-11-01", "text": "更新后的回顾。"})
    content = service.memory.read(written["path"])["content"]
    assert "更新后的回顾。" in content and "这周做得好。" not in content
    assert (
        content.split("## 笔记\n", 1)[1]
        == (original + "\n自己补充的笔记。\n").split("## 笔记\n", 1)[1]
    )
    assert service.memory.search("更新后的回顾")
    await service.daily.write(NOW.date())
    today = service.memory.read("每日/2026-11-01/今天.md")["content"]
    assert "[本周回顾](本周回顾.md)：本周回顾写好了：2026-W44" in today
    assert "../../周报/2026-W44.md" in service.memory.read("每日/2026-11-01/本周回顾.md")["content"]
    assert await plugin.daily(date(2026, 11, 2)) is None
    plugin.now = lambda: datetime(2026, 11, 3, 23, 59, 59, tzinfo=ZONE)
    assert len(await plugin.cards()) == 1
    plugin.now = lambda: datetime(2026, 11, 4, 0, tzinfo=ZONE)
    assert await plugin.cards() == []
    assert service.memory.read(written["path"])


async def test_bot_can_write_review_but_memory_write_stays_restricted(weekly):
    service, mcp, _ = weekly
    with as_reader(bot()):
        result = await call(
            mcp, "weekly_review_write", {"date": "2026-11-01", "text": "本周回顾正文。"}
        )
        assert result["accepted"]
        assert "本周回顾正文。" in service.memory.read(result["path"])["content"]
        for path in (result["path"], "周报/其他.md", "健康/训练目标.md", "系统/其他.md"):
            with pytest.raises(ToolError, match="只能写自己的角色记忆和每日笔记"):
                await mcp.call_tool("memory_write", {"path": path, "content": "不能写"})


async def test_review_survives_reenable_and_iso_year_file_name(weekly):
    service, _, plugin = weekly
    await plugin.review_write({"date": "2026-11-01", "text": "已保存的回顾。"})
    await service.plugins.patch("weekly_review", PluginPatch(enabled=False))
    plugin = await enable(service, "weekly_review")
    plugin.now = lambda: NOW
    assert (await plugin.cards())[0]["data"]["text"] == "已保存的回顾。"
    result = await plugin.review_write({"date": "2025-12-31", "text": "跨年的一周。"})
    assert result["path"] == "周报/2026-W01.md"
    assert "2025-12-29–2026-01-04" in service.memory.read(result["path"])["content"]


async def test_file_write_failure_does_not_publish_a_card(weekly, monkeypatch):
    service, _, plugin = weekly

    def unavailable(*_args):
        raise OSError("write failed")

    monkeypatch.setattr(service.memory, "save", unavailable)
    with pytest.raises(OSError):
        await plugin.review_write({"date": "2026-11-01", "text": "没能写到文件。"})
    assert await plugin.cards() == []
    assert plugin.context.get("reviews", {}) == {}


class Agent:
    def __init__(self, effect=None, result=None):
        self.effect, self.result, self.prompts = effect, result or RunResult(True), []

    async def run(self, prompt, mcp_config, timeout):
        self.prompts.append(prompt)
        if self.effect:
            await self.effect()
        return self.result


async def test_agent_default_fallback_persona_and_rerun_requires_a_new_write(weekly):
    service, mcp, plugin = weekly
    path = service.memory.path("Bots/助理/soul.md")
    path.parent.mkdir(parents=True)
    path.write_text("称呼{PlayerName}，自然地说话。", encoding="utf-8")
    service.memory.write("Bots/助理/记忆.md", "周末整理错题。")

    async def write():
        await call(mcp, "weekly_review_write", {"date": "2026-11-01", "text": "本周回顾正文。"})

    first, second = Agent(), Agent(write)
    service.jobs.harnesses = {"deepseek": first, "codex": second}
    service.db.put("settings", RuntimeSettings(harness_order=["codex"]).model_dump())
    await plugin.review()
    assert len(first.prompts) == len(second.prompts) == 1
    assert "称呼{PlayerName}" in first.prompts[0] and "周末整理错题。" in first.prompts[0]
    assert "weekly_review_input" in first.prompts[0] and "weekly_review_write" in first.prompts[0]
    runs = {r["harness"]: r for r in service.store.runs()["items"] if r["harness"]}
    assert runs["deepseek"]["error"] == "review_not_written"
    assert runs["codex"]["status"] == "succeeded"
    second.effect = None
    with pytest.raises(RuntimeError, match="all_harnesses_failed"):
        await plugin.review()  # An old review must not satisfy this run's succeeded callback.
    assert len(first.prompts) == len(second.prompts) == 2
    assert all(r["status"] == "failed" for r in service.store.runs()["items"][:2])


async def test_missing_write_fails_the_scheduled_job(weekly):
    service, _, _ = weekly
    service.jobs.harnesses = {"deepseek": Agent()}
    service.db.put(
        "settings", RuntimeSettings(harness_order=["deepseek"], retry_attempts=1).model_dump()
    )
    await service.jobs.submit("plugin:weekly_review:review")
    runs = service.store.runs()["items"]
    assert len(runs) == 2 and all(r["status"] == "failed" for r in runs)
    assert {r["error"] for r in runs} == {"review_not_written", "all_harnesses_failed"}


async def test_chosen_agent_schedule_and_bad_arguments(weekly):
    service, mcp, _ = weekly
    plugin = await enable(
        service, "weekly_review", {"review_agent": "codex", "review_cron": "15 20 * * 7"}
    )
    plugin.now = lambda: NOW
    calls = []

    async def run_agent(job, prompt, succeeded, incomplete_error, harness):
        calls.append((job, harness))
        assert not succeeded()
        await plugin.review_write({"date": "2026-11-01", "text": "回顾写好了。"})
        assert succeeded() and incomplete_error == "review_not_written"

    plugin.context.run_agent = run_agent
    await plugin.review()
    assert calls == [("plugin:weekly_review:review", "codex")]
    for arguments in ({"date": "bad"}, {"days": 7}):
        with pytest.raises(ToolError):
            await call(mcp, "weekly_review_input", arguments)
    for body in ("", " ", "x" * 6001, "正文\n\n## 笔记\n伪造的笔记"):
        with pytest.raises(ToolError):
            await call(mcp, "weekly_review_write", {"date": "2026-11-01", "text": body})


def test_crontab_sunday_and_iso_year():
    cron = review_schedule("0 21 * * 0")
    next_run = CronTrigger.from_crontab(cron, timezone=ZONE).get_next_fire_time(
        None, datetime(2026, 10, 26, 0, tzinfo=ZONE)
    )
    assert next_run == datetime(2026, 11, 1, 21, tzinfo=ZONE)
    assert review_schedule("15 20 * * 7") == "15 20 * * sun"
    assert review_schedule("0 21 * * 0,1-5") == "0 21 * * sun,mon,tue,wed,thu,fri"
    assert review_schedule("0 21 * * */2") == "0 21 * * sun,tue,thu,sat"
    assert review_schedule("0 21 * * 1/2") == "0 21 * * mon,wed,fri,sun"
    assert week_id(date(2025, 12, 29)) == "2026-W01"
    for value in ("bad", "0 21 * * 8", "0 21 * * */0"):
        with pytest.raises(ValueError):
            review_schedule(value)
