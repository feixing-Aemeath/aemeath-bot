"""homework, campus and grades: the school plugins behind 作业Bot, 校园Bot and 成绩Bot."""

import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from core.mcp_server import create_mcp
from core.models import MessageInput, PluginPatch, TodoCreate, TodoPatch
from plugins.grades.plugin import current_term, parse_score, points, term_label
from plugins.timetable.plugin import SCHEMA as TIMETABLE_SCHEMA

ZONE = ZoneInfo("Asia/Shanghai")
# Wednesday 21 October 2026, 10:00 in Shanghai.
NOW = datetime(2026, 10, 21, 10, tzinfo=ZONE)


def ingest(service, chat, text, external_id, sent_at=NOW):
    service.store.ingest(
        "qq_screen",
        [
            MessageInput(
                external_id=external_id, chat_name=chat, sender="老师", sent_at=sent_at, text=text
            )
        ],
        [chat],
    )
    rows = service.db.rows("SELECT id FROM messages WHERE external_id=?", (external_id,))
    return rows[0]["id"]


async def enable(service, identifier, config=None):
    await service.plugins.patch(identifier, PluginPatch(enabled=True, config=config or {}))
    instance = service.plugins.instances[identifier]
    instance.now = lambda: NOW
    return instance


async def call(mcp, name, arguments):
    result = await mcp.call_tool(name, {"arguments": arguments})
    return json.loads(result[0].text)


def add_todo(service, message_id, title, excerpt, due_at=None, has_time=False):
    return service.store.add_todo(
        TodoCreate(
            title=title,
            due_at=due_at,
            has_time=has_time,
            source_plugin="qq_screen",
            source_refs=[message_id],
            source_excerpt=excerpt,
            confidence=0.95,
        )
    )["todo"]


@pytest.fixture
async def started(service):
    mcp = create_mcp(service)
    await service.start()
    yield service, mcp
    await service.close()


async def test_school_plugins_offer_their_tools_when_enabled(started):
    service, mcp = started
    names = {tool.name for tool in await mcp.list_tools()}
    assert not names & {"homework_mark", "campus_event_add", "grades_record"}
    for identifier in ("homework", "campus", "grades"):
        view = await service.plugins.patch(identifier, PluginPatch(enabled=True))
        assert view["state"] == "enabled" and view["mascot"]["sticker"]
    names = {tool.name for tool in await mcp.list_tools()}
    assert {
        "homework_mark",
        "homework_list",
        "campus_event_add",
        "campus_leave_record",
        "campus_list",
        "grades_record",
        "grades_list",
        "grades_score_record",
    } <= names


async def test_homework_comes_from_course_chats_keywords_and_marks(started):
    service, mcp = started
    plugin = await enable(service, "homework", {"course_chats": ["数据结构课程群"]})
    course = ingest(service, "数据结构课程群", "周五前交第三章题目", "m1")
    keyword = ingest(service, "班级群", "离散数学作业周六晚上交", "m2")
    marked = ingest(service, "班级群", "下周一交实验三的纸质版", "m3")
    other = ingest(service, "班级群", "周四下午开班会", "m4")
    first = add_todo(
        service, course, "交第三章题目", "周五前交第三章题目", "2026-10-23T00:00:00+08:00"
    )
    add_todo(
        service,
        keyword,
        "离散数学作业",
        "离散数学作业周六晚上交",
        "2026-10-24T23:59:00+08:00",
        True,
    )
    third = add_todo(
        service, marked, "交实验三纸质版", "下周一交实验三的纸质版", "2026-10-26T00:00:00+08:00"
    )
    add_todo(service, other, "参加班会", "周四下午开班会", "2026-10-22T15:00:00+08:00", True)

    assert await call(mcp, "homework_mark", {"todo_id": third["id"], "course": "大学物理"})
    with pytest.raises(ToolError):
        await call(mcp, "homework_mark", {"todo_id": "missing"})

    listed = await call(mcp, "homework_list", {"status": "pending"})
    assert [(t["title"], t["course"]) for t in listed["items"]] == [
        ("交第三章题目", "数据结构课程群"),
        ("离散数学作业", None),
        ("交实验三纸质版", "大学物理"),
    ]

    service.store.update_todo(first["id"], TodoPatch(status="completed"))
    cards = {card["title"]: card for card in await plugin.cards()}
    week = cards["这周要交的作业"]["data"]
    # Saturday's is still to come this week; Monday's belongs to next week.
    assert week["value"] == 1 and week["label"] == "最近一项：周六 23:59 · 离散数学作业"
    assert [t["title"] for t in cards["还没交"]["data"]["todos"]] == [
        "离散数学作业",
        "交实验三纸质版",
    ]
    assert "course" not in cards["还没交"]["data"]["todos"][0]
    assert cards["最近 4 周的作业"]["data"]["points"][-1] == {"x": "本周", "y": 2}
    assert cards["最近交完的"]["data"]["items"][0]["subtitle"] == "数据结构课程群"


