"""Goals use synthetic conversations, a local clock and temporary storage only."""

import json
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.mcp_server import create_mcp
from core.models import ChatMessage, PluginPatch, TodoPatch
from harness.base import RunResult
from plugins.goals.planning import INTERNAL, week
from plugins.timetable.plugin import SCHEMA as TIMETABLE_SCHEMA
from plugins.timetable.plugin import day_entries
from tests.test_chat import Agent, ids, tool


def test_replies_are_optional_and_bounded():
    fields = dict(
        id="x",
        bot_id="assistant",
        author="bot",
        text="今天做哪步？",
        status="ready",
        error=None,
        created_at="2026-11-08T21:40:00+08:00",
        updated_at="2026-11-08T21:40:00+08:00",
    )
    assert ChatMessage(**fields, replies=["好", "改天"]).replies == ["好", "改天"]
    assert ChatMessage(**fields).replies is None
    with pytest.raises(ValueError):
        ChatMessage(**fields, replies=["a"] * 5)


async def test_goals_default_off_creates_no_tables_or_jobs(service):
    await service.start()
    try:
        assert service.plugins.view("goals")["state"] == "disabled"
        assert not service.db.rows("SELECT name FROM sqlite_master WHERE name LIKE 'goals_%'")
        assert not any(key.startswith("plugin:goals:") for key in service.jobs.callbacks)
        await service.plugins.patch("goals", PluginPatch(enabled=True))
        assert service.plugins.view("goals")["state"] == "enabled"
        assert "plugin:goals:follow" in service.jobs.callbacks
    finally:
        await service.close()


ZONE = ZoneInfo("Asia/Shanghai")


def profile(**changes):
    return dict(
        title="六级",
        why="申请交换要六级 500",
        deadline="2026-12-12",
        result_date="2027-02-26",
        criteria=[
            dict(id="papers", title="做完 6 套真题", kind="steps", target=6),
            dict(
                id="errors",
                title="听力错 8 个以内",
                kind="number",
                target=8,
                comparison="lte",
                question="听力错了几个？",
                replies=[
                    dict(text="8 个以内", value=8),
                    dict(text="9–12 个", value=12),
                    dict(text="12 个以上", value=13),
                    dict(text="先不说"),
                ],
            ),
            dict(id="score", title="出分至少 500", kind="result", target=500),
        ],
        schedule=[
            dict(weekday=1, time="16:30", minutes=40),
            dict(weekday=3, time="16:30", minutes=40),
            dict(weekday=5, time="09:30", minutes=40),
        ],
        weekly_title="这周做完 2 套听力",
        weekly_target=2,
        step_title="六级听力 · 第 {n} 套",
        blocker_choices=[
            dict(text="长对话", kind="content"),
            dict(text="时间不够", kind="time"),
            dict(text="等材料", kind="materials"),
        ],
        smaller_step=dict(title="先只练长对话", minutes=20, blocker="长对话"),
        deadline_label="考试",
        result_question="出分了吗？",
        waiting_triggers=["考完了"],
        **changes,
    )


@pytest.fixture
async def goals(service, monkeypatch):
    mcp = create_mcp(service)
    await service.start()
    await service.plugins.patch("goals", PluginPatch(enabled=True))
    plugin = service.plugins.instances["goals"]
    clock = SimpleNamespace(value=datetime(2026, 11, 8, 21, 40, tzinfo=ZONE))
    plugin.now = lambda: clock.value
    monkeypatch.setattr("core.store.now", lambda: clock.value.isoformat())
    monkeypatch.setattr("core.chat.now", lambda: clock.value.isoformat())
    try:
        yield service, mcp, plugin, clock
    finally:
        await service.close()


def at(clock, text):
    clock.value = datetime.fromisoformat(text).replace(tzinfo=ZONE)


async def say(service, text):
    result = await service.chat.send("assistant", text)
    if task := service.chat.tasks.get("assistant"):
        await task
    return service.chat.get(result["reply"]["id"])


def messages(service):
    return service.db.rows(
        "SELECT * FROM chat_messages WHERE author='bot' AND status='ready' ORDER BY rowid"
    )


