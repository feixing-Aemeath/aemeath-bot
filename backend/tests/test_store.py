from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from core.db import Database
from core.digests import NOTHING_NEW, brief, combine, latest_date
from core.models import HealthBatch, MessageInput, TodoPatch
from core.prompts import PROMPTS
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


def test_update_appends_validated_sources_across_plugins(seeded):
    original = seeded.store.add_todo(candidate(seeded))["todo"]
    other = seeded.store.unprocessed("feishu")[0]["id"]
    patch = TodoPatch(
        due_at="2026-10-15T17:00:00+08:00",
        notes="10月12日改期，原为 10月12日 18:00",
        source_refs=[other, other],
    )
    seeded.store.update_todo(original["id"], patch)
    updated = seeded.store.update_todo(original["id"], patch)
    assert updated["source_refs"] == [*original["source_refs"], other]
    assert updated["source_plugin"] == "feishu,qq_screen"
    assert [s["plugin"] for s in updated["sources"]] == ["qq_screen", "feishu"]
    assert updated["due_at"] == "2026-10-15T09:00:00+00:00"
    assert updated["has_time"] and updated["dedup_key"] != original["dedup_key"]
    assert seeded.store.todos()["total"] == 1


def test_update_sources_and_fields_roll_back_together(seeded):
    original = seeded.store.add_todo(candidate(seeded))["todo"]
    other = seeded.store.add_todo(candidate(seeded, title="其他任务"))["todo"]
    source = seeded.store.unprocessed("feishu")[0]["id"]
    with pytest.raises(ValueError, match="source_reference_missing"):
        seeded.store.update_todo(
            original["id"], TodoPatch(status="cancelled", source_refs=[source, "missing"])
        )
    with pytest.raises(ConflictError, match="duplicate_todo"):
        seeded.store.update_todo(
            original["id"], TodoPatch(title=other["title"], source_refs=[source])
        )
    assert seeded.store.todo(original["id"]) == original


def test_cancelled_todo_is_retained_and_hidden_by_default(seeded):
    original = seeded.store.add_todo(candidate(seeded))["todo"]
    reason = "10月12日取消，老师确认不用交表"
    updated = seeded.store.update_todo(original["id"], TodoPatch(status="cancelled", notes=reason))
    assert updated["status"] == "cancelled" and updated["notes"] == reason
    assert seeded.store.todos()["total"] == seeded.store.todos("pending")["total"] == 0
    assert seeded.store.todos("cancelled")["items"] == [updated]
    # Retrying an old extraction cannot resurrect the cancelled task.
    assert seeded.store.add_todo(candidate(seeded))["todo"]["status"] == "cancelled"


def test_same_title_with_different_deadline_is_not_automatically_merged(seeded):
    first = seeded.store.add_todo(candidate(seeded))["todo"]
    second = seeded.store.add_todo(candidate(seeded, due_at="2026-10-19T18:00:00+08:00"))["todo"]
    assert first["id"] != second["id"]
    assert seeded.store.todos()["total"] == 2


@pytest.mark.parametrize("status", ["pending", "cancelled"])
def test_old_extraction_retries_keep_the_rescheduled_task(seeded, status):
    data = candidate(seeded)
    original = seeded.store.add_todo(data)["todo"]
    updated = seeded.store.update_todo(
        original["id"],
        TodoPatch(due_at="2026-10-19T18:00:00+08:00", status=status),
    )
    retry = seeded.store.add_todo(data)
    assert retry["disposition"] == "merged"
    assert retry["todo"]["id"] == original["id"]
    assert retry["todo"]["due_at"] == updated["due_at"]
    assert retry["todo"]["status"] == status
    assert len(seeded.db.rows("SELECT id FROM todos")) == 1


