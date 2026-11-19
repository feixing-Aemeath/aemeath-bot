import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.apple import AppleSync, FakeAppleBridge, SwiftAppleBridge
from core.db import Database
from core.models import TodoPatch
from tests.conftest import RecordingAppleBridge, candidate

MOMENT = datetime(2026, 11, 1, 10, tzinfo=UTC)


async def synced(seeded, tmp_path):
    bridge = RecordingAppleBridge(tmp_path / "apple")
    seeded.apple = AppleSync(seeded.db, bridge)
    todo = (await seeded.add_todo(candidate(seeded)))["todo"]
    bridge.calls.clear()
    return bridge, todo


async def test_complete_reopen_and_restart_without_reminder_writes(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], completed=True)
    result = await seeded.apple.reconcile(MOMENT)
    assert result == {"checked": 1, "changed": 1, "failed": 0, "mass_missing": 0}
    completed = seeded.store.todo(todo["id"])
    assert completed["status"] == "completed"
    assert completed["status_source"] == "reminders"
    assert completed["reminder_completed_at"]
    assert seeded.store.todos("pending")["total"] == 0
    assert seeded.db.rows("SELECT reminders FROM outbox") == [{"reminders": 0}]
    await AppleSync(seeded.db, bridge).flush()
    assert [(r, a) for r, a, _ in bridge.calls] == [("reminders", "read"), ("calendar", "upsert")]
    assert seeded.store.todo(todo["id"])["sync_status"] == "synced"
    bridge.calls.clear()
    assert (await seeded.apple.reconcile(MOMENT))["changed"] == 0
    await seeded.apple.flush()
    assert [(r, a) for r, a, _ in bridge.calls] == [("reminders", "read")]

    bridge.change_reminder(todo["reminder_id"], completed=False)
    await AppleSync(seeded.db, bridge).reconcile(MOMENT + timedelta(minutes=5))
    await seeded.apple.flush()
    reopened = seeded.store.todo(todo["id"])
    assert reopened["status"] == "pending" and reopened["reminder_completed_at"] is None
    assert reopened["status_source"] == "reminders"
    await seeded.update_todo(todo["id"], TodoPatch(status="completed"))
    assert seeded.store.todo(todo["id"])["status_source"] is None


async def test_batch_includes_completed_but_excludes_deleted_and_app_only(seeded, tmp_path):
    bridge, first = await synced(seeded, tmp_path)
    second = (await seeded.add_todo(candidate(seeded, title="另一份报名表")))["todo"]
    await seeded.update_todo(second["id"], TodoPatch(status="completed"))
    deleted = (await seeded.add_todo(candidate(seeded, title="已删除")))["todo"]
    await seeded.update_todo(deleted["id"], TodoPatch(status="deleted"))
    seeded.db.execute("UPDATE todos SET reminder_id='deleted-id' WHERE id=?", (deleted["id"],))
    app_only = (await seeded.add_todo(candidate(seeded, title="仅 App 显示")))["todo"]
    seeded.db.execute("UPDATE todos SET source_plugin='watchdog' WHERE id=?", (app_only["id"],))
    bridge.calls.clear()
    await seeded.apple.reconcile(MOMENT)
    assert len(bridge.calls) == 1
    assert set(bridge.calls[0][2]["reminder_ids"]) == {first["reminder_id"], second["reminder_id"]}


async def test_two_misses_hold_writes_then_delete_calendar_without_resurrection(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.delete_reminder(todo["reminder_id"])
    seeded.store.update_todo(todo["id"], TodoPatch(notes="尚未写出的更新"))
    assert (await seeded.apple.reconcile(MOMENT))["changed"] == 0
    assert (await seeded.apple.flush())["attempted"] == 0
    assert seeded.store.todo(todo["id"])["status"] == "pending"
    # A job retry seconds later is still the first poll.
    await seeded.apple.reconcile(MOMENT + timedelta(seconds=1))
    assert seeded.store.todo(todo["id"])["status"] == "pending"
    await AppleSync(seeded.db, bridge).reconcile(MOMENT + timedelta(minutes=5))
    assert seeded.store.todo(todo["id"])["status"] == "deleted"
    await seeded.apple.flush()
    row = seeded.store.todo(todo["id"])
    assert row["reminder_id"] is None and row["calendar_event_id"] is None
    assert row["status_source"] == "reminders" and row["sync_status"] == "synced"
    assert not seeded.db.rows("SELECT * FROM outbox")
    assert not bridge.items("reminders") and not bridge.items("calendar")
    assert all(a == "read" for r, a, _ in bridge.calls if r == "reminders")
    # Re-extracting the same notification must not revive a deleted todo either.
    await seeded.add_todo(candidate(seeded))
    assert not bridge.items("reminders") and not bridge.items("calendar")


@pytest.mark.parametrize(
    "failure",
    [
        "denied",
        "no_lists",
        "exception",
        "incomplete",
        "malformed",
        "timeout",
        "wrong_boolean",
        "duplicate",
        "unknown",
        "empty_scope",
        "contradictory",
    ],
)
async def test_bad_reads_change_no_todos_and_break_missing_streak(seeded, tmp_path, failure):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.delete_reminder(todo["reminder_id"])
    await seeded.apple.reconcile(MOMENT)
    before = seeded.store.todo(todo["id"])

    class BadRead:
        async def call(self, resource, action, payload):
            if failure == "exception":
                raise RuntimeError("private provider detail")
            if failure == "timeout":
                raise TimeoutError()
            if failure == "incomplete":
                return {"list_ids": ["fake-ai-todos"], "items": []}
            if failure == "malformed":
                return {
                    "list_ids": ["fake-ai-todos"],
                    "items": [{"id": todo["reminder_id"], "exists": False}],
                }
            bridge.reminders_authorized = failure != "denied"
            bridge.reminder_lists = set() if failure == "no_lists" else {"fake-ai-todos"}
            result = await bridge.call(resource, action, payload)
            if failure == "wrong_boolean":
                result["items"][0]["exists"] = "false"
            elif failure == "duplicate":
                result["items"].append(result["items"][0].copy())
            elif failure == "unknown":
                result["items"][0]["id"] = "unknown-id"
            elif failure == "empty_scope":
                result["list_ids"] = []
            elif failure == "contradictory":
                result["items"][0]["completed"] = True
            return result

    with pytest.raises(RuntimeError, match="^apple_sync_failed$"):
        await AppleSync(seeded.db, BadRead()).reconcile(MOMENT + timedelta(minutes=5))
    assert seeded.store.todo(todo["id"]) == before
    assert (await seeded.apple.flush())["attempted"] == 0
    bridge.reminders_authorized = True
    bridge.reminder_lists = {"fake-ai-todos"}
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=10))
    assert seeded.store.todo(todo["id"])["status"] == "pending"
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=15))
    assert seeded.store.todo(todo["id"])["status"] == "deleted"