async def test_homework_without_any_shows_how_it_fills(started):
    service, _ = started
    plugin = await enable(service, "homework")
    [card] = await plugin.cards()
    assert card["type"] == "text" and "course_chats" in card["data"]["text"]


async def test_cancelled_homework_is_absent_from_lists_and_card_counts(started):
    service, mcp = started
    plugin = await enable(service, "homework")
    message = ingest(service, "班级群", "今天交作业", "cancelled")
    cancelled = add_todo(service, message, "已取消的作业", "今天交作业", NOW, True)
    add_todo(service, message, "仍需提交的作业", "今天交作业", NOW, True)
    service.store.update_todo(cancelled["id"], TodoPatch(status="cancelled", notes="老师取消"))
    assert [t["title"] for t in (await call(mcp, "homework_list", {}))["items"]] == [
        "仍需提交的作业"
    ]
    cards = {card["title"]: card for card in await plugin.cards()}
    assert cards["最近 4 周的作业"]["data"]["points"][-1]["y"] == 1
    assert "最近交完的" not in cards


async def test_homework_date_only_deadline_lasts_all_day(started):
    service, _ = started
    plugin = await enable(service, "homework")
    message = ingest(service, "班级群", "今天交作业", "date-only")
    add_todo(service, message, "今天交作业", "今天交作业", "2026-10-21T00:00:00+08:00")
    add_todo(service, message, "九点交作业", "今天交作业", "2026-10-21T09:00:00+08:00", True)
    add_todo(service, message, "昨天的作业", "今天交作业", "2026-10-20T00:00:00+08:00")
    cards = {c["title"]: c for c in await plugin.cards()}
    stat = cards["这周要交的作业"]["data"]
    assert stat["value"] == 1
    assert stat["label"] == "最近一项：今天 · 今天交作业"
    assert stat["delta"] == "已过截止 2 项"
    plugin.now = lambda: NOW.replace(day=22)
    stat = (await plugin.cards())[0]["data"]
    assert stat["value"] == 0 and stat["delta"] == "已过截止 3 项"


async def test_homework_empty_keywords_disables_keyword_matching(started):
    service, _ = started
    plugin = await enable(service, "homework", {"keywords": []})
    message = ingest(service, "班级群", "交作业", "no-keywords")
    add_todo(service, message, "交作业", "交作业")
    assert (await plugin.homework_list({}))["items"] == []


