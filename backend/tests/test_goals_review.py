"""Review regressions: generic goals and replies in the correct conversation context."""

import json
from datetime import date

import pytest

from core.models import TodoPatch
from tests.test_chat import Agent, ids, tool
from tests.test_goals import active_todos, at, build, messages, profile, say, timetable
from tests.test_goals import goals as goals


@pytest.mark.parametrize(
    "text", ["做了", "做完了", "那次不算", "先歇一周", "考完了", "有点卡", "长对话"]
)
async def test_free_text_is_never_a_goal_quick_reply(goals, text):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    goal = plugin.storage.current()
    calls = []

    async def answer(mcp, prompt):
        calls.append(prompt)
        _, reply = ids(prompt)
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "收到这句话，再看看你指哪件事。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, answer)}
    await say(service, text)
    assert len(calls) == 1
    assert not plugin.storage.records(goal["id"])
    assert plugin.storage.current()["status"] == "active"
    assert len(active_todos(service)) == 1


async def test_keyword_in_normal_chat_keeps_normal_tools(goals):
    service, mcp, plugin, _ = goals
    await build(service, mcp, plugin)
    observed = []

    async def answer(mcp, prompt):
        names = {t.name for t in await mcp.list_tools()}
        observed.append(names)
        _, reply = ids(prompt)
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "可以，按普通待办来记。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, answer)}
    await say(service, "今天的目标是把报告写完，帮我记个待办")
    assert {"todos_add", "memory_read"} <= observed[0]


async def test_unchanged_goal_note_is_not_saved_or_reindexed(goals, monkeypatch):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    writes = []
    original = service.memory.save

    def save(path, content):
        writes.append(content)
        return original(path, content)

    monkeypatch.setattr(service.memory, "save", save)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    at(clock, "2026-11-10T07:45")
    await plugin.follow()
    assert not writes
    await service.update_todo(active_todos(service)[0]["id"], TodoPatch(status="completed"))
    await plugin.follow()
    assert len(writes) == 1
    await plugin.follow()
    assert len(writes) == 1


async def test_watchdog_warning_has_a_meaningful_todo_title(goals):
    _, _, plugin, _ = goals
    plugin.context.put("retry", {"count": 3})
    warning = (await plugin.watch())[0]
    assert warning["title"] == "目标提醒暂时没发出来"
    assert warning["hint"] == "今天的目标提醒没发出来，我在重试，你不用做什么。"


async def test_goal_completion_does_not_invent_a_nickname(goals):
    service, mcp, plugin, clock = goals
    plan = profile()
    plan.update(
        deadline=None,
        result_date=None,
        criteria=[dict(id="once", title="做一次", kind="steps", target=1)],
    )
    await build(service, mcp, plugin, plan)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    await service.update_todo(active_todos(service)[0]["id"], TodoPatch(status="completed"))
    await plugin.follow()
    reply = await say(service, "达成了")
    assert "同桌" not in reply["text"]


async def test_move_uses_nearest_gap_and_names_the_actual_conflict(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin)
    timetable(service)
    service.db.execute(
        "INSERT INTO timetable_entries VALUES('2026-11-12','16:00','18:00','临时实验','课程')"
    )
    at(clock, "2026-11-12T07:40")
    await plugin.follow()
    text = messages(service)[-1]["text"]
    assert "18:15" in text and "临时实验" in text
    assert "20:30" not in text and "加课了" not in text
    service.plugins.instances.pop("timetable")


def maths_profile():
    return dict(
        title="高数85分",
        why="想学好基础课",
        deadline="2026-12-20",
        result_date="2026-12-25",
        deadline_label="期末",
        result_question="成绩公布了吗？",
        waiting_triggers=["期末结束了"],
        blocker_choices=[
            dict(text="选择题", kind="content"),
            dict(text="时间不够", kind="time"),
            dict(text="等材料", kind="materials"),
        ],
        smaller_step=dict(title="先做5道选择题", minutes=15),
        criteria=[
            dict(id="practice", title="做完4次练习", kind="steps", target=4),
            dict(
                id="correct",
                title="练习对8题",
                kind="number",
                target=8,
                question="这次练习对了几道？",
                replies=[
                    dict(text="8道", value=8),
                    dict(text="5道", value=5),
                    dict(text="我说个数"),
                    dict(text="先不说"),
                ],
            ),
            dict(id="score", title="成绩至少85", kind="result", target=85),
        ],
        schedule=[
            dict(weekday=1, time="16:30", minutes=40),
            dict(weekday=3, time="16:30", minutes=40),
        ],
        weekly_title="这周做两次练习",
        weekly_target=2,
        step_title="高数练习 · 第 {n} 次",
    )