async def test_reappearing_item_resets_the_streak(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    original = bridge.items("reminders")
    bridge.delete_reminder(todo["reminder_id"])
    await seeded.apple.reconcile(MOMENT)
    bridge.save("reminders", original)
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=5))
    bridge.delete_reminder(todo["reminder_id"])
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=10))
    assert seeded.store.todo(todo["id"])["status"] == "pending"
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=15))
    assert seeded.store.todo(todo["id"])["status"] == "deleted"


async def test_disappeared_account_list_is_not_a_deletion(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    await seeded.apple.reconcile(MOMENT)
    bridge.reminder_lists = {"some-other-account"}
    for minutes in (5, 10, 15):
        await seeded.apple.reconcile(MOMENT + timedelta(minutes=minutes))
    assert seeded.store.todo(todo["id"])["status"] == "pending"
    bridge.reminder_lists.add("fake-ai-todos")
    bridge.change_reminder(todo["reminder_id"], completed=True)
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=20))
    assert seeded.store.todo(todo["id"])["status"] == "completed"


@pytest.mark.parametrize("remote_deleted", [False, True])
async def test_calendar_failure_retries_only_calendar_after_readback(
    seeded, tmp_path, remote_deleted
):
    bridge, todo = await synced(seeded, tmp_path)
    if remote_deleted:
        bridge.delete_reminder(todo["reminder_id"])
        await seeded.apple.reconcile(MOMENT)
    else:
        bridge.change_reminder(todo["reminder_id"], completed=True)
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=5))

    class FailCalendar:
        async def call(self, resource, action, payload):
            if resource == "calendar":
                raise RuntimeError("failure")
            return await bridge.call(resource, action, payload)

    assert (await AppleSync(seeded.db, FailCalendar()).flush())["failed"] == 1
    row = seeded.store.todo(todo["id"])
    assert row["status"] == ("deleted" if remote_deleted else "completed")
    assert row["sync_error"] == "apple_sync_failed"
    bridge.calls.clear()
    assert (await AppleSync(seeded.db, bridge).flush())["failed"] == 0
    assert [r for r, _, _ in bridge.calls] == ["calendar"]


async def test_user_completes_between_reminder_success_and_calendar_retry(seeded, tmp_path):
    bridge = RecordingAppleBridge(tmp_path / "apple")

    class FailCalendar:
        async def call(self, resource, action, payload):
            if resource == "calendar":
                raise RuntimeError("failure")
            return await bridge.call(resource, action, payload)

    seeded.apple = AppleSync(seeded.db, FailCalendar())
    todo = (await seeded.add_todo(candidate(seeded)))["todo"]
    bridge.change_reminder(todo["reminder_id"], completed=True)
    seeded.apple = AppleSync(seeded.db, bridge)
    bridge.calls.clear()
    await seeded.jobs.sync()
    assert seeded.store.todo(todo["id"])["status"] == "completed"
    assert [(r, a) for r, a, _ in bridge.calls] == [("reminders", "read"), ("calendar", "upsert")]


async def test_local_patch_during_read_wins(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], completed=True)

    class ConcurrentEdit:
        async def call(self, resource, action, payload):
            seeded.store.update_todo(todo["id"], TodoPatch(notes="读取期间改过"))
            return await bridge.call(resource, action, payload)

    await AppleSync(seeded.db, ConcurrentEdit()).reconcile(MOMENT)
    row = seeded.store.todo(todo["id"])
    assert row["status"] == "pending" and row["notes"] == "读取期间改过"
    assert seeded.db.rows("SELECT reminders FROM outbox") == [{"reminders": 1}]


async def test_unsent_app_status_is_not_undone_by_old_remote_state(seeded, tmp_path):
    _, todo = await synced(seeded, tmp_path)
    seeded.store.update_todo(todo["id"], TodoPatch(status="completed"))
    await seeded.apple.reconcile(MOMENT)
    assert seeded.store.todo(todo["id"])["status"] == "completed"
    await seeded.apple.flush()
    assert (await seeded.apple.reconcile(MOMENT))["changed"] == 0


async def test_fake_reads_are_durable_and_do_not_write(tmp_path):
    bridge = FakeAppleBridge(tmp_path)
    result = await bridge.call("reminders", "upsert", {"client_id": "stable", "title": "任务"})
    bridge.change_reminder(result["id"], completed=True)
    path = tmp_path / "reminders.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    read = await FakeAppleBridge(tmp_path).call(
        "reminders", "read", {"reminder_ids": [result["id"], "absent"]}
    )
    assert read["items"][0]["completed"] is True and read["items"][0]["completed_at"]
    assert read["items"][1]["exists"] is False
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_readback_migration_preserves_existing_outbox(seeded):
    todo = seeded.store.add_todo(candidate(seeded))["todo"]
    with seeded.db.connect() as conn:
        for column in ("status_source", "reminder_completed_at", "user_modified_fields"):
            conn.execute(f"ALTER TABLE todos DROP COLUMN {column}")
        conn.execute("ALTER TABLE outbox DROP COLUMN reminders")
        conn.execute("ALTER TABLE outbox DROP COLUMN fields")
        conn.execute("ALTER TABLE outbox DROP COLUMN agent_base")
        conn.execute("DROP TABLE apple_reminder_state")
        conn.execute("PRAGMA user_version=5")
    db = Database(seeded.db.path)
    assert db.rows("SELECT * FROM outbox") == [
        {
            "todo_id": todo["id"],
            "revision": 1,
            "reminders": 1,
            "fields": '["title","due_at","has_time","status"]',
            "agent_base": "{}",
        }
    ]
    assert db.rows("SELECT status_source,reminder_completed_at FROM todos") == [
        {"status_source": None, "reminder_completed_at": None}
    ]
    assert db.rows("SELECT user_modified_fields FROM todos") == [{"user_modified_fields": "[]"}]
    assert db.rows("PRAGMA user_version")[0]["user_version"] == 10
    Database(seeded.db.path)


