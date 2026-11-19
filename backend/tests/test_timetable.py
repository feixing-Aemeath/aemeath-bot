"""课表 (plugins/timetable): the school's week view read from recognized text, stored per week,
guessed for weeks the page has not shown, and written to the day's folder."""

import asyncio
import json
from datetime import date, datetime, timedelta
from uuid import UUID

import pytest

from core.config import Settings
from core.mcp_server import create_mcp
from core.models import PluginPatch
from core.service import Service
from plugins.screen.model import TextBlock
from plugins.timetable import page
from plugins.timetable.page import PageError, parse_month, parse_week
from tests.conftest import RecordingAppleBridge
from tests.test_school import ZONE, call

MONDAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 20, 7, 30, tzinfo=ZONE)  # Tuesday morning
COURSES = [
    (0, "09:55-11:30", ["俱乐部体育1"]),
    (1, "08:00-09:35", ["大学英语", "(1)"]),  # OCR reads the brackets either way
    (1, "09:55-12:20", ["高等数学|", "（A）"]),  # and 高等数学I as 高等数学|
    (2, "08:00-09:35", ["大学生职业发", "展与就业指导", "（A）"]),
    (3, "15:55-18:20", ["思想道德与法", "治"]),
]


def block(text, x, y, w=0.04, h=0.016):
    return TextBlock(text=text, x=x, y=y, w=w, h=h)


def week_page(monday=MONDAY, courses=COURSES, own=(), picker=True, numbers=True):
    """Text blocks as Vision returns them for the week view (fractions of the window)."""
    blocks = [
        block("学校门户-个人日程", 0.153, 0.016, 0.087),
        block("周历", 0.487, 0.252, 0.022),
        block("日历名称", 0.061, 0.316),
        block("个人日程", 0.064, 0.374, h=0.019),
        block("我的课程", 0.062, 0.527),
        # The legend on the right names the calendars again.
        block("个人日程", 0.609, 0.294),
        block("我的课程", 0.626, 0.436),
    ]
    if picker:
        blocks.append(block((monday + timedelta(days=6)).isoformat(), 0.108, 0.251, 0.058))
    for index in range(7):
        x = 0.124 + index * 0.069
        if numbers:
            blocks.append(block(str((monday + timedelta(days=index)).day), x + 0.02, 0.305, 0.013))
        blocks.append(block(f"周{'一二三四五六日'[index]}", x + 0.017, 0.327, 0.02))
    for entries, top in ((own, 0.358), (courses, 0.421)):
        bottoms = {}
        for index, span, lines in entries:
            x, y = 0.124 + index * 0.069, bottoms.get(index, top)
            for text in [span, *lines]:
                blocks.append(block(text, x, y, 0.05))
                y += 0.018
            bottoms[index] = y + 0.012
    return blocks


def test_the_week_view_is_read_from_its_text():
    own = [(4, "19:00-20:00", ["社团例会"])]
    week = parse_week(week_page(own=own), NOW.date())
    assert week.monday == MONDAY
    assert [(e.day.day, e.start, e.end, e.title, e.kind) for e in week.entries] == [
        (19, "09:55", "11:30", "俱乐部体育1", "课程"),
        (20, "08:00", "09:35", "大学英语（1）", "课程"),
        (20, "09:55", "12:20", "高等数学I（A）", "课程"),
        (21, "08:00", "09:35", "大学生职业发展与就业指导（A）", "课程"),
        (22, "15:55", "18:20", "思想道德与法治", "课程"),
        (23, "19:00", "20:00", "社团例会", "日程"),
    ]


def test_the_week_comes_from_the_day_numbers_without_the_picker():
    other = MONDAY + timedelta(weeks=2)
    week = parse_week(week_page(monday=other, picker=False), NOW.date())
    assert week.monday == other and week.entries[0].day == other
    # A misread picker is checked against the numbers and not believed.
    blocks = [
        b for b in week_page(monday=other) if b.text != (other + timedelta(days=6)).isoformat()
    ]
    blocks.append(block("2026-10-21", 0.108, 0.251, 0.058))
    assert parse_week(blocks, NOW.date()).monday == other
    with pytest.raises(PageError, match="^timetable_week_unreadable$"):
        parse_week(week_page(picker=False, numbers=False), NOW.date())


def test_other_views_are_not_read_as_a_week():
    month = [b for b in week_page() if b.text != "日历名称"]
    with pytest.raises(PageError, match="^timetable_not_week_view$"):
        parse_week(month, NOW.date())
    with pytest.raises(PageError, match="^timetable_not_week_view$"):
        parse_week([b for b in week_page() if b.text != "周三"], NOW.date())
    empty = parse_week(week_page(courses=()), NOW.date())
    assert empty.monday == MONDAY and empty.entries == ()


def test_wrapped_course_time_is_read_as_one_range():
    blocks = week_page(courses=[(0, "09:55-", ["11:30", "俱乐部体育1"])])
    week = parse_week(blocks, NOW.date())
    assert [(e.start, e.end, e.title) for e in week.entries] == [("09:55", "11:30", "俱乐部体育1")]


def test_a_clock_in_the_course_title_is_kept_as_title_text():
    title = "实验课（14:00签到）"
    week = parse_week(week_page(courses=[(0, "14:00-15:35", [title])]), NOW.date())
    assert [e.title for e in week.entries] == [title]


