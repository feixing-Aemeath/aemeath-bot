"""Synthetic rosters and fake screenshots only; no desktop or network access."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest

from core.db import Database, dump
from core.mcp_server import create_mcp
from core.message_signals import message_flags, open_reason
from core.models import MessageInput, PluginPatch, TodoCreate
from core.plugins import PluginContext
from core.prompts import render
from core.store import Store
from plugins.qq_screen.plugin import Plugin as QQPlugin
from plugins.wechat_screen.plugin import Plugin as WechatPlugin
from tests.screen_render import block, document
from tests.test_screen import GROUP, NOW, chat_history, read, reader_for

# Absolute on every system; on Windows a path without a drive is not.
FAKE_HELPER = str(Path("/fake/helper").resolve())


@pytest.mark.parametrize(
    ("text", "names", "expected"),
    [
        ("未交名单：张 三、李四", ["张三"], ["self_listed"]),
        ("以下同学：张\n三", ["张三"], ["self_listed"]),
        ("未提交：20260012", ["20260012"], ["self_listed"]),
        ("未提交：202600123", ["20260012"], []),
        ("未交：120260012", ["20260012"], []),
        ("未交：2026 0012", ["20260012"], []),
        ("你好张三", ["张三"], []),
        ("未交名单：李四", ["张三"], []),
        ("#接龙\n全班报名\n1. 李四\n2. 张 三", ["张三"], ["self_joined"]),
        ("接龙 全班报名 1. 李四 2. 王五", ["张三"], ["self_missing"]),
        ("接龙：张三组织\n1. 李四", ["张三"], ["self_missing"]),
        ("接龙还没发，请等等", ["张三"], []),
        ("作业：1. 题目甲 2. 题目乙", ["张三"], []),
        ("#接龙\n1. 李四", [], []),
    ],
)
def test_local_roster_rules(text, names, expected):
    assert message_flags(text, names) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("未交名单：张三", None),
        ("未交名单：李四", None),
        ("未交名单：张三…", "truncated"),
        ("#接龙 1. 张三", None),
        ("#接龙 1. 李四", None),
        ("请查看未交名单", "membership_unknown"),
        ("一般的消息…", None),
    ],
)
def test_open_only_when_roster_is_incomplete_or_unknown(text, expected):
    assert open_reason(text, ["张三"]) == expected


def input_message(identifier="m", **changes):
    return MessageInput.model_validate(
        {
            "external_id": identifier,
            "chat_name": GROUP,
            "sender": "老师",
            "sent_at": NOW,
            "text": "未交名单：张三",
            **changes,
        }
    )


def test_flags_merge_priority_and_direction_survive_rereads(service):
    wakeups = []
    service.store.on_attention = lambda: wakeups.append(1)
    store = service.store
    ordinary = input_message("ordinary", text="@全体成员 普通通知", priority=True)
    flagged = input_message(flags=["self_listed"], outgoing=True)
    assert store.ingest("qq_screen", [ordinary, flagged], [GROUP]) == 2
    [row, other] = store.unprocessed()
    assert row["flags"] == ["self_listed"] and row["priority"] > other["priority"]
    assert row["outgoing"] is True
    store.mark_processed([row["id"]])
    assert (
        store.ingest("qq_screen", [flagged.model_copy(update={"flags": ["self_joined"]})], [GROUP])
        == 0
    )
    assert wakeups == [1]
    assert not any(m["id"] == row["id"] for m in store.unprocessed())
    persisted = service.db.rows("SELECT flags,outgoing FROM messages WHERE id=?", (row["id"],))[0]
    assert json.loads(persisted["flags"]) == ["self_joined", "self_listed"]
    assert persisted["outgoing"] == 1
    # A new Store on the same database does not re-trigger a previously observed flag.
    restarted = Store(Database(service.db.path))
    restarted.on_attention = lambda: wakeups.append(1)
    restarted.ingest("qq_screen", [flagged], [GROUP])
    assert wakeups == [1]


def test_new_flags_reopen_processed_messages_and_rollback_does_not_wake(service):
    store = service.store
    wakeups = []
    store.on_attention = lambda: wakeups.append(1)
    plain = input_message()
    store.ingest("qq_screen", [plain], [GROUP])
    store.mark_processed([store.unprocessed()[0]["id"]])
    flagged = plain.model_copy(update={"flags": ["self_missing"]})
    with pytest.raises(ValueError, match="chat_not_whitelisted"):
        store.ingest("qq_screen", [flagged, input_message("bad", chat_name="outside")], [GROUP])
    assert not wakeups and not store.unprocessed()
    store.ingest("qq_screen", [flagged], [GROUP])
    assert wakeups == [1] and store.unprocessed()[0]["flags"] == ["self_missing"]


def test_preview_replacement_merges_flags_and_saves_outgoing(service):
    store = service.store
    wakeups = []
    store.on_attention = lambda: wakeups.append(1)
    preview = input_message("p", text="未交名单…", preview=True, outgoing=True)
    store.ingest("qq_screen", [preview], [GROUP])
    [original] = store.unprocessed()
    assert original["outgoing"] is False
    store.mark_processed([original["id"]])
    full = input_message("f", flags=["self_listed"], outgoing=True)
    store.ingest("qq_screen", [full], [GROUP])
    [row] = store.unprocessed()
    assert row["id"] == original["id"] and row["revised"] and row["outgoing"]
    assert row["flags"] == ["self_listed"] and wakeups == [1]
    # A later preview must merge signals into the existing full copy, without downgrading it.
    store.ingest("qq_screen", [preview.model_copy(update={"flags": ["self_missing"]})], [GROUP])
    assert set(store.unprocessed()[0]["flags"]) == {"self_listed", "self_missing"}
    assert wakeups == [1]


async def test_new_flags_submit_immediately_and_coalesce_behind_running_job(service):
    jobs = service.jobs
    started, release = asyncio.Event(), asyncio.Event()
    active = 0
    calls = []

    async def extract():
        nonlocal active
        active += 1
        assert active == 1
        calls.append(1)
        started.set()
        await release.wait()
        active -= 1

    jobs.callbacks["extract_todos"] = extract
    submissions = []
    submit = jobs.submit

    def recording_submit(job, **options):
        submissions.append((job, options))
        return submit(job, **options)

    jobs.submit = recording_submit
    try:
        first = input_message(flags=["self_listed"])
        service.store.ingest("qq_screen", [first], [GROUP])
        await asyncio.wait_for(started.wait(), 1)
        task = jobs.tasks["extract_todos"]
        # Re-reading one message never submits again, including a second urgent flag.
        service.store.ingest(
            "qq_screen", [first.model_copy(update={"flags": ["self_missing"]})], [GROUP]
        )
        for identifier in ("m2", "m3", "m4"):
            service.store.ingest(
                "qq_screen", [input_message(identifier, flags=["self_missing"])], [GROUP]
            )
        assert len(submissions) == 4
        assert jobs.pending == {"extract_todos"} and jobs.tasks["extract_todos"] is task
        release.set()
        await asyncio.wait_for(task, 1)
        assert len(calls) == 2 and not jobs.pending
    finally:
        release.set()
        await jobs.close()


def preview_document(text="未交名单：张三…", chats=None):
    previews = [
        block(GROUP, 0.2, 0.05, 0.016),
        block("20:41", 0.88, 0.052, 0.012, char_w=0.016),
        block(text, 0.2, 0.08, 0.014, char_w=0.014),
    ]
    return document(
        chats
        or {
            GROUP: [
                {"time": "20:38"},
                {"from": "老师", "text": "未交名单：张三、李四"},
                {"me": "好的我晚上发"},
            ]
        },
        preview=previews,
    )


@pytest.mark.parametrize(("plugin", "layout"), [("qq_screen", None), ("wechat_screen", "wechat")])
async def test_on_demand_reads_one_group_and_commits_cursor(service, plugin, layout):
    from plugins.screen.layout import QQ_LAYOUT, WECHAT_LAYOUT

    reader, capture = reader_for(
        service,
        preview_document(),
        plugin=plugin,
        layout=WECHAT_LAYOUT if layout else QQ_LAYOUT,
        preview_only=True,
        self_names=["张三"],
        open_on_demand=[GROUP],
    )
    messages = await read(reader)
    assert any(m.preview and m.flags == ["self_listed"] for m in messages)
    assert any(m.outgoing for m in messages if not m.preview)
    assert all(op[1] == GROUP for op in capture.operations if op[0] == "capture_chat")
    assert capture.operations[0] == capture.operations[-1] == ("preview",)
    assert reader.cursor(GROUP) is None
    service.store.ingest(plugin, messages, [GROUP])
    await reader.commit_fetch()
    assert reader.cursor(GROUP)
    # The same unchanged preview cannot burn the daily allowance repeatedly.
    capture.operations.clear()
    assert all(m.preview for m in await read(reader))
    assert capture.operations == [("preview",)]
    runs = service.store.runs()["items"]
    assert len(runs) == 1 and ":truncated:1" in runs[0]["job"]
    assert GROUP not in dump(runs) and "张三" not in dump(runs)


@pytest.mark.parametrize(
    ("text", "allowed", "opens"),
    [
        ("未交名单：张三", True, False),
        ("未交名单：张三…", True, True),
        ("未交名单：张三…", False, False),
        ("[@全体成员]明天开会", True, True),
        ("请查看未交名单", True, True),
        ("普通消息…", True, False),
    ],
)
async def test_preview_open_conditions(service, text, allowed, opens):
    reader, capture = reader_for(
        service,
        preview_document(text),
        preview_only=True,
        self_names=["张三"],
        open_on_demand=[GROUP] if allowed else [],
    )
    await read(reader)
    assert any(op[0] == "capture_chat" for op in capture.operations) is opens


async def test_preview_sender_is_not_a_roster_entry(service):
    reader, capture = reader_for(
        service,
        preview_document("张三：未交名单：李四"),
        preview_only=True,
        self_names=["张三"],
        open_on_demand=[GROUP],
    )
    [message] = await read(reader)
    assert message.flags == [] and message.outgoing is False
    assert capture.operations == [("preview",)]


async def test_requested_page_count_and_revoked_permission(service):
    reader, capture = reader_for(
        service,
        preview_document(chats={GROUP: chat_history(count=50)}),
        preview_only=True,
        open_on_demand=[GROUP],
        on_demand_pages=3,
    )
    await read(reader)
    assert len([op for op in capture.operations if op[0] == "capture_chat"]) == 3
    reader.request_open(GROUP)
    reader.config["open_on_demand"] = []
    capture.operations.clear()
    await read(reader)
    assert capture.operations == [("preview",)]


def missed_document(label, text="高数作业：第三章习题…", chats=None):
    """The list on Thursday 10/15: the group's newest row is from an earlier day."""
    previews = [
        block(GROUP, 0.2, 0.05, 0.016),
        block(label, 0.88, 0.052, 0.012, char_w=0.016),
        block(text, 0.2, 0.08, 0.014, char_w=0.014),
    ]
    return document(
        chats
        or {
            GROUP: [
                {"time": "星期二 14:03"},
                {"from": "李老师", "text": "高数作业：第三章习题 1-12，下周一上课前交"},
            ]
        },
        preview=previews,
    )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("星期二", "2026-10-13"),
        ("周日", "2026-10-11"),
        ("昨天", "2026-10-14"),
        ("昨天20:59", "2026-10-14"),
        ("前天", "2026-10-13"),
        ("10/12", None),
        ("20:41", None),
    ],
)
def test_list_day_covers_the_last_week_only(label, expected):
    from plugins.screen.timeparse import parse_list_day

    day = parse_list_day(label, NOW)
    assert (day.isoformat() if day else None) == expected