@pytest.mark.parametrize("platform", ["linux", "win32"])
async def test_real_bridge_needs_mac(monkeypatch, platform):
    monkeypatch.setattr("core.apple.sys.platform", platform)
    with pytest.raises(RuntimeError, match="macos_required"):
        await SwiftAppleBridge(Path("never-executed")).call(
            "reminders", "read", {"reminder_ids": ["id"]}
        )


@pytest.mark.parametrize(
    "code",
    [
        "apple_reminders_denied",
        "apple_reminders_list_unavailable",
        "apple_reminders_read_failed",
        "private provider details",
    ],
)
async def test_bridge_keeps_only_known_error_codes(monkeypatch, code):
    monkeypatch.setattr("core.apple.sys.platform", "darwin")

    async def fake_process(argv, data, timeout, **kwargs):
        assert argv == ["never-executed", "reminders", "read"]
        assert json.loads(data) == {"reminder_ids": ["id"]}
        assert kwargs["capture_error"] is True
        return 1, code.encode()

    monkeypatch.setattr("core.apple.helper_process.run", fake_process)
    expected = code if code.startswith("apple_reminders_") else "apple_helper_failed"
    with pytest.raises(RuntimeError, match=f"^{expected}$"):
        await SwiftAppleBridge(Path("never-executed")).call(
            "reminders", "read", {"reminder_ids": ["id"]}
        )


async def test_http_today_and_list_reflect_completion_and_reopening(seeded, tmp_path):
    import httpx

    from core.api import create_app

    bridge, todo = await synced(seeded, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service=seeded)),
        base_url="http://testserver",
        headers={"Authorization": "Bearer " + seeded.settings.token.get_secret_value()},
    ) as client:
        bridge.change_reminder(todo["reminder_id"], completed=True)
        await seeded.apple.reconcile(MOMENT)
        assert (await client.get("/api/v1/todos?status=pending")).json()["total"] == 0
        assert (await client.get("/api/v1/today")).json()["todos"] == []
        completed = (await client.get("/api/v1/todos?status=completed")).json()["items"][0]
        assert completed["status_source"] == "reminders" and completed["reminder_completed_at"]
        bridge.change_reminder(todo["reminder_id"], completed=False)
        await seeded.apple.reconcile(MOMENT + timedelta(minutes=5))
        assert (await client.get("/api/v1/today")).json()["todos"][0]["id"] == todo["id"]


async def test_read_failures_use_one_watchdog_alert_and_recover(seeded, tmp_path):
    bridge, _ = await synced(seeded, tmp_path)
    seeded.db.put("settings", {"retry_attempts": 1})
    seeded.jobs.configure()
    bridge.reminders_authorized = False
    moment = datetime.now(UTC)
    await seeded.jobs._run("apple_sync", seeded.jobs.sync)
    assert seeded.store.runs()["items"][0]["error"] == "apple_sync_failed"
    await seeded.watchdog.check(moment)
    await seeded.watchdog.check(moment + timedelta(hours=3))
    alerts = seeded.db.rows("SELECT id,status FROM todos WHERE source_plugin='watchdog'")
    assert len(alerts) == 1 and alerts[0]["status"] == "pending"
    await seeded.jobs._run("apple_sync", seeded.jobs.sync)
    await seeded.watchdog.check(moment + timedelta(hours=3, minutes=5))
    assert len(seeded.db.rows("SELECT id FROM todos WHERE source_plugin='watchdog'")) == 1
    bridge.reminders_authorized = True
    await seeded.jobs._run("apple_sync", seeded.jobs.sync)
    await seeded.watchdog.check(moment + timedelta(hours=3, minutes=10))
    assert seeded.store.todo(alerts[0]["id"])["status"] == "completed"


@pytest.mark.parametrize(
    ("changes", "locked"),
    [
        ({"title": "我改过的报名任务"}, ["title"]),
        ({"due_at": "2026-11-05T20:00:00+08:00"}, ["due_at", "has_time"]),
        ({"due_at": "2026-11-05T00:00:00+08:00", "has_time": False}, ["due_at", "has_time"]),
        ({"due_at": None, "has_time": False}, ["due_at", "has_time"]),
        (
            {"title": "我改过的任务", "due_at": None, "has_time": False},
            ["due_at", "has_time", "title"],
        ),
    ],
)
async def test_user_edits_read_back_and_only_update_calendar(seeded, tmp_path, changes, locked):
    from core.store import dedup_key

    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], **changes)
    assert (await seeded.apple.reconcile(MOMENT))["changed"] == 1
    await AppleSync(seeded.db, bridge).flush()
    row = seeded.store.todo(todo["id"])
    assert row["user_modified_fields"] == locked
    assert row["status_source"] is None  # A title edit did not change the status.
    for field, expected in changes.items():
        actual = row[field]
        if field == "due_at" and expected is not None:
            assert datetime.fromisoformat(actual) == datetime.fromisoformat(expected)
        else:
            assert actual == expected
    assert row["dedup_key"] == dedup_key(row["title"], row["due_at"], row["has_time"])
    assert all(a == "read" for r, a, _ in bridge.calls if r == "reminders")
    assert len(bridge.items("calendar")) == int(row["has_time"] and row["due_at"] is not None)
    assert (await seeded.apple.reconcile(MOMENT + timedelta(minutes=5)))["changed"] == 0