@pytest.mark.parametrize("span", ["08:00-29:35", "09:35-08:00", "08:00-0g:35"])
def test_bad_course_times_are_not_silently_dropped(span):
    with pytest.raises(PageError, match="^timetable_entries_unreadable$"):
        parse_week(week_page(courses=[(0, span, ["大学英语"])]), NOW.date())


NOVEMBER = {  # weekday: the courses of that day, every week
    0: [("09:55-11:30", ["俱乐部体育1"]), ("14:00-15:35", ["写作与沟通"])],
    1: [("08:00-09:35", ["大学英语（1）"]), ("09:55-12:20", ["高等数学|（A）"])],
    3: [("08:00-09:35", ["大学英语（1）"]), ("15:55-18:20", ["思想道德与法治"])],
}


def month_page(shown=date(2026, 11, 1), courses=NOVEMBER, picker=True, misread=None):
    """Text blocks as Vision returns them for the month view (月历): day numbers ("11 01" on
    a month's first) over a lunar date, then each course's time and title; days of the
    months before and after show no courses."""
    first = shown.replace(day=1)
    start = first - timedelta(days=first.weekday())
    blocks = [
        block("学校门户-个人日程", 0.124, 0.010, 0.092),
        block("周历", 0.622, 0.124, 0.019),
        block("月历", 0.658, 0.124, 0.020),
        block("个人日程", 0.748, 0.184, 0.036),  # the legend on the right
        block("我的课程", 0.747, 0.248, 0.038),
    ]
    if picker:
        blocks.append(block(shown.isoformat(), 0.164, 0.124, 0.054, 0.012))
    for index in range(7):
        blocks.append(
            block(f"周{'一二三四五六日'[index]}", 0.147 + index * 0.087, 0.153, 0.017, 0.01)
        )
    for row in range(6):
        top = 0.174 + row * 0.13
        for index in range(7):
            day = start + timedelta(weeks=row, days=index)
            x = 0.131 + index * 0.087
            number = f"{day.month}{day.day:02d}" if day.day == 1 else f"{day.day:02d}"
            blocks.append(block(number, x + 0.016, top, 0.015, 0.011))
            blocks.append(block("廿四", x + 0.016, top + 0.013, 0.015, 0.009))
            if day.month != first.month:
                continue
            y = top + 0.03
            for span, lines in courses.get(index, []):
                if misread and day == misread[0] and lines == [misread[1]]:
                    lines = [misread[2]]
                for text in [span, *lines]:
                    blocks.append(block(text, x, y, 0.048, 0.01))
                    y += 0.011
                y += 0.011
    return blocks


def test_the_month_view_gives_the_weeks_wholly_inside_the_month():
    weeks = parse_month(month_page(misread=(date(2026, 11, 5), "思想道德与法治", "思根道德与法治")))
    # 10/26–11/1 and 11/30–12/6 cross the month's edge: the month view shows nothing on the
    # other month's days, so those weeks are left to the week view and the guessing.
    assert [w.monday for w in weeks] == [date(2026, 11, d) for d in (2, 9, 16, 23)]
    assert [len(w.entries) for w in weeks] == [6, 6, 6, 6]
    first = weeks[0].entries
    assert [(e.day.day, e.start, e.end, e.title, e.kind) for e in first[:3]] == [
        (2, "09:55", "11:30", "俱乐部体育1", "课程"),
        (2, "14:00", "15:35", "写作与沟通", "课程"),
        (3, "08:00", "09:35", "大学英语（1）", "课程"),
    ]
    # A one-character misread takes the spelling the other weeks show.
    assert {e.title for w in weeks for e in w.entries} == {
        "俱乐部体育1", "写作与沟通", "大学英语（1）", "高等数学I（A）", "思想道德与法治"
    }  # fmt: skip


def test_a_month_view_without_its_month_is_not_guessed():
    with pytest.raises(PageError, match="^timetable_month_unreadable$"):
        parse_month(month_page(picker=False))
    # The picker says October while the day numbers are November's: a misread, not October.
    wrong = [b for b in month_page() if b.text != "2026-11-01"]
    wrong.append(block("2026-10-01", 0.164, 0.124, 0.054, 0.012))
    with pytest.raises(PageError, match="^timetable_month_unreadable$"):
        parse_month(wrong)
    with pytest.raises(PageError, match="^timetable_not_week_view$"):
        parse_month(week_page())


def test_missing_month_row_cannot_shift_later_courses_into_an_earlier_week():
    # OCR lost the day numbers for 11/23–29, but retained that row's course text.
    top = 0.174 + 4 * 0.13
    blocks = [
        b
        for b in month_page()
        if not (abs(b.y - top) < 0.001 and page.DAY_NUMBER.fullmatch(page.compact(b.text)))
    ]
    with pytest.raises(PageError, match="^timetable_month_unreadable$"):
        parse_month(blocks)


def test_month_spelling_keeps_distinct_courses_and_corrects_the_same_slot():
    courses = {
        0: [("08:00-09:35", ["大学英语（1）"])],
        1: [("08:00-09:35", ["大学英语（1）"])],
        2: [("08:00-09:35", ["大学英语（2）"])],
        3: [("15:55-18:20", ["思想道德与法治"])],
    }
    weeks = parse_month(month_page(courses=courses))
    assert {e.title for w in weeks for e in w.entries} == {
        "大学英语（1）",
        "大学英语（2）",
        "思想道德与法治",
    }
    entries = (
        page.Entry(date(2026, 11, 2), "08:00", "09:35", "写作与交流", "课程"),
        page.Entry(date(2026, 11, 9), "08:00", "09:35", "写作与交流", "课程"),
        page.Entry(date(2026, 11, 3), "08:00", "09:35", "写作与交谈", "课程"),
    )
    assert page.settle_spelling(entries) == entries  # another weekday is a different course