async def test_maths_goal_day_by_day_uses_confirmed_words(goals):
    service, mcp, plugin, clock = goals
    plan = maths_profile()
    await build(service, mcp, plugin, plan)
    at(clock, "2026-11-09T07:40")
    await plugin.follow()
    assert "离期末还有" in (await plugin.cards())[0]["data"]["text"]
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    assert active_todos(service)[0]["title"] == "高数练习 · 第 1 次（约 40 分钟）"
    # A delivered template reply can open the confirmed goal-specific blocker choices.
    plugin.ask(
        plugin.storage.current(), "practice-check", "step", "这一步还顺利吗？", ["好", "有点卡"]
    )
    plugin.speech.drain(clock.value)
    reply = await say(service, "有点卡")
    assert reply["replies"] == ["选择题", "时间不够", "等材料"]

    async def help_with_notes(mcp, prompt):
        _, reply = ids(prompt)
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "这道题可以一起看看。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, help_with_notes)}
    assert (await say(service, "选择题"))["status"] == "ready"
    assert plugin.storage.current()["state"]["blocker"] == "选择题"
    at(clock, "2026-11-10T17:20")
    await service.update_todo(active_todos(service)[0]["id"], TodoPatch(status="completed"))
    assert "这次练习对了几道？" in messages(service)[-1]["text"]
    assert json.loads(messages(service)[-1]["replies"]) == ["8道", "5道", "我说个数", "先不说"]
    reply = await say(service, "5道")
    assert reply["replies"] is None and "下次" not in reply["text"]
    at(clock, "2026-11-12T07:40")
    await plugin.follow()
    assert active_todos(service)[0]["title"] == "高数练习 · 第 2 次（约 40 分钟）"
    await service.update_todo(active_todos(service)[0]["id"], TodoPatch(status="completed"))
    await say(service, "8道")
    at(clock, "2026-11-15T21:00")
    data = plugin.weekly_input(clock.value.date())
    assert (data["done"], data["days_advanced"]) == (2, 2)
    await plugin.weekly_finished(date(2026, 11, 9))
    runtime = messages(service)
    text = json.dumps([runtime, await plugin.cards(), data], ensure_ascii=False)
    assert not any(word in text for word in ("长对话", "听力", "慢跑", "六级", "出分", "考试"))
    at(clock, "2026-12-21T07:40")
    await plugin.follow()
    assert plugin.storage.current()["status"] == "waiting"
    at(clock, "2026-12-25T07:40")
    await plugin.follow()
    assert messages(service)[-1]["text"] == "成绩公布了吗？"


async def test_strength_training_day_by_day_counts_any_watch_workout(goals):
    service, mcp, plugin, clock = goals
    from core.models import PluginPatch

    plan = dict(
        title="每周去两次健身房",
        why="保持活动习惯",
        deadline=None,
        result_date=None,
        activity="exercise",
        criteria=[dict(id="sessions", title="去四次", kind="steps", target=4)],
        weekly_title="这周去两次健身房",
        step_title="力量训练",
        weekly_target=2,
        schedule=[
            dict(weekday=1, time="16:30", minutes=40),
            dict(weekday=3, time="16:30", minutes=40),
        ],
    )
    await build(service, mcp, plugin, plan)
    await service.plugins.patch("health", PluginPatch(enabled=True))
    for day, activity in (("2026-11-10", "TraditionalStrengthTraining"), ("2026-11-12", "Cycling")):
        at(clock, day + "T07:40")
        await plugin.follow()
        todo = active_todos(service)[0]
        at(clock, day + "T17:20")
        service.db.execute(
            "INSERT INTO health_workouts VALUES(?,?,?,?,?,30,'watch','none',NULL,NULL,'{}',NULL,NULL,NULL,?)",
            (day, day, day + "T16:30:00+08:00", day + "T17:00:00+08:00", activity, day),
        )
        await plugin.follow()
        assert service.store.todo(todo["id"])["status"] == "completed"
    data = plugin.weekly_input(clock.value.date())
    assert (data["done"], data["days_advanced"]) == (2, 2)
    assert [r["text"] for r in data["records"]] == [
        "手表记了力量训练 30 分钟",
        "手表记了骑行 30 分钟",
    ]
    service.db.execute(
        "INSERT INTO sleep_sessions VALUES("
        "'2026-11-12','2026-11-11T23:30:00+08:00','2026-11-12T05:00:00+08:00',"
        "1410,300,330,60,200,70,0,0,1,0,0,'watch',1,'ok','[]','2026-11-12')"
    )
    assert "今天的力量训练可以轻一点，约 20 分钟" in (await plugin.cards())[0]["data"]["text"]
    text = json.dumps([messages(service), await plugin.cards(), data], ensure_ascii=False)
    assert not any(word in text for word in ("长对话", "听力", "慢跑", "六级", "出分", "考试"))