async def test_mcp_protects_user_fields_atomically_but_http_user_can_edit(seeded, tmp_path):
    import httpx

    from core.api import create_app

    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], title="用户标题", due_at=None, has_time=False)
    await seeded.apple.reconcile(MOMENT)
    await seeded.apple.flush()
    before = seeded.store.todo(todo["id"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service=seeded)),
        base_url="http://testserver",
        headers={"Authorization": "Bearer " + seeded.settings.token.get_secret_value()},
    ) as client:
        for patch in (
            {"title": "Agent 想覆盖", "notes": "也不能部分写入", "status": "completed"},
            {"due_at": "2026-11-09T18:00:00+08:00", "has_time": True},
            {"has_time": True},
        ):
            response = (
                await client.post(
                    "/internal/tools/todos_update", json={"id": todo["id"], "patch": patch}
                )
            ).json()
            assert response["isError"] is True
            assert "todo_fields_user_modified" in json.dumps(response)
            assert seeded.store.todo(todo["id"]) == before
            assert not seeded.db.rows("SELECT * FROM outbox")
        # Notes, completion, and equal protected values still work for an Agent.
        response = (
            await client.post(
                "/internal/tools/todos_update",
                json={
                    "id": todo["id"],
                    "patch": {"title": "用户标题", "notes": "新增来源说明", "status": "completed"},
                },
            )
        ).json()
        assert response["isError"] is False
        assert seeded.store.todo(todo["id"])["status"] == "completed"
        patched = await client.patch(f"/api/v1/todos/{todo['id']}", json={"title": "用户再次修改"})
        assert patched.status_code == 200
        assert patched.json()["title"] == "用户再次修改"
        assert patched.json()["user_modified_fields"] == ["due_at", "has_time", "title"]


@pytest.mark.parametrize(
    ("original", "remote"),
    [
        ({"has_time": False}, {"due_at": "2026-10-12T00:00:00+08:00"}),
        ({"due_at": "2026-10-12T18:00:00.123456+08:00"}, {"due_at": "2026-10-12T10:00:00Z"}),
        ({}, {"due_at": "2026-10-12T10:00:00+00:00"}),
    ],
)
async def test_eventkit_date_normalization_does_not_lock_fields(seeded, tmp_path, original, remote):
    bridge = RecordingAppleBridge(tmp_path / "apple")
    seeded.apple = AppleSync(seeded.db, bridge)
    todo = (await seeded.add_todo(candidate(seeded, **original)))["todo"]
    bridge.change_reminder(todo["reminder_id"], **remote)
    assert (await seeded.apple.reconcile(MOMENT))["changed"] == 0
    assert seeded.store.todo(todo["id"])["user_modified_fields"] == []


async def test_conflicting_remote_title_fails_safely_then_recovers(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    await seeded.add_todo(candidate(seeded, title="重复标题"))
    bridge.change_reminder(todo["reminder_id"], title="重复标题", completed=True)
    before = seeded.store.todo(todo["id"])
    result = await seeded.apple.reconcile(MOMENT)
    assert result["failed"] == 1
    row = seeded.store.todo(todo["id"])
    assert row["title"] == before["title"] and row["user_modified_fields"] == []
    assert row["status"] == "completed"  # The optional edit cannot block status readback.
    bridge.change_reminder(todo["reminder_id"], title="用户解决了重名")
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=5))
    row = seeded.store.todo(todo["id"])
    assert row["status"] == "completed" and row["user_modified_fields"] == ["title"]


async def test_cancelled_read_breaks_streak_without_swallowing_cancellation(seeded, tmp_path):
    import asyncio

    bridge, todo = await synced(seeded, tmp_path)
    bridge.delete_reminder(todo["reminder_id"])
    await seeded.apple.reconcile(MOMENT)

    class CancelRead:
        async def call(self, resource, action, payload):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await AppleSync(seeded.db, CancelRead()).reconcile(MOMENT + timedelta(minutes=5))
    assert seeded.store.todo(todo["id"])["status"] == "pending"
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=10))
    assert seeded.store.todo(todo["id"])["status"] == "pending"


async def test_immediate_notes_update_preserves_phone_completion(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], completed=True)
    await seeded.update_todo(todo["id"], TodoPatch(notes="Agent 补充来源"), from_agent=True)
    assert seeded.store.todo(todo["id"])["status"] == "completed"
    assert bridge.items("reminders")[todo["id"]]["completed"] is True


async def test_immediate_update_does_not_recreate_deleted_reminder(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.delete_reminder(todo["reminder_id"])
    await seeded.update_todo(todo["id"], TodoPatch(notes="补充来源"))
    assert not bridge.items("reminders")
    assert seeded.store.todo(todo["id"])["sync_status"] == "pending"


async def test_agent_update_reads_user_edit_before_checking_protection(seeded, tmp_path):
    from core.store import ConflictError

    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], title="手机上的标题")
    with pytest.raises(ConflictError, match="todo_fields_user_modified"):
        await seeded.update_todo(todo["id"], TodoPatch(title="Agent 的旧标题"), from_agent=True)
    assert seeded.store.todo(todo["id"])["title"] == "手机上的标题"