@pytest.fixture
def mac(tmp_path):
    return Service(
        Settings(
            token="test-token-000000000000000000000000",
            data_dir=tmp_path / "state",
            scheduler_enabled=False,
            _env_file=None,
        ),
        platform="macos",
        apple_bridge=RecordingAppleBridge(tmp_path / "apple"),
    )


@pytest.fixture
def screen(monkeypatch):
    """What the fake helper sees: blocks of a page, or the helper's error code."""
    shown = {"blocks": week_page()}
    requests = []

    async def read_window(helper, out_dir, title):
        requests.append((helper, out_dir, title))
        if isinstance(shown["blocks"], str):
            raise RuntimeError(shown["blocks"])
        return None, shown["blocks"]

    monkeypatch.setattr(page, "read_window", read_window)
    shown["requests"] = requests
    return shown


async def started(service, tmp_path):
    await service.start()
    await service.plugins.patch(
        "timetable", PluginPatch(enabled=True, config={"helper": str(tmp_path / "helper")})
    )
    plugin = service.plugins.instances["timetable"]
    await plugin.reading
    plugin.now = lambda: NOW
    return plugin


async def test_a_read_fills_the_cards_the_day_file_and_the_tool(mac, screen, tmp_path):
    mcp = create_mcp(mac)  # before the plugin is enabled, which registers its tool
    plugin = await started(mac, tmp_path)
    try:
        assert screen["requests"][0] == (
            tmp_path / "helper",
            mac.settings.data_dir / "screens" / "timetable",
            "学校门户-个人日程",
        )
        await plugin.read()
        cards = await plugin.cards()
        assert [c["title"] for c in cards] == ["今天的课", "明天的课", "这周课表"]
        assert [i["title"] for i in cards[0]["data"]["items"]] == [
            "大学英语（1）",
            "高等数学I（A）",
        ]
        assert cards[0]["data"]["items"][0]["subtitle"] == "08:00–09:35"
        # A timeline; at 10:00 the first class is over and the second is on, tomorrow's not.
        assert (
            cards[0]["data"]["style"] == "timeline" and "state" not in cards[0]["data"]["items"][0]
        )
        plugin.now = lambda: NOW.replace(hour=10)
        later = await plugin.cards()
        assert [i.get("state") for i in later[0]["data"]["items"]] == ["done", "now"]
        assert "state" not in later[1]["data"]["items"][0]
        plugin.now = lambda: NOW
        assert "**周四** 15:55 思想道德与法治" in cards[2]["data"]["text"]
        assert "今天 07:30 读到这一周" in cards[2]["data"]["text"]
        # Today's classes go first on 作业Bot's page; the week is also a table, today's column
        # (Tuesday) marked, with no Saturday or Sunday when they have nothing.
        assert cards[0]["data"]["first"] is True and "first" not in cards[1]["data"]
        week = cards[2]["data"]
        assert week["table"] == {
            "columns": ["", "周一", "周二", "周三", "周四", "周五"],
            "rows": [
                ["08:00", "", "大学英语（1）", "大学生职业发展与就业指导（A）", "", ""],
                ["09:55", "俱乐部体育1", "高等数学I（A）", "", "", ""],
                ["15:55", "", "", "", "思想道德与法治", ""],
            ],
            "highlight": 2,
        }
        assert week["footnote"] == "今天 07:30 读到这一周。"

        part = await plugin.daily(date(2026, 10, 20))
        assert part["file"] == "课程.md" and part["bot"] == "作业Bot"
        assert part["summary"] == "2 节课：08:00 大学英语（1）、09:55 高等数学I（A）"
        assert part["body"] == "## 课程\n- 08:00–09:35 大学英语（1）\n- 09:55–12:20 高等数学I（A）"
        # Next week has not been shown yet: the same weekday of this week, marked as a guess.
        guess = await plugin.daily(date(2026, 10, 27))
        assert guess["summary"].endswith("（推测）") and "10月19日–25日 那周" in guess["body"]
        assert (await plugin.daily(date(2026, 10, 24)))["summary"] == "没有课"
        assert "还没有读到" in (await plugin.daily(date(2026, 10, 12)))["summary"]
        assert "还没有读到" in (await plugin.daily(date(2026, 12, 7)))["summary"]

        result = await call(mcp, "timetable_days", {"from": "2026-10-25", "days": 3})
        days = result["days"]
        assert [d["weekday"] for d in days] == ["周日", "周一", "周二"]
        assert days[0]["entries"] == [] and "estimated_from" not in days[0]
        assert days[1]["entries"][0]["title"] == "俱乐部体育1"
        assert days[1]["estimated_from"] == "2026-10-19"
        assert result["week_read"] == "2026-10-19" and result["problem"] is None
    finally:
        await mac.close()