def test_reposted_old_notice_does_not_revive_a_rescheduled_cancellation(seeded):
    data = candidate(seeded)
    original = seeded.store.add_todo(data)["todo"]
    seeded.store.update_todo(
        original["id"],
        TodoPatch(due_at="2026-10-19T18:00:00+08:00", status="cancelled"),
    )
    seeded.store.ingest("qq_screen", [message("repost", data.source_excerpt, 35)], ["学校"])
    ref = next(m["id"] for m in seeded.store.unprocessed() if m["sender"] == "李老师")
    # Match the original sender as well as the conversation and verbatim text.
    seeded.db.execute("UPDATE messages SET sender='老师' WHERE id=?", (ref,))
    retry = seeded.store.add_todo(data.model_copy(update={"source_refs": [ref]}))
    assert retry["todo"]["id"] == original["id"]
    assert retry["todo"]["status"] == "cancelled"
    assert ref in retry["todo"]["source_refs"]


def test_cancelled_key_does_not_swallow_a_new_undated_occurrence(seeded):
    data = candidate(seeded, due_at=None, has_time=False)
    original = seeded.store.add_todo(data)["todo"]
    seeded.store.update_todo(original["id"], TodoPatch(status="cancelled"))
    seeded.store.ingest(
        "qq_screen", [message("new-week", "本周请提交新一轮报名表，期限另行通知", 40)], ["学校"]
    )
    ref = next(m["id"] for m in seeded.store.unprocessed() if "新一轮" in m["text"])
    result = seeded.store.add_todo(
        data.model_copy(update={"source_refs": [ref], "source_excerpt": "本周请提交新一轮报名表"})
    )
    assert result["disposition"] == "added" and result["todo"]["status"] == "pending"
    assert result["todo"]["id"] != original["id"]
    # A stale extraction must still resolve to the cancelled row even after the new one exists.
    assert seeded.store.add_todo(data)["todo"]["id"] == original["id"]


def test_explicit_update_can_use_a_cancelled_key_but_restore_checks_collisions(seeded):
    data = candidate(seeded)
    original = seeded.store.add_todo(data)["todo"]
    seeded.store.update_todo(original["id"], TodoPatch(status="cancelled"))
    other = seeded.store.add_todo(candidate(seeded, "feishu", title="新的报名任务"))["todo"]
    updated = seeded.store.update_todo(other["id"], TodoPatch(title=data.title))
    assert updated["status"] == "pending" and updated["title"] == data.title
    before = seeded.store.todo(original["id"])
    with pytest.raises(ConflictError, match="duplicate_todo"):
        seeded.store.update_todo(original["id"], TodoPatch(status="pending"))
    assert seeded.store.todo(original["id"]) == before
    assert seeded.store.add_todo(data)["todo"]["id"] == original["id"]


def test_cancelled_history_can_be_deleted_after_its_key_is_reused(seeded):
    data = candidate(seeded)
    original = seeded.store.add_todo(data)["todo"]
    seeded.store.update_todo(original["id"], TodoPatch(status="cancelled"))
    other = seeded.store.add_todo(candidate(seeded, "feishu", title="新的报名任务"))["todo"]
    seeded.store.update_todo(other["id"], TodoPatch(title=data.title))
    for _ in range(2):
        deleted = seeded.store.update_todo(original["id"], TodoPatch(status="deleted"))
        assert deleted["status"] == "deleted"
    with pytest.raises(ConflictError, match="terminal"):
        seeded.store.update_todo(original["id"], TodoPatch(status="pending"))


def test_cancelled_key_migration_preserves_replays_and_is_idempotent(seeded):
    data = candidate(seeded)
    original = seeded.store.add_todo(data)["todo"]
    # Model the previous schema: cancelled rows still occupied their canonical key.
    seeded.db.execute("UPDATE todos SET status='cancelled' WHERE id=?", (original["id"],))
    seeded.db.execute("DROP TABLE todo_dedup_keys")
    Database(seeded.db.path)
    snapshot = seeded.store.todo(original["id"])
    assert snapshot["dedup_key"] != original["dedup_key"]
    assert seeded.store.add_todo(data)["todo"]["id"] == original["id"]
    Database(seeded.db.path)
    assert seeded.store.todo(original["id"])["dedup_key"] == snapshot["dedup_key"]
    assert len(seeded.db.rows("SELECT * FROM todo_dedup_keys")) == 1