async def test_queued_notes_merge_phone_fields_after_restart(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    seeded.store.update_todo(todo["id"], TodoPatch(notes="尚未发送的备注"))
    bridge.change_reminder(todo["reminder_id"], title="手机标题", completed=True)
    sync = AppleSync(Database(seeded.db.path), bridge)
    await sync.reconcile(MOMENT)
    row = seeded.store.todo(todo["id"])
    assert row["status"] == "completed" and row["title"] == "手机标题"
    assert row["notes"] == "尚未发送的备注"
    await sync.flush()
    remote = bridge.items("reminders")[todo["id"]]
    assert remote["completed"] is True and remote["title"] == "手机标题"


async def test_legacy_unknown_list_never_guesses_deletion(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    seeded.db.execute("DELETE FROM apple_reminder_state")
    bridge.reminder_lists = {"another-account"}
    for minutes in (0, 5, 10):
        await seeded.apple.reconcile(MOMENT + timedelta(minutes=minutes))
    assert seeded.store.todo(todo["id"])["status"] == "pending"
    seeded.store.update_todo(todo["id"], TodoPatch(notes="等待账号回来"))
    assert (await seeded.apple.flush())["attempted"] == 0


async def test_moved_reminder_is_held_until_returning_to_managed_list(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.other_reminder_lists = {"personal-list"}
    bridge.change_reminder(todo["reminder_id"], list_id="personal-list")
    seeded.store.update_todo(todo["id"], TodoPatch(notes="等待移动回来"))
    for minutes in (0, 5, 10):
        await seeded.apple.reconcile(MOMENT + timedelta(minutes=minutes))
        assert (await seeded.apple.flush())["attempted"] == 0
    assert seeded.store.todo(todo["id"])["status"] == "pending"
    assert bridge.items("reminders")[todo["id"]]["list_id"] == "personal-list"
    bridge.change_reminder(todo["reminder_id"], list_id="fake-ai-todos", completed=True)
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=15))
    await seeded.apple.flush()
    assert seeded.store.todo(todo["id"])["status"] == "completed"


async def test_older_icloud_snapshot_does_not_undo_local_write_or_lock_fields(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    old_snapshot = await bridge.call("reminders", "read", {"reminder_ids": [todo["reminder_id"]]})
    await seeded.update_todo(todo["id"], TodoPatch(title="App 新标题", status="completed"))

    class StaleRead:
        async def call(self, resource, action, payload):
            assert (resource, action) == ("reminders", "read")
            return old_snapshot

    assert (await AppleSync(seeded.db, StaleRead()).reconcile(MOMENT))["changed"] == 0
    row = seeded.store.todo(todo["id"])
    assert row["title"] == "App 新标题" and row["status"] == "completed"
    assert row["user_modified_fields"] == []


async def test_read_failure_does_not_starve_new_outbox_items(seeded, tmp_path):
    bridge, _ = await synced(seeded, tmp_path)
    new = seeded.store.add_todo(candidate(seeded, title="另一条尚未同步"))["todo"]
    bridge.reminders_authorized = False
    with pytest.raises(RuntimeError, match="apple_sync_failed"):
        await seeded.jobs.sync()
    # This fake only denies reads, modelling an independent failed read. Existing writes
    # stay held, while unrelated new items and calendar-only retries still get attempted.
    assert seeded.store.todo(new["id"])["reminder_id"]


async def test_invalid_phone_title_is_not_overwritten_by_immediate_retry(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    seeded.store.update_todo(todo["id"], TodoPatch(notes="等待发送"))
    bridge.change_reminder(todo["reminder_id"], title="")
    assert (await seeded.apple.flush())["failed"] == 1
    assert bridge.items("reminders")[todo["id"]]["title"] == ""
    assert seeded.store.todo(todo["id"])["sync_error"] == "apple_sync_failed"


async def test_app_can_correct_an_invalid_phone_title(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], title="")
    await seeded.update_todo(todo["id"], TodoPatch(title="App 修正的标题"))
    assert bridge.items("reminders")[todo["id"]]["title"] == "App 修正的标题"
    assert seeded.store.todo(todo["id"])["user_modified_fields"] == []


@pytest.mark.parametrize("deleted", [False, True])
async def test_phone_edit_between_preflight_and_write_is_not_overwritten(seeded, tmp_path, deleted):
    bridge, todo = await synced(seeded, tmp_path)
    seeded.store.update_todo(todo["id"], TodoPatch(notes="等待发送"))

    class EditBeforeWrite:
        async def call(self, resource, action, payload):
            if (resource, action) == ("reminders", "upsert"):
                if deleted:
                    bridge.delete_reminder(todo["reminder_id"])
                else:
                    bridge.change_reminder(todo["reminder_id"], completed=True)
            return await bridge.call(resource, action, payload)

    assert (await AppleSync(seeded.db, EditBeforeWrite()).flush())["failed"] == 1
    assert seeded.store.todo(todo["id"])["sync_status"] == "pending"
    if deleted:
        assert not bridge.items("reminders")
        await seeded.apple.reconcile(MOMENT)
        await seeded.apple.reconcile(MOMENT + timedelta(minutes=5))
    else:
        assert bridge.items("reminders")[todo["id"]]["completed"] is True
        await seeded.apple.reconcile(MOMENT)
    await seeded.apple.flush()
    assert seeded.store.todo(todo["id"])["status"] == ("deleted" if deleted else "completed")


async def test_app_edit_during_agent_preflight_cannot_bypass_user_protection(seeded, tmp_path):
    import asyncio

    from core.store import ConflictError

    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], title="用户标题")
    reading, release = asyncio.Event(), asyncio.Event()

    class PausedRead:
        async def call(self, resource, action, payload):
            if action == "read" and not reading.is_set():
                reading.set()
                await release.wait()
            return await bridge.call(resource, action, payload)

    seeded.apple = AppleSync(seeded.db, PausedRead())
    agent = asyncio.create_task(
        seeded.update_todo(todo["id"], TodoPatch(title="Agent 旧标题"), from_agent=True)
    )
    await asyncio.wait_for(reading.wait(), 2)
    app = asyncio.create_task(seeded.update_todo(todo["id"], TodoPatch(notes="App 备注")))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.wait_for(asyncio.gather(agent, app, return_exceptions=True), 2)
    assert isinstance(results[0], ConflictError)
    assert str(results[0]) == "todo_fields_user_modified"
    assert not isinstance(results[1], Exception)
    row = seeded.store.todo(todo["id"])
    assert row["title"] == "用户标题" and row["notes"] == "App 备注"


async def test_reextract_original_after_user_rename_and_delete_does_not_resurrect(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], title="用户的新标题")
    await seeded.apple.reconcile(MOMENT)
    bridge.delete_reminder(todo["reminder_id"])
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=5))
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=10))
    assert seeded.store.todo(todo["id"])["status"] == "deleted"
    result = await seeded.add_todo(candidate(seeded))
    assert result["disposition"] == "merged" and result["todo"]["id"] == todo["id"]
    assert seeded.db.rows("SELECT count(*) AS n FROM todos")[0]["n"] == 1
    assert not bridge.items("reminders") and not bridge.items("calendar")