async def test_a_missed_day_opens_an_allowed_chat_once(service):
    reader, capture = reader_for(
        service, missed_document("星期二"), preview_only=True, open_on_demand=[GROUP]
    )
    messages = await read(reader)
    # No preview with a made-up time: the chat itself is read, with its real times.
    assert [(m.text, m.sent_at.isoformat(), m.preview) for m in messages] == [
        ("高数作业：第三章习题 1-12，下周一上课前交", "2026-10-13T14:03:00+08:00", False)
    ]
    assert ":missed:1" in service.store.runs()["items"][0]["job"]
    assert reader.context.get("missed_chats") == []
    service.store.ingest("qq_screen", messages, [GROUP])
    await reader.commit_fetch()
    # Tuesday is stored now: the same row is old news.
    capture.operations.clear()
    assert await read(reader) == []
    assert capture.operations == [("preview",)]


async def test_a_missed_day_without_permission_is_shown_not_guessed(service):
    instance = QQPlugin()
    context = PluginContext(service.store, service.settings.data_dir, "qq_screen")
    await instance.setup(context, {"whitelist": [GROUP], "helper": FAKE_HELPER})
    instance.reader, capture = reader_for(service, missed_document("星期二"), preview_only=True)
    assert await read(instance.reader) == []
    assert capture.operations == [("preview",)]
    [found] = await instance.watch()
    assert found["key"] == "missed" and found["hours"] == 0 and GROUP in found["hint"]


