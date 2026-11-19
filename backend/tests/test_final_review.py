"""Deployment review checks using synthetic messages and Apple/weather fakes."""

import asyncio
from datetime import UTC, date, datetime

from core.apple import AppleSync
from core.db import dump
from core.models import MessageInput, TodoPatch
from core.plugins import PluginContext
from plugins.weather.plugin import Plugin as WeatherPlugin
from tests.conftest import candidate
from tests.test_reminder_readback import synced
from tests.test_weather import answer


async def test_app_edit_during_reminder_write_keeps_its_outbox_revision(
    seeded, tmp_path, monkeypatch
):
    bridge, todo = await synced(seeded, tmp_path)
    seeded.store.update_todo(todo["id"], TodoPatch(title="第一版"))
    writing, release = asyncio.Event(), asyncio.Event()

    class PausedWrite:
        async def call(self, resource, action, payload):
            if resource == "reminders" and action == "upsert" and not writing.is_set():
                writing.set()
                await release.wait()
            return await bridge.call(resource, action, payload)

    seeded.apple = AppleSync(seeded.db, PausedWrite())
    sync = asyncio.create_task(seeded.apple.flush())
    await asyncio.wait_for(writing.wait(), 2)
    saved = asyncio.Event()
    update = seeded.store.update_todo

    def save(*args, **kwargs):
        result = update(*args, **kwargs)
        saved.set()
        return result

    monkeypatch.setattr(seeded.store, "update_todo", save)
    edit = asyncio.create_task(seeded.update_todo(todo["id"], TodoPatch(title="第二版")))
    try:
        await asyncio.wait_for(saved.wait(), 2)
        assert seeded.store.todo(todo["id"])["title"] == "第二版"
        assert seeded.db.rows("SELECT revision FROM outbox")[0]["revision"] == 2
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(sync, edit), 2)
    assert seeded.store.todo(todo["id"])["title"] == "第二版"
    assert bridge.items("reminders")[todo["id"]]["title"] == "第二版"
    assert seeded.store.todo(todo["id"])["sync_status"] == "synced"
    assert not seeded.db.rows("SELECT * FROM outbox")


async def test_agent_edit_holds_each_rejected_phone_row_without_blocking_valid_row(
    seeded, tmp_path
):
    bridge, first = await synced(seeded, tmp_path)
    second = (await seeded.add_todo(candidate(seeded, title="第二份材料")))["todo"]
    valid = (await seeded.add_todo(candidate(seeded, title="第三份材料")))["todo"]
    for row in (first, second):
        bridge.change_reminder(row["reminder_id"], title="长" * 501)
    for row in (second, valid):
        seeded.store.update_todo(row["id"], TodoPatch(notes="待同步备注"))
    await seeded.update_todo(first["id"], TodoPatch(notes="Agent 新备注"), from_agent=True)
    for row in (first, second):
        assert seeded.store.todo(row["id"])["sync_status"] == "pending"
        assert bridge.items("reminders")[row["id"]]["title"] == "长" * 501
    assert seeded.store.todo(valid["id"])["sync_status"] == "synced"
    assert bridge.items("reminders")[valid["id"]]["notes"].startswith("待同步备注")
    assert {row["todo_id"] for row in seeded.db.rows("SELECT * FROM outbox")} == {
        first["id"],
        second["id"],
    }


def test_search_returns_redacted_full_revision_in_place_of_processed_preview(service):
    service.db.execute(
        "INSERT INTO plugins(id,config_json) VALUES(?,?)",
        ("qq_screen", dump({"self_names": ["张三"]})),
    )
    original = MessageInput(
        external_id="preview",
        chat_name="合成群",
        sender="老师",
        sent_at=datetime.now(UTC),
        text="未交名单：张 三…",
        preview=True,
    )
    service.store.ingest("qq_screen", [original], ["合成群"])
    identifier = service.store.unprocessed()[0]["id"]
    service.store.mark_processed([identifier])
    full = original.model_copy(
        update={
            "external_id": "full",
            "text": "未交名单：张 三、李四，请补交。",
            "preview": False,
        }
    )
    service.store.ingest("qq_screen", [full], ["合成群"])
    [found] = service.store.messages_search("未交名单")
    assert found["id"] == identifier and found["revised"] and not found["preview"]
    assert found["text"] == "未交名单：[本人]、李四，请补交。"
    assert "screenshot_path" not in found


def test_weather_missing_tomorrow_keeps_other_days_attached_to_their_dates(service, tmp_path):
    plugin = WeatherPlugin()
    plugin.context = PluginContext(service.store, tmp_path, "weather")
    plugin.place = "上海"
    data = answer()
    data["daily"]["uv_index_max"][1] = None
    plugin.context.put("forecast", {"data": data, "error": None})
    cards = plugin._cards(date(2026, 10, 26))
    assert [card["title"] for card in cards] == ["上海今天", "出门提醒"]
    assert list(plugin.days()) == [date(2026, 10, 26), date(2026, 10, 28)]
    assert "14–17" in plugin._daily(date(2026, 10, 28))["body"]