async def build(service, mcp, plugin, plan=None):
    plan = plan or profile()

    async def act(mcp, prompt):
        message, reply = ids(prompt)
        text = service.db.rows("SELECT text FROM messages WHERE id=?", (message,))[0]["text"]
        if text == "定下来":
            draft = service.db.rows("SELECT id FROM goals_drafts ORDER BY rowid DESC LIMIT 1")[0][
                "id"
            ]
            await tool(mcp, "goals_confirm", {"arguments": {"draft": draft}})
            await tool(
                mcp, "chat_reply", {"reply_id": reply, "text": "定好啦，计划日早上放进待办。"}
            )
        else:
            await tool(mcp, "goals_propose", {"arguments": {"profile": plan}})
            await tool(
                mcp,
                "chat_reply",
                {
                    "reply_id": reply,
                    "text": f"**目标** {plan['title']}\n**节奏** {plan['weekly_title']}\n定下来吗？",
                    "replies": ["定下来", "再改改"],
                },
            )

    agent = Agent(mcp, act)
    service.jobs.harnesses = {
        "claude_code": agent,
        "codex": Agent(mcp, result=RunResult(False, "quota_exhausted")),
    }
    await say(service, plan["title"] + "，" + plan["why"])
    current = plugin.storage.current()
    assert current is None or current["status"] in {"achieved", "dropped"}
    result = await say(service, "定下来")
    assert result["status"] == "ready"
    assert plugin.storage.current()["status"] == "active"
    return agent


async def revise(service, mcp, plugin, text, changes):
    """Free text goes through the Agent, then a displayed plan and user confirmation."""

    async def act(mcp, prompt):
        message, reply = ids(prompt)
        user_text = service.db.rows("SELECT text FROM messages WHERE id=?", (message,))[0]["text"]
        if user_text == "定下来":
            draft = service.db.rows("SELECT id FROM goals_drafts ORDER BY rowid DESC LIMIT 1")[0][
                "id"
            ]
            await tool(mcp, "goals_confirm", {"arguments": {"draft": draft}})
            await tool(mcp, "chat_reply", {"reply_id": reply, "text": "好，按刚才说好的日期再聊。"})
        else:
            goal = (await tool(mcp, "goals_get", {"arguments": {}}))["goal"]
            await tool(
                mcp,
                "goals_propose",
                {
                    "arguments": {
                        "profile": goal["profile"],
                        "revision": goal["revision"],
                        "changes": changes,
                    }
                },
            )
            await tool(
                mcp,
                "chat_reply",
                {
                    "reply_id": reply,
                    "text": f"**目标** {goal['profile']['title']}\n**再聊** {changes['ask_on']}\n定下来吗？",
                    "replies": ["定下来", "再改改"],
                },
            )

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    await say(service, text)
    await say(service, "定下来")


def timetable(service):
    with service.db.connect() as conn:
        conn.executescript(TIMETABLE_SCHEMA)
        conn.execute("INSERT INTO timetable_weeks VALUES('2026-11-09','2026-11-08')")
        for day in ("2026-11-10", "2026-11-12"):
            conn.execute(
                "INSERT INTO timetable_entries VALUES(?,'14:00','16:10','线性代数','课程')", (day,)
            )
    # An enabled synthetic timetable exposes the same public day_entries as the real plugin.
    original = service.plugins.instances.get
    service.plugins.instances["timetable"] = SimpleNamespace(
        day_entries=lambda day: day_entries(service.db, day)
    )
    return original


def active_todos(service):
    return service.db.rows("SELECT * FROM todos WHERE source_plugin='goals' AND status='pending'")