def test_source_and_key_history_roll_back_on_validation_and_collision(seeded):
    original = seeded.store.add_todo(candidate(seeded, has_time=False, due_at=None))["todo"]
    other = seeded.store.add_todo(
        candidate(seeded, title="另一个任务", has_time=False, due_at=None)
    )["todo"]
    ref = seeded.store.unprocessed("feishu")[0]["id"]
    keys = seeded.db.rows("SELECT * FROM todo_dedup_keys ORDER BY todo_id,dedup_key")
    outbox = seeded.db.rows("SELECT * FROM outbox ORDER BY todo_id")
    for patch, error in [
        (TodoPatch(source_refs=[ref], has_time=True), ValueError),
        (TodoPatch(source_refs=[ref], title=other["title"]), ConflictError),
    ]:
        with pytest.raises(error):
            seeded.store.update_todo(original["id"], patch)
        assert seeded.store.todo(original["id"]) == original
        assert seeded.db.rows("SELECT * FROM todo_dedup_keys ORDER BY todo_id,dedup_key") == keys
        assert seeded.db.rows("SELECT * FROM outbox ORDER BY todo_id") == outbox


@pytest.mark.parametrize("refs", [None, [], ["m"] * 201])
def test_patch_rejects_invalid_source_lists(refs):
    with pytest.raises(ValueError):
        TodoPatch(source_refs=refs)


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


def test_sectioned_batches_merge_into_one_list_per_day(service):
    def write(text):
        return service.store.digest("2026-11-01", "qq_screen", text, append=True)["summary"]

    write(NOTHING_NEW)
    write("**通知**\n- 软件1-4班：周一排球课开始")
    write(NOTHING_NEW)
    write("**新待办**\n- 高数群：第3次作业，11月3日 23:59\n\n**通知**\n- 软件1-4班：周一排球课开始")
    summary = write("**通知**\n- 英语群：周五单词测试")
    assert summary == (
        "**新待办**\n- 高数群：第3次作业，11月3日 23:59\n\n"
        "**通知**\n- 软件1-4班：周一排球课开始\n- 英语群：周五单词测试"
    )
    # A batch in another form is kept as written, after the sections.
    assert write("预览看不全。").endswith("单词测试\n预览看不全。")


def test_a_day_with_nothing_new_says_so_once(service):
    service.store.digest("2026-11-01", "qq_screen", NOTHING_NEW, append=True)
    service.store.digest("2026-11-01", "qq_screen", NOTHING_NEW, append=True)
    assert service.store.digests()["items"][0]["summary"] == NOTHING_NEW
    assert combine(["**通知**\n- 甲", NOTHING_NEW, "**通知**\n- 乙"]) == "**通知**\n- 甲\n- 乙"


def test_the_extraction_prompt_writes_what_the_merge_expects():
    prompt = (PROMPTS / "extract_todos.md").read_text(encoding="utf-8")
    assert NOTHING_NEW in prompt
    assert "**新待办**" in prompt and "**通知**" in prompt and "**还不确定**" in prompt