async def test_only_draft_turns_narrow_tools_and_abandon_restores_them(goals):
    service, mcp, plugin, _ = goals
    seen = []

    async def answer(mcp, prompt):
        message, reply = ids(prompt)
        text = service.db.rows("SELECT text FROM messages WHERE id=?", (message,))[0]["text"]
        seen.append({t.name for t in await mcp.list_tools()})
        if text == "看看目标":
            await tool(mcp, "goals_get", {"arguments": {}})
        if text == "开始建":
            await tool(mcp, "goals_propose", {"arguments": {"profile": maths_profile()}})
        if text == "先不建了":
            await tool(mcp, "goals_propose", {"arguments": {"abandon": True}})
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "好，按你说的来。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, answer)}
    for line in ("看看目标", "继续聊聊", "开始建", "先不建了", "今天的目标是记一个待办"):
        assert (await say(service, line))["status"] == "ready"
    for index in (0, 1, 2, 4):
        assert {"todos_add", "memory_read"} <= seen[index]
    assert {"todos_add", "memory_read"} <= seen[3]
    assert not plugin.context.get("building")
    assert not service.db.rows("SELECT * FROM goals_drafts")
    assert plugin.storage.current() is None


@pytest.mark.parametrize("state", ["undelivered", "expired", "superseded", "answered"])
async def test_invalid_question_reply_goes_to_agent(goals, state):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    goal = plugin.storage.current()
    key = plugin.ask(goal, "context", "missed", "今天这一步做了没？", ["做了", "有点卡"])
    if state != "undelivered":
        plugin.speech.drain(clock.value)
        assert plugin.speech.delivered(key)
    if state == "expired":
        at(clock, "2026-11-20T09:00")
    elif state == "answered":
        service.db.execute("UPDATE goals_questions SET answered_by='previous' WHERE key=?", (key,))
    calls = []

    async def answer(mcp, prompt):
        calls.append(prompt)
        _, reply = ids(prompt)
        await tool(
            mcp,
            "chat_reply",
            {"reply_id": reply, "text": "实验报告交了吗？", "replies": ["做了", "还没"]},
        )

    service.jobs.harnesses = {"claude_code": Agent(mcp, answer)}
    if state == "superseded":
        await say(service, "说说实验报告")
        calls.clear()
    await say(service, "做了")
    assert len(calls) == 1
    assert not plugin.storage.records(goal["id"])
    assert active_todos(service)


async def test_homework_conflict_uses_deadline_and_title(goals):
    from core.models import PluginPatch
    from tests.test_school import add_todo, ingest

    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    source = ingest(service, "课程群", "交实验报告", "synthetic-report")
    add_todo(
        service, source, "交实验报告", "交实验报告", "2026-11-10T16:45:00+08:00", has_time=True
    )
    await service.plugins.patch("homework", PluginPatch(enabled=True))
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    assert messages(service)[-1]["text"] == "今天 16:30 有交实验报告，挪到 17:00？"


def test_unknown_workout_uses_generic_name():
    from plugins.goals.exercise import exercise_name

    assert exercise_name("HKWorkoutActivityTypeTraditionalStrengthTraining") == "力量训练"
    assert exercise_name("NewActivity") == "运动"