async def test_cet_week_day_by_day_and_waiting(goals):
    service, mcp, plugin, clock = goals
    timetable(service)
    agent = await build(service, mcp, plugin)
    # Sunday creation and Monday are quiet, with a local next-step card.
    at(clock, "2026-11-09T07:40")
    await plugin.follow()
    assert not active_todos(service)
    card = (await plugin.cards())[0]["data"]
    assert "0/2" in card["headline"] and "周二 16:30" in card["text"]
    count = len(messages(service))
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    todo = active_todos(service)[0]
    assert todo["title"] == "六级听力 · 第 1 套（约 40 分钟）"
    assert "16:10 下课后有空" in (await plugin.cards())[0]["data"]["text"]
    assert len(messages(service)) == count
    at(clock, "2026-11-10T17:20")
    await service.update_todo(todo["id"], TodoPatch(status="completed"))
    assert json.loads(messages(service)[-1]["replies"]) == [
        "8 个以内",
        "9–12 个",
        "12 个以上",
        "先不说",
    ]
    calls = len(agent.prompts)
    reply = await say(service, "9–12 个")
    assert reply["replies"] is None and "下次" not in reply["text"]
    assert len(agent.prompts) == calls
    assert "1/2" in (await plugin.cards())[0]["data"]["headline"]
    assert "长对话" not in (await plugin.cards())[0]["data"]["text"]
    # Thursday's added lab asks before moving. No response never accepts the proposal.
    at(clock, "2026-11-12T07:40")
    service.db.execute(
        "INSERT INTO timetable_entries VALUES('2026-11-12','16:00','18:00','临时实验','课程')"
    )
    await plugin.follow()
    assert messages(service)[-1]["text"] == "今天 16:30 有临时实验，挪到 18:15？"
    assert json.loads(messages(service)[-1]["replies"]) == ["好", "今天跳过"]
    await plugin.follow()
    assert not active_todos(service)
    at(clock, "2026-11-13T07:40")
    before = len(messages(service))
    await plugin.follow()
    assert len(messages(service)) == before and not active_todos(service)
    assert plugin.steps(plugin.storage.current())[-1]["state"] == "missed"
    at(clock, "2026-11-14T07:40")
    await plugin.follow()
    todo = active_todos(service)[0]
    assert todo["title"] == "六级听力 · 第 2 套（约 40 分钟）"
    assert json.loads(messages(service)[-1]["replies"]) == ["好", "今天也够呛", "有点卡"]
    at(clock, "2026-11-14T10:15")
    await service.update_todo(todo["id"], TodoPatch(status="completed"))
    assert "这周都做完" in messages(service)[-1]["text"]
    assert "2/2" in (await plugin.cards())[0]["data"]["headline"]
    assert len(agent.prompts) == calls
    at(clock, "2026-11-15T21:00")
    data = plugin.weekly_input(clock.value.date())
    assert (data["done"], data["days_advanced"]) == (2, 2)
    assert data["missed"] == ["2026-11-12"]
    await plugin.weekly_finished(date(2026, 11, 9))
    assert messages(service)[-1]["text"] == "这周记好啦～下周照这个来？"
    assert json.loads(messages(service)[-1]["replies"]) == ["照这个来", "改小一点", "下周歇一歇"]
    at(clock, "2026-11-17T07:40")
    await plugin.follow()
    assert "第 3 套" in active_todos(service)[0]["title"]
    assert plugin.storage.current()["profile"]["weekly_target"] == 2
    at(clock, "2026-12-12T18:00")
    await revise(service, mcp, plugin, "考完了", {"status": "waiting", "ask_on": "2027-02-26"})
    assert not active_todos(service)
    before = len(messages(service))
    for day in ("2026-12-13T21:00", "2027-01-10T21:00", "2027-02-25T09:00"):
        at(clock, day)
        await plugin.follow()
        await plugin.weekly_finished(week(clock.value.date()))
        assert plugin.weekly_input(clock.value.date()) is None
    assert len(messages(service)) == before
    at(clock, "2027-02-26T09:00")
    await plugin.follow()
    await plugin.follow()
    assert len(messages(service)) == before + 1
    assert "出分了吗" in messages(service)[-1]["text"]
    for word in INTERNAL:
        assert word not in json.dumps(await plugin.cards(), ensure_ascii=False)
        assert all(word not in m["text"] for m in messages(service))
    await service.apple.flush()
    assert not any(
        service.store.todo(s["todo_id"])["reminder_id"]
        for s in plugin.steps(plugin.storage.current())
    )
    # Remove synthetic instance before the real manager tears down plugins.
    service.plugins.instances.pop("timetable")


