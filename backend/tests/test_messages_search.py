from datetime import UTC, datetime, timedelta

import httpx
import pytest

from core import store
from core.api import create_app
from core.db import dump
from core.models import MessageInput
from core.prompts import render
from core.task_grants import extraction_grant
from core.tokens import task_grant
from tests.test_task_grants import invoke, names, payload

NOW = datetime(2026, 11, 5, 8, tzinfo=UTC)


@pytest.fixture
def messages(service, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(store, "datetime", Clock)

    def add(text="宿舍安全检查", *, plugin="qq_screen", processed=False, **changes):
        message = MessageInput(
            **{
                "external_id": str(service.db.rows("SELECT count(*) AS n FROM messages")[0]["n"]),
                "chat_name": "宿舍群",
                "sender": "老师",
                "sent_at": NOW - timedelta(days=3),
                "text": text,
                "screenshot_path": "private/screenshot.png",
                **changes,
            }
        )
        service.store.ingest(plugin, [message], [message.chat_name])
        identifier = service.db.rows(
            "SELECT id FROM messages WHERE plugin=? AND external_id=?",
            (plugin, message.external_id),
        )[0]["id"]
        if processed:
            service.store.mark_processed([identifier])
        return identifier

    return add


def test_messages_search_all_keywords_chat_and_processed_states(service, messages):
    old = messages("宿舍周五安全检查，请留一人开门", processed=True)
    new = messages("安全检查在周五进行", plugin="feishu")
    partial = messages("周五上课")
    elsewhere = messages("周五安全检查", chat_name="其他群")
    result = service.store.messages_search("  周五\t安全检查\n")
    assert {row["id"] for row in result} == {old, new, elsewhere}
    result = service.store.messages_search("周五 安全检查", chat_name="宿舍群")
    assert {row["id"] for row in result} == {old, new}
    assert service.store.messages_search("安全检查", chat_name="宿舍") == []
    assert {row["id"] for row in service.store.unprocessed()} == {new, elsewhere, partial}
    with pytest.raises(ValueError, match="chat_not_whitelisted"):
        service.store.ingest(
            "qq_screen",
            [
                MessageInput(
                    external_id="private",
                    chat_name="私密群",
                    sender="某人",
                    sent_at=NOW,
                    text="周五安全检查",
                )
            ],
            ["宿舍群"],
        )
    assert len(service.store.messages_search("安全检查")) == 3


def test_messages_search_window_inclusive_and_newest_first(service, messages):
    boundary = messages(sent_at=NOW - timedelta(days=14), flags=["self_listed"])
    recent = messages(sent_at=NOW)
    older = messages(sent_at=NOW - timedelta(days=14, microseconds=1))
    oldest = messages(sent_at=NOW - timedelta(days=90))
    messages(sent_at=NOW - timedelta(days=90, microseconds=1))
    messages(sent_at=NOW + timedelta(microseconds=1))
    assert [row["id"] for row in service.store.messages_search("检查")] == [recent, boundary]
    assert [row["id"] for row in service.store.messages_search("检查", days=1)] == [recent]
    assert [row["id"] for row in service.store.messages_search("检查", days=90)] == [
        recent,
        boundary,
        older,
        oldest,
    ]


def test_messages_search_default_limit_and_cap(service, messages):
    ids = [messages(sent_at=NOW - timedelta(minutes=i)) for i in range(201)]
    assert [row["id"] for row in service.store.messages_search("检查")] == ids[:20]
    assert [row["id"] for row in service.store.messages_search("检查", limit=1)] == ids[:1]
    assert [row["id"] for row in service.store.messages_search("检查", limit=200)] == ids[:200]


@pytest.mark.parametrize("literal", ["%", "_", "!", "\\", "' OR 1=1 --"])
def test_messages_search_escapes_like_and_binds_query(service, messages, literal):
    match = messages("检查备注：" + literal)
    messages("检查备注：普通文字")
    assert [row["id"] for row in service.store.messages_search("检查 " + literal)] == [match]


@pytest.mark.parametrize(
    "arguments",
    [{"query": ""}, {"query": " \n\t "}, {"days": 0}, {"days": 91}, {"limit": 0}, {"limit": 201}],
)
def test_messages_search_rejects_empty_query_and_out_of_range(service, arguments):
    with pytest.raises(ValueError, match="invalid"):
        service.store.messages_search(**{"query": "检查", **arguments})


def test_messages_search_reuses_redaction_flags_and_preview_fields(service, messages):
    service.db.execute(
        "INSERT INTO plugins(id,config_json) VALUES(?,?)",
        ("qq_screen", dump({"self_names": ["张三", "20260012"]})),
    )
    normal = messages(
        "检查未交名单：张 三、20260012",
        sender="张三",
        chat_name="张三宿舍群",
        flags=["self_listed"],
        outgoing=True,
    )
    messages("检查预览", preview=True, outgoing=True)
    revised = messages("检查完整内容", flags=["self_joined"])
    service.db.execute("UPDATE messages SET revised=1 WHERE id=?", (revised,))
    expected = {row["id"]: row for row in service.store.unprocessed()}
    service.store.mark_processed(list(expected))
    found = {row["id"]: row for row in service.store.messages_search("检查")}
    assert found == expected
    assert found[normal]["sender"] == "[本人]"
    assert found[normal]["chat_name"] == "[本人]宿舍群"
    assert found[normal]["text"] == "检查未交名单：[本人]、[本人]"
    assert found[normal]["flags"] == ["self_listed"] and found[normal]["outgoing"] is True
    assert all(
        set(row)
        == {
            "id",
            "plugin",
            "chat_name",
            "sender",
            "sent_at",
            "text",
            "priority",
            "flags",
            "outgoing",
            "preview",
            "revised",
            "note",
        }
        for row in found.values()
    )
    assert (
        service.db.rows("SELECT sender FROM messages WHERE id=?", (normal,))[0]["sender"] == "张三"
    )


@pytest.mark.parametrize("protocol", ["internal", "mcp"])
async def test_messages_search_extraction_can_cite_processed_context(service, messages, protocol):
    old = messages("11月6日下午2点到4点宿舍检查，需留一人开门", processed=True)
    new = messages("周五宿舍检查你帮忙开下门", chat_name="室友")
    app = create_app(service=service)
    async with app.router.lifespan_context(app):
        token = service.tokens.temporary(extraction_grant(NOW.date()), 30)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer " + token},
        ) as client:
            assert "messages_search" in await names(client, protocol)
            result = await invoke(client, protocol, "messages_search", {"query": "宿舍 检查"})
            assert not result["isError"]
            rows = payload(result)["result"]
            assert {row["id"] for row in rows} == {old, new}
            assert "screenshot_path" not in str(result)
            added = await invoke(
                client,
                protocol,
                "todos_add",
                {
                    "todo": {
                        "title": "宿舍检查开门",
                        "source_plugin": "qq_screen",
                        "source_refs": [old, new],
                        "source_excerpt": next(row["text"] for row in rows if row["id"] == old),
                        "due_at": "2026-11-06T14:00:00+08:00",
                        "has_time": True,
                        "confidence": 0.95,
                    }
                },
            )
            assert not added["isError"]
            todo = payload(added)["todo"]
            assert todo["source_refs"] == [old, new] and todo["has_time"]
            assert todo["due_at"] == "2026-11-06T06:00:00+00:00"
            invalid = await invoke(client, protocol, "messages_search", {"query": "%", "days": 91})
            assert invalid["isError"]