async def test_the_daily_folder_has_the_courses_first(mac, screen, tmp_path):
    await started(mac, tmp_path)
    try:
        await mac.daily.write(date(2026, 10, 22))
        today = mac.memory.read("每日/2026-10-22/今天.md")["content"]
        assert "- 作业Bot · [课程](课程.md)：1 节课：15:55 思想道德与法治" in today
        assert today.index("[课程]") < today.index("消息来源")
        assert "思想道德与法治" in mac.memory.read("每日/2026-10-22/课程.md")["content"]
    finally:
        await mac.close()


async def test_a_new_read_replaces_its_week(mac, screen, tmp_path):
    plugin = await started(mac, tmp_path)
    try:
        screen["blocks"] = week_page(courses=COURSES[:1])
        await plugin.read()
        assert (await plugin.daily(date(2026, 10, 20)))["summary"] == "没有课"
        # Reading the next week keeps this one and ends the guessing.
        screen["blocks"] = week_page(monday=MONDAY + timedelta(weeks=1), courses=COURSES[3:4])
        await plugin.read()
        assert (await plugin.daily(date(2026, 10, 19)))["summary"] == "1 节课：09:55 俱乐部体育1"
        assert (await plugin.daily(date(2026, 10, 28)))[
            "summary"
        ] == "1 节课：08:00 大学生职业发展与就业指导（A）"
    finally:
        await mac.close()


async def test_same_class_start_with_different_ends_keeps_both_entries(mac, screen, tmp_path):
    screen["blocks"] = week_page(
        courses=[(1, "08:00-09:35", ["大学英语"]), (1, "08:00-11:30", ["大学英语"])]
    )
    week = parse_week(screen["blocks"], NOW.date())
    assert len(week.entries) == 2
    plugin = await started(mac, tmp_path)
    try:
        plugin.store_week(week, NOW)
        rows, _ = plugin.day_entries(NOW.date())
        assert {(r["start_time"], r["end_time"]) for r in rows} == {
            ("08:00", "09:35"),
            ("08:00", "11:30"),
        }
        plugin.store_week(week, NOW)
        assert len(plugin.day_entries(NOW.date())[0]) == 2
        assert len(plugin.calendar_entries()) == 2
        await plugin.sync_calendar()
        assert {item["end_at"] for item in calendar_items(mac).values()} == {
            "2026-10-20T09:35:00+08:00",
            "2026-10-20T11:30:00+08:00",
        }
        mac.apple.bridge.calls.clear()
        await plugin.sync_calendar()
        assert mac.apple.bridge.calls == []
    finally:
        await mac.close()


@pytest.mark.parametrize("view", ["week", "month"])
async def test_partial_course_ocr_keeps_saved_courses_and_calendar(mac, screen, tmp_path, view):
    plugin = await started(mac, tmp_path)
    try:
        screen["blocks"] = week_page() if view == "week" else month_page()
        if view == "month":
            plugin.now = lambda: NOW.replace(month=11, day=3)
        await plugin.read()
        saved = mac.db.rows("SELECT * FROM timetable_entries ORDER BY day,start_time,title")
        weeks = mac.db.rows("SELECT * FROM timetable_weeks ORDER BY monday")
        status = plugin.status()
        mac.apple.bridge.calls.clear()
        # One time range was read, but OCR missed its title. Do not treat that as a deletion.
        screen["blocks"] = [b for b in screen["blocks"] if b.text != "俱乐部体育1"]
        with pytest.raises(RuntimeError, match="^timetable_entries_unreadable$"):
            await plugin.read()
        assert mac.db.rows("SELECT * FROM timetable_entries ORDER BY day,start_time,title") == saved
        assert mac.db.rows("SELECT * FROM timetable_weeks ORDER BY monday") == weeks
        assert plugin.status()["read_at"] == status["read_at"]
        assert mac.apple.bridge.calls == []
    finally:
        await mac.close()


async def test_unknown_days_are_not_shown_as_no_classes(mac, screen, tmp_path):
    plugin = await started(mac, tmp_path)
    try:
        plugin.now = lambda: NOW + timedelta(weeks=5)
        cards = {c["title"]: c for c in await plugin.cards()}
        for title in ("今天的课", "明天的课"):
            assert "还没读到" in cards[title]["data"]["text"]
            assert "没有课" not in cards[title]["data"]["text"]
    finally:
        await mac.close()


async def test_a_read_of_the_month_view_saves_its_weeks_and_keeps_the_others(mac, screen, tmp_path):
    plugin = await started(mac, tmp_path)
    try:
        assert (await plugin.daily(date(2026, 10, 20)))["summary"].startswith("2 节课")
        screen["blocks"] = month_page()
        await plugin.read()
        assert plugin.status()["error"] is None and plugin.status()["week"] == "2026-11-23"
        # November's weeks are read, not guessed; the week read before stays as it was.
        assert (await plugin.daily(date(2026, 11, 5)))["summary"] == (
            "2 节课：08:00 大学英语（1）、15:55 思想道德与法治"
        )
        assert (await plugin.daily(date(2026, 10, 20)))["summary"].startswith("2 节课")
        # 11/30 starts a week that ends in December: guessed from 11/23.
        assert (await plugin.daily(date(2026, 11, 30)))["summary"].endswith("（推测）")
    finally:
        await mac.close()


