import json
from datetime import UTC, datetime

import pytest

from core.apple import AppleSync, FakeAppleBridge
from core.models import TodoPatch
from core.watchdog import Watchdog
from tests.conftest import RecordingAppleBridge, candidate


@pytest.mark.parametrize(
    ("changes", "alarms", "calendar_action"),
    [
        ({}, [60, 0], "upsert"),
        ({"has_time": False, "due_at": "2026-10-12T00:00:00+08:00"}, [-480], "delete"),
        ({"has_time": False, "due_at": None}, [], "delete"),
    ],
)
async def test_todo_alarms(seeded, tmp_path, changes, alarms, calendar_action):
    bridge = RecordingAppleBridge(tmp_path / "apple")
    seeded.apple = AppleSync(seeded.db, bridge)
    await seeded.add_todo(candidate(seeded, **changes))
    assert [(r, a) for r, a, _ in bridge.calls] == [
        ("reminders", "upsert"),
        ("calendar", calendar_action),
    ]
    assert all(p["alarms"] == (alarms if r == "reminders" else []) for r, _, p in bridge.calls)
    assert all(p["timezone"] == "Asia/Shanghai" for _, _, p in bridge.calls)


@pytest.mark.parametrize("status", ["completed", "deleted", "cancelled"])
async def test_finished_todos_clear_alarms(seeded, tmp_path, status):
    bridge = RecordingAppleBridge(tmp_path / "apple")
    seeded.apple = AppleSync(seeded.db, bridge)
    result = await seeded.add_todo(candidate(seeded))
    bridge.calls.clear()
    await seeded.update_todo(result["todo"]["id"], TodoPatch(status=status))
    done = status == "completed"
    assert [(r, a) for r, a, _ in bridge.calls] == ([("reminders", "read")] if done else []) + [
        ("reminders", "upsert" if done else "delete"),
        ("calendar", "upsert" if done else "delete"),
    ]
    assert all(p["alarms"] == [] for _, a, p in bridge.calls if a != "read")


async def test_cancellation_retries_calendar_delete_and_never_resurrects(seeded):
    bridge = seeded.apple.bridge
    original = (await seeded.add_todo(candidate(seeded)))["todo"]

    class FailCalendar:
        async def call(self, resource, action, payload):
            if resource == "calendar":
                raise RuntimeError("offline")
            return await bridge.call(resource, action, payload)

    seeded.apple = AppleSync(seeded.db, FailCalendar())
    cancelled = await seeded.update_todo(
        original["id"], TodoPatch(status="cancelled", notes="10月12日通知取消，无需交表")
    )
    assert cancelled["sync_status"] == "pending" and cancelled["reminder_id"] is None
    assert cancelled["calendar_event_id"] == original["calendar_event_id"]
    seeded.apple = AppleSync(seeded.db, bridge)
    await seeded.apple.flush()
    await seeded.add_todo(candidate(seeded))
    seeded.db.put("settings", {"todo_alarm_minutes": [30]})
    await seeded.apple.flush()
    row = seeded.store.todo(original["id"])
    assert row["status"] == "cancelled" and row["notes"] == cancelled["notes"]
    assert row["sync_status"] == "synced"
    assert row["reminder_id"] is None and row["calendar_event_id"] is None
    for resource in ("reminders", "calendar"):
        assert json.loads((bridge.directory / f"{resource}.json").read_text()) == {}


