"""Second-review regressions, using only temporary data and scripted Agents."""

import json
from datetime import date

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from core.models import PluginPatch, TodoPatch
from core.prompts import PROMPTS
from tests.test_chat import Agent, ids, tool
from tests.test_goals import active_todos, at, build, messages, say, timetable
from tests.test_goals import goals as goals
from tests.test_goals_review import maths_profile


async def stage(service, mcp, plugin, *, plan=None, changes=None):
    async def act(mcp, prompt):
        _, reply = ids(prompt)
        goal = plugin.storage.current()
        args = {"profile": plan or (goal["profile"] if goal else maths_profile())}
        if goal and goal["status"] in {"active", "paused", "waiting"}:
            args["revision"] = goal["revision"]
        if changes:
            args["changes"] = changes
        await tool(mcp, "goals_propose", {"arguments": args})
        await tool(
            mcp,
            "chat_reply",
            {
                "reply_id": reply,
                "text": "**目标** 商量好的计划\n定下来吗？",
                "replies": ["定下来", "再改改"],
            },
        )

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    await say(service, "商量一下计划")
    return service.db.rows("SELECT id FROM goals_drafts ORDER BY rowid DESC LIMIT 1")[0]["id"]


async def confirm(service, mcp, draft):
    async def act(mcp, prompt):
        _, reply = ids(prompt)
        result = await tool(mcp, "goals_confirm", {"arguments": {"draft": draft}})
        assert result["accepted"]
        await tool(
            mcp, "chat_reply", {"reply_id": reply, "text": result.get("text") or "好，定下来啦。"}
        )

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    assert (await say(service, "定下来"))["status"] == "ready"


async def record(service, mcp, text, **fields):
    async def act(mcp, prompt):
        message, reply = ids(prompt)
        await tool(
            mcp, "goals_record", {"arguments": {"message_id": message, "quote": text, **fields}}
        )
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "算一次，说“那次不算”就能撤销。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    return await say(service, text)


async def question(plugin, clock, kind, replies, payload=None):
    key = plugin.ask(
        plugin.storage.current(),
        f"test:{kind}:{clock.value.isoformat()}",
        kind,
        "这一步怎么安排？",
        replies,
        payload,
        reactive=True,
    )
    plugin.speech.drain(clock.value)
    return key


async def test_unrelated_turn_discards_draft_and_keeps_normal_tools(goals):
    service, mcp, plugin, clock = goals
    old = await stage(service, mcp, plugin)
    at(clock, "2026-11-09T09:00")

    async def act(mcp, prompt):
        _, reply = ids(prompt)
        names = {t.name for t in await mcp.list_tools()}
        assert {"todos_add", "memory_read"} <= names
        assert not service.db.rows("SELECT * FROM goals_drafts")
        assert not plugin.context.get("building")
        with pytest.raises(ToolError):
            await tool(mcp, "goals_confirm", {"arguments": {"draft": old}})
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "记个普通待办。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    assert (await say(service, "帮我记个待办：周五交报告"))["status"] == "ready"


@pytest.mark.parametrize("answer", ["定下来", "再改改"])
async def test_summary_answers_are_the_only_narrow_turns(goals, answer):
    service, mcp, plugin, _ = goals
    draft = await stage(service, mcp, plugin)

    async def act(mcp, prompt):
        _, reply = ids(prompt)
        names = {t.name for t in await mcp.list_tools()}
        assert "goals_confirm" in names and "todos_add" not in names
        if answer == "定下来":
            await tool(mcp, "goals_confirm", {"arguments": {"draft": draft}})
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "想改哪里？"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    assert (await say(service, answer))["status"] == "ready"
    if answer == "再改改":
        await stage(service, mcp, plugin)
        assert service.db.rows("SELECT id FROM goals_drafts")[0]["id"] != draft