async def test_a_page_left_on_last_week_says_to_turn_it(mac, screen, tmp_path):
    plugin = await started(mac, tmp_path)
    try:
        await plugin.read()
        assert "停在" not in str(await plugin.cards())
        plugin.now = lambda: NOW + timedelta(weeks=1)  # the page still shows 10/19–25
        cards = await plugin.cards()
        assert cards[0]["title"] == "课表页面停在旧的一周" and cards[0]["data"]["first"]
        assert "10月19日–25日 那周" in cards[0]["data"]["text"]
        assert cards[1]["title"].endswith("· 推测")
    finally:
        await mac.close()


async def test_a_page_that_is_not_open_is_shown_and_logged_once(mac, screen, tmp_path):
    screen["blocks"] = "screen_page_not_open"
    plugin = await started(mac, tmp_path)
    try:
        cards = await plugin.cards()
        assert cards[0]["title"] == "课表没读到" and "学校门户-个人日程" in cards[0]["data"]["text"]
        assert len(cards) == 1
        await plugin.read()  # the same problem again: no second failure
        screen["blocks"] = [block("Search...", 0.4, 0.12)]
        with pytest.raises(RuntimeError, match="^timetable_not_week_view$"):
            await plugin.read()
        screen["blocks"] = week_page()
        await plugin.read()
        assert [c["title"] for c in await plugin.cards()][0] == "今天的课"
        screen["blocks"] = "see /Users/someone/private"
        with pytest.raises(RuntimeError, match="^timetable_read_failed$"):
            await plugin.read()
        cards = await plugin.cards()  # what was read stays, under the problem
        assert cards[0]["title"] == "课表没读到" and cards[1]["title"] == "今天的课"
    finally:
        await mac.close()


async def test_setup_needs_the_helper_and_the_plugin_is_mac_only(mac, service):
    await mac.start()
    try:
        view = await mac.plugins.patch(
            "timetable", PluginPatch(enabled=True, config={"helper": "relative/helper"})
        )
        assert view["state"] == "error" and "timetable" not in mac.plugins.instances
    finally:
        await mac.close()
    await service.start()
    try:
        view = next(v for v in service.plugins.list() if v["id"] == "timetable")
        assert view["state"] == "unsupported" and view["permissions"] == ["screen_recording"]
    finally:
        await service.close()


async def test_the_watchdog_hears_of_a_page_unread_or_left_on_an_old_week(mac, screen, tmp_path):
    screen["blocks"] = "screen_page_not_open"
    plugin = await started(mac, tmp_path)
    try:
        [page_entry] = await plugin.watch()
        assert page_entry["title"] == "课表读不到了" and page_entry["ok_at"] is None
        assert "学校门户-个人日程" in page_entry["hint"] and page_entry["jobs"] == ["read"]
        screen["blocks"] = week_page()
        await plugin.read()
        [page_entry] = await plugin.watch()
        assert page_entry["ok_at"] == NOW.isoformat()
        plugin.now = lambda: NOW + timedelta(weeks=1)  # the page still shows 10/19–25
        page_entry, week_entry = await plugin.watch()
        assert week_entry["title"] == "课表页面停在旧的一周"
        assert week_entry["ok_at"] == "2026-10-26T00:00:00+08:00"
    finally:
        await mac.close()


async def test_a_month_already_read_needs_no_alerts_while_reads_fail(mac, screen, tmp_path):
    """As on 11/6: November read from the month view on 11/1, the page left on 10/26–11/1
    afterwards, and the display off at night. The classes are right, so nothing says otherwise."""
    plugin = await started(mac, tmp_path)
    try:
        plugin.now = lambda: datetime(2026, 11, 1, 15, 0, tzinfo=ZONE)
        screen["blocks"] = month_page()
        await plugin.read()
        friday = datetime(2026, 11, 6, 14, 30, tzinfo=ZONE)
        plugin.now = lambda: friday
        screen["blocks"] = week_page(monday=date(2026, 10, 26))
        await plugin.read()
        cards = await plugin.cards()
        assert "停在" not in str(cards)
        week = next(c for c in cards if c["title"] == "这周课表")
        assert week["data"]["footnote"] == "11月1日 15:00 读到这一周。"
        assert [e["key"] for e in await plugin.watch()] == ["page"]

        night = friday.replace(hour=23)
        plugin.now = lambda: night
        screen["blocks"] = "screen_locked"
        with pytest.raises(RuntimeError, match="^screen_locked$"):
            await plugin.read()
        cards = await plugin.cards()
        assert "课表没读到" not in [c["title"] for c in cards]
        [entry] = await plugin.watch()
        assert entry["ok_at"] == night.isoformat() and entry["jobs"] == ["read"]
        # A problem the user has to fix (the site logged out) is still told, last and calmly.
        screen["blocks"] = "screen_page_not_open"
        with pytest.raises(RuntimeError, match="^screen_page_not_open$"):
            await plugin.read()
        cards = await plugin.cards()
        assert cards[0]["title"] == "今天的课" and cards[-1]["title"] == "课表没读到"
        assert "不急" in cards[-1]["data"]["text"] and "first" not in cards[-1]["data"]

        # A week before the classes run out (11/30 starts a week the month did not give),
        # the failing page is an alert again.
        screen["blocks"] = "screen_locked"
        late = datetime(2026, 11, 24, 23, 0, tzinfo=ZONE)
        plugin.now = lambda: late
        with pytest.raises(RuntimeError, match="^screen_locked$"):
            await plugin.read()
        assert (await plugin.cards())[0]["title"] == "课表没读到"
        [entry] = await plugin.watch()
        assert entry["ok_at"] == friday.isoformat()
        assert "HDMI" in entry["hint"] and "需要密码" not in entry["hint"]
    finally:
        await mac.close()