@pytest.mark.parametrize("resource", ["reminders", "calendar"])
@pytest.mark.parametrize("after_delete", [False, True])
async def test_cancel_delete_retries_and_explicit_restore(seeded, resource, after_delete):
    bridge = seeded.apple.bridge
    data = candidate(seeded)
    original = (await seeded.add_todo(data))["todo"]

    class FailDelete:
        async def call(self, target, action, payload):
            if target == resource and action == "delete":
                if after_delete:
                    await bridge.call(target, action, payload)
                raise RuntimeError("delete_failed")
            return await bridge.call(target, action, payload)

    seeded.apple = AppleSync(seeded.db, FailDelete())
    cancelled = await seeded.update_todo(
        original["id"],
        TodoPatch(status="cancelled", due_at="2026-10-19T18:00:00+08:00", notes="取消报名"),
    )
    assert cancelled["sync_status"] == "pending" and cancelled["sync_error"] == "apple_sync_failed"
    seeded.apple = AppleSync(seeded.db, bridge)
    retry = await seeded.add_todo(data)
    assert retry["todo"]["id"] == original["id"] and retry["todo"]["status"] == "cancelled"
    assert retry["todo"]["sync_status"] == "synced"
    assert seeded.db.rows("SELECT * FROM outbox") == []
    for target in ("reminders", "calendar"):
        assert json.loads((bridge.directory / f"{target}.json").read_text()) == {}
    restored = await seeded.update_todo(original["id"], TodoPatch(status="pending"))
    assert restored["reminder_id"] and restored["calendar_event_id"]
    assert restored["notes"] == "取消报名" and restored["sync_status"] == "synced"
    for target in ("reminders", "calendar"):
        assert len(json.loads((bridge.directory / f"{target}.json").read_text())) == 1


async def test_upgrade_adds_alarms_to_already_synced_todos(seeded, tmp_path):
    bridge = RecordingAppleBridge(tmp_path / "apple")
    seeded.apple = AppleSync(seeded.db, bridge)
    await seeded.add_todo(candidate(seeded))
    seeded.db.execute("DELETE FROM kv WHERE key='apple:alarm_settings:v1'")
    bridge.calls.clear()
    await AppleSync(seeded.db, bridge).flush()
    assert [(r, a) for r, a, _ in bridge.calls] == [
        ("reminders", "read"),
        ("reminders", "upsert"),
        ("calendar", "upsert"),
    ]
    assert all(
        p["alarms"] == ([60, 0] if r == "reminders" else [])
        for r, a, p in bridge.calls
        if a != "read"
    )
    bridge.calls.clear()
    await seeded.apple.flush()
    assert bridge.calls == []


async def test_alarm_settings_changed_during_sync_keep_the_new_revision(seeded):
    bridge = seeded.apple.bridge

    class ChangeSettings:
        changed = False

        async def call(self, resource, action, payload):
            if not self.changed:
                self.changed = True
                seeded.db.put("settings", {"todo_alarm_minutes": [30]})
                seeded.apple.queue_alarm_changes()
            return await bridge.call(resource, action, payload)

    seeded.apple = AppleSync(seeded.db, ChangeSettings())
    result = await seeded.add_todo(candidate(seeded))
    assert result["todo"]["sync_status"] == "pending"
    await seeded.apple.flush()
    items = json.loads((bridge.directory / "reminders.json").read_text())
    assert items[result["todo"]["id"]]["alarms"] == [30]
    assert seeded.store.todo(result["todo"]["id"])["sync_status"] == "synced"


async def test_retry_after_reminder_saved_before_calendar_failure(seeded):
    bridge = seeded.apple.bridge

    class FailCalendar:
        async def call(self, resource, action, payload):
            if resource == "calendar":
                raise RuntimeError("secret raw provider error")
            return await bridge.call(resource, action, payload)

    seeded.apple = AppleSync(seeded.db, FailCalendar())
    result = await seeded.add_todo(candidate(seeded))
    assert result["todo"]["sync_status"] == "pending"
    assert result["todo"]["reminder_id"]
    assert result["todo"]["sync_error"] == "apple_sync_failed"
    seeded.apple = AppleSync(seeded.db, bridge)
    await seeded.apple.flush()
    assert seeded.store.todo(result["todo"]["id"])["sync_status"] == "synced"
    items = json.loads((bridge.directory / "reminders.json").read_text(encoding="utf-8"))
    assert len(items) == 1