async def test_daily_goal_and_watchdog_are_separate_sections(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    from tests.test_school import add_todo, ingest

    source = ingest(service, "合成系统", "后台检查提醒", "alert")
    alert = add_todo(service, source, "后台检查提醒", "后台检查提醒")
    service.db.execute("UPDATE todos SET source_plugin='watchdog' WHERE id=?", (alert["id"],))
    due = service.daily.todos(clock.value.date())
    body = service.daily.todos_file(clock.value.date(), due)
    goal_section = body.split("## 今天的目标\n")[1].split("\n## ")[0]
    assert "16:30" in goal_section and "截止" not in goal_section
    system_section = body.split("## 系统提醒\n")[1]
    assert "后台检查提醒" in system_section and "高数练习" not in system_section
    summary = service.daily.todos_summary(due)
    assert "目标 1 步" in summary and "1 条系统提醒" in summary


async def test_drop_requires_confirmation_then_releases_goal_slot(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    first = plugin.storage.current()["id"]
    draft = await stage(service, mcp, plugin, changes={"status": "dropped"})
    assert plugin.storage.current()["status"] == "active"
    await confirm(service, mcp, draft)
    assert plugin.storage.current()["status"] == "dropped"
    assert "高数85分" in messages(service)[-1]["text"] and "0 天" in messages(service)[-1]["text"]
    assert "下一个目标" in json.dumps(await plugin.cards(), ensure_ascii=False)
    await build(service, mcp, plugin, maths_profile())
    assert plugin.storage.current()["id"] != first
    assert len(service.db.rows("SELECT * FROM goals_profiles")) == 2


@pytest.mark.parametrize("answer", ["先放下", "接着准备"])
async def test_below_target_result_offers_a_dignified_ending(goals, answer):
    service, mcp, plugin, clock = goals
    plan = maths_profile()
    plan["criteria"] = [dict(id="score", title="成绩至少85", kind="result", target=85)]
    await build(service, mcp, plugin, plan)
    at(clock, "2026-12-21T09:00")
    await plugin.follow()
    at(clock, "2026-12-25T09:00")
    await plugin.follow()
    assert (await record(service, mcp, "这次80分", criterion="score", value=80))[
        "status"
    ] == "ready"
    await plugin.follow()
    assert json.loads(messages(service)[-1]["replies"]) == ["接着准备", "先放下"]
    assert "成绩至少85" in messages(service)[-1]["text"] and "5" in messages(service)[-1]["text"]
    reply = await say(service, answer)
    if answer == "先放下":
        assert plugin.storage.current()["status"] == "dropped"
        assert "高数85分" in reply["text"] and "0 天" in reply["text"]
        note = service.memory.read(plugin.storage.current()["state"]["note_path"])["content"]
        assert "先放下了" in note and "2026-12-25" in note
    else:
        assert reply["text"] == "好，下一次是哪天？说一句就行。"


@pytest.mark.parametrize("state", ["expired", "answered", "cancelled"])
async def test_goal_question_replies_are_cleared_in_api(goals, state):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    key = await question(plugin, clock, "missed", ["好", "今天也够呛"])
    identifier = plugin.speech.delivered(key)
    if state == "expired":
        at(clock, "2026-11-10T08:00")
    elif state == "cancelled":
        plugin.speech.cancel(f"goals:{plugin.storage.current()['id']}:")
    else:
        await say(service, "好")
    assert service.chat.get(identifier)["replies"] is None
    assert (
        next(m for m in service.chat.page("assistant")["items"] if m["id"] == identifier)["replies"]
        is None
    )


async def test_goal_todo_provenance_uses_goal_title(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    todo = service.store.todo(active_todos(service)[0]["id"])
    source = todo["sources"][0]
    assert source["plugin"] == "goals" and source["chat_name"] == "高数85分"
    assert source["sender"] in {"", "高数85分"}


async def test_goal_prompt_is_enabled_only_for_assistant(goals):
    service, mcp, plugin, _ = goals

    async def act(mcp, prompt):
        _, reply = ids(prompt)
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "普通回答"})

    agent = Agent(mcp, act)
    service.jobs.harnesses = {"claude_code": agent}
    await say(service, "聊聊")
    assert "goals_propose" in agent.prompts[-1]
    await service.plugins.patch("goals", PluginPatch(enabled=False))
    await say(service, "聊聊")
    assert "goals_propose" not in agent.prompts[-1]
    assert "可选 replies" not in agent.prompts[-1]


@pytest.mark.parametrize("replies", [["x"] * 5, ["x" * 31], [""], ["   "], [], "不是数组"])
async def test_invalid_replies_never_reject_text(goals, replies, caplog):
    service, mcp, plugin, _ = goals

    async def act(mcp, prompt):
        _, reply = ids(prompt)
        await tool(
            mcp, "chat_reply", {"reply_id": reply, "text": "这句应该照常保存", "replies": replies}
        )

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    reply = await say(service, "普通问题")
    assert reply["status"] == "ready" and reply["text"] == "这句应该照常保存"
    assert reply["replies"] is None
    assert "chat_replies_ignored" in caplog.text
    assert "不是数组" not in caplog.text


async def blocker(goals, choice):
    service, mcp, plugin, clock = goals
    plan = maths_profile()
    plan["blocker_choices"] = [
        dict(text="选择题", kind="content"),
        dict(text="时间不够", kind="time"),
        dict(text="等材料", kind="materials"),
    ]
    await build(service, mcp, plugin, plan)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    await question(plugin, clock, "missed", ["好", "有点卡"])
    await say(service, "有点卡")
    if choice == "选择题":

        async def help_with_notes(mcp, prompt):
            _, reply = ids(prompt)
            assert "memory_read" in {t.name for t in await mcp.list_tools()}
            assert plugin.storage.current()["state"]["blocker_kind"] == "content"
            await tool(
                mcp,
                "chat_reply",
                {"reply_id": reply, "text": "先看看这道选择题，我可以查相关笔记。"},
            )

        service.jobs.harnesses = {"claude_code": Agent(mcp, help_with_notes)}
    return await say(service, choice)


async def test_time_blocker_offers_smaller_step(goals):
    reply = await blocker(goals, "时间不够")
    assert reply["status"] == "ready" and reply["replies"] == ["好", "照原计划"]
    assert "先做5道选择题" in reply["text"]


async def test_materials_blocker_pauses_until_agreed_day(goals):
    service, _, plugin, clock = goals
    reply = await blocker(goals, "等材料")
    assert reply["replies"] == ["三天后", "下周", "我自己说"]
    await say(service, "三天后")
    assert plugin.storage.current()["status"] == "paused"
    assert plugin.storage.current()["state"]["ask_on"] == "2026-11-13"
    assert not active_todos(service)
    at(clock, "2026-11-13T09:00")
    await plugin.follow()
    assert "材料" in messages(service)[-1]["text"]


async def test_content_blocker_goes_to_agent_with_memory_tools(goals):
    service, mcp, plugin, _ = goals
    reply = await blocker(goals, "选择题")
    assert reply["status"] == "ready"
    assert plugin.storage.current()["state"]["blocker_kind"] == "content"
    assert "相关笔记" in reply["text"]
    assert len(service.jobs.harnesses["claude_code"].prompts) == 1


async def test_first_bad_number_does_not_reduce_and_two_bad_numbers_offer_once(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    payload = dict(criterion="correct", target=8, comparison="gte", values={"5道": 5})
    for day, offer in (("2026-11-10", False), ("2026-11-12", True), ("2026-11-14", False)):
        at(clock, day + "T09:00")
        await question(plugin, clock, "number", ["5道", "先不说"], payload)
        reply = await say(service, "5道")
        assert bool(reply["replies"]) is offer
        if offer:
            assert reply["replies"] == ["好", "照原计划"]
        else:
            assert "5" in reply["text"] and "下次" not in reply["text"]


@pytest.mark.parametrize("text", ["做完了，感觉还不错", "背了 50 个单词", "去了健身房"])
async def test_agent_can_record_clear_user_activity_without_word_lists(goals, text):
    service, mcp, plugin, _ = goals
    await build(service, mcp, plugin, maths_profile())
    reply = await record(service, mcp, text)
    assert reply["status"] == "ready"
    assert plugin.storage.records(plugin.storage.current()["id"])[0]["text"] == text
    assert "算一次" in reply["text"]


async def test_completion_mentions_goal_why_dates_and_weekly_closure(goals):
    service, mcp, plugin, clock = goals
    plan = maths_profile()
    plan.update(
        deadline=None,
        result_date=None,
        criteria=[dict(id="one", kind="steps", title="做一次", target=1)],
    )
    await build(service, mcp, plugin, plan)
    at(clock, "2026-11-10T07:40")
    await plugin.follow()
    await service.update_todo(active_todos(service)[0]["id"], TodoPatch(status="completed"))
    await plugin.follow()
    reply = await say(service, "达成了")
    for word in ("高数85分", "想学好基础课", "1 天", "2026/11/08", "2026/11/10"):
        assert word in reply["text"]
    data = plugin.weekly_input(date(2026, 11, 15))
    assert data["finished"][0]["status"] == "achieved"
    assert "下一个目标" in json.dumps(await plugin.cards(), ensure_ascii=False)
    closed_id = plugin.storage.current()["id"]
    await build(service, mcp, plugin, plan)
    assert plugin.storage.current()["id"] != closed_id
    assert plugin.weekly_input(date(2026, 11, 15))["finished"][0]["status"] == "achieved"


async def test_deadline_transition_says_thanks_with_daily_budget(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    at(clock, "2026-12-21T08:00")
    await plugin.follow()
    assert "高数85分那天过去啦，辛苦了" in messages(service)[-1]["text"]
    count = len(messages(service))
    await plugin.follow()
    assert len(messages(service)) == count


async def test_smaller_week_is_temporary_and_actually_smaller(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    at(clock, "2026-11-10T08:00")
    await question(plugin, clock, "busy", ["改小一点", "照原计划"])
    reply = await say(service, "改小一点")
    assert "这周先做 1 次，下周回到 2 次" in reply["text"]
    assert "0/1" in (await plugin.cards())[0]["data"]["headline"]
    at(clock, "2026-11-16T08:00")
    await plugin.follow()
    assert "0/2" in (await plugin.cards())[0]["data"]["headline"]
    assert plugin.storage.current()["profile"]["weekly_target"] == 2


async def test_weekly_reply_does_not_sound_like_resuming(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    at(clock, "2026-11-15T21:00")
    await plugin.weekly_finished(date(2026, 11, 9))
    reply = await say(service, "照这个来")
    assert reply["text"] == "好，下周还是 2 次。"


async def test_move_leaves_fifteen_minutes_after_class(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    timetable(service)
    service.db.execute(
        "INSERT INTO timetable_entries VALUES('2026-11-12','16:00','18:00','临时实验','课程')"
    )
    at(clock, "2026-11-12T07:40")
    await plugin.follow()
    assert messages(service)[-1]["text"] == "今天 16:30 有临时实验，挪到 18:15？"
    service.plugins.instances.pop("timetable")


def test_user_words_are_not_rewritten_and_truncation_has_ellipsis():
    from plugins.goals.planning import visible

    assert visible("怕失败") == "怕失败"
    assert visible("一二三四五", 4) == "一二三…"


async def test_overdue_deadline_and_named_note_survive_rename(goals):
    service, mcp, plugin, clock = goals
    plan = maths_profile()
    plan.update(result_date=None, criteria=[dict(id="one", kind="steps", title="做四次", target=4)])
    await build(service, mcp, plugin, plan)
    goal = plugin.storage.current()
    assert goal["state"]["note_path"] == "目标/2026-11 高数85分.md"
    original = service.memory.path(goal["state"]["note_path"])
    original.write_text(original.read_text() + "\n用户自己的笔记\n")
    at(clock, "2026-12-21T08:00")
    await plugin.follow()
    assert "-1 天" not in json.dumps(await plugin.cards(), ensure_ascii=False)
    changed = dict(plugin.storage.current()["profile"], title="高数基础")
    draft = await stage(service, mcp, plugin, plan=changed)
    await confirm(service, mcp, draft)
    newpath = plugin.storage.current()["state"]["note_path"]
    assert newpath == "目标/2026-11 高数基础.md"
    assert "用户自己的笔记" in service.memory.read(newpath)["content"]
    assert not original.exists()


def test_goal_prompt_does_not_confirm_implementation_wording():
    text = (PROMPTS / "goals_chat.md").read_text()
    assert "不放进复述" in text and "必要的日期" in text


def test_program_templates_have_no_internal_terms():
    import ast

    from plugins.goals.planning import INTERNAL

    text = (PROMPTS.parent / "plugins/goals/plugin.py").read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any("\u4e00" <= c <= "\u9fff" for c in node.value)
        ):
            if (
                node.value.startswith(("# ", "目标「"))
                or "只能" in node.value
                or "请先" in node.value
            ):
                continue
            assert not any(word in node.value for word in INTERNAL), node.value


@pytest.mark.parametrize("bot_id", ["assistant", "homework"])
async def test_replies_are_ignored_outside_enabled_assistant_goals(goals, bot_id):
    service, mcp, plugin, _ = goals
    if bot_id == "assistant":
        await service.plugins.patch("goals", PluginPatch(enabled=False))
    else:
        await service.plugins.patch("homework", PluginPatch(enabled=True))

    async def act(mcp, prompt):
        _, reply = ids(prompt)
        assert "goals_propose" not in prompt
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "普通回答", "replies": ["好"]})

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    accepted = await service.chat.send(bot_id, "聊聊")
    if task := service.chat.tasks.get(bot_id):
        await task
    reply = service.chat.get(accepted["reply"]["id"])
    assert reply["status"] == "ready" and reply["replies"] is None


async def test_draft_summary_must_still_be_latest_ready_bot_line(goals):
    service, mcp, plugin, clock = goals
    old = await stage(service, mcp, plugin)
    plugin.speech.enqueue(
        "unrelated",
        "assistant",
        "reply",
        "另一件事想问你",
        None,
        clock.value,
        clock.value.replace(day=9),
    )
    plugin.speech.drain(clock.value)
    observed = []

    async def act(mcp, prompt):
        _, reply = ids(prompt)
        observed.append({t.name for t in await mcp.list_tools()})
        assert not service.db.rows("SELECT * FROM goals_drafts")
        with pytest.raises(ToolError):
            await tool(mcp, "goals_confirm", {"arguments": {"draft": old}})
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "需要重新商量计划。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    assert (await say(service, "定下来"))["status"] == "ready"
    assert "todos_add" in observed[0]


async def test_old_open_goal_index_migrates_to_allow_dropped_history(goals):
    from plugins.goals.storage import Storage

    service, mcp, plugin, _ = goals
    await build(service, mcp, plugin, maths_profile())
    old_id = plugin.storage.current()["id"]
    legacy_profile = dict(plugin.storage.current()["profile"])
    legacy_profile["blocker_choices"] = ["选择题", "时间不够", "等材料"]
    service.db.execute(
        "UPDATE goals_profiles SET profile=? WHERE id=?", (json.dumps(legacy_profile), old_id)
    )
    service.db.execute("DROP INDEX goals_one_open")
    service.db.execute(
        "CREATE UNIQUE INDEX goals_one_open ON goals_profiles((1)) WHERE status!='achieved'"
    )
    plugin.storage = Storage(service.db)
    assert [
        choice["kind"] for choice in plugin.storage.current()["profile"]["blocker_choices"]
    ] == ["content", "time", "materials"]
    plugin.set_status(plugin.storage.current(), "dropped")
    draft = await stage(service, mcp, plugin)
    await confirm(service, mcp, draft)
    assert plugin.storage.current()["id"] != old_id


async def test_named_note_migration_preserves_notes_after_index_retry(goals, monkeypatch):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    goal = plugin.storage.current()
    named = service.memory.path(goal["state"]["note_path"])
    legacy = service.memory.path(f"目标/{goal['id']}.md")
    named.rename(legacy)
    legacy.write_text(legacy.read_text() + "\n用户自己的内容\n")
    state = dict(goal["state"])
    state.pop("note_path")
    plugin.storage.save(goal, state=state)
    index = service.memory.index_one
    calls = []

    def fail_once(path):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("synthetic index error")
        return index(path)

    monkeypatch.setattr(service.memory, "index_one", fail_once)
    plugin.note_later(plugin.storage.current())
    at(clock, "2026-11-09T08:00")
    await plugin.follow()
    assert plugin.storage.current()["state"]["note_path"] == "目标/2026-11 高数85分.md"
    assert (
        "用户自己的内容"
        in service.memory.read(plugin.storage.current()["state"]["note_path"])["content"]
    )
    assert not legacy.exists()
    assert len(list(named.parent.glob("*.md"))) == 1


async def test_new_blocker_choices_require_agent_supplied_types(goals):
    service, mcp, plugin, _ = goals
    plan = maths_profile()
    plan["blocker_choices"] = ["选择题", "时间不够", "等材料"]

    async def act(mcp, prompt):
        _, reply = ids(prompt)
        with pytest.raises(ToolError):
            await tool(mcp, "goals_propose", {"arguments": {"profile": plan}})
        await tool(mcp, "chat_reply", {"reply_id": reply, "text": "先把计划补完整。"})

    service.jobs.harnesses = {"claude_code": Agent(mcp, act)}
    assert (await say(service, "开始商量"))["status"] == "ready"
    assert not service.db.rows("SELECT * FROM goals_drafts")


async def test_reducing_week_cancels_older_queued_questions(goals):
    service, mcp, plugin, clock = goals
    await build(service, mcp, plugin, maths_profile())
    await question(plugin, clock, "busy", ["改小一点", "照原计划"])
    old = plugin.ask(
        plugin.storage.current(), "old-blocker", "blocker", "之前的问题", ["做了", "有点卡"], days=7
    )
    await say(service, "改小一点")
    at(clock, "2026-11-09T08:00")
    plugin.speech.drain(clock.value)
    assert plugin.speech.delivered(old) is None
