"""homework, campus and grades: the school plugins behind 作业Bot, 校园Bot and 成绩Bot."""

import json
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