async def test_campus_events_merge_across_groups_and_leaves_update(started):
    service, mcp = started
    plugin = await enable(service, "campus")
    first = ingest(service, "学院通知群", "周六14:00学术报告厅AI讲座，周五前报名", "e1")
    second = ingest(service, "班级群", "转发：周六 AI 前沿讲座", "e2")
    event = {
        "title": "人工智能前沿讲座",
        "kind": "讲座",
        "starts_at": "2026-10-24T14:00:00+08:00",
        "place": "学术报告厅",
        "signup_deadline": "2026-10-23",
        "source_refs": [first],
    }
    added = await call(mcp, "campus_event_add", event)
    merged = await call(
        mcp,
        "campus_event_add",
        {"title": "人工智能 前沿讲座！", "starts_at": "2026-10-24T14:00:00+08:00",
         "source_refs": [second]},
    )  # fmt: skip
    assert added["disposition"] == "added" and merged["disposition"] == "updated"
    record = merged["record"]
    assert record["source_refs"] == [first, second] and record["place"] == "学术报告厅"
    assert record["kind"] == "讲座"  # not reset by the second, shorter call

    leave_message = ingest(service, "辅导员", "你28号的事假我看到了，在走流程", "l1")
    approved = ingest(service, "辅导员", "假条批了", "l2")
    leave = {
        "kind": "事假",
        "starts_at": "2026-10-28",
        "reason": "回家办证件（半天）",
        "approver": "王老师",
        "source_refs": [leave_message],
    }
    recorded = await call(mcp, "campus_leave_record", leave)
    assert recorded["record"]["status"] == "审批中"
    updated = await call(
        mcp,
        "campus_leave_record",
        {"kind": "事假", "starts_at": "2026-10-28", "status": "已批准", "source_refs": [approved]},
    )
    assert updated["record"]["id"] == recorded["record"]["id"]
    assert updated["record"]["status"] == "已批准"
    assert updated["record"]["reason"] == "回家办证件（半天）"

    cards = {card["title"]: card for card in await plugin.cards()}
    assert cards["请假记录"]["data"]["items"] == [
        {"title": "10月28日 事假", "detail": "已批准", "subtitle": "回家办证件（半天） · 王老师"}
    ]
    assert cards["近期活动"]["data"]["items"] == [
        {
            "title": "人工智能前沿讲座",
            "detail": "讲座",
            "subtitle": "周六 14:00 · 学术报告厅 · 后天前报名",
        }
    ]
    stat = cards["这周的活动"]["data"]
    assert stat["value"] == 1 and stat["label"].startswith("1 个还能报名，最早后天截止：")

    listed = await call(mcp, "campus_list", {"what": "leaves", "upcoming_only": True})
    assert len(listed["items"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "聚会"},
        {"starts_at": "2026-10-24T14:00:00"},  # no offset
        {"source_refs": ["missing"]},
        {"source_refs": []},
        {"colour": "red"},
        {"credits": 0},
        {"credits": "0.5"},
        {"credits": True},
    ],
)
async def test_campus_rejects_bad_records(started, change):
    service, mcp = started
    await enable(service, "campus")
    message = ingest(service, "学院通知群", "周六讲座", "e1")
    event = {"title": "讲座", "starts_at": "2026-10-24T14:00:00+08:00", "source_refs": [message]}
    with pytest.raises(ToolError):
        await call(mcp, "campus_event_add", {**event, **change})


async def test_campus_recommends_open_activities_with_credits_first_and_clashes_last(started):
    service, mcp = started
    plugin = await enable(service, "campus")
    # The timetable's week of 10/19 (read on the Mac): 高等数学 on Thursday morning; the week
    # of 10/26 is guessed from it.
    with service.db.connect() as conn:
        conn.executescript(TIMETABLE_SCHEMA)
        conn.execute("INSERT INTO timetable_weeks VALUES('2026-10-19','2026-10-19T08:00:00+08:00')")
        conn.execute(
            "INSERT INTO timetable_entries VALUES('2026-10-22','09:55','12:20','高等数学I（A）','课程')"
        )
    message = ingest(service, "学院通知群", "本周活动汇总", "e1")

    async def add(title, kind, **fields):
        await call(
            mcp,
            "campus_event_add",
            {"title": title, "kind": kind, "source_refs": [message], **fields},
        )

    await add(
        "人工智能前沿讲座",
        "讲座",
        starts_at="2026-10-24T14:00:00+08:00",
        signup_deadline="2026-10-23",
        credits=0.5,
        credit_kind="学术科技",
    )
    await add("学院篮球赛", "比赛", starts_at="2026-10-22T10:00:00+08:00", credits=1)
    await add("秋季运动会", "文体", starts_at="2026-11-08", signup_deadline="2026-10-22")
    await add("就业讲座", "讲座", starts_at="2026-10-29T11:00:00+08:00")
    await add("班会", "会议", starts_at="2026-10-23T19:00:00+08:00")  # not one to choose
    await add(
        "早就截止的讲座",
        "讲座",
        starts_at="2026-10-30T14:00:00+08:00",
        signup_deadline="2026-10-20",
    )
    await add("昨天的讲座", "讲座", starts_at="2026-10-20T14:00:00+08:00")  # fmt: skip

    picks = (await call(mcp, "campus_list", {"what": "recommended"}))["items"]
    assert [(p["title"], p["clash"]) for p in picks] == [
        ("人工智能前沿讲座", None),
        ("秋季运动会", None),
        ("学院篮球赛", "高等数学I（A）"),
        ("就业讲座", "高等数学I（A）"),  # a guessed week clashes too
    ]
    card = (await plugin.cards())[0]
    assert card["title"] == "推荐参加"
    first, *_, last = card["data"]["items"]
    assert first["subtitle"].startswith("加 0.5 分第二课堂（学术科技） · ")
    assert last["subtitle"].endswith("和「高等数学I（A）」冲突")