async def test_revision_changed_during_sync_is_not_lost(seeded):
    bridge = seeded.apple.bridge

    class ConcurrentUpdate:
        changed = False

        async def call(self, resource, action, payload):
            if not self.changed:
                self.changed = True
                from core.models import TodoPatch

                seeded.store.update_todo(payload["client_id"], TodoPatch(title="更新后的任务"))
            return await bridge.call(resource, action, payload)

    seeded.apple = AppleSync(seeded.db, ConcurrentUpdate())
    result = await seeded.add_todo(candidate(seeded))
    assert result["todo"]["sync_status"] == "pending"
    await seeded.apple.flush()
    assert seeded.store.todo(result["todo"]["id"])["sync_status"] == "synced"
    items = json.loads((bridge.directory / "reminders.json").read_text(encoding="utf-8"))
    assert next(iter(items.values()))["title"] == "更新后的任务"


async def test_fake_stable_ids_after_restart(tmp_path):
    payload = {"client_id": "stable", "title": "任务"}
    a = await FakeAppleBridge(tmp_path).call("reminders", "upsert", payload)
    b = await FakeAppleBridge(tmp_path).call("reminders", "upsert", payload)
    assert a["id"] == b["id"] and a["list_id"] == b["list_id"]
    assert datetime.fromisoformat(b["last_modified_at"]) >= datetime.fromisoformat(
        a["last_modified_at"]
    )


async def test_fake_calendars_are_scoped_and_omitted_alarms_are_preserved(tmp_path):
    bridge = FakeAppleBridge(tmp_path)
    payload = {"client_id": "stable", "title": "课程"}
    await bridge.call("calendar", "upsert", {**payload, "alarms": [60]})
    await bridge.call("calendar", "upsert", payload)
    await bridge.call("calendar", "upsert", {**payload, "calendar": "AI 课表", "alarms": [15]})
    items = json.loads((tmp_path / "calendar.json").read_text())
    assert len(items) == 2 and items["stable"]["alarms"] == [60]
    await bridge.call("calendar", "delete", {**payload, "calendar": "AI 课表"})
    assert len(json.loads((tmp_path / "calendar.json").read_text())) == 1
    with pytest.raises(ValueError, match="invalid apple calendar"):
        await bridge.call("calendar", "delete", {**payload, "calendar": "个人"})


async def test_watchdog_todos_stay_in_the_app(seeded, tmp_path):
    bridge = RecordingAppleBridge(tmp_path / "apple")
    seeded.apple = AppleSync(seeded.db, bridge)
    moment = datetime.now(UTC)
    entry = {"title": "QQ 屏幕读取一直失败", "ok_at": None, "hint": "打开 QQ。"}
    identifier = await Watchdog(seeded).open(entry, moment, moment)
    await seeded.update_todo(identifier, TodoPatch(status="completed"))
    assert bridge.calls == []
    row = seeded.db.rows("SELECT * FROM todos WHERE id=?", (identifier,))[0]
    assert (row["reminder_id"], row["calendar_event_id"], row["sync_status"]) == (
        None,
        None,
        "synced",
    )

    # One that reached Reminders before is taken back on the next sync, once.
    synced = (await seeded.add_todo(candidate(seeded, title="课表读不到了")))["todo"]["id"]
    seeded.db.execute("UPDATE todos SET source_plugin='watchdog' WHERE id=?", (synced,))
    bridge.calls.clear()
    await seeded.apple.flush()
    assert [(r, a) for r, a, _ in bridge.calls] == [("reminders", "delete"), ("calendar", "delete")]
    row = seeded.db.rows("SELECT * FROM todos WHERE id=?", (synced,))[0]
    assert (row["reminder_id"], row["calendar_event_id"], row["status"]) == (None, None, "pending")
    assert json.loads((tmp_path / "apple" / "reminders.json").read_text()) == {}
    bridge.calls.clear()
    await seeded.apple.flush()
    assert bridge.calls == []