async def test_running_watch_record_day_by_day(goals):
    service, mcp, plugin, clock = goals
    plan = profile()
    plan.update(
        title="跑步",
        why="想跑得轻松一点",
        activity="exercise",
        deadline=None,
        result_date=None,
        criteria=[dict(id="runs", title="跑三次", kind="steps", target=3)],
        weekly_target=3,
        weekly_title="这周跑 3 次",
        step_title="慢跑 3 km",
        schedule=[
            dict(weekday=1, time="19:00", minutes=30),
            dict(weekday=3, time="19:00", minutes=30),
            dict(weekday=5, time="09:30", minutes=30),
        ],
    )
    agent = await build(service, mcp, plugin, plan)
    await service.plugins.patch("health", PluginPatch(enabled=True))
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    todo = active_todos(service)[0]
    assert todo["title"] == "慢跑 3 km（约 30 分钟）"
    at(clock, "2026-11-10T19:40")
    service.db.execute(
        "INSERT INTO health_workouts VALUES('run-1','2026-11-10',"
        "'2026-11-10T19:00:00+08:00','2026-11-10T19:30:00+08:00',"
        "'Running',30,'watch','none',NULL,NULL,'{}',NULL,NULL,NULL,'2026-11-10')"
    )
    before = len(messages(service))
    await plugin.follow()
    assert service.store.todo(todo["id"])["status"] == "completed"
    assert "1/3" in (await plugin.cards())[0]["data"]["headline"]
    assert len(messages(service)) == before
    at(clock, "2026-11-12T07:40")
    await plugin.follow()
    assert len(active_todos(service)) == 1
    at(clock, "2026-11-13T07:40")
    await plugin.follow()
    assert not active_todos(service)
    at(clock, "2026-11-14T07:40")
    service.db.execute(
        "INSERT INTO sleep_sessions VALUES("
        "'2026-11-14','2026-11-13T23:30:00+08:00','2026-11-14T05:00:00+08:00',"
        "1410,300,330,60,200,70,0,0,1,0,0,'watch',1,'ok','[]','2026-11-14')"
    )
    await plugin.follow()
    todo = active_todos(service)[0]
    assert "今天的慢跑 3 km可以轻一点，约 20 分钟" in (await plugin.cards())[0]["data"]["text"]
    assert "30 分钟" in todo["title"]
    at(clock, "2026-11-14T10:15")
    service.db.execute(
        "INSERT INTO health_workouts VALUES('run-2','2026-11-14',"
        "'2026-11-14T09:30:00+08:00','2026-11-14T10:10:00+08:00',"
        "'Running',40,'watch','none',NULL,NULL,'{}',NULL,NULL,NULL,'2026-11-14')"
    )
    await plugin.follow()
    assert service.store.todo(todo["id"])["status"] == "completed"
    at(clock, "2026-11-15T21:00")
    report = plugin.weekly_input(clock.value.date())
    assert (report["done"], report["target"], report["days_advanced"]) == (2, 3, 2)
    assert [r["kind"] for r in report["records"]] == ["workout", "workout"]
    assert len(agent.prompts) == 2
    assert report["missed"] == ["2026-11-12"]