def test_grade_scores_scales_and_terms():
    assert parse_score(88) == (88.0, "88", True)
    assert parse_score("良好") == (85, "良好", True)
    assert parse_score("b+") == (88, "b+", True)
    assert parse_score("通过") == (None, "通过", True)
    assert parse_score("不及格")[2] is False and parse_score("59.5")[2] is False
    with pytest.raises(ValueError):
        parse_score(101)
    assert points(85, "standard4") == 3.0 and points(59, "standard4") == 0
    assert points(85, "linear5") == 3.5 and points(100, "pku4") == 4
    assert current_term(datetime(2026, 10, 22)) == "2026-2027-1"
    assert current_term(datetime(2027, 1, 10)) == "2026-2027-1"
    assert current_term(datetime(2027, 3, 1)) == "2026-2027-2"
    assert term_label("2026-2027-2") == "2027春" and term_label("大二上") == "大二上"


async def test_grades_from_messages_and_a_school_csv(started, tmp_path):
    service, mcp = started
    sheet = tmp_path / "成绩.csv"
    sheet.write_bytes(
        "学年学期,课程名称,学分,总评成绩,课程性质\n"
        "2025-2026-2,高等数学,5,92,必修\n"
        "2025-2026-2,大学英语,2,良好,必修\n"
        "2025-2026-2,形势与政策,1,通过,通识\n".encode("gb18030")
    )
    plugin = await enable(service, "grades", {"required_credits": 160, "import_csv": str(sheet)})
    await plugin.import_csv()
    message = ingest(service, "离散数学课程群", "离散数学成绩已出，你的总评 88", "g1")
    recorded = await call(
        mcp,
        "grades_record",
        {"course": "离散数学", "score": 88, "credits": 3, "category": "必修",
         "source_refs": [message]},
    )  # fmt: skip
    assert recorded["grade"]["term"] == "2026-2027-1" and recorded["grade"]["passed"] is True
    again = await call(
        mcp, "grades_record", {"course": "离散数学", "score": 90, "source_refs": [message]}
    )
    assert again["disposition"] == "updated" and again["grade"]["credits"] == 3

    listed = await call(mcp, "grades_list", {})
    assert listed["earned_credits"] == 11
    # standard4, credit-weighted: 高数 5×4, 英语 2×3, 离散 3×4; 通过 is not graded.
    assert listed["gpa"] == round((5 * 4 + 2 * 3 + 3 * 4) / 10, 2)

    cards = {card["title"]: card for card in await plugin.cards()}
    assert cards["学业进度"]["data"]["style"] == "rings"
    rows = {item["title"]: item for item in cards["学业进度"]["data"]["items"]}
    assert rows["平均绩点"]["detail"] == "3.80 / 4"
    assert rows["平均绩点"]["subtitle"].endswith("上学期 3.71")
    assert rows["平均绩点"]["progress"] == {"value": 3.8, "max": 4.0}
    assert rows["已修学分"] == {
        "title": "已修学分", "subtitle": "毕业要 160，还差 149", "detail": "11 / 160",
        "progress": {"value": 11, "max": 160},
    }  # fmt: skip
    assert cards["2026秋已出成绩"]["data"]["items"] == [
        {"title": "离散数学", "subtitle": "3 学分 · 必修", "detail": "90"}
    ]
    assert [p["x"] for p in cards["各学期绩点"]["data"]["points"]] == ["2026春", "2026秋"]


async def test_grades_reject_records_without_sources(started):
    service, mcp = started
    await enable(service, "grades")
    with pytest.raises(ToolError):
        await call(mcp, "grades_record", {"course": "离散数学", "score": 88})
    [card] = await service.plugins.instances["grades"].cards()
    # Before the first exam: every bar is all gray.
    assert [(i["title"], i["detail"], i["progress"]["value"]) for i in card["data"]["items"]] == [
        ("平均绩点", "— / 4", 0), ("综测", "— / 100", 0), ("体测", "— / 100", 0),
        ("已修学分", "0 学分", 0),
    ]  # fmt: skip
    assert card["data"]["items"][0]["subtitle"] == "还没考过试，第一次期末出分后算"


