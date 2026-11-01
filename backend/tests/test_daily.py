"""每日文件夹 (core/daily.py): one day's files from the database and the plugins' daily hooks."""

import json
from datetime import UTC, timedelta

import pytest

from core.daily import keep_notes
from core.mcp_server import create_mcp
from core.models import TodoPatch
from tests.test_health_reports import MORNING, enable_health
from tests.test_school import NOW, add_todo, call, enable, ingest

DAY = NOW.date()  # Wednesday 21 October 2026
FOLDER = f"每日/{DAY.isoformat()}"


@pytest.fixture
async def started(service):
    mcp = create_mcp(service)
    await service.start()
    yield service, mcp
    await service.close()


def read(service, name, day=DAY):
    return service.memory.read(f"每日/{day.isoformat()}/{name}")["content"]


def stamp_on(service, table, identifier, column, moment):
    """The store stamps the real clock; move a row onto the test's day."""
    service.db.execute(
        f"UPDATE {table} SET {column}=? WHERE id=?",
        (moment.astimezone(UTC).isoformat(), identifier),
    )


async def test_a_day_from_messages_todos_and_summaries(started):
    service, _ = started
    report = ingest(service, "软件1班", "今天 18:00 前交实验报告", "m1")
    ingest(service, "软件1班", "昨天的通知", "m0", sent_at=NOW - timedelta(days=1))
    due = add_todo(service, report, "交实验报告", "交实验报告", NOW.replace(hour=18), True)
    late = add_todo(service, report, "补交问卷", "交实验报告", NOW - timedelta(days=2), True)
    later = add_todo(service, report, "准备期中", "交实验报告", NOW + timedelta(days=5))
    done = add_todo(service, report, "领教材", "交实验报告")
    service.store.update_todo(done["id"], TodoPatch(status="completed"))
    for todo in (due, late, later, done):
        stamp_on(service, "todos", todo["id"], "created_at", NOW - timedelta(days=3))
    stamp_on(service, "todos", later["id"], "created_at", NOW)
    stamp_on(service, "todos", done["id"], "updated_at", NOW)
    service.store.digest(DAY.isoformat(), "qq_screen", "软件1班：今天交实验报告。")

    written = await service.daily.write(DAY)
    assert sorted(written) == [f"{FOLDER}/{name}" for name in ("今天.md", "待办.md", "消息.md")]

    messages = read(service, "消息.md")
    assert "## QQ 屏幕读取 · 软件1班\n- 10:00 老师：今天 18:00 前交实验报告" in messages
    assert "昨天的通知" not in messages
    todos = read(service, "待办.md")
    assert "## 今天截止\n- [ ] 交实验报告 · 18:00 截止 · 来自 QQ 屏幕读取 软件1班" in todos
    assert "## 已经逾期\n- [ ] 补交问卷 · 10月19日 10:00 截止" in todos
    assert "## 今天新记下的\n- [ ] 准备期中 · 10月26日 截止" in todos
    assert "## 今天完成的\n- [x] 领教材" in todos
    today = read(service, "今天.md")
    assert today.startswith("# 10月21日 周三\n")
    assert "- 助理Bot · [待办](待办.md)：今天截止 1 件，逾期 1 件，新记下 1 件" in today
    assert "- 消息来源 · [消息](消息.md)：1 个会话 1 条" in today
    assert "### QQ 屏幕读取\n\n软件1班：今天交实验报告。" in today
    assert today.endswith("## 笔记\n\n（Bot 和你写在这一节，后台重新整理时不会改动。）\n")

    # Nothing changed: nothing is written again (the search index and Obsidian stay calm).
    assert await service.daily.write(DAY) == []


async def test_the_watchdogs_alerts_are_not_the_days_deadlines(started):
    service, _ = started
    report = ingest(service, "软件1班", "今天 18:00 前交实验报告", "m1")
    add_todo(service, report, "交实验报告", "交实验报告", NOW.replace(hour=18), True)
    alert = add_todo(service, report, "课表读不到了", "交实验报告", NOW.replace(hour=0))
    fixed = add_todo(service, report, "QQ 屏幕读取一直失败", "交实验报告", NOW.replace(hour=0))
    notes = (
        "从 10月21日 01:30 起就没成功过。\n怎么处理：解锁后会自己恢复。\n（助理每 10 分钟看一次。）"
    )
    for todo in (alert, fixed):
        service.db.execute(
            "UPDATE todos SET source_plugin='watchdog', notes=? WHERE id=?", (notes, todo["id"])
        )
    service.store.update_todo(fixed["id"], TodoPatch(status="completed"))

    await service.daily.write(DAY)
    todos = read(service, "待办.md")
    deadlines = todos.split("## 今天截止\n")[1].split("\n\n")[0]
    assert "交实验报告" in deadlines and "读不到" not in deadlines
    assert (
        "## 系统提醒\n数据看门狗报的后台问题，不是要用户去做的待办；好了以后会自己消失。\n"
        "- 课表读不到了：从 10月21日 01:30 起就没成功过。怎么处理：解锁后会自己恢复。"
    ) in todos
    assert "QQ 屏幕读取一直失败" not in todos and "助理每" not in todos
    assert "今天截止 1 件；另有 1 条系统提醒（不是待办）" in read(service, "今天.md")


