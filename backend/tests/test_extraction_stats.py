"""Accounting boundaries, using only synthetic messages and temporary SQLite state."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.db import uid
from core.extraction_stats import extraction_stats
from core.models import (
    InboxItem,
    MessageInput,
    PluginPatch,
    RuntimeSettings,
    TodoCreate,
    TodoNew,
    TodoPatch,
)
from plugins.weekly_review.inputs import Inputs

ZONE = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 12, 9, 14, tzinfo=ZONE)
MONDAY = NOW.date() - timedelta(days=NOW.weekday())


def task(service, created, due=None, *, plugin="qq_screen", creator="agent"):
    identifier = uid()
    service.store.ingest(
        plugin,
        [
            MessageInput(
                external_id=identifier,
                chat_name="合成测试群",
                sender="通知人",
                sent_at=created,
                text="准备讨论材料",
            )
        ],
        ["合成测试群"],
    )
    message = service.db.rows("SELECT id FROM messages WHERE external_id=?", (identifier,))[0]
    todo = service.store.add_todo(
        TodoCreate(
            title="材料-" + identifier,
            due_at=due,
            has_time=due is not None,
            source_plugin=plugin,
            source_refs=[message["id"]],
            source_excerpt="准备讨论材料",
            confidence=1,
            created_by=creator,
        )
    )["todo"]
    service.db.execute(
        "UPDATE todos SET created_at=? WHERE id=?", (created.isoformat(), todo["id"])
    )
    return todo


def test_caught_due_late_median_and_source_exclusions(service, monkeypatch):
    created = NOW - timedelta(hours=4)
    for lead in (None, -1, 0, 8, 32):
        task(service, created, created + timedelta(hours=lead) if lead is not None else None)
    task(service, created, plugin="share", creator="codex")
    task(service, created, plugin="feishu", creator="claude_code")
    task(service, created, plugin="mail", creator="deepseek")
    for plugin, creator in (
        ("watchdog", "watchdog"),
        ("goals", "goals"),
        ("test", "agent"),
        ("qq_screen", "user"),
        ("app", "user"),
    ):
        task(service, created, plugin=plugin, creator=creator)
    # A merged cross-source todo still counts once.
    row = service.db.rows("SELECT id FROM todos WHERE created_by='agent' LIMIT 1")[0]
    service.db.execute("UPDATE todos SET source_plugin='feishu,qq_screen' WHERE id=?", (row["id"],))
    monkeypatch.setattr("core.store.now", lambda: created.isoformat())
    service.store.new_todo(TodoNew(title="手动补上的", missed=True))
    service.store.inbox(InboxItem(text="另一条漏掉的", missed=True))
    result = extraction_stats(service.store, 1, now=NOW)["weeks"][0]
    assert result == {
        "week_start": MONDAY.isoformat(),
        # Five from QQ, one each from feishu and mail; the one the Agent only found in a message
        # the user forwarded (share) is not caught.
        "caught": 7,
        "with_due": 4,
        "late": 1,
        "median_lead_hours": 8,
        "missed": 2,
        "not_a_todo": 0,
        "recall": 7 / 9,
    }


def test_forwarded_only_is_not_caught_and_a_duplicate_is_not_missed(service, monkeypatch):
    created = NOW - timedelta(hours=4)
    forwarded = task(service, created, plugin="share")
    assert extraction_stats(service.store, 1, now=NOW)["weeks"][0]["caught"] == 0
    # Found in a chat as well: the assistant did read it by itself.
    service.db.execute(
        "UPDATE todos SET source_plugin='qq_screen,share' WHERE id=?", (forwarded["id"],)
    )
    assert extraction_stats(service.store, 1, now=NOW)["weeks"][0]["caught"] == 1
    # Adding by hand what is already there creates nothing, so nothing was missed.
    monkeypatch.setattr("core.store.now", lambda: created.isoformat())
    again = service.store.new_todo(TodoNew(title=forwarded["title"], missed=True))
    assert again["disposition"] == "merged" and again["todo"]["id"] == forwarded["id"]
    assert extraction_stats(service.store, 1, now=NOW)["weeks"][0]["missed"] == 0
    added = service.store.new_todo(TodoNew(title="真的漏掉的一条", missed=True))
    assert added["disposition"] == "added"
    week = extraction_stats(service.store, 1, now=NOW)["weeks"][0]
    assert week["missed"] == 1 and week["recall"] == 0.5


def test_even_median_and_all_late_or_no_deadline(service):
    created = NOW - timedelta(hours=4)
    task(service, created, created + timedelta(hours=2))
    task(service, created, created + timedelta(hours=13))
    assert extraction_stats(service.store, 1, now=NOW)["weeks"][0]["median_lead_hours"] == 7.5
    service.db.execute("UPDATE todos SET due_at=?", ((created - timedelta(seconds=1)).isoformat(),))
    week = extraction_stats(service.store, 1, now=NOW)["weeks"][0]
    assert week["late"] == 2 and week["median_lead_hours"] is None
    service.db.execute("UPDATE todos SET due_at=NULL,has_time=0")
    week = extraction_stats(service.store, 1, now=NOW)["weeks"][0]
    assert week["with_due"] == week["late"] == 0 and week["median_lead_hours"] is None


def test_date_only_accounting_compares_the_stored_deadline(service):
    created = NOW - timedelta(hours=1)
    todo = task(service, created, created.replace(hour=0))
    service.db.execute("UPDATE todos SET has_time=0 WHERE id=?", (todo["id"],))
    week = extraction_stats(service.store, 1, now=NOW)["weeks"][0]
    assert week["with_due"] == week["late"] == 1
    assert week["median_lead_hours"] is None


def test_weeks_use_creation_not_deadline_and_include_cutoff(service):
    start = datetime(2026, 12, 7, tzinfo=ZONE)
    task(service, start - timedelta(microseconds=1), NOW + timedelta(days=8))
    task(service, start, NOW - timedelta(days=10))
    task(service, NOW, NOW)
    task(service, NOW + timedelta(microseconds=1), NOW + timedelta(days=1))
    task(service, start - timedelta(days=7), None)
    task(service, start - timedelta(days=14, microseconds=1), None)
    weeks = extraction_stats(service.store, 3, now=NOW)["weeks"]
    assert [row["week_start"] for row in weeks] == ["2026-12-07", "2026-11-30", "2026-11-23"]
    assert [row["caught"] for row in weeks] == [2, 2, 0]
    assert weeks[0]["late"] == 1 and weeks[0]["median_lead_hours"] == 0
    assert weeks[1]["with_due"] == 1
    assert weeks[2]["recall"] is None
    # An explicit historical week uses the identical calculation without including later rows.
    past = extraction_stats(service.store, 1, now=NOW, monday=start.date() - timedelta(days=7))
    assert past["weeks"][0] == weeks[1]


@pytest.mark.parametrize(
    "zone,now,start",
    [
        ("Asia/Shanghai", "2026-12-08T12:00:00+08:00", "2026-12-06T16:00:00+00:00"),
        ("America/New_York", "2026-11-02T08:00:00-05:00", "2026-11-02T05:00:00+00:00"),
        ("America/New_York", "2026-03-09T08:00:00-04:00", "2026-03-09T04:00:00+00:00"),
    ],
)
def test_timezone_week_boundary_and_dst(service, zone, now, start):
    service.db.put("settings", RuntimeSettings(timezone=zone).model_dump())
    start = datetime.fromisoformat(start)
    task(service, start - timedelta(microseconds=1))
    task(service, start)
    weeks = extraction_stats(service.store, 2, now=datetime.fromisoformat(now))["weeks"]
    assert [row["caught"] for row in weeks] == [1, 1]
    assert weeks[0]["week_start"] == start.astimezone(ZoneInfo(zone)).date().isoformat()


def test_lead_hours_are_elapsed_hours_across_dst(service):
    service.db.put("settings", RuntimeSettings(timezone="America/New_York").model_dump())
    created = datetime.fromisoformat("2026-11-01T00:30:00-04:00")
    task(service, created, datetime.fromisoformat("2026-11-01T02:30:00-05:00"))
    week = extraction_stats(service.store, 1, now=datetime(2026, 11, 1, 12, tzinfo=UTC))["weeks"][0]
    assert week["median_lead_hours"] == 3


def test_missed_and_rejection_use_feedback_week_not_creation_week(service, monkeypatch):
    old = NOW - timedelta(weeks=1)
    todo = task(service, old)
    user = task(service, old, creator="user")
    monkeypatch.setattr("core.store.now", lambda: NOW.isoformat())
    service.store.update_todo(todo["id"], TodoPatch(status="cancelled", reason="not_a_todo"))
    service.store.update_todo(todo["id"], TodoPatch(status="pending"))
    service.store.update_todo(todo["id"], TodoPatch(status="deleted", reason="not_a_todo"))
    service.store.update_todo(user["id"], TodoPatch(status="deleted", reason="not_a_todo"))
    # Agent-originated changes never masquerade as user feedback.
    agent = task(service, old)
    service.store.update_todo(
        agent["id"], TodoPatch(status="deleted", reason="not_a_todo"), from_agent=True
    )
    service.store.new_todo(TodoNew(title="手动一", missed=True))
    service.store.new_todo(TodoNew(title="手动一", missed=True))
    service.store.new_todo(TodoNew(title="手动二", missed=False))
    service.store.inbox(InboxItem(text="遗漏的通知", missed=True))
    service.store.inbox(InboxItem(text="遗漏的通知", missed=True))
    service.store.inbox(InboxItem(text="普通通知"))
    weeks = extraction_stats(service.store, 2, now=NOW)["weeks"]
    assert weeks[0]["caught"] == 0 and weeks[0]["missed"] == 2
    assert weeks[0]["not_a_todo"] == 1 and weeks[0]["recall"] == 0
    assert weeks[1]["caught"] == 2 and weeks[1]["not_a_todo"] == 0
    assert weeks[1]["recall"] == 1


async def test_weekly_review_uses_same_stats_and_excludes_token_input(service, monkeypatch):
    await service.start()
    try:
        task(service, NOW - timedelta(hours=1), NOW + timedelta(hours=3))
        monkeypatch.setattr("core.store.now", lambda: NOW.isoformat())
        service.store.inbox(InboxItem(text="补上的消息", missed=True))
        await service.plugins.patch("weekly_review", PluginPatch(enabled=True))
        plugin = service.plugins.instances["weekly_review"]
        plugin.now = lambda: NOW
        data = await plugin.review_input({})
        assert data["extraction"] == extraction_stats(service.store, 1, now=NOW)["weeks"][0]
        assert data["extraction"]["caught"] == data["extraction"]["missed"] == 1
        assert "tokens" not in data
        past = MONDAY - timedelta(days=7)
        data = await Inputs(plugin.context, past, NOW).read()
        assert (
            data["extraction"]
            == extraction_stats(service.store, 1, now=NOW, monday=past)["weeks"][0]
        )
    finally:
        await service.close()


def test_future_events_in_the_repeated_dst_hour_do_not_count(service):
    service.db.put("settings", RuntimeSettings(timezone="America/New_York").model_dump())
    task(service, datetime.fromisoformat("2026-11-01T01:15:00-04:00"))
    task(service, datetime.fromisoformat("2026-11-01T01:15:00-05:00"))
    first_hour = datetime.fromisoformat("2026-11-01T01:30:00-04:00")
    assert extraction_stats(service.store, 1, now=first_hour)["weeks"][0]["caught"] == 1
