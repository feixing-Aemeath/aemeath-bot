"""App fallback entry points, entirely local with fake Apple sync and synthetic text."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from core.api import create_app
from core.db import Database, dump
from core.models import InboxItem, PluginPatch, TodoPatch
from core.source_views import unsure_items
from core.store import ConflictError
from harness.base import RunResult
from tests.conftest import candidate
from tests.test_screen import reader_for

ZONE = ZoneInfo("Asia/Shanghai")


@pytest.fixture
async def api(service):
    app = create_app(service=service)
    await service.start()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + service.settings.token.get_secret_value()},
    ) as client:
        yield client
    await service.close()


def block_job(service, name):
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()

    service.jobs.callbacks[name] = blocked
    return entered, release


async def screen(service, identifier="qq_screen"):
    service.plugins.platform = "macos"
    await service.plugins.patch(
        identifier,
        PluginPatch(
            enabled=True,
            config={
                "helper": str(Path("/fake/screen-helper").resolve()),
                "whitelist": ["实验室", "读书会"],
                "open_on_demand": ["实验室"],
                "preview_only": True,
            },
        ),
    )
    plugin = service.plugins.instances[identifier]
    plugin.reader, _ = reader_for(
        service, {"captured_at": datetime.now(ZONE).isoformat()}, plugin=identifier, **plugin.config
    )
    return plugin


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/v1/todos", {"title": "预约讨论"}),
        ("POST", "/api/v1/inbox", {"text": "请下周带练习册"}),
        ("GET", "/api/v1/todos/missing/sources", None),
        ("GET", "/api/v1/today", None),
        ("POST", "/api/v1/plugins/qq_screen/open-chat", {"chat_name": "实验室"}),
        ("PATCH", "/api/v1/todos/missing", {"status": "deleted", "reason": "not_a_todo"}),
        ("GET", "/api/v1/stats/extraction", None),
    ],
)
async def test_app_authentication(api, service, method, path, body):
    for authorization in ("", "Bearer invalid-test-credential"):
        result = await api.request(
            method, path, json=body, headers={"Authorization": authorization}
        )
        assert result.status_code == 401
    for scope in ("mcp", "dav", "health_upload"):
        token = service.tokens.create("scope-" + scope, [scope])[0]
        result = await api.request(
            method, path, json=body, headers={"Authorization": "Bearer " + token}
        )
        assert result.status_code == 403
        assert result.json() == {"detail": "insufficient_scope"}


async def test_manual_creation_sync_dedup_and_protection(api, service):
    token = service.tokens.create("app-device", ["app"])[0]
    body = {
        "title": "预约讨论",
        "notes": "先确认教室",
        "due_at": "2026-12-03T14:15:00+08:00",
        "has_time": True,
        "missed": True,
        "missed_source": {"plugin": "wechat_screen", "chat_name": "实验室"},
    }
    first = await api.post("/api/v1/todos", json=body, headers={"Authorization": "Bearer " + token})
    assert first.status_code == 201
    todo = first.json()
    assert (todo["created_by"], todo["source_plugin"], todo["sync_status"]) == (
        "user",
        "app",
        "synced",
    )
    assert todo["reminder_id"] and todo["calendar_event_id"]
    assert set(todo["user_modified_fields"]) == {"title", "due_at", "has_time"}
    second = await api.post("/api/v1/todos", json={**body, "title": "预约 讨论！"})
    assert second.status_code == 200 and second.json() == todo
    assert service.db.rows("SELECT count(*) n FROM extraction_missed")[0]["n"] == 1
    assert (
        json.loads(service.db.rows("SELECT source_json FROM extraction_missed")[0]["source_json"])
        == body["missed_source"]
    )
    assert service.store.unprocessed() == []
    assert len(service.db.rows("SELECT id FROM messages")) == 1
    for patch in (TodoPatch(title="被模型重写"), TodoPatch(due_at=None), TodoPatch(has_time=False)):
        with pytest.raises(ConflictError, match="todo_fields_user_modified"):
            await service.update_todo(todo["id"], patch, from_agent=True)
    # Both a date without a clock and no deadline use the same normal sync behavior.
    for extra in ({}, {"due_at": "2026-12-10T00:00:00+08:00"}):
        result = await api.post("/api/v1/todos", json={"title": "整理资料" + str(extra), **extra})
        assert result.status_code == 201
        assert result.json()["calendar_event_id"] is None
        assert result.json()["has_time"] is False


async def test_manual_can_match_agent_pending_and_recreate_closed(api, seeded):
    existing = seeded.store.add_todo(candidate(seeded))["todo"]
    body = {"title": existing["title"], "due_at": existing["due_at"], "has_time": True}
    result = await api.post("/api/v1/todos", json=body)
    assert result.status_code == 200 and result.json()["id"] == existing["id"]
    assert result.json()["created_by"] == "agent"
    await api.patch(f"/api/v1/todos/{existing['id']}", json={"status": "completed"})
    result = await api.post("/api/v1/todos", json=body)
    assert result.status_code == 201 and result.json()["id"] != existing["id"]
    # Old Agent retries retain their old occurrence and its completed state.
    replay = seeded.store.add_todo(candidate(seeded))["todo"]
    assert replay["id"] == existing["id"] and replay["status"] == "completed"


async def test_manual_sync_failure_is_durable_and_retry_is_idempotent(api, service, monkeypatch):
    call = service.apple.bridge.call

    async def unavailable(*_args):
        raise RuntimeError("apple_helper_failed")

    monkeypatch.setattr(service.apple.bridge, "call", unavailable)
    response = await api.post("/api/v1/todos", json={"title": "补做阅读记录"})
    assert response.status_code == 201
    todo = response.json()
    assert todo["sync_status"] == "pending"
    assert service.db.rows("SELECT todo_id FROM outbox")[0]["todo_id"] == todo["id"]
    monkeypatch.setattr(service.apple.bridge, "call", call)
    response = await api.post("/api/v1/todos", json={"title": "补做阅读记录"})
    assert response.status_code == 200 and response.json()["id"] == todo["id"]
    assert response.json()["sync_status"] == "synced"
    assert len(service.apple.bridge.items("reminders")) == 1


async def test_inbox_concurrent_retries_and_exact_ten_minute_boundary(api, service, monkeypatch):
    # Separate shares can arrive while another thread/process saves the same original text.
    data = InboxItem(text="查收下周日程", missed=True)
    identifiers = await asyncio.gather(
        *[asyncio.to_thread(service.store.inbox, data) for _ in range(6)]
    )
    assert len(set(identifiers)) == 1
    first_time = datetime.fromisoformat(
        service.db.rows("SELECT sent_at FROM messages")[0]["sent_at"]
    )
    monkeypatch.setattr("core.store.now", lambda: (first_time + timedelta(minutes=10)).isoformat())
    assert service.store.inbox(data) == identifiers[0]
    assert len(service.db.rows("SELECT * FROM extraction_missed")) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": " "},
        {"title": "x" * 501},
        {"title": "安排", "has_time": True},
        {"title": "安排", "due_at": "2026-12-01T12:00:00"},
        {"title": "安排", "created_by": "agent"},
        {"title": "安排", "missed_source": {"plugin": "qq_screen"}},
    ],
)
async def test_manual_validation(api, body):
    assert (await api.post("/api/v1/todos", json=body)).status_code == 422


async def test_inbox_preserves_text_metadata_idempotency_and_busy_job(api, service, monkeypatch):
    entered, release = block_job(service, "extract_todos")
    body = {
        "text": "请准备讨论材料",
        "note": "我需要参加吗？",
        "source_app": "微信",
        "chat_name": "实验室",
        "missed": True,
    }
    first = await api.post("/api/v1/inbox", json=body)
    assert first.status_code == 202 and first.json()["job_started"] is True
    await entered.wait()
    again = await api.post("/api/v1/inbox", json={**body, "note": "重复分享"})
    assert again.json() == {"message_id": first.json()["message_id"], "job_started": False}
    row = service.store.unprocessed("share")[0]
    assert row["text"] == body["text"] and row["note"] == body["note"]
    assert row["sender"] == "我" and row["source_app"] == "微信"
    assert service.db.rows("SELECT count(*) n FROM extraction_missed")[0]["n"] == 1
    another = await api.post("/api/v1/inbox", json={"text": "自带水杯"})
    assert another.json()["job_started"] is False
    rows = service.store.unprocessed("share")
    assert rows[1]["chat_name"] == "转给助手"
    assert "share" not in {p["id"] for p in (await api.get("/api/v1/plugins")).json()}
    first_time = datetime.fromisoformat(row["sent_at"])
    monkeypatch.setattr(
        "core.store.now", lambda: (first_time + timedelta(minutes=10, microseconds=1)).isoformat()
    )
    later = await api.post("/api/v1/inbox", json=body)
    assert later.json()["message_id"] != row["id"]
    release.set()
    await service.jobs.tasks["extract_todos"]
    assert len(service.db.rows("SELECT * FROM extraction_missed")) == 2


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": ""},
        {"text": "x" * 8001},
        {"text": "x", "note": "x" * 501},
        {"text": "x", "source_app": "x" * 41},
        {"text": "x", "chat_name": "x" * 81},
        {"text": "x", "unexpected": True},
    ],
)
async def test_inbox_validation(api, body):
    assert (await api.post("/api/v1/inbox", json=body)).status_code == 422


async def test_share_extraction_mcp_digest_daily_and_watchdog(api, service):
    # The fake harness performs the same MCP writes a model must make, under its task grant.
    app = api._transport.app
    mcp = app.state.mcp

    async def call(name, arguments):
        result = await mcp.call_tool(name, arguments)
        if isinstance(result, tuple):
            structured = result[1]
            return structured["result"] if set(structured) == {"result"} else structured
        return json.loads(result[0].text)

    class Agent:
        async def run(self, prompt, mcp_config, timeout):
            assert 'plugin="share"' in prompt
            messages = await call("messages_get_unprocessed", {"plugin": "share"})
            assert len(messages) == 1
            row = messages[0]
            added = await call(
                "todos_add",
                {
                    "todo": {
                        "title": "准备讨论材料",
                        "source_plugin": "share",
                        "source_refs": [row["id"]],
                        "source_excerpt": row["text"],
                        "confidence": 0.95,
                    }
                },
            )
            assert added["disposition"] == "added"
            day = service.daily.today().isoformat()
            await call(
                "digest_write",
                {"date": day, "plugin": "share", "summary": "**新待办**\n- 实验室：准备讨论材料"},
            )
            await call("messages_mark_processed", {"ids": [row["id"]]})
            return RunResult(True)

    service.jobs.harnesses = {"claude_code": Agent()}
    response = await api.post("/api/v1/inbox", json={"text": "准备讨论材料", "chat_name": "实验室"})
    assert response.status_code == 202
    await service.jobs.tasks["extract_todos"]
    assert service.store.unprocessed("share") == []
    assert service.store.todos()["items"][0]["source_plugin"] == "share"
    assert service.store.runs()["items"][0]["status"] == "succeeded"
    today = (await api.get("/api/v1/today")).json()
    assert "准备讨论材料" in today["summary"] and today["unsure"] == []
    day = service.daily.today().isoformat()
    assert "转给助手" in service.memory.read(f"每日/{day}/消息.md")["content"]
    assert "转给助手" in service.memory.read(f"每日/{day}/今天.md")["content"]
    assert service.watchdog.job_entry("fetch:share", "job_failed", None)["title"].startswith(
        "转给助手"
    )
    await service.watchdog.check()


async def test_sources_preserve_order_truncate_and_allow_missing(api, seeded):
    todo = seeded.store.add_todo(candidate(seeded))["todo"]
    refs = [r["id"] for r in seeded.store.unprocessed()]
    seeded.db.execute(
        "UPDATE messages SET text=?,screenshot_path='private/screenshot.png' WHERE id=?",
        ("中" * 4100, refs[0]),
    )
    seeded.db.execute(
        "UPDATE todos SET source_refs=? WHERE id=?", (dump(refs[::-1] + ["gone"]), todo["id"])
    )
    response = await api.get(f"/api/v1/todos/{todo['id']}/sources")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [r["message_id"] for r in items] == refs[::-1]
    assert len(items[1]["text"]) == 4000
    assert all(
        set(r) == {"message_id", "plugin", "chat_name", "sender", "sent_at", "text"} for r in items
    )
    seeded.db.execute("DELETE FROM messages")
    assert (await api.get(f"/api/v1/todos/{todo['id']}/sources")).json() == {"items": []}
    assert (await api.get("/api/v1/todos/missing/sources")).status_code == 404


async def test_open_chat_errors_budget_idempotency_and_running(api, service):
    path = "/api/v1/plugins/qq_screen/open-chat"
    body = {"chat_name": "实验室"}
    for identifier in ("missing", "weekly_review", "qq_screen", "share"):
        assert (
            await api.post(f"/api/v1/plugins/{identifier}/open-chat", json=body)
        ).status_code == 404
    plugin = await screen(service)
    for name, code in (
        ("不在名单", "screen_open_not_whitelisted"),
        ("读书会", "screen_open_not_allowed"),
    ):
        result = await api.post(path, json={"chat_name": name})
        assert result.status_code == 403 and result.json() == {"detail": code}
    entered, release = block_job(service, "fetch:qq_screen")
    result = await api.post(path, json=body)
    assert result.status_code == 202 and result.json() == {"accepted": True, "job_started": True}
    await entered.wait()
    pending = plugin.context.get("screen_open_requests")
    result = await api.post(path, json=body)
    assert result.json() == {"accepted": True, "job_started": False}
    assert plugin.context.get("screen_open_requests") == pending
    plugin.context.put(
        "screen_opens:实验室", {"day": plugin.reader._now().date().isoformat(), "count": 4}
    )
    result = await api.post(path, json=body)
    assert result.status_code == 409 and result.json() == {"detail": "screen_open_daily_limit"}
    assert (await api.post(path, json={})).status_code == 422
    release.set()
    await service.jobs.tasks["fetch:qq_screen"]
    await service.plugins.patch("qq_screen", PluginPatch(enabled=False))
    assert (await api.post(path, json=body)).status_code == 404


async def test_today_unsure_order_stable_ids_and_daily_budget(api, service):
    plugin = await screen(service)
    day = service.daily.today().isoformat()
    service.store.digest(
        day,
        "qq_screen",
        "**还不确定**\n- 实验室：准备材料，预览\n- 读书会：时间不明\n- 未知群：看不到全文\n\n**通知**\n- 不应计入",
    )
    service.store.digest(day, "feishu", "**通知**\n- 已放假\n\n**还不确定**\n- 临时安排：时间不明")
    service.store.digest(day, "share", "**还不确定**\n- 实验室：准备材料，预览\n- 另一条：需要核实")
    service.store.digest(
        (service.daily.today() - timedelta(days=1)).isoformat(),
        "qq_screen",
        "**还不确定**\n- 实验室：旧线索",
    )
    today = (await api.get("/api/v1/today")).json()
    items = today["unsure"]
    # Digests are ordered by plugin, and section/item order stays intact within each digest.
    assert [r["text"] for r in items] == [
        "临时安排：时间不明",
        "实验室：准备材料，预览",
        "读书会：时间不明",
        "未知群：看不到全文",
        "实验室：准备材料，预览",
        "另一条：需要核实",
    ]
    assert items[1]["plugin"] == "qq_screen" and items[1]["chat_name"] == "实验室"
    assert items[1]["id"] == items[4]["id"]
    assert items[1]["can_open"] is True and items[2]["can_open"] is False
    assert items[0]["plugin"] is items[0]["chat_name"] is None
    assert (await api.get("/api/v1/today")).json()["unsure"] == items
    plugin.context.put("screen_opens:实验室", {"day": day, "count": 4})
    exhausted = (await api.get("/api/v1/today")).json()["unsure"]
    assert exhausted[1] == {**items[1], "can_open": False}
    plugin.context.put("screen_opens:实验室", {"day": "2001-01-01", "count": 4})
    assert (await api.get("/api/v1/today")).json()["unsure"][1]["can_open"] is True


async def test_unsure_old_digests_ambiguous_names_and_disabled_plugin(api, service):
    await screen(service, "qq_screen")
    await screen(service, "wechat_screen")
    today = service.daily.today()
    digest = {"plugin": "share", "summary": "**还不确定**\n- 实验室：尚未明确"}
    row = unsure_items(service, today, [digest])[0]
    assert row["plugin"] is None and row["chat_name"] is None and row["can_open"] is False
    own = unsure_items(service, today, [{**digest, "plugin": "wechat_screen"}])[0]
    assert own["plugin"] == "wechat_screen" and own["can_open"] is True
    assert unsure_items(service, today, [{"plugin": "share", "summary": "旧版普通摘要。"}]) == []
    tomorrow = unsure_items(service, today + timedelta(days=1), [digest])[0]
    assert tomorrow["id"] != row["id"]
    await service.plugins.patch("wechat_screen", PluginPatch(enabled=False))
    disabled = unsure_items(service, today, [{**digest, "plugin": "wechat_screen"}])[0]
    assert disabled["plugin"] == "wechat_screen" and disabled["can_open"] is False


@pytest.mark.parametrize("status", ["deleted", "cancelled"])
@pytest.mark.parametrize("reason", ["not_a_todo", "done_elsewhere", "duplicate", "wrong_time"])
async def test_reasons_are_recorded_only_with_status_changes(api, seeded, status, reason):
    todo = seeded.store.add_todo(candidate(seeded))["todo"]
    path = f"/api/v1/todos/{todo['id']}"
    assert (await api.patch(path, json={"notes": "记录", "reason": reason})).status_code == 200
    assert seeded.db.rows("SELECT * FROM todo_changes") == []
    assert (await api.patch(path, json={"status": status, "reason": reason})).status_code == 200
    assert seeded.db.rows("SELECT * FROM todo_changes")[0]["reason"] == reason
    assert (await api.patch(path, json={"status": status, "reason": reason})).status_code == 200
    assert len(seeded.db.rows("SELECT * FROM todo_changes")) == 1


async def test_reason_validation_and_old_patch_behavior(api, seeded):
    todo = seeded.store.add_todo(candidate(seeded))["todo"]
    path = f"/api/v1/todos/{todo['id']}"
    assert (await api.patch(path, json={"reason": "invalid"})).status_code == 422
    # A reason by itself changes nothing and is not an error.
    alone = await api.patch(path, json={"reason": "duplicate"})
    assert alone.status_code == 200 and alone.json()["status"] == "pending"
    assert seeded.db.rows("SELECT * FROM todo_changes") == []
    assert (
        await api.patch(path, json={"status": "completed", "reason": "not_a_todo"})
    ).status_code == 200
    assert seeded.db.rows("SELECT reason FROM todo_changes")[0]["reason"] is None
    assert (await api.patch(path, json={"status": "deleted"})).status_code == 200
    assert (await api.patch(path, json={"title": "终态"})).status_code == 409
    assert (
        await api.patch("/api/v1/todos/missing", json={"reason": "not_a_todo"})
    ).status_code == 404


async def test_stats_endpoint_weeks_validation_and_empty_old_data(api):
    default = await api.get("/api/v1/stats/extraction")
    assert default.status_code == 200 and len(default.json()["weeks"]) == 4
    assert all(
        w["recall"] is None and w["median_lead_hours"] is None for w in default.json()["weeks"]
    )
    for value in ("0", "27", "x", "1.5"):
        assert (await api.get("/api/v1/stats/extraction?weeks=" + value)).status_code == 422
    assert len((await api.get("/api/v1/stats/extraction?weeks=26")).json()["weeks"]) == 26


def test_upgrade_preserves_old_messages_todos_and_changes(seeded):
    todo = seeded.store.add_todo(candidate(seeded))["todo"]
    seeded.store.update_todo(todo["id"], TodoPatch(status="completed"))
    with seeded.db.connect() as conn:
        conn.execute("ALTER TABLE todo_changes DROP COLUMN reason")
        conn.execute("DROP TABLE inbox_submissions")
        conn.execute("DROP TABLE extraction_missed")
    before = seeded.store.todo(todo["id"])
    for _ in range(2):
        db = Database(seeded.db.path)
        assert db.rows("SELECT * FROM todo_changes")[0]["reason"] is None
        assert seeded.store.todo(todo["id"]) == before
        assert len(seeded.store.todo_source_texts(todo["id"])["items"]) == 1
    assert len(seeded.store.unprocessed()) == 2


async def test_manual_creation_uses_current_key_after_rename(api):
    old = (await api.post("/api/v1/todos", json={"title": "领取资料"})).json()
    await api.patch(f"/api/v1/todos/{old['id']}", json={"title": "领取另一份资料"})
    created = await api.post("/api/v1/todos", json={"title": "领取资料"})
    assert created.status_code == 201
    assert created.json()["id"] != old["id"] and created.json()["title"] == "领取资料"
    retry = await api.post("/api/v1/todos", json={"title": "领取资料"})
    assert retry.status_code == 200 and retry.json()["id"] == created.json()["id"]


async def test_cancelled_todo_requires_explicit_reopen_before_completion(api, seeded):
    original = (await seeded.add_todo(candidate(seeded)))["todo"]
    path = f"/api/v1/todos/{original['id']}"
    assert (await api.patch(path, json={"status": "cancelled"})).status_code == 200
    cancelled = seeded.store.todo(original["id"])
    invalid = await api.patch(path, json={"status": "completed", "notes": "意外恢复"})
    assert invalid.status_code == 409
    assert seeded.store.todo(original["id"]) == cancelled
    assert not seeded.apple.bridge.items("calendar")


async def test_completed_todo_keeps_retired_key_when_edited(api, service):
    body = {"title": "每周交表"}
    original = (await api.post("/api/v1/todos", json=body)).json()
    path = f"/api/v1/todos/{original['id']}"
    assert (await api.patch(path, json={"status": "completed"})).status_code == 200
    current = (await api.post("/api/v1/todos", json=body)).json()
    assert current["id"] != original["id"]
    edited = await api.patch(path, json={"notes": "上周已交"})
    assert edited.status_code == 200 and edited.json()["notes"] == "上周已交"
    assert (await api.patch(path, json={"status": "pending"})).status_code == 409
    assert service.store.todo(current["id"])["status"] == "pending"
