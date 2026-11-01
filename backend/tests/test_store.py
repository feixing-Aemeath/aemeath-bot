from datetime import UTC, datetime

import pytest

from core.models import HealthBatch, MessageInput, TodoPatch
from core.store import ConflictError
from tests.conftest import candidate


async def test_cross_source_dedup_and_apple_lifecycle(seeded):
    first = await seeded.add_todo(candidate(seeded))
    second = await seeded.add_todo(candidate(seeded, "feishu", title=" 提交 报名表！ "))
    assert second["disposition"] == "merged"
    todo = second["todo"]
    assert todo["id"] == first["todo"]["id"]
    assert len(todo["source_refs"]) == 2
    assert set(todo["source_plugin"].split(",")) == {"feishu", "qq_screen"}
    assert todo["sync_status"] == "synced" and todo["calendar_event_id"]
    completed = await seeded.update_todo(todo["id"], TodoPatch(status="completed"))
    assert completed["status"] == "completed"
    changed = await seeded.update_todo(todo["id"], TodoPatch(due_at=None))
    assert changed["calendar_event_id"] is None and changed["has_time"] is False
    deleted = await seeded.update_todo(todo["id"], TodoPatch(status="deleted"))
    assert deleted["reminder_id"] is None
    assert seeded.store.todos()["total"] == 0
    with pytest.raises(ConflictError):
        seeded.store.update_todo(todo["id"], TodoPatch(status="pending"))


async def test_low_confidence_only_digest_and_provenance(seeded):
    result = await seeded.add_todo(candidate(seeded, confidence=0.3))
    assert result["disposition"] == "below_threshold"
    assert seeded.store.todos()["total"] == 0
    assert seeded.store.digests()["total"] == 1
    with pytest.raises(ValueError, match="source_reference"):
        seeded.store.add_todo(candidate(seeded, source_refs=["nonexistent"]))
    with pytest.raises(ValueError, match="excerpt"):
        seeded.store.add_todo(candidate(seeded, source_excerpt="invented text"))
    with pytest.raises(ValueError, match="plugin_mismatch"):
        seeded.store.add_todo(candidate(seeded, source_plugin="mail"))


def test_ingest_atomic_whitelist_and_incremental(seeded):
    messages = seeded.store.unprocessed()
    assert all("screenshot_path" not in message for message in messages)
    model = MessageInput(
        external_id="other",
        chat_name="不允许",
        sender="x",
        sent_at=datetime.now(UTC),
        text="secret",
    )
    with pytest.raises(ValueError, match="whitelist"):
        seeded.store.ingest("qq_screen", [model], ["学校"])
    assert len(seeded.store.unprocessed()) == 2
    ids = [m["id"] for m in messages]
    assert seeded.store.mark_processed(ids) == {"updated": 2}
    assert seeded.store.mark_processed(ids) == {"updated": 0}
    with pytest.raises(KeyError):
        seeded.store.mark_processed(["missing"])


def test_digest_append_idempotent_and_health_idempotent(service):
    service.store.digest("2026-10-11", "feishu", "第一批", append=True)
    service.store.digest("2026-10-11", "feishu", "第二批", append=True)
    service.store.digest("2026-10-11", "feishu", "第二批", append=True)
    assert service.store.digests()["items"][0]["summary"] == "第一批\n第二批"
    batch = HealthBatch.model_validate(
        {
            "samples": [
                {
                    "id": "a",
                    "type": "steps",
                    "value": 30,
                    "unit": "count",
                    "source": "phone",
                    "start_at": "2026-10-11T01:00:00Z",
                    "end_at": "2026-10-11T02:00:00Z",
                }
            ]
        }
    )
    assert service.store.health(batch) == {"accepted": 1, "duplicates": 0}
    assert service.store.health(batch) == {"accepted": 0, "duplicates": 1}