async def alarm_started(mac, screen, tmp_path):
    # No classes until started() has installed the fixed clock; no real screen or EventKit.
    screen["blocks"] = "screen_page_not_open"
    plugin = await started(mac, tmp_path)
    screen["blocks"] = week_page(own=[(4, "19:00-20:00", ["社团例会"])])
    mac.apple.bridge.calls.clear()
    return plugin


def calendar_items(mac):
    return json.loads((mac.apple.bridge.directory / "calendar.json").read_text())


async def test_class_alarms_write_a_week_with_stable_ids_and_no_unchanged_writes(
    mac, screen, tmp_path
):
    plugin = await alarm_started(mac, screen, tmp_path)
    try:
        await plugin.read()
        calls = mac.apple.bridge.calls
        assert len(calls) == 5  # Tue–Mon: four read classes and one estimated Monday class
        for resource, action, payload in calls:
            assert resource == "calendar" and action == "upsert"
            assert str(UUID(payload["client_id"])) == payload["client_id"]
            assert payload["calendar"] == "AI 课表" and payload["alarms"] == [15]
            assert "社团例会" not in payload["title"]
        english = next(p for _, _, p in calls if p["title"] == "大学英语（1）")
        assert english["due_at"] == "2026-10-20T08:00:00+08:00"
        assert english["end_at"] == "2026-10-20T09:35:00+08:00"
        guess = next(p for _, _, p in calls if p["title"].endswith("（推测）"))
        assert guess["due_at"].startswith("2026-10-26") and "2026-10-19" in guess["notes"]
        assert all(r["event_id"] for r in mac.db.rows("SELECT * FROM timetable_calendar_events"))
        mac.apple.bridge.calls.clear()
        await plugin.read()
        assert mac.apple.bridge.calls == []
        plugin.config["class_alarm_minutes"] = 10
        await plugin.sync_calendar()
        assert len(mac.apple.bridge.calls) == 5
        assert all(p["alarms"] == [10] and p["id"] for _, _, p in mac.apple.bridge.calls)
        assert len(calendar_items(mac)) == 5
    finally:
        await mac.close()


async def test_class_end_time_updates_and_replaced_courses_delete_the_old_event(
    mac, screen, tmp_path
):
    plugin = await alarm_started(mac, screen, tmp_path)
    try:
        await plugin.read()
        mac.apple.bridge.calls.clear()
        courses = [*COURSES]
        courses[1] = (1, "08:00-10:00", ["大学英语", "(1)"])
        screen["blocks"] = week_page(courses=courses)
        await plugin.read()
        [(resource, action, updated)] = mac.apple.bridge.calls
        assert (resource, action) == ("calendar", "upsert")
        assert updated["id"] and updated["end_at"] == "2026-10-20T10:00:00+08:00"
        old_id = updated["id"]
        mac.apple.bridge.calls.clear()
        courses[1] = (1, "10:00-11:35", ["大学英语（改）"])
        screen["blocks"] = week_page(courses=courses)
        await plugin.read()
        calls = mac.apple.bridge.calls
        assert [a for _, a, _ in calls] == ["upsert", "delete"]
        assert calls[0][2]["client_id"] != updated["client_id"]
        assert calls[1][2]["id"] == old_id and calls[1][2]["calendar"] == "AI 课表"
        assert old_id not in [e["id"] for e in calendar_items(mac).values()]
        assert len(calendar_items(mac)) == 5
    finally:
        await mac.close()


async def test_a_guessed_class_becomes_read_in_the_same_event(mac, screen, tmp_path):
    plugin = await alarm_started(mac, screen, tmp_path)
    try:
        await plugin.read()
        guessed = next(e for e in calendar_items(mac).values() if e["title"].endswith("（推测）"))
        mac.apple.bridge.calls.clear()
        screen["blocks"] = week_page(monday=MONDAY + timedelta(weeks=1))
        await plugin.read()
        [(_, action, actual)] = mac.apple.bridge.calls
        assert action == "upsert" and actual["client_id"] == guessed["client_id"]
        assert actual["id"] == guessed["id"] and actual["title"] == "俱乐部体育1"
        assert "推测" not in actual["notes"] and len(calendar_items(mac)) == 5
    finally:
        await mac.close()