async def test_grades_comprehensive_and_fitness_scores(started):
    service, mcp = started
    plugin = await enable(service, "grades", {"comprehensive_required": 60})
    with pytest.raises(ToolError):
        await call(mcp, "grades_score_record", {"kind": "体测", "required": 60})
    with pytest.raises(ToolError):
        await call(mcp, "grades_score_record", {"kind": "综测"})
    message = ingest(service, "班级群", "体测成绩已出，你 78 分", "f1")
    added = await call(
        mcp, "grades_score_record", {"kind": "体测", "score": 78, "source_refs": [message]}
    )
    assert added["disposition"] == "added" and added["score"]["full"] == 100
    await call(mcp, "grades_score_record", {"kind": "综测", "score": 52.5})
    [card] = await plugin.cards()
    rows = {item["title"]: item for item in card["data"]["items"]}
    assert rows["综测"]["subtitle"] == "毕业要 60 分，还差 7.5"
    assert rows["综测"]["progress"] == {"value": 52.5, "max": 100}
    assert rows["体测"]["subtitle"] == "离满分差 22" and rows["体测"]["detail"] == "78 / 100"
    # The school's own line replaces the config's; the score stays.
    await call(mcp, "grades_score_record", {"kind": "综测", "required": 50})
    [card] = await plugin.cards()
    assert card["data"]["items"][1]["subtitle"] == "毕业要 50 分，已经够了"
    assert (await call(mcp, "grades_list", {}))["scores"]["体测"]["score"] == 78
    # The credits graduating needs; the earned ones come only from the grades.
    with pytest.raises(ToolError):
        await call(mcp, "grades_score_record", {"kind": "学分", "score": 20})
    await call(mcp, "grades_score_record", {"kind": "学分", "required": 155})
    [card] = await plugin.cards()
    assert card["data"]["items"][3] == {
        "title": "已修学分", "subtitle": "毕业要 155，还差 155", "detail": "0 / 155",
        "progress": {"value": 0, "max": 155.0},
    }  # fmt: skip
    assert (await call(mcp, "grades_list", {}))["required_credits"] == 155


def test_dates_are_utc_in_storage():
    # A guard for the shared helper the three plugins use.
    from plugins.school.common import moment

    assert moment({"at": "2026-10-28"}, "at", ZONE) == ("2026-10-27T16:00:00+00:00", False)
    assert (
        moment({"at": "2026-10-28T08:30:00+08:00"}, "at", ZONE)[0]
        == datetime(2026, 10, 28, 0, 30, tzinfo=UTC).isoformat()
    )


@pytest.mark.parametrize("credits", ["-3", "nan", "inf", "31"])
async def test_grades_csv_rejects_invalid_credits(started, tmp_path, credits):
    service, _ = started
    plugin = await enable(service, "grades")
    sheet = tmp_path / "invalid.csv"
    sheet.write_text(f"课程,学分,成绩\n离散数学,{credits},90\n", encoding="utf-8")
    with pytest.raises(ValueError, match="credits"):
        plugin._import(sheet)
    assert plugin.rows() == []


async def test_grades_zero_credit_courses_do_not_affect_gpa(started, tmp_path):
    service, _ = started
    plugin = await enable(service, "grades")
    sheet = tmp_path / "zero.csv"
    sheet.write_text("课程,学分,成绩\n讲座,0,100\n", encoding="utf-8")
    plugin._import(sheet)
    assert (await plugin.grades_list({}))["gpa"] is None
    sheet.write_text("课程,学分,成绩\n讲座,0,100\n数学,3,80\n", encoding="utf-8")
    plugin._import(sheet)
    assert (await plugin.grades_list({}))["gpa"] == 3.0


async def test_grades_unchanged_csv_keeps_release_date(started, tmp_path):
    service, _ = started
    plugin = await enable(service, "grades")
    sheet = tmp_path / "stable.csv"
    sheet.write_text("课程,学分,成绩\n数学,3,80\n", encoding="utf-8")
    plugin._import(sheet)
    recorded = "2026-10-20T02:00:00+00:00"
    service.db.execute("UPDATE grades SET created_at=?,updated_at=?", (recorded, recorded))
    plugin._import(sheet)
    assert plugin.rows()[0]["updated_at"] == recorded
    assert "这一天出的成绩" not in plugin._daily(NOW.date(), ZONE)["body"]
    sheet.write_text("课程,学分,成绩\n数学,3,90\n", encoding="utf-8")
    plugin._import(sheet)
    assert plugin.rows()[0]["updated_at"] != recorded