async def test_a_missed_day_with_its_minute_is_kept_as_a_preview(service):
    reader, capture = reader_for(
        service, missed_document("昨天20:59", "明天交实验报告"), preview_only=True
    )
    [message] = await read(reader)
    assert message.preview and message.sent_at.isoformat() == "2026-10-14T20:59:00+08:00"
    assert capture.operations == [("preview",)]
    service.store.ingest("qq_screen", [message], [GROUP])
    assert await read(reader) == []


async def test_a_day_already_read_is_not_caught_up_on(service):
    service.store.ingest(
        "qq_screen",
        [input_message(sent_at=NOW - timedelta(days=2, hours=5))],
        [GROUP],
    )
    reader, capture = reader_for(
        service, missed_document("星期二"), preview_only=True, open_on_demand=[GROUP]
    )
    assert await read(reader) == []
    assert capture.operations == [("preview",)]
    assert reader.context.get("missed_chats") == []


async def test_mcp_schedules_plugin_fetch_and_ingestion_wakes_extraction(service):
    plugin = "qq_screen"
    instance = QQPlugin()
    context = PluginContext(service.store, service.settings.data_dir, plugin)
    config = {
        "whitelist": [GROUP],
        "open_on_demand": [GROUP],
        "self_names": ["张三"],
        "helper": FAKE_HELPER,
    }
    await instance.setup(context, config)
    instance.reader, capture = reader_for(
        service,
        preview_document("普通消息"),
        preview_only=True,
        **config,
    )
    service.plugins.instances[plugin] = instance
    service.plugins.manifests[plugin] = {}
    service.db.execute("INSERT INTO plugins(id,config_json) VALUES(?,?)", (plugin, dump(config)))
    gate = asyncio.Event()
    extraction_done = asyncio.Event()
    extracted = []

    async def fetch():
        await gate.wait()
        await service.plugins.fetch(plugin)

    async def extract():
        extracted.extend(service.store.unprocessed())
        extraction_done.set()

    service.jobs.callbacks.update({"fetch:qq_screen": fetch, "extract_todos": extract})
    try:
        mcp = create_mcp(service)
        result = await call_mcp(mcp, "screen_open_chat", {"plugin": plugin, "chat_name": GROUP})
        assert result["status"] == "scheduled" and not capture.operations
        task = service.jobs.tasks["fetch:qq_screen"]
        gate.set()
        await asyncio.wait_for(task, 1)
        await asyncio.wait_for(extraction_done.wait(), 1)
        assert extracted and extracted[0]["flags"] == ["self_listed"]
        assert "张三" not in dump(extracted)
        assert instance.reader.cursor(GROUP) and context.get("screen_open_requests") == {}
    finally:
        gate.set()
        await service.jobs.close()
        service.plugins.instances.clear()