async def test_v6_upgrade_keeps_streak_and_calendar_only_retry(seeded, tmp_path):
    bridge, first = await synced(seeded, tmp_path)
    second = (await seeded.add_todo(candidate(seeded, title="等待日历同步")))["todo"]
    bridge.change_reminder(second["reminder_id"], completed=True)
    bridge.delete_reminder(first["reminder_id"])
    await seeded.apple.reconcile(MOMENT)
    with seeded.db.connect() as conn:
        conn.execute("ALTER TABLE outbox DROP COLUMN fields")
        conn.execute("ALTER TABLE apple_reminder_state DROP COLUMN last_modified_at")
        conn.execute("PRAGMA user_version=6")
    db = Database(seeded.db.path)
    assert db.rows(
        "SELECT missing_reads FROM apple_reminder_state WHERE todo_id=?", (first["id"],)
    ) == [{"missing_reads": 1}]
    assert db.rows("SELECT reminders FROM outbox WHERE todo_id=?", (second["id"],)) == [
        {"reminders": 0}
    ]
    bridge.calls.clear()
    sync = AppleSync(db, bridge)
    await sync.reconcile(MOMENT + timedelta(minutes=5))
    await sync.flush()
    assert seeded.store.todo(first["id"])["status"] == "deleted"
    assert seeded.store.todo(second["id"])["status"] == "completed"
    assert not db.rows("SELECT * FROM outbox")
    assert all(action == "read" for resource, action, _ in bridge.calls if resource == "reminders")


async def test_notes_after_legacy_calendar_retry_do_not_inherit_dirty_status(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    seeded.db.execute("INSERT INTO outbox(todo_id,revision,reminders) VALUES(?,1,0)", (todo["id"],))
    seeded.store.update_todo(todo["id"], TodoPatch(notes="仅补充备注"))
    bridge.change_reminder(todo["reminder_id"], completed=True)
    await seeded.apple.reconcile(MOMENT)
    assert seeded.store.todo(todo["id"])["status"] == "completed"


async def test_idle_poll_is_one_read_and_no_write(seeded, tmp_path):
    bridge, _ = await synced(seeded, tmp_path)
    await seeded.jobs.sync()
    assert [(r, a) for r, a, _ in bridge.calls] == [("reminders", "read")]
    bridge.calls.clear()
    await seeded.apple.flush()
    assert bridge.calls == []


async def test_read_bridge_uses_one_process_for_many_ids(monkeypatch):
    monkeypatch.setattr("core.apple.sys.platform", "darwin")
    ids = [f"synthetic-{index}" for index in range(5000)]
    calls = []

    async def process(argv, data, timeout, **kwargs):
        calls.append(argv)
        assert json.loads(data)["reminder_ids"] == ids
        return 0, b'{"list_ids":["list"],"items":[]}'

    monkeypatch.setattr("core.apple.helper_process.run", process)
    await SwiftAppleBridge(Path("never-executed")).call("reminders", "read", {"reminder_ids": ids})
    assert calls == [["never-executed", "reminders", "read"]]


# What the review left to the merge with cancelled todos, and to Apple's identifiers ------------


async def polls(seeded, count=3):
    """Full polls far enough apart for a missing reminder to be confirmed."""
    for index in range(count):
        result = await seeded.apple.reconcile(MOMENT + timedelta(minutes=10 * index))
        await seeded.apple.flush()
    return result


async def test_clearing_a_completed_reminder_leaves_the_todo_completed(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], completed=True)
    await polls(seeded, 1)
    calendar_id = seeded.store.todo(todo["id"])["calendar_event_id"]
    assert calendar_id

    bridge.delete_reminder(todo["reminder_id"])  # 「清除已完成」
    await polls(seeded)
    row = seeded.store.todo(todo["id"])
    assert row["status"] == "completed" and row["reminder_id"] is None
    assert row["calendar_event_id"] == calendar_id and bridge.items("calendar")

    # The same notice extracted again does not bring a done item back to the list.
    merged = await seeded.add_todo(candidate(seeded))
    assert merged["todo"]["id"] == todo["id"] and merged["todo"]["sync_status"] == "synced"
    assert not bridge.items("reminders")

    # Reopened in the App it needs a reminder again.
    reopened = await seeded.update_todo(todo["id"], TodoPatch(status="pending"))
    assert reopened["reminder_id"] and len(bridge.items("reminders")) == 1


async def test_clearing_many_completed_reminders_is_not_a_mass_disappearance(seeded, tmp_path):
    bridge, kept = await synced(seeded, tmp_path)
    done = [
        (await seeded.add_todo(candidate(seeded, title=f"做完的 {index}")))["todo"]
        for index in range(4)
    ]
    for todo in done:
        bridge.change_reminder(todo["reminder_id"], completed=True)
    await polls(seeded, 1)
    for todo in done:
        bridge.delete_reminder(todo["reminder_id"])
    assert (await polls(seeded))["mass_missing"] == 0
    await seeded.jobs.sync()
    assert {seeded.store.todo(t["id"])["status"] for t in done} == {"completed"}
    assert seeded.store.todo(kept["id"])["status"] == "pending"


async def test_many_pending_reminders_missing_together_are_not_deleted(seeded, tmp_path):
    bridge, first = await synced(seeded, tmp_path)
    others = [
        (await seeded.add_todo(candidate(seeded, title=f"另一件 {index}")))["todo"]
        for index in range(3)
    ]
    await polls(seeded, 1)  # each now has a known list, so a miss would count
    missing = [first, *others[:2]]
    for todo in missing:
        bridge.delete_reminder(todo["reminder_id"])
    assert (await polls(seeded))["mass_missing"] == 3
    assert {seeded.store.todo(t["id"])["status"] for t in missing} == {"pending"}
    assert all(seeded.store.todo(t["id"])["reminder_id"] for t in missing)
    await seeded.jobs._run("apple_sync", seeded.jobs.sync)
    assert seeded.store.runs()["items"][0]["error"] == "apple_reminders_mass_missing"

    # The user says they did delete them: what is left is judged as usual again.
    for todo in missing[:2]:
        await seeded.update_todo(todo["id"], TodoPatch(status="deleted"))
    result = await seeded.apple.reconcile(MOMENT + timedelta(hours=1))
    assert result["mass_missing"] == 0
    assert seeded.store.todo(missing[2]["id"])["status"] == "deleted"
    assert seeded.store.todo(others[2]["id"])["status"] == "pending"