async def test_grades_csv_keeps_courses_with_different_symbols(started, tmp_path):
    service, _ = started
    plugin = await enable(service, "grades")
    sheet = tmp_path / "symbols.csv"
    sheet.write_text("课程,学分,成绩\nC++,3,90\nC#,3,80\n", encoding="utf-8")
    plugin._import(sheet)
    assert {(r["course"], r["score"]) for r in plugin.rows()} == {("C++", 90), ("C#", 80)}
    original = {r["course"]: r["id"] for r in plugin.rows()}
    sheet.write_text("课程,学分,成绩\nｃ＋＋,3,90\nc #,3,80\n", encoding="utf-8")
    plugin._import(sheet)
    assert {r["id"] for r in plugin.rows()} == set(original.values())
    assert (await plugin.grades_list({}))["earned_credits"] == 6


async def test_grades_explicit_zero_requirement_overrides_default(started):
    service, _ = started
    plugin = await enable(service, "grades", {"comprehensive_required": 60})
    result = await plugin.grades_score_record({"kind": "综测", "required": 0})
    assert result["score"]["required"] == 0
    assert (await plugin.grades_list({}))["scores"]["综测"]["required"] == 0


async def test_campus_date_only_event_expires_after_its_local_day(started):
    service, _ = started
    plugin = await enable(service, "campus", {"past_days": 0})
    message = ingest(service, "学院通知群", "今天文体活动", "all-day")
    await plugin.campus_event_add(
        {"title": "文体活动", "kind": "文体", "starts_at": "2026-10-21", "source_refs": [message]}
    )
    assert len(plugin.events(True, NOW)) == 1
    assert len(plugin.recommend(NOW)) == 1
    assert plugin.events(True, NOW.replace(day=22, hour=0)) == []


async def test_campus_week_stat_excludes_activities_already_started(started):
    service, _ = started
    plugin = await enable(service, "campus")
    message = ingest(service, "学院通知群", "活动汇总", "started")
    for title, hour in (("已开始", "09"), ("还没开始", "11")):
        await plugin.campus_event_add(
            {"title": title, "starts_at": f"2026-10-21T{hour}:00:00+08:00",
             "signup_deadline": "2026-10-21", "source_refs": [message]}
        )  # fmt: skip
    stat = next(c for c in await plugin.cards() if c["title"] == "这周的活动")["data"]
    assert stat["value"] == 1
    assert stat["label"].startswith("1 个还能报名")


@pytest.mark.parametrize(
    "entry,starts,ends,expected",
    [
        (
            ("2026-10-22", "09:00", "10:00", "散步", "自己的日程"),
            "2026-10-22T09:00:00+08:00",
            "2026-10-22T10:00:00+08:00",
            None,
        ),
        (
            ("2026-10-23", "08:00", "09:00", "数学", "课程"),
            "2026-10-22T23:00:00+08:00",
            "2026-10-23T10:00:00+08:00",
            "数学",
        ),
        (  # runs over two days: work handed in by a date, not a time to be there
            ("2026-10-23", "08:00", "09:00", "数学", "课程"),
            "2026-10-22T20:00:00+08:00",
            "2026-10-24T22:00:00+08:00",
            None,
        ),
    ],
)
async def test_campus_clashes_check_courses_across_all_event_days(
    started, entry, starts, ends, expected
):
    service, _ = started
    plugin = await enable(service, "campus")
    with service.db.connect() as conn:
        conn.executescript(TIMETABLE_SCHEMA)
        conn.execute("INSERT INTO timetable_weeks VALUES('2026-10-19','2026-10-19T00:00:00+08:00')")
        conn.execute("INSERT INTO timetable_entries VALUES(?,?,?,?,?)", entry)
    assert plugin.clash({"starts_at": starts, "ends_at": ends, "has_time": True}, ZONE) == expected


# The 第二课堂 site as its student page lists activities (fields as plugins/campus/dekt.py picks
# them), around NOW.
DEKT_TALK = {
    "id": "a1", "name": "人工智能前沿讲座", "start": "2026-10-24 14:00:00",
    "end": "2026-10-24 16:00:00", "signup_end": "2026-10-23 23:59:00", "place": "图书馆报告厅",
    "organizer": "信息学院", "module": "科技创新 学习培训",
}  # fmt: skip
DEKT_SHOW = {
    "id": "a2", "name": "校园漫展", "start": "2026-10-23 00:00:00", "end": "2026-10-24 00:00:00",
    "signup_end": "2026-10-22 23:59:00", "place": "欣苑四楼小礼堂", "organizer": "动漫社",
    "module": "美育润心 社团活动",
}  # fmt: skip