async def test_two_misses_reduce_to_weekly_and_user_returns(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    for moment in ("2026-11-10T07:40", "2026-11-11T07:40", "2026-11-12T07:40", "2026-11-13T07:40"):
        at(clock, moment)
        await plugin.follow()
    assert not active_todos(service)
    assert plugin.storage.current()["state"]["silent"]
    assert "改小一点" in json.loads(messages(service)[-1]["replies"])
    assert "等你回来" in (await plugin.cards())[0]["data"]["text"]
    before = len(messages(service))
    at(clock, "2026-11-14T07:40")
    await plugin.follow()
    assert not active_todos(service) and len(messages(service)) == before
    at(clock, "2026-11-15T21:00")
    await plugin.weekly_finished(date(2026, 11, 9))
    assert len(messages(service)) == before
    reply = await say(service, "照原计划")
    assert reply["text"] == "好，还是这个量，慢慢接上。"
    at(clock, "2026-11-17T07:40")
    await plugin.follow()
    assert len(active_todos(service)) == 1


async def test_agent_completion_does_not_count_and_revoke_rolls_back(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    todo = active_todos(service)[0]
    await service.update_todo(todo["id"], TodoPatch(status="completed"), from_agent=True)
    assert not plugin.storage.records(plugin.storage.current()["id"])
    assert "0/2" in (await plugin.cards())[0]["data"]["headline"]
    await service.update_todo(todo["id"], TodoPatch(status="pending"))
    await service.update_todo(todo["id"], TodoPatch(status="completed"))
    assert "1/2" in (await plugin.cards())[0]["data"]["headline"]

    async def retract(mcp, prompt):
        _, reply = ids(prompt)
        data = await tool(mcp, "goals_get", {"arguments": {}})
        await tool(mcp, "goals_revoke", {"arguments": {"record_id": data["records"][0]["id"]}})
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "好，这一次不算了。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, retract)}
    await say(service, "那次不算")
    assert "0/2" in (await plugin.cards())[0]["data"]["headline"]
    assert plugin.storage.records(plugin.storage.current()["id"], active=False)[0]["revoked"]


async def test_pause_stays_quiet_until_one_due_question(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    await revise(service, mcp, plugin, "先歇一周", {"status": "paused", "ask_on": "2026-11-17"})
    before = len(messages(service))
    for i in range(1, 7):
        at(clock, f"2026-11-{10 + i:02}T09:00")
        await plugin.follow()
        assert not active_todos(service)
        assert plugin.weekly_input(clock.value.date()) is None
    assert len(messages(service)) == before
    at(clock, "2026-11-17T06:00")
    await plugin.follow()
    assert len(messages(service)) == before
    at(clock, "2026-11-17T07:40")
    await plugin.follow()
    await plugin.follow()
    assert len(messages(service)) == before + 1
    await say(service, "接着来")
    assert plugin.storage.current()["status"] == "active"
    await plugin.follow()
    assert active_todos(service)


async def test_shared_quota_quiet_hours_classes_and_restart_dedup(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    timetable(service)
    speech = service.chat.proactive
    at(clock, "2026-11-10T06:30")
    end = datetime(2026, 11, 12, tzinfo=ZONE)
    speech.enqueue(
        "health:1", "assistant", "health", "昨晚睡得怎么样？", ["还好"], clock.value, end
    )
    before = len(messages(service))
    speech.drain(clock.value)
    assert len(messages(service)) == before
    at(clock, "2026-11-10T14:30")
    speech.drain(clock.value, day_entries(service.db, clock.value.date())[0])
    assert len(messages(service)) == before
    at(clock, "2026-11-10T16:20")
    speech.drain(clock.value)
    speech.enqueue("goal:1", "assistant", "goals", "今天接着来？", ["好"], clock.value, end)
    speech.enqueue("goal:2", "assistant", "goals", "明天接着来？", ["好"], clock.value, end)
    speech.drain(clock.value)
    assert len(messages(service)) == before + 2
    assert speech.delivered("goal:1") and not speech.delivered("goal:2")
    from core.proactive import Proactive

    speech = Proactive(service.chat)
    speech.enqueue("goal:1", "assistant", "goals", "重复", ["好"], clock.value, end)
    speech.drain(clock.value)
    assert len(messages(service)) == before + 2
    at(clock, "2026-11-11T07:40")
    speech.drain(clock.value)
    assert len(messages(service)) == before + 3
    service.plugins.instances.pop("timetable")


async def test_restarts_keep_steps_and_harness_fallback(goals):
    service, mcp, plugin, clock = goals
    agent = await build(service, mcp, plugin)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    first = active_todos(service)[0]["id"]
    from plugins.goals.plugin import Plugin

    replacement = Plugin()
    await replacement.setup(service.plugins.context("goals"), {})
    replacement.now = lambda: clock.value
    service.plugins.instances["goals"] = replacement
    await replacement.follow()
    assert [t["id"] for t in active_todos(service)] == [first]
    assert len(messages(service)) == 2

    async def answer(mcp, prompt):
        _, reply = ids(prompt)
        data = await tool(mcp, "goals_get", {"arguments": {}})
        assert data["goal"]["id"] == replacement.storage.current()["id"]
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "还记着呢，还是原来的目标。"})

    fallback = Agent(mcp, answer)
    service.jobs.harnesses["codex"] = fallback
    service.jobs.harnesses["claude_code"] = Agent(mcp, result=RunResult(False, "quota_exhausted"))
    result = await say(service, "查看目标")
    assert result["status"] == "ready" and len(fallback.prompts) == 1
    assert len(agent.prompts) == 2
    assert active_todos(service)[0]["id"] == first


async def test_system_error_retries_and_watchdog_owns_warning(goals, monkeypatch):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    calls = []

    def broken():
        calls.append(1)
        raise OSError("synthetic")

    monkeypatch.setattr(plugin, "tick", broken)
    for moment in ("2026-11-10T08:00", "2026-11-10T08:05", "2026-11-10T08:15"):
        at(clock, moment)
        with pytest.raises(OSError):
            await plugin.follow()
    warning = (await plugin.watch())[0]
    assert warning["hint"] == "今天的目标提醒没发出来，我在重试，你不用做什么。"
    await plugin.follow()
    assert len(calls) == 3
    assert not any("synthetic" in m["text"] for m in messages(service))


async def test_completion_is_user_confirmed_and_written_to_memory(goals):
    service, mcp, plugin, clock = goals
    plan = profile()
    plan.update(
        deadline=None,
        result_date=None,
        criteria=[dict(id="one", title="做一次", kind="steps", target=1)],
    )
    await build(service, mcp, plugin, plan)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    todo = active_todos(service)[0]
    await service.update_todo(todo["id"], TodoPatch(status="completed"))
    await plugin.follow()
    assert plugin.storage.current()["status"] == "active"
    assert json.loads(messages(service)[-1]["replies"]) == ["达成了", "还没有"]
    await say(service, "达成了")
    goal = plugin.storage.current()
    assert goal["status"] == "achieved"
    assert (await plugin.cards())[0]["title"] == "下一个目标"
    body = service.memory.path(goal["state"]["note_path"]).read_text()
    assert all(
        word in body for word in ("**目标**", "**为什么**", "**过程**", "**结果**", "已达成")
    )
    before = len(messages(service))
    at(clock, "2026-11-12T07:40")
    await plugin.follow()
    assert len(messages(service)) == before and not active_todos(service)


async def test_result_number_records_exact_user_words_then_confirms(goals):
    service, mcp, plugin, clock = goals
    plan = profile()
    plan["criteria"] = [dict(id="score", title="出分至少500", kind="result", target=500)]
    await build(service, mcp, plugin, plan)
    at(clock, "2026-12-12T18:00")
    await revise(service, mcp, plugin, "考完了", {"status": "waiting", "ask_on": "2027-02-26"})
    at(clock, "2027-02-26T09:00")
    await plugin.follow()

    async def act(mcp, prompt):
        message, reply = ids(prompt)
        await tool(
            mcp,
            "goals_record",
            {
                "arguments": {
                    "message_id": message,
                    "quote": "六级 532 分",
                    "criterion": "score",
                    "value": 532,
                }
            },
        )
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "532 分记下啦～"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    await say(service, "六级 532 分")
    # Reaching the result is a response to user input and may ask for confirmation today.
    at(clock, "2027-02-27T09:00")
    await plugin.follow()
    assert json.loads(messages(service)[-1]["replies"]) == ["达成了", "还没有"]
    await say(service, "达成了")
    assert plugin.storage.current()["status"] == "achieved"
    records = plugin.storage.records(plugin.storage.current()["id"])
    assert records[0]["text"] == "六级 532 分"
    assert records[0]["ref"] in {
        r["id"] for r in service.db.rows("SELECT id FROM messages WHERE plugin='chat'")
    }


async def test_model_cannot_create_without_user_confirmation_or_invent_progress(goals):
    service, mcp, plugin, clock = goals
    from mcp.server.fastmcp.exceptions import ToolError

    async def act(mcp, prompt):
        message, reply = ids(prompt)
        draft = await tool(mcp, "goals_propose", {"arguments": {"profile": profile()}})
        with pytest.raises(ToolError):
            await tool(mcp, "goals_confirm", {"arguments": {"draft": draft["draft"]}})
        await tool(
            mcp,
            "chat_reply",
            {"reply_id": reply, "text": "先看看这份计划。", "replies": ["定下来", "再改改"]},
        )

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    await say(service, "这学期我想考六级")
    assert plugin.storage.current() is None
    await build(service, mcp, plugin)

    async def forge(mcp, prompt):
        message, reply = ids(prompt)
        for args in (
            {"message_id": message, "quote": "模型说我做完了"},
            {
                "message_id": message,
                "quote": "目标里还没有做完",
                "criterion": "score",
                "value": 532,
            },
            {"message_id": "not-this-turn", "quote": "目标里还没有做完"},
        ):
            with pytest.raises(ToolError):
                await tool(mcp, "goals_record", {"arguments": args})
        names = {t.name for t in await mcp.list_tools()}
        assert {"todos_add", "memory_read", "goals_get", "goals_record"} <= names
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "没做完也正常，我们按实际的记。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, forge)}
    result = await say(service, "目标里还没有做完")
    assert result["status"] == "ready"
    assert not plugin.storage.records(plugin.storage.current()["id"])
    with pytest.raises(ValueError):
        await plugin.goals_record({"message_id": "fake", "quote": "我做完了"})


async def test_graceful_restart_does_not_cancel_today_or_resend_question(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    todo = active_todos(service)[0]
    await service.update_todo(todo["id"], TodoPatch(status="completed"))
    before = len(messages(service))
    await service.close()
    from core.service import Service

    restored = Service(service.settings, platform="linux")
    try:
        await restored.start()
        restored_plugin = restored.plugins.instances["goals"]
        restored_plugin.now = lambda: clock.value
        await restored_plugin.follow()
        assert len(messages(restored)) == before
        assert len(restored_plugin.steps(restored_plugin.storage.current())) == 1
        assert "1/2" in (await restored_plugin.cards())[0]["data"]["headline"]
    finally:
        await restored.close()


async def test_no_late_step_or_old_questions_after_long_downtime(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    at(clock, "2026-11-10T21:30")
    await plugin.follow()
    assert not active_todos(service)
    await revise(service, mcp, plugin, "先歇一周", {"status": "paused", "ask_on": "2026-11-17"})
    before = len(messages(service))
    at(clock, "2026-11-25T07:40")
    await plugin.follow()
    assert len(messages(service)) == before


async def test_weekly_review_reads_goal_and_delivers_the_same_week_question(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    await service.plugins.patch("weekly_review", PluginPatch(enabled=True))
    weekly = service.plugins.instances["weekly_review"]
    at(clock, "2026-11-15T21:00")
    weekly.now = lambda: clock.value
    data = await weekly.review_input({"date": "2026-11-15"})
    assert data["goals"]["target"] == 2
    await weekly.review_write(
        {"date": "2026-11-15", "text": "**目标 · 六级** 这周没顾上\n- 下周还是两套听力"}
    )
    assert messages(service)[-1]["text"] == "这周记好啦～下周照这个来？"
    before = len(messages(service))
    await weekly.review_write({"date": "2026-11-15", "text": "**目标 · 六级** 这周没顾上"})
    assert len(messages(service)) == before


async def test_approved_move_survives_follow_and_skipped_day_stays_hidden(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    timetable(service)
    at(clock, "2026-11-12T07:40")
    service.db.execute(
        "INSERT INTO timetable_entries VALUES('2026-11-12','16:00','18:00','临时实验','课程')"
    )
    await plugin.follow()
    await say(service, "好")
    todo = active_todos(service)[0]
    assert datetime.fromisoformat(todo["due_at"]).astimezone(ZONE).hour == 18
    await plugin.follow()
    assert [t["id"] for t in active_todos(service)] == [todo["id"]]
    service.plugins.instances.pop("timetable")


async def test_homework_submission_finishes_step_and_unsubmit_retracts(goals):
    service, mcp, plugin, clock = goals
    from tests.test_school import add_todo, ingest

    source = ingest(service, "合成课程群", "周二交作业", "hw-1")
    homework = add_todo(service, source, "交作业", "周二交作业", "2026-11-10T23:00:00+08:00")
    plan = profile()
    plan.update(
        activity="homework",
        homework_ids=[homework["id"]],
        step_title="提交本周作业",
        deadline=None,
        result_date=None,
        criteria=[dict(id="submit", kind="steps", title="交一次", target=1)],
    )
    await build(service, mcp, plugin, plan)
    await service.plugins.patch("homework", PluginPatch(enabled=True))
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    step = active_todos(service)[0]
    await service.update_todo(homework["id"], TodoPatch(status="completed"))
    assert service.store.todo(step["id"])["status"] == "completed"
    assert "1/2" in (await plugin.cards())[0]["data"]["headline"]
    await service.update_todo(homework["id"], TodoPatch(status="pending"))
    assert "0/2" in (await plugin.cards())[0]["data"]["headline"]


async def test_four_question_creation_dialogue_and_stale_revision(goals):
    service, mcp, plugin, clock = goals
    from mcp.server.fastmcp.exceptions import ToolError

    answers = [
        ("为什么想考到500？", ["为了交换", "想试试看"]),
        ("先做完六套，出分500，就这样？", ["就这样", "改一下"]),
        ("周二周四和周六，每次40分钟？", ["每周3次", "2次就好"]),
        ("**目标** 六级500\n**这周** 两套听力\n定下来吗？", ["定下来", "再改改"]),
    ]
    stage = 0
    draft = None

    async def act(mcp, prompt):
        nonlocal stage, draft
        _, reply = ids(prompt)
        if stage < 4:
            if stage == 3:
                data = await tool(mcp, "goals_propose", {"arguments": {"profile": profile()}})
                draft = data["draft"]
            text, replies = answers[stage]
            await tool(mcp, "chat_reply", {"reply_id": reply, "text": text, "replies": replies})
        else:
            await tool(mcp, "goals_confirm", {"arguments": {"draft": draft}})
            with pytest.raises(ToolError):
                await tool(
                    mcp, "goals_propose", {"arguments": {"profile": profile(), "revision": 0}}
                )
            await tool(mcp, "chat_reply", {"reply_id": reply, "text": "定好啦～"})
        stage += 1

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    for index, line in enumerate(("这学期我想六级500", "为了交换", "就这样", "每周3次")):
        reply = await say(service, line)
        assert reply["replies"] == answers[index][1]
        assert plugin.storage.current() is None
    reply = await say(service, "定下来")
    assert (
        reply["status"] == "ready"
        and plugin.storage.current()["profile"]["why"] == "申请交换要六级 500"
    )


async def test_achievement_note_retries_after_disk_error(goals, monkeypatch):
    service, mcp, plugin, clock = goals
    plan = profile()
    plan.update(
        deadline=None,
        result_date=None,
        criteria=[dict(id="one", title="做一次", kind="steps", target=1)],
    )
    await build(service, mcp, plugin, plan)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    await service.update_todo(active_todos(service)[0]["id"], TodoPatch(status="completed"))
    await plugin.follow()
    original = plugin.write_note

    def unavailable(_goal):
        raise OSError("synthetic disk")

    monkeypatch.setattr(plugin, "write_note", unavailable)
    reply = await say(service, "达成了")
    assert reply["status"] == "ready"
    assert plugin.context.get("note_pending")
    monkeypatch.setattr(plugin, "write_note", original)
    await plugin.follow()
    assert not plugin.context.get("note_pending")
    goal = plugin.storage.current()
    assert "已达成" in service.memory.path(goal["state"]["note_path"]).read_text()


async def test_custom_pause_date_and_blocker_require_confirmed_plan(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    current = plugin.storage.current()
    draft = None

    async def act(mcp, prompt):
        nonlocal draft
        message, reply = ids(prompt)
        line = service.db.rows("SELECT text FROM messages WHERE id=?", (message,))[0]["text"]
        if line == "定下来":
            await tool(mcp, "goals_confirm", {"arguments": {"draft": draft}})
            await tool(mcp, "chat_reply", {"reply_id": reply, "text": "好，到周五再问。"})
        else:
            data = await tool(
                mcp,
                "goals_propose",
                {
                    "arguments": {
                        "profile": profile(),
                        "revision": current["revision"],
                        "changes": {
                            "status": "paused",
                            "ask_on": "2026-11-13",
                            "blocker": "等材料",
                            "blocker_day": "2026-11-13",
                        },
                    }
                },
            )
            draft = data["draft"]
            await tool(
                mcp,
                "chat_reply",
                {
                    "reply_id": reply,
                    "text": "等材料，周五再聊，定下来吗？",
                    "replies": ["定下来", "再改改"],
                },
            )

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    await say(service, "目标先歇到周五，等材料")
    assert plugin.storage.current()["status"] == "active" and active_todos(service)
    await say(service, "定下来")
    assert plugin.storage.current()["status"] == "paused" and not active_todos(service)
    assert plugin.storage.current()["state"]["blocker"] == "等材料"
    at(clock, "2026-11-13T07:40")
    await plugin.follow()
    assert messages(service)[-1]["text"] == "歇得怎么样，接着来吗？"


async def test_non_listening_number_standard_uses_its_own_target(goals):
    service, mcp, plugin, clock = goals
    plan = profile()
    plan.update(
        deadline=None,
        result_date=None,
        criteria=[dict(id="words", title="读完30页", kind="number", target=30)],
    )
    await build(service, mcp, plugin, plan)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    await service.update_todo(active_todos(service)[0]["id"], TodoPatch(status="completed"))
    assert json.loads(messages(service)[-1]["replies"]) == ["30", "我说个数", "先不说"]
    assert "听力" not in messages(service)[-1]["text"]
    await say(service, "30")
    record = plugin.storage.records(plugin.storage.current()["id"])[-1]
    assert record["criterion"] == "words" and record["value"] == 30


async def test_goal_todo_stays_app_only_when_another_source_is_merged(goals):
    service, mcp, plugin, clock = goals
    from core.models import MessageInput, TodoCreate

    await build(service, mcp, plugin)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    todo = active_todos(service)[0]
    service.store.ingest(
        "chat",
        [
            MessageInput(
                external_id="copy",
                chat_name="合成对话",
                sender="我",
                sent_at=clock.value,
                text=todo["title"],
            )
        ],
        ["合成对话"],
    )
    source = service.store.unprocessed("chat")[0]
    merged = await service.add_todo(
        TodoCreate(
            title=todo["title"],
            due_at=todo["due_at"],
            has_time=True,
            source_plugin="chat",
            source_refs=[source["id"]],
            source_excerpt=todo["title"],
            confidence=1,
        )
    )
    assert merged["todo"]["reminder_id"] is None
    assert merged["todo"]["calendar_event_id"] is None