async def test_daily_limit_is_durable_and_resets_next_day(service):
    reader, capture = reader_for(
        service,
        preview_document(),
        preview_only=True,
        open_on_demand=[GROUP],
        max_opens_per_chat_per_day=1,
    )
    reader._now = lambda: NOW
    await read(reader)
    # A new reader/process still sees the same reservation, even without commit_fetch.
    fresh, _ = reader_for(
        service,
        preview_document(),
        preview_only=True,
        open_on_demand=[GROUP],
        max_opens_per_chat_per_day=1,
    )
    fresh._now = lambda: NOW
    assert fresh.request_open(GROUP)["reason"] == "screen_open_daily_limit"
    capture.operations.clear()
    await read(reader)
    assert capture.operations == [("preview",)]
    fresh._now = lambda: NOW + timedelta(days=1)
    assert fresh.request_open(GROUP)["status"] == "scheduled"


@pytest.mark.parametrize(
    ("code", "page", "refunded"),
    [
        ("screen_accessibility_denied", 0, True),
        ("screen_recording_denied", 0, True),
        ("screen_app_not_running", 0, True),
        # Page 0 already clicked the chat open.
        ("screen_accessibility_denied", 1, False),
        # The helper may have clicked before the window went away.
        ("screen_window_not_found", 0, False),
    ],
)
async def test_an_open_that_clicked_nothing_does_not_use_the_daily_limit(
    service, code, page, refunded
):
    # Long enough to need a second screen.
    items = [{"time": "19:00"}] + [{"from": "老师", "text": f"第{i}条通知"} for i in range(40)]
    reader, capture = reader_for(
        service,
        preview_document(chats={GROUP: items}),
        preview_only=True,
        open_on_demand=[GROUP],
        max_opens_per_chat_per_day=1,
        on_demand_pages=2,
    )
    capture_chat = capture.capture_chat

    async def fail(chat, at):
        if at == page:
            raise RuntimeError(code)
        return await capture_chat(chat, at)

    capture.capture_chat = fail
    await read(reader)
    assert service.store.runs()["items"][0]["error"] == code
    assert (reader.can_open(GROUP) is None) is refunded