def test_dekt_replies_and_fields():
    from plugins.campus import dekt

    with pytest.raises(RuntimeError, match="^chrome_js_disabled$"):
        dekt.parse(b'{"error": "chrome_js_disabled"}')
    for reply in (
        b"<html>",
        b"[]",
        b'{"open": []}',
        b'{"error": "Login page: \\u8bf7\\u767b\\u5f55"}',
    ):
        with pytest.raises(RuntimeError, match="^dekt_bad_reply$"):
            dekt.parse(reply)
    assert dekt.parse(b'{"open": [], "mine": []}') == {"open": [], "mine": []}

    values = dekt.event_values(DEKT_TALK, ZONE)
    assert values == {
        "title": "人工智能前沿讲座",
        "kind": "讲座",
        "starts_at": "2026-10-24T06:00:00+00:00",
        "ends_at": "2026-10-24T08:00:00+00:00",
        "has_time": 1,
        "place": "图书馆报告厅",
        "signup_deadline": "2026-10-23T15:59:00+00:00",
        "note": "信息学院主办",
        "credit_kind": "科技创新",
    }
    assert dekt.event_values(DEKT_SHOW, ZONE)["kind"] == "文体"
    assert dekt.event_values({**DEKT_SHOW, "module": "科技创新 社团活动"}, ZONE)["kind"] == "其他"
    assert dekt.event_values({**DEKT_TALK, "name": " "}, ZONE) is None
    assert (
        dekt.event_values({**DEKT_TALK, "start": "待定", "signup_end": None}, ZONE)["starts_at"]
        is None
    )
    assert dekt.ref(DEKT_TALK) == "dekt:a1"
    assert "signup_block" not in values and "credits" not in values  # not read from its page

    # What the activity's sign-up button says to the user, in fixed words; taking-part credits.
    def opened(can_enroll, credits=1.0):
        return dekt.event_values({**DEKT_TALK, "can_enroll": can_enroll, "credits": credits}, ZONE)

    assert (opened("")["signup_block"], opened("")["credits"]) == (None, 1.0)
    assert opened("报名通过")["signup_block"] is None
    assert opened("报名对象不符")["signup_block"] == "不在报名范围"
    assert opened("名额不足")["signup_block"] == "名额已满"
    assert opened("报名已截止，请联系管理员 13800000000")["signup_block"] == "暂不能报名"
    assert opened(None)["signup_block"] is None  # its page failed: not hidden
    assert "credits" not in opened("", 20) and "credits" not in opened("", True)


async def test_dekt_fetch_runs_the_fixed_script_in_the_tab(monkeypatch):
    from core import helper_process
    from plugins.campus import dekt

    calls = []

    async def run(argv, data, timeout, capture_error=False):
        calls.append(argv)
        return replies.pop(0)

    monkeypatch.setattr(helper_process, "run", run)
    replies = [(0, b'{"open": [], "mine": []}'), (1, b"execution error: (-1743)"), (1, b"other")]
    assert await dekt.fetch() == {"open": [], "mine": []}
    osascript, flag, script, tab, page_script = calls[0]
    assert (osascript, flag, script, tab, page_script) == (
        "/usr/bin/osascript", "-e", dekt.RUN, "dekt.example.edu.cn/yb/", dekt.SCRIPT
    )  # fmt: skip
    # Read-only: the page's list, activity page and 我的活动 requests, nothing that signs up or
    # cancels.
    assert set(re.findall(r"'(/[A-Za-z]+/[A-Za-z]+)'", page_script)) == {
        "/wxController/ActivityForStu",
        "/wxController/getActivity",
        "/acinfoService/MyActivity",
    }
    for code in ("chrome_automation_denied", "chrome_failed"):
        with pytest.raises(RuntimeError, match=f"^{code}$"):
            await dekt.fetch()