@pytest.mark.parametrize("protocol", ["internal", "mcp"])
async def test_messages_search_other_background_task_cannot_list_or_call(
    service, messages, protocol
):
    messages("检查私密合成文字", processed=True)
    app = create_app(service=service)
    async with app.router.lifespan_context(app):
        token = service.tokens.temporary(task_grant("new_background_task"), 30)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer " + token},
        ) as client:
            assert "messages_search" not in await names(client, protocol)
            result = await invoke(client, protocol, "messages_search", {"query": "检查"})
            assert result["isError"] and "tool_not_allowed" in str(result)
            assert "私密合成文字" not in str(result)


def test_extraction_prompt_searches_context_without_reextracting_history():
    prompt = render("extract_todos", local_now="NOW", timezone="TZ", confidence_threshold=0.8)
    assert "messages_search" in prompt
    assert "旧消息" in prompt and "不可信数据" in prompt
    assert "source_refs 只能用读到过的消息 ID" in prompt
    assert "source_refs 只能用本批读到的消息 ID" not in prompt


def test_messages_search_cannot_test_guesses_at_the_name_behind_the_mark(service, messages):
    service.db.execute(
        "INSERT INTO plugins(id,config_json) VALUES(?,?)",
        ("qq_screen", dump({"self_names": ["张三"]})),
    )
    match = messages("未交名单：张三，请补交", chat_name="张三宿舍群")
    assert [row["id"] for row in service.store.messages_search("未交名单")] == [match]
    assert service.store.messages_search("未交名单 张三") == []
    assert service.store.messages_search("未交名单", chat_name="张三宿舍群") == []
    found = service.store.messages_search("未交名单 [本人]", chat_name="[本人]宿舍群")
    assert [row["id"] for row in found] == [match]
    assert service.store.messages_search("未交名单", chat_name="没有这个群") == []
    assert [row["id"] for row in service.store.messages_search("[本人]，请")] == [match]


def test_search_applies_visible_text_and_chat_filters_before_limit(service, messages):
    service.db.execute(
        "INSERT INTO plugins(id,config_json) VALUES(?,?)",
        ("qq_screen", dump({"self_names": ["张三"]})),
    )
    expected = messages("请张三到场", chat_name="张三宿舍群")
    messages("普通通知", chat_name="张三宿舍群", sent_at=NOW)
    messages("报名通知", plugin="feishu", chat_name="张三宿舍群", sent_at=NOW)
    assert [r["id"] for r in service.store.messages_search("[本人]", limit=1)] == [expected]
    assert not service.store.messages_search("报名", chat_name="[本人]宿舍群")
    assert [r["id"] for r in service.store.messages_search("本人]到", limit=1)] == [expected]


def test_message_batch_reads_identity_and_share_metadata_once(service, messages, monkeypatch):
    from contextlib import contextmanager

    from core.models import InboxItem

    for i in range(12):
        service.store.inbox(InboxItem(text=f"合成分享 {i}", note=f"备注 {i}"))
        messages(f"通知 {i}")
    calls = 0
    connect = service.db.connect

    @contextmanager
    def counted():
        nonlocal calls
        calls += 1
        with connect() as conn:
            yield conn

    monkeypatch.setattr(service.db, "connect", counted)
    page = service.store.unprocessed(limit=100)
    assert len(page) == 24 and {r["note"] for r in page if r["plugin"] == "share"} == {
        f"备注 {i}" for i in range(12)
    }
    assert calls <= 4