@pytest.mark.parametrize("helper_rejected", [False, True])
async def test_wrong_title_never_recognizes_chat_or_advances_cursor(service, helper_rejected):
    reader, capture = reader_for(
        service,
        preview_document(),
        preview_only=True,
        open_on_demand=[GROUP],
        max_opens_per_chat_per_day=1,
    )
    capture.confirmed_chat = GROUP + "家长群"
    if helper_rejected:

        async def reject_title(chat, page):
            raise RuntimeError("screen_chat_not_open")

        capture.capture_chat = reject_title
    # A failed open is recorded, but the conversation-list previews still come through.
    assert [m.preview for m in await read(reader)] == [True]
    assert reader.recognizer.calls == ["fake/preview.png"]
    assert reader.cursor(GROUP) is None
    assert reader.can_open(GROUP) == "screen_open_daily_limit"
    assert capture.operations[-1] == ("preview",)
    assert service.store.runs()["items"][0]["error"] == "screen_chat_mismatch"


async def test_page_budget_does_not_skip_unreached_cursor(service):
    items = []
    for i in range(70):
        if i % 5 == 0:
            items.append({"time": f"19:{i // 5 * 4:02d}"})  # every screen shows a time
        items.append({"from": "老师", "text": f"第{i}条通知"})
    reader, capture = reader_for(
        service,
        preview_document(chats={GROUP: items}),
        preview_only=True,
        open_on_demand=[GROUP],
        on_demand_pages=1,
    )
    cursor = {"anchor": "unreached", "sent_at": "2026-10-15T18:00:00+08:00"}
    reader.context.put(f"screen_cursor:{GROUP}", cursor)
    # The newest screen is kept; the cursor stays so a full read can still fill the gap.
    messages = await read(reader)
    assert any(not m.preview for m in messages)
    assert len([op for op in capture.operations if op[0] == "capture_chat"]) == 1
    await reader.commit_fetch()
    assert reader.cursor(GROUP) == cursor


async def test_full_mode_reads_every_chat_and_meets_requests(service):
    reader, capture = reader_for(
        service, preview_document(), open_on_demand=[GROUP], max_opens_per_chat_per_day=0
    )
    reader.context.put("screen_open_requests", {GROUP: "token"})
    messages = await read(reader)
    assert messages and not any(m.preview for m in messages)
    await reader.commit_fetch()
    assert reader.context.get("screen_open_requests") == {}


async def test_park_chat_after_an_on_demand_read(service):
    from plugins.screen.plugin_base import validate_config

    quiet = "安静的群"
    chats = {
        GROUP: [{"time": "20:38"}, {"from": "老师", "text": "未交名单：张三、李四"}],
        quiet: [{"time": "20:00"}, {"from": "甲", "text": "嗯"}],
    }
    reader, capture = reader_for(
        service,
        preview_document(chats=chats),
        preview_only=True,
        open_on_demand=[GROUP],
        park_chat=quiet,
    )
    await read(reader)
    opened = [op[1] for op in capture.operations if op[0] == "capture_chat"]
    assert opened[0] == GROUP and opened[-1] == quiet
    assert capture.operations[-1] == ("preview",)
    with pytest.raises(ValueError, match="screen_park_not_whitelisted"):
        validate_config({"whitelist": [GROUP], "park_chat": quiet})