async def test_calendar_write_and_delete_retry_keep_the_receipts(
    mac, screen, tmp_path, monkeypatch
):
    plugin = await alarm_started(mac, screen, tmp_path)
    bridge = mac.apple.bridge
    call_bridge = bridge.call
    lost = None

    async def lose_reply(resource, action, payload):
        nonlocal lost
        result = await call_bridge(resource, action, payload)
        if lost is None:
            lost = payload["client_id"]
            raise RuntimeError("reply_lost")  # EventKit saved it; the core never got the ID
        return result

    monkeypatch.setattr(bridge, "call", lose_reply)
    try:
        with pytest.raises(RuntimeError, match="^timetable_calendar_failed$"):
            await plugin.read()
        pending = mac.db.rows("SELECT * FROM timetable_calendar_events WHERE client_id=?", (lost,))[
            0
        ]
        assert pending["event_id"] is None and pending["synced_json"] is None
        assert len(calendar_items(mac)) == 5
        bridge.calls.clear()
        monkeypatch.setattr(bridge, "call", call_bridge)
        await plugin.sync_calendar()
        assert len(bridge.calls) == 1 and bridge.calls[0][2]["client_id"] == lost
        assert len(calendar_items(mac)) == 5

        async def unavailable(*_args):
            raise RuntimeError("apple_unavailable")

        monkeypatch.setattr(bridge, "call", unavailable)
        screen["blocks"] = week_page(courses=())
        with pytest.raises(RuntimeError, match="^timetable_calendar_failed$"):
            await plugin.read()
        assert len(mac.db.rows("SELECT * FROM timetable_calendar_events")) == 5
        # Receipts survive a new DB connection, and the next attempt can remove every item.
        monkeypatch.setattr(bridge, "call", call_bridge)
        bridge.calls.clear()
        await plugin.sync_calendar()
        assert len(bridge.calls) == 5 and all(a == "delete" for _, a, _ in bridge.calls)
        assert (
            calendar_items(mac) == {}
            and mac.db.rows("SELECT * FROM timetable_calendar_events") == []
        )
    finally:
        await mac.close()


async def test_zero_class_alarm_writes_nothing_and_removes_previous_events(mac, screen, tmp_path):
    plugin = await alarm_started(mac, screen, tmp_path)
    try:
        config = {"helper": str(tmp_path / "helper"), "class_alarm_minutes": 0}
        await mac.plugins.patch("timetable", PluginPatch(config=config))
        plugin = mac.plugins.instances["timetable"]
        await plugin.reading
        assert mac.apple.bridge.calls == []
        plugin.now = lambda: NOW
        plugin.config["class_alarm_minutes"] = 15
        await plugin.read()
        assert len(calendar_items(mac)) == 5
        mac.apple.bridge.calls.clear()
        await mac.plugins.patch("timetable", PluginPatch(config=config))
        await mac.plugins.instances["timetable"].reading
        assert len(mac.apple.bridge.calls) == 5
        assert all(a == "delete" for _, a, _ in mac.apple.bridge.calls)
        assert calendar_items(mac) == {}
    finally:
        await mac.close()


async def test_the_calendar_window_moves_forward_even_when_the_page_is_closed(
    mac, screen, tmp_path
):
    plugin = await alarm_started(mac, screen, tmp_path)
    try:
        await plugin.read()
        mac.apple.bridge.calls.clear()
        plugin.now = lambda: NOW + timedelta(days=1)
        screen["blocks"] = "screen_page_not_open"
        with pytest.raises(RuntimeError, match="^screen_page_not_open$"):
            await plugin.read()
        calls = mac.apple.bridge.calls
        assert len(calls) == 4  # two Tuesday classes expire; next Tuesday's two guesses enter
        assert sum(a == "delete" for _, a, _ in calls) == 2
        assert all(
            p["due_at"].startswith("2026-10-27") and p["title"].endswith("（推测）")
            for _, a, p in calls
            if a == "upsert"
        )
        mac.apple.bridge.calls.clear()
        await plugin.read()
        assert mac.apple.bridge.calls == []
    finally:
        await mac.close()


async def test_uncertain_update_is_repaired_even_if_the_class_changes_back(
    mac, screen, tmp_path, monkeypatch
):
    plugin = await alarm_started(mac, screen, tmp_path)
    try:
        await plugin.read()
        bridge = mac.apple.bridge
        call_bridge = bridge.call

        async def lose_reply(resource, action, payload):
            await call_bridge(resource, action, payload)
            raise RuntimeError("reply_lost")

        monkeypatch.setattr(bridge, "call", lose_reply)
        changed = [*COURSES]
        changed[1] = (1, "08:00-10:00", ["大学英语", "(1)"])
        screen["blocks"] = week_page(courses=changed)
        with pytest.raises(RuntimeError, match="^timetable_calendar_failed$"):
            await plugin.read()
        english = next(e for e in calendar_items(mac).values() if e["title"] == "大学英语（1）")
        assert english["end_at"].endswith("10:00:00+08:00")
        monkeypatch.setattr(bridge, "call", call_bridge)
        bridge.calls.clear()
        screen["blocks"] = week_page()
        await plugin.read()
        [(_, action, repaired)] = bridge.calls
        assert action == "upsert" and repaired["id"] == english["id"]
        assert repaired["end_at"].endswith("09:35:00+08:00")
    finally:
        await mac.close()