async def test_a_reminder_apple_gave_a_new_id_is_found_by_its_marker(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    await polls(seeded, 1)
    bridge.reidentify_reminder(todo["reminder_id"], "new-apple-id")
    assert (await polls(seeded))["mass_missing"] == 0
    row = seeded.store.todo(todo["id"])
    assert row["status"] == "pending" and row["reminder_id"] == "new-apple-id"
    assert seeded.db.rows(
        "SELECT reminder_id,missing_reads FROM apple_reminder_state WHERE todo_id=?", (todo["id"],)
    ) == [{"reminder_id": "new-apple-id", "missing_reads": 0}]
    # Later edits on the phone and in the App still reach the same reminder.
    bridge.change_reminder("new-apple-id", completed=True)
    await polls(seeded, 1)
    assert seeded.store.todo(todo["id"])["status"] == "completed"
    await seeded.update_todo(todo["id"], TodoPatch(notes="补一句"))
    assert [item["notes"].split("\n")[0] for item in bridge.items("reminders").values()] == [
        "补一句"
    ]


async def test_only_a_full_poll_confirms_a_deletion(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    await polls(seeded, 1)
    bridge.delete_reminder(todo["reminder_id"])
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=10))
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=20), todo_ids={todo["id"]})
    assert seeded.store.todo(todo["id"])["status"] == "pending"
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=30))
    assert seeded.store.todo(todo["id"])["status"] == "deleted"


async def test_cancelling_a_held_todo_still_removes_its_copies_and_stops_reading_it(
    seeded, tmp_path
):
    bridge, todo = await synced(seeded, tmp_path)
    await polls(seeded, 1)
    bridge.delete_reminder(todo["reminder_id"])
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=10))  # first miss: writes are held
    assert bridge.items("calendar")
    cancelled = await seeded.update_todo(
        todo["id"], TodoPatch(status="cancelled", notes="通知取消了")
    )
    assert cancelled["sync_status"] == "synced" and cancelled["reminder_id"] is None
    assert not bridge.items("calendar")
    bridge.calls.clear()
    assert (await seeded.apple.reconcile(MOMENT + timedelta(hours=1)))["checked"] == 0
    assert bridge.calls == []
    assert seeded.store.todo(todo["id"])["status"] == "cancelled"


async def test_a_new_message_with_the_old_title_merges_after_a_phone_rename(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], title="用户的新标题")
    await seeded.apple.reconcile(MOMENT)
    result = await seeded.add_todo(candidate(seeded, plugin="feishu"))
    assert result["disposition"] == "merged" and result["todo"]["id"] == todo["id"]
    assert result["todo"]["title"] == "用户的新标题"
    assert len(bridge.items("reminders")) == 1


async def test_an_edit_that_cannot_be_kept_names_itself_in_the_run(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], title="长" * 501)
    await seeded.jobs._run("apple_sync", seeded.jobs.sync)
    assert seeded.store.runs()["items"][0]["error"] == "apple_reminder_edit_rejected"
    assert (
        "太长"
        in seeded.watchdog.job_entry("apple_sync", "apple_reminder_edit_rejected", None)["hint"]
    )


async def test_agent_note_is_kept_when_the_read_fails_and_written_after_it_works(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], title="手机上改的标题")
    bridge.reminders_authorized = False
    row = await seeded.update_todo(todo["id"], TodoPatch(notes="Agent 的备注"), from_agent=True)
    assert row["notes"] == "Agent 的备注" and row["sync_error"] == "apple_sync_failed"
    assert "Agent 的备注" not in bridge.items("reminders")[todo["id"]]["notes"]
    bridge.reminders_authorized = True
    await seeded.apple.flush()
    stored = bridge.items("reminders")[todo["id"]]
    assert "Agent 的备注" in stored["notes"] and stored["title"] == "手机上改的标题"
    assert seeded.store.todo(todo["id"])["title"] == "手机上改的标题"


@pytest.mark.parametrize(
    "patch",
    [
        {"title": "Agent 改的标题"},
        {"due_at": datetime(2026, 12, 1, tzinfo=UTC)},
        {"title": "Agent 改的标题", "status": "cancelled"},
        {"title": "Agent 改的标题", "status": "deleted"},
    ],
)
async def test_agent_cannot_change_title_or_date_without_a_read(seeded, tmp_path, patch):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.change_reminder(todo["reminder_id"], title="手机上改的标题")
    bridge.reminders_authorized = False
    with pytest.raises(RuntimeError, match="^apple_sync_failed$"):
        await seeded.update_todo(todo["id"], TodoPatch(**patch), from_agent=True)
    assert seeded.store.todo(todo["id"])["status"] == "pending"
    assert bridge.items("reminders")[todo["id"]]["title"] == "手机上改的标题"
    bridge.reminders_authorized = True
    await seeded.apple.reconcile(MOMENT)
    assert seeded.store.todo(todo["id"])["title"] == "手机上改的标题"


async def test_agent_cancel_is_kept_when_the_read_fails(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)
    bridge.reminders_authorized = False
    row = await seeded.update_todo(todo["id"], TodoPatch(status="cancelled"), from_agent=True)
    assert row["status"] == "cancelled"


async def test_app_edit_is_saved_while_a_sync_is_reading(seeded, tmp_path):
    import asyncio

    bridge, todo = await synced(seeded, tmp_path)
    reading, release = asyncio.Event(), asyncio.Event()

    class PausedRead:
        async def call(self, resource, action, payload):
            if action == "read" and not reading.is_set():
                reading.set()
                await release.wait()
            return await bridge.call(resource, action, payload)

    seeded.apple = AppleSync(seeded.db, PausedRead())
    sync = asyncio.create_task(seeded.apple.reconcile(MOMENT))
    await asyncio.wait_for(reading.wait(), 2)
    edit = asyncio.create_task(seeded.update_todo(todo["id"], TodoPatch(status="completed")))
    await asyncio.sleep(0.01)
    assert not edit.done() and seeded.store.todo(todo["id"])["status"] == "completed"
    release.set()
    await asyncio.wait_for(asyncio.gather(sync, edit), 2)
    assert bridge.items("reminders")[todo["id"]]["completed"] is True