@pytest.mark.parametrize(
    ("plugin", "cls"), [("qq_screen", QQPlugin), ("wechat_screen", WechatPlugin)]
)
async def test_mcp_requests_are_authorized_coalesced_and_executed_by_reader(service, plugin, cls):
    context = PluginContext(service.store, service.settings.data_dir, plugin)
    instance = cls()
    with pytest.raises(ValueError, match="screen_open_not_whitelisted"):
        await instance.setup(context, {"open_on_demand": [GROUP]})
    await instance.setup(
        context,
        {"helper": FAKE_HELPER, "whitelist": [GROUP, "other"], "open_on_demand": [GROUP]},
    )
    assert instance.config["self_names"] == [] and instance.config["on_demand_pages"] == 2
    assert instance.config["max_opens_per_chat_per_day"] == 4
    instance.reader, capture = reader_for(
        service,
        preview_document("一般消息"),
        plugin=plugin,
        preview_only=True,
        open_on_demand=[GROUP],
        max_opens_per_chat_per_day=1,
    )
    service.plugins.instances[plugin] = instance
    mcp = create_mcp(service)

    async def open_chat(**arguments):
        return await call_mcp(mcp, "screen_open_chat", arguments)

    result = await open_chat(plugin="feishu", chat_name=GROUP)
    assert result["reason"] == "screen_plugin_not_allowed"
    assert (await open_chat(plugin=plugin, chat_name="other"))["status"] == "denied"
    assert (await open_chat(plugin=plugin, chat_name=GROUP))["message"] == "已安排"
    token = instance.reader.context.get("screen_open_requests")[GROUP]
    assert (await open_chat(plugin=plugin, chat_name=GROUP))["status"] == "scheduled"
    assert instance.reader.context.get("screen_open_requests")[GROUP] == token
    assert not capture.operations  # request never calls a helper
    await read(instance.reader)
    await instance.commit_fetch()
    assert instance.reader.context.get("screen_open_requests") == {}
    assert (await open_chat(plugin=plugin, chat_name=GROUP))["reason"] == "screen_open_daily_limit"
    service.plugins.instances.clear()
    assert (await open_chat(plugin=plugin, chat_name=GROUP))["reason"] == "plugin_disabled"


async def test_mcp_flags_outgoing_and_redacted_excerpt(service):
    service.db.execute(
        "INSERT INTO plugins(id,config_json) VALUES(?,?)",
        ("qq_screen", dump({"self_names": ["张三", "20260012"]})),
    )
    raw = input_message(
        sender="张三", text="未交名单：张 三、20260012", flags=["self_listed"], outgoing=True
    )
    service.store.ingest("qq_screen", [raw], [GROUP])
    mcp = create_mcp(service)
    [row] = await call_mcp(mcp, "messages_get_unprocessed", {})
    assert row["flags"] == ["self_listed"] and row["outgoing"] is True
    assert row["text"] == "未交名单：[本人]、[本人]"
    assert "20260012" not in dump(row) and "张 三" not in dump(row)
    assert "screenshot_path" not in row
    todo = TodoCreate(
        title="提交材料",
        source_plugin="qq_screen",
        source_refs=[row["id"]],
        source_excerpt=row["text"],
        confidence=1,
    )
    added = await call_mcp(mcp, "todos_add", {"todo": todo.model_dump(mode="json")})
    assert added["todo"]["sources"][0]["sender"] == "[本人]"
    listed = await call_mcp(mcp, "todos_list", {})
    assert "张三" not in dump(listed) and "20260012" not in dump(listed)
    assert service.db.rows("SELECT text FROM messages")[0]["text"] == raw.text


async def test_invalid_subset_rejected_before_disabling_plugin(service):
    await service.start()
    try:
        with pytest.raises(ValueError, match="screen_open_not_whitelisted"):
            await service.plugins.patch(
                "qq_screen", PluginPatch(config={"whitelist": [], "open_on_demand": [GROUP]})
            )
        assert service.plugins.view("qq_screen")["config"] == {}
    finally:
        await service.close()


def test_extraction_prompt_renders_new_rules():
    prompt = render("extract_todos", local_now="NOW", timezone="TZ", confidence_threshold=0.8)
    assert "{local_now}" not in prompt and "NOW" in prompt
    for rule in (
        "self_listed",
        "self_missing",
        "self_joined",
        "outgoing",
        "screen_open_chat",
        "名单上有你",
    ):
        assert rule in prompt


async def call_mcp(mcp, name, arguments):
    result = await mcp.call_tool(name, arguments)
    if isinstance(result, tuple):
        return result[1].get("result", result[1])
    return json.loads(result[0].text)