async def test_cancelled_read_does_not_start_calendar_writes(mac, screen, tmp_path, monkeypatch):
    plugin = await alarm_started(mac, screen, tmp_path)
    try:
        await plugin.read()
        mac.apple.bridge.calls.clear()
        plugin.now = lambda: NOW + timedelta(days=1)

        async def cancelled(*_args):
            raise asyncio.CancelledError

        monkeypatch.setattr(page, "read_window", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await plugin.read()
        assert mac.apple.bridge.calls == []
    finally:
        await mac.close()


async def test_plugin_reload_keeps_the_same_calendar_receipts(mac, screen, tmp_path, monkeypatch):
    plugin = await alarm_started(mac, screen, tmp_path)
    try:
        await plugin.read()
        before = calendar_items(mac)
        mac.apple.bridge.calls.clear()
        ready = asyncio.Event()
        read_window = page.read_window

        async def waiting(*args):
            await ready.wait()
            return await read_window(*args)

        monkeypatch.setattr(page, "read_window", waiting)
        await mac.plugins.patch(
            "timetable", PluginPatch(config={"helper": str(tmp_path / "helper")})
        )
        reloaded = mac.plugins.instances["timetable"]
        reloaded.now = lambda: NOW
        ready.set()
        await reloaded.reading
        assert mac.apple.bridge.calls == [] and calendar_items(mac) == before
    finally:
        await mac.close()


async def test_events_without_a_returned_id_can_still_be_deleted(
    mac, screen, tmp_path, monkeypatch
):
    plugin = await alarm_started(mac, screen, tmp_path)
    bridge = mac.apple.bridge
    call_bridge = bridge.call

    async def lose_reply(resource, action, payload):
        await call_bridge(resource, action, payload)
        raise RuntimeError("reply_lost")

    monkeypatch.setattr(bridge, "call", lose_reply)
    try:
        with pytest.raises(RuntimeError, match="^timetable_calendar_failed$"):
            await plugin.read()
        assert all(
            r["event_id"] is None for r in mac.db.rows("SELECT * FROM timetable_calendar_events")
        )
        monkeypatch.setattr(bridge, "call", call_bridge)
        bridge.calls.clear()
        screen["blocks"] = week_page(courses=())
        await plugin.read()
        assert len(bridge.calls) == 5
        assert all(a == "delete" and p["id"] is None for _, a, p in bridge.calls)
        assert calendar_items(mac) == {}
    finally:
        await mac.close()


async def test_the_page_is_reloaded_between_reads_and_a_denial_is_told_once(
    mac, screen, tmp_path, monkeypatch
):
    monkeypatch.setattr("sys.platform", "darwin")  # Exercise Mac automation using a fake.
    calls, answer = [], {"value": 1}

    async def reload_tabs(title):
        calls.append(title)
        if isinstance(answer["value"], str):
            raise RuntimeError(answer["value"])
        return answer["value"]

    plugin = await started(mac, tmp_path)
    # The plugin manager loads its own copy of the module, under another name.
    monkeypatch.setitem(type(plugin).refresh.__globals__, "reload_tabs", reload_tabs)
    try:
        assert plugin.jobs()["refresh"]["cron"] == "5-59/10 * * * *"
        await plugin.refresh()
        assert calls == ["学校门户-个人日程"]
        assert plugin.context.get("refresh")["ok_at"] == NOW.isoformat()
        assert all(e["key"] != "refresh" for e in await plugin.watch())

        answer["value"] = "chrome_automation_denied"
        with pytest.raises(RuntimeError, match="^chrome_automation_denied$"):
            await plugin.refresh()
        await plugin.refresh()  # the same problem again: no second failure
        entry = next(e for e in await plugin.watch() if e["key"] == "refresh")
        assert entry["title"] == "课表页面没法自动刷新" and entry["ok_at"] == NOW.isoformat()
        assert "自动化" in entry["hint"] and entry["jobs"] == ["refresh"]

        answer["value"] = 0  # logged out: another title, nothing reloaded
        await plugin.refresh()
        state = plugin.context.get("refresh")
        assert (
            state["error"] is None and state["reloaded"] == 0 and state["ok_at"] == NOW.isoformat()
        )

        await mac.plugins.patch(
            "timetable",
            PluginPatch(
                enabled=True, config={"helper": str(tmp_path / "helper"), "keep_alive": False}
            ),
        )
        assert "refresh" not in mac.plugins.instances["timetable"].jobs()
    finally:
        await mac.close()


async def test_the_reload_script_names_the_tab_and_classifies_failures(monkeypatch):
    from plugins.timetable import plugin as module

    monkeypatch.setattr("sys.platform", "darwin")
    seen = {}

    async def run(argv, data, timeout, *, capture_error=False, env=None):
        seen["argv"] = argv
        return seen["result"]

    monkeypatch.setattr(module.helper_process, "run", run)
    seen["result"] = (0, b"2\n")
    assert await module.reload_tabs('课表 "页"') == 2
    assert seen["argv"][:2] == ["/usr/bin/osascript", "-e"] and seen["argv"][3] == '课表 "页"'
    seen["result"] = (1, b"execution error: Not authorized to send Apple events (-1743)")
    with pytest.raises(RuntimeError, match="^chrome_automation_denied$"):
        await module.reload_tabs("x")
    seen["result"] = (1, b"something else")
    with pytest.raises(RuntimeError, match="^chrome_refresh_failed$"):
        await module.reload_tabs("x")

    async def waiting(argv, data, timeout, *, capture_error=False, env=None):
        raise TimeoutError

    monkeypatch.setattr(module.helper_process, "run", waiting)
    with pytest.raises(RuntimeError, match="^chrome_refresh_timeout$"):
        await module.reload_tabs("x")


async def test_windows_keeps_page_reads_without_scheduling_mac_automation(monkeypatch):
    from plugins.timetable.plugin import Plugin, reload_tabs

    monkeypatch.setattr("sys.platform", "win32")
    plugin = Plugin()
    plugin.config = {"keep_alive": True}
    assert set(plugin.jobs()) == {"read"}
    with pytest.raises(RuntimeError, match="^chrome_refresh_requires_macos$"):
        await reload_tabs("课表")