async def test_notes_survive_rewriting(started):
    service, _ = started
    message = ingest(service, "软件1班", "周五交作业", "n1")
    await service.daily.write(DAY)
    path = f"{FOLDER}/待办.md"
    note = "先做实验报告，再看期中。\n"
    content = service.memory.read(path)["content"]
    head = content[: content.index("## 笔记")]
    service.memory.write(path, head + "## 笔记\n\n" + note)
    add_todo(service, message, "交作业", "周五交作业", NOW.replace(hour=20), True)
    assert path in await service.daily.write(DAY)
    rewritten = read(service, "待办.md")
    assert "交作业 · 20:00 截止" in rewritten and rewritten.endswith("## 笔记\n\n" + note)
    assert (
        keep_notes(None, "# 新的")
        == "# 新的\n\n## 笔记\n\n（Bot 和你写在这一节，后台重新整理时不会改动。）\n"
    )


@pytest.mark.parametrize("identities", [[], ["张三", "2026123456"]])
async def test_messages_redact_local_identities_before_indexing(started, identities):
    service, _ = started
    service.db.execute(
        "UPDATE plugins SET config_json=? WHERE id='qq_screen'",
        (json.dumps({"self_names": identities}),),
    )
    original = "张 三（２０２６１２３４５６）请交作业"
    message = ingest(service, "张三的班级", original, "private")
    service.db.execute("UPDATE messages SET sender='张三' WHERE id=?", (message,))

    await service.daily.write(DAY)

    content = read(service, "消息.md")
    if identities:
        assert "[本人]的班级" in content
        assert "[本人]：[本人]（[本人]）请交作业" in content
        assert not service.memory.search("２０２６１２３４５６")
        assert "张三" not in content and "张 三" not in content
    else:
        assert f"张三：{original}" in content
    assert (
        service.db.rows("SELECT text FROM messages WHERE external_id='private'")[0]["text"]
        == original
    )


async def test_school_plugins_add_their_own_files(started):
    service, mcp = started
    await enable(service, "homework", {"course_chats": ["高等数学"]})
    await enable(service, "campus")
    await enable(service, "grades")
    homework = ingest(service, "高等数学", "今晚 22:00 前交习题 3.2", "h1")
    add_todo(service, homework, "习题 3.2", "交习题 3.2", NOW.replace(hour=22), True)
    lecture = ingest(service, "学院通知群", "今天 14:00 AI 讲座", "c1")
    await call(
        mcp,
        "campus_event_add",
        {"title": "AI 讲座", "kind": "讲座", "starts_at": "2026-10-21T14:00:00+08:00",
         "place": "学术报告厅", "source_refs": [lecture]},
    )  # fmt: skip
    score = ingest(service, "离散数学课程群", "离散数学成绩已出，总评 88", "g1")
    recorded = await call(
        mcp,
        "grades_record",
        {"course": "离散数学", "score": 88, "credits": 3, "source_refs": [score]},
    )
    stamp_on(service, "grades", recorded["grade"]["id"], "updated_at", NOW)

    await service.daily.write(DAY)
    today = read(service, "今天.md")
    assert "- 作业Bot · [作业](作业.md)：今天要交 1 项" in today
    assert "- 校园Bot · [活动与请假](活动与请假.md)：今天活动 1 个" in today
    assert "- 成绩Bot · [成绩](成绩.md)：这一天出了 1 门成绩" in today
    assert today.index("作业Bot") < today.index("校园Bot") < today.index("成绩Bot")
    homework_file = read(service, "作业.md")
    assert homework_file.startswith("# 作业 · 10月21日 周三\n\n负责：作业Bot。")
    assert "## 今天要交\n- [ ] 高等数学：习题 3.2 · 今天 22:00 截止" in homework_file
    assert "## 今天的活动\n- AI 讲座（讲座） · 今天 14:00 · 学术报告厅" in read(
        service, "活动与请假.md"
    )
    assert "- 离散数学：88（2026秋，3 学分）" in read(service, "成绩.md")


async def test_a_broken_hook_is_recorded_and_the_rest_written(started):
    service, _ = started

    class Broken:
        async def daily(self, day):
            return {"file": "../待办.md", "bot": "坏Bot", "summary": "", "body": ""}

        async def teardown(self):
            pass

    service.plugins.instances["broken"] = Broken()
    try:
        written = await service.daily.write(DAY)
    finally:
        service.plugins.instances.pop("broken")
    assert f"{FOLDER}/今天.md" in written and "坏Bot" not in read(service, "今天.md")
    runs = service.db.rows("SELECT status,error FROM runs WHERE job='plugin:broken:daily'")
    assert runs == [{"status": "failed", "error": "plugin_daily_failed"}]


async def test_health_writes_the_night_and_readiness(service):
    _, plugin = await enable_health(service)
    try:
        part = await plugin.daily(MORNING.date())
        assert part["file"] == "健康.md" and part["bot"] == "健康Bot"
        assert "昨晚睡了" in part["summary"]
        assert "## 昨晚睡眠" in part["body"] and "入睡" in part["body"]
    finally:
        await service.close()


async def test_daily_job_is_scheduled(started):
    service, _ = started
    service.jobs.configure()
    assert service.jobs.scheduler.get_job("daily_notes") is not None
    assert "daily_notes" in service.jobs.callbacks