def test_the_brief_leaves_out_what_has_gone_by_and_leads_with_two_todos():
    now = datetime(2026, 11, 14, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    said = "\n".join([
        "**新待办**",
        "- 软件1班：招聘会实践，10月30日 8:30",
        "- 软件1班：手册考试，11月15日 23:00",
        "- 班长：交照片",
        "- 高数群：第3次作业，11月3日 23:59",
        "- 软件1-4班：入学问卷，11月14日",  # today without a time: until the day ends
        "- 英语群：作文，11月20日",
        "- 体育群：体测，11月14日 9:00",  # earlier today
        "",
        "**通知**",
        "- 软件1班：11月6日19点黄梅戏演出",
        "- 软件1-4班：AI素养赛报名至11月15日",
        "- 实验课：报告改到 11/18，原为 11/10",  # one date still to come
        "- 学院：放假安排",
        "",
        "**还不确定**",
        "- 软件1班：心得11月13日23点截止，身份待确认",
    ])  # fmt: skip
    assert brief(said, now) == (
        "**新待办**\n"
        "- 软件1-4班：入学问卷，11月14日\n"
        "- 软件1班：手册考试，11月15日 23:00\n"
        "- 还有 2 件，在待办列表里\n\n"
        "**通知**\n"
        "- 软件1-4班：AI素养赛报名至11月15日\n"
        "- 实验课：报告改到 11/18，原为 11/10\n"
        "- 学院：放假安排"
    )
    # Two or fewer upcoming todos need no count; when everything has gone by, the brief says so.
    assert brief("**新待办**\n- 甲：交表，11月20日\n- 乙：开会", now) == (
        "**新待办**\n- 甲：交表，11月20日\n- 乙：开会"
    )
    assert brief("**通知**\n- 甲：10月1日放假", now) == NOTHING_NEW
    # Text in another form, and nothing at all, stay as they are.
    assert brief("昨天一共用了 **338M** tokens。", now) == "昨天一共用了 **338M** tokens。"
    assert brief(NOTHING_NEW, now) == NOTHING_NEW


def test_dates_without_a_year_are_taken_nearest_to_now():
    zone = ZoneInfo("Asia/Shanghai")
    new_year = datetime(2026, 12, 30, 12, 0, tzinfo=zone)
    assert latest_date("- 甲：交表，1月3日", new_year).year == 2027
    assert latest_date("- 甲：交表，12月29日", new_year).date().isoformat() == "2026-12-29"
    assert latest_date("- 甲：周五交表", new_year) is None
    assert latest_date("- 甲：2月30日", new_year) is None  # no such day


def test_digest_sections_keep_their_order_whichever_batch_wrote_first():
    merged = combine(["**通知**\n- 放假安排", "**还不确定**\n- 可能调课", "**新待办**\n- 交作业"])
    assert merged == "**新待办**\n- 交作业\n\n**通知**\n- 放假安排\n\n**还不确定**\n- 可能调课"
    # A section the prompt does not name stays after them, in the order it came.
    assert combine(["**别的**\n- 一", "**通知**\n- 二"]) == "**通知**\n- 二\n\n**别的**\n- 一"


def test_digest_merge_preserves_prefix_items_and_inline_headline_details(service):
    write = service.store.digest
    write("2026-11-01", "qq_screen", "**通知**\n- 提交报名表", append=True)
    result = write("2026-11-01", "qq_screen", "**通知**\n- 提交报名", append=True)
    assert result["summary"].splitlines() == ["**通知**", "- 提交报名表", "- 提交报名"]
    assert combine(["**睡眠** 昨晚没有记录", "**睡眠** 今天已补传"]) == (
        "**睡眠** 昨晚没有记录\n**睡眠** 今天已补传"
    )


def test_first_add_records_all_cited_plugins_and_repairs_old_rows(seeded):
    from core.db import dump

    refs = [message["id"] for message in seeded.store.unprocessed()]
    candidate_with_sources = candidate(seeded, plugin="feishu", source_refs=refs)
    todo = seeded.store.add_todo(candidate_with_sources)["todo"]
    assert todo["source_plugin"] == "feishu,qq_screen"
    assert todo["source_refs"] == refs
    # A v9 todo may have only the Agent's declared plugin, including on an already synced row.
    seeded.db.execute("UPDATE todos SET source_plugin='feishu',sync_status='synced'")
    seeded.db.execute("DELETE FROM outbox")
    seeded.db.execute("PRAGMA user_version=9")
    db = Database(seeded.db.path)
    assert db.rows("SELECT source_plugin,source_refs,sync_status FROM todos") == [
        {"source_plugin": "feishu,qq_screen", "source_refs": dump(refs), "sync_status": "pending"}
    ]
    queued = db.rows("SELECT * FROM outbox")
    assert len(queued) == 1 and queued[0]["fields"] == "[]"
    assert Database(db.path).rows("SELECT * FROM outbox") == queued