async def test_campus_reads_signups_and_open_activities_from_dekt(started, monkeypatch):
    from plugins.campus import dekt

    service, mcp = started
    plugin = await enable(service, "campus", {"dekt_sync": True})
    assert set(plugin.jobs()) == {"dekt"}
    # A QQ notice of the same talk, with the credits it promised.
    message = ingest(service, "学院通知群", "周六人工智能讲座，加 0.5 分", "e1")
    await call(
        mcp,
        "campus_event_add",
        {
            "title": "人工智能前沿讲座",
            "kind": "讲座",
            "starts_at": "2026-10-24T14:00:00+08:00",
            "credits": 0.5,
            "source_refs": [message],
        },
    )
    other_college = {
        **DEKT_TALK,
        "id": "a3",
        "name": "外院新生寝室设计大赛",
        "can_enroll": "报名对象不符",
    }
    full = {**DEKT_TALK, "id": "a4", "name": "校园寻宝大赛", "can_enroll": "名额不足", "credits": 1}
    site = {"open": [DEKT_TALK, DEKT_SHOW, other_college, full], "mine": [DEKT_SHOW]}

    async def fetch():
        return site

    monkeypatch.setattr(dekt, "fetch", fetch)
    status = await plugin.sync_dekt()
    assert (status["open"], status["mine"], status["error"]) == (4, 1, None)

    # The one not open to the user is left out; the full one is listed as full.
    events = {e["title"]: e for e in (await call(mcp, "campus_list", {"what": "events"}))["items"]}
    assert set(events) == {"人工智能前沿讲座", "校园漫展", "校园寻宝大赛"}
    assert events["校园寻宝大赛"]["signup_block"] == "名额已满"
    assert events["校园寻宝大赛"]["credits"] == 1.0
    del events["校园寻宝大赛"]
    talk, show = events["人工智能前沿讲座"], events["校园漫展"]
    assert talk["credits"] == 0.5 and talk["credit_kind"] == "科技创新"  # merged, credits kept
    assert talk["source_refs"] == [message, "dekt:a1"]
    assert (
        talk["signup_deadline"] == "2026-10-23T15:59:00+00:00" and talk["place"] == "图书馆报告厅"
    )
    assert talk["signup_status"] is None and show["signup_status"] == "已报名"

    # The one signed up for is not recommended again; it leads the cards.
    picks = (await call(mcp, "campus_list", {"what": "recommended"}))["items"]
    assert [p["title"] for p in picks] == ["人工智能前沿讲座"]
    cards = await plugin.cards()
    assert [c["title"] for c in cards][:2] == ["我报名的活动", "推荐参加"]
    assert cards[0]["data"]["items"] == [
        {"title": "校园漫展", "detail": "已报名", "subtitle": "后天 00:00 · 欣苑四楼小礼堂"}
    ]
    daily = await plugin.daily(NOW.date().replace(day=23))
    assert "校园漫展（文体） · 已报名" in daily["body"]

    recent = next(c for c in cards if c["title"] == "近期活动")["data"]["items"]
    assert any(i["title"] == "校园寻宝大赛" and "名额已满" in i["subtitle"] for i in recent)

    # Cancelled on the site: no longer marked; a second read changes nothing else.
    site["mine"] = []
    await plugin.sync_dekt()
    show = next(
        e for e in (await call(mcp, "campus_list", {"what": "events"}))["items"]
        if e["title"] == "校园漫展"
    )  # fmt: skip
    assert show["signup_status"] is None
    assert len((await call(mcp, "campus_list", {"what": "events"}))["items"]) == 3


async def test_dekt_problems_reach_the_runs_once_and_the_watchdog(started, monkeypatch):
    from plugins.campus import dekt

    service, _ = started
    plugin = await enable(service, "campus", {"dekt_sync": True})

    async def disabled():
        raise RuntimeError("chrome_js_disabled")

    monkeypatch.setattr(dekt, "fetch", disabled)
    with pytest.raises(RuntimeError, match="^chrome_js_disabled$"):
        await plugin.sync_dekt()
    await plugin.sync_dekt()  # the same problem again: not another failed run
    [problem] = await plugin.watch()
    assert problem["key"] == "dekt" and problem["jobs"] == ["dekt"]
    assert "允许 Apple 事件中的 JavaScript" in problem["hint"]

    async def leaky():
        raise RuntimeError("Unexpected token < in JSON at 第二课堂")

    monkeypatch.setattr(dekt, "fetch", leaky)
    with pytest.raises(RuntimeError, match="^dekt_failed$"):  # page text never reaches runs
        await plugin.sync_dekt()

    off = await enable(service, "campus", {})
    assert off.jobs() == {} and await off.watch() == []