def test_update_collision_rolls_back(seeded):
    a = seeded.store.add_todo(candidate(seeded))["todo"]
    b = seeded.store.add_todo(candidate(seeded, title="其他任务"))["todo"]
    with pytest.raises(ConflictError):
        seeded.store.update_todo(b["id"], TodoPatch(title=a["title"]))
    assert seeded.store.todo(b["id"])["title"] == "其他任务"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": " "},
        {"has_time": True, "due_at": None},
        {"confidence": float("nan")},
        {"due_at": "2026-10-12T18:00:00"},
    ],
)
def test_bad_todo_values(seeded, kwargs):
    with pytest.raises(ValueError):
        candidate(seeded, **kwargs)


def message(external_id, text, minute, preview=False, sender="李老师"):
    return MessageInput(
        external_id=external_id,
        chat_name="学校",
        sender=sender,
        sent_at=datetime(2026, 10, 11, 12, minute, tzinfo=UTC),
        text=text,
        preview=preview,
    )


def test_preview_matching_rules(service):
    store = service.store
    previews = [
        message("p1", "李老师：周五前交实验报告，格式见…", 30, preview=True),
        message("p2", "李老师：明天的课改到…", 30, preview=True),
        message("p3", "收到…", 50, preview=True, sender="王同学"),
    ]
    assert store.ingest("qq_screen", previews, ["学校"]) == 3
    # Same start of text, within the window: replaces p1 (not yet processed → not revised).
    full = message("f1", "周五前交实验报告，格式见群文件《实验报告模板》", 26)
    # Different text at the same time: a separate message, p2 stays a preview.
    other = message("f2", "明天下午没有课", 29)
    # Matching text but outside the window (a full read dates by the separator above it).
    late = message("f3", "收到", 59, sender="王同学")
    assert store.ingest("qq_screen", [full, other, late], ["学校"]) == 3
    rows = {row["text"]: row for row in store.unprocessed()}
    assert set(rows) == {
        full.text,
        other.text,
        "李老师：明天的课改到…",
        "收到…",
        "收到",
    }
    assert not rows[full.text]["preview"] and not rows[full.text]["revised"]
    assert rows["李老师：明天的课改到…"]["preview"]
    assert rows["收到…"]["preview"] and not rows["收到"]["preview"]
    # A preview read after the full message adds nothing; a one-character preview never
    # matches anything.
    again = message("p4", "李老师：周五前交实验报告…", 31, preview=True)
    tiny = message("p5", "好…", 31, preview=True)
    assert store.ingest("qq_screen", [again, tiny], ["学校"]) == 1
    assert store.ingest("qq_screen", [message("f4", "好的", 31)], ["学校"]) == 1
    assert len(service.db.rows("SELECT id FROM messages WHERE preview=1")) == 3


def test_old_databases_gain_the_preview_columns(tmp_path):
    import sqlite3

    from core.db import Database

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, plugin TEXT NOT NULL, external_id TEXT "
            "NOT NULL, chat_name TEXT NOT NULL, sender TEXT NOT NULL, sent_at TEXT NOT NULL, "
            "text TEXT NOT NULL, screenshot_path TEXT, priority INTEGER NOT NULL DEFAULT 0, "
            "processed INTEGER NOT NULL DEFAULT 0, UNIQUE(plugin,external_id))"
        )
        conn.execute(
            "INSERT INTO messages VALUES('m1','qq_screen','e1','学校','老师',"
            "'2026-10-11T01:00:00+00:00','请交表',NULL,0,0)"
        )
    db = Database(path)
    assert db.rows("SELECT preview,revised,flags,outgoing FROM messages") == [
        {"preview": 0, "revised": 0, "flags": "[]", "outgoing": 0}
    ]
    Database(path)  # opening again is a no-op


async def test_todos_name_the_chat_and_sender_of_each_source(seeded):
    first = await seeded.add_todo(candidate(seeded))
    merged = await seeded.add_todo(candidate(seeded, "feishu"))
    todo = merged["todo"]
    assert [(s["plugin"], s["chat_name"], s["sender"]) for s in todo["sources"]] == [
        ("qq_screen", "学校", "老师"),
        ("feishu", "学校", "老师"),
    ]
    assert [s["message_id"] for s in todo["sources"]] == todo["source_refs"]
    assert first["todo"]["sources"][0]["sent_at"] == "2026-10-11T01:00:00+00:00"
    listed = seeded.store.todos()["items"][0]
    assert listed["sources"] == todo["sources"]