async def test_app_edit_does_not_queue_behind_an_agent_waiting_for_a_sync(seeded, tmp_path):
    import asyncio

    bridge, todo = await synced(seeded, tmp_path)
    reading, release = asyncio.Event(), asyncio.Event()

    class PausedRead:
        async def call(self, resource, action, payload):
            if action == "read" and not reading.is_set():
                reading.set()
                await release.wait()
            return await bridge.call(resource, action, payload)

    seeded.apple = AppleSync(seeded.db, PausedRead())
    sync = asyncio.create_task(seeded.apple.reconcile(MOMENT))
    await asyncio.wait_for(reading.wait(), 2)
    agent = asyncio.create_task(
        seeded.update_todo(todo["id"], TodoPatch(notes="Agent"), from_agent=True)
    )
    await asyncio.sleep(0.01)
    app = asyncio.create_task(seeded.update_todo(todo["id"], TodoPatch(title="App 改的标题")))
    await asyncio.sleep(0.01)
    assert seeded.store.todo(todo["id"])["title"] == "App 改的标题"
    release.set()
    await asyncio.wait_for(asyncio.gather(sync, agent, app), 2)


async def test_restore_after_lost_reminder_delete_receipt_survives_restart(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)

    class LostReceipt:
        async def call(self, resource, action, payload):
            result = await bridge.call(resource, action, payload)
            if (resource, action) == ("reminders", "delete"):
                raise RuntimeError("lost_receipt")
            return result

    seeded.apple = AppleSync(seeded.db, LostReceipt())
    cancelled = await seeded.update_todo(todo["id"], TodoPatch(status="cancelled"))
    assert cancelled["sync_status"] == "pending" and not bridge.items("reminders")
    seeded.apple = AppleSync(seeded.db, bridge)
    await seeded.update_todo(todo["id"], TodoPatch(status="pending"))
    seeded.apple = AppleSync(Database(seeded.db.path), bridge)
    start = datetime.now(UTC)
    for minute in (0, 5, 10):
        await seeded.apple.reconcile(start + timedelta(minutes=minute))
        await seeded.apple.flush()
    restored = seeded.store.todo(todo["id"])
    assert restored["status"] == "pending" and restored["sync_status"] == "synced"
    assert bridge.items("reminders")[todo["id"]]["id"] == restored["reminder_id"]
    assert bridge.items("calendar")[todo["id"]]["id"] == restored["calendar_event_id"]


async def test_failed_targeted_read_keeps_other_reminders_missing_streak(seeded, tmp_path):
    bridge, missing = await synced(seeded, tmp_path)
    other = (await seeded.add_todo(candidate(seeded, title="另一项")))["todo"]
    bridge.delete_reminder(missing["reminder_id"])
    await seeded.apple.reconcile(MOMENT)
    bridge.reminders_authorized = False
    with pytest.raises(RuntimeError, match="apple_sync_failed"):
        await seeded.apple.reconcile(MOMENT, todo_ids={other["id"]})
    bridge.reminders_authorized = True
    await seeded.apple.reconcile(MOMENT + timedelta(minutes=5))
    assert seeded.store.todo(missing["id"])["status"] == "deleted"


async def test_reopening_retired_reminder_cannot_duplicate_current_pending_todo(seeded, tmp_path):
    from core.models import TodoNew

    bridge, first = await synced(seeded, tmp_path)
    await seeded.update_todo(first["id"], TodoPatch(status="completed"))
    body = TodoNew.model_validate({key: first[key] for key in ("title", "due_at", "has_time")})
    current = (await seeded.new_todo(body))["todo"]
    bridge.change_reminder(first["reminder_id"], completed=False)
    read = await seeded.apple.reconcile(MOMENT)
    assert read["failed"] == 1 and read["failed_ids"] == [first["id"]]
    assert [row["id"] for row in seeded.store.todos("pending")["items"]] == [current["id"]]
    # The user resolves the collision on the phone without losing the reopened state.
    bridge.change_reminder(first["reminder_id"], title="补交上周的报名表")
    assert (await seeded.apple.reconcile(MOMENT))["failed"] == 0
    assert seeded.store.todo(first["id"])["status"] == "pending"


@pytest.mark.parametrize("field", ["title", "due_at"])
async def test_pending_agent_edit_yields_to_new_phone_edit_on_retry(seeded, tmp_path, field):
    bridge, todo = await synced(seeded, tmp_path)
    agent_value, phone_value = (
        ("Agent 标题", "用户标题")
        if field == "title"
        else ("2026-12-21T10:00:00+00:00", "2026-12-22T12:00:00+00:00")
    )

    class WriteRace:
        async def call(self, resource, action, payload):
            if (resource, action) == ("reminders", "upsert"):
                bridge.change_reminder(todo["reminder_id"], **{field: phone_value})
            return await bridge.call(resource, action, payload)

    seeded.apple = AppleSync(seeded.db, WriteRace())
    row = await seeded.update_todo(todo["id"], TodoPatch(**{field: agent_value}), from_agent=True)
    assert row["sync_status"] == "pending"
    seeded.apple = AppleSync(Database(seeded.db.path), bridge)
    await seeded.apple.flush()
    row = seeded.store.todo(todo["id"])
    assert row[field] == phone_value and field in row["user_modified_fields"]
    assert bridge.items("reminders")[todo["id"]][field] == phone_value
    assert bridge.items("calendar")[todo["id"]][field] == phone_value


async def test_next_agent_edit_after_a_lost_upsert_receipt(seeded, tmp_path):
    bridge, todo = await synced(seeded, tmp_path)

    class LostUpsert:
        async def call(self, resource, action, payload):
            result = await bridge.call(resource, action, payload)
            if (resource, action) == ("reminders", "upsert"):
                raise RuntimeError("lost_receipt")
            return result

    seeded.apple = AppleSync(seeded.db, LostUpsert())
    await seeded.update_todo(todo["id"], TodoPatch(title="第一次修订"), from_agent=True)
    assert bridge.items("reminders")[todo["id"]]["title"] == "第一次修订"
    seeded.apple = AppleSync(seeded.db, bridge)
    row = await seeded.update_todo(todo["id"], TodoPatch(title="第二次修订"), from_agent=True)
    assert row["title"] == "第二次修订" and row["user_modified_fields"] == []
