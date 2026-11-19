"""Replay the helper JSON protocol. No screenshot files, windows or real helper processes."""

import asyncio
import ctypes
import json
import plistlib
from datetime import timedelta

import pytest

from core.models import Card, MessageInput
from core.plugins import PluginContext
from plugins.qq_screen.plugin import Plugin as QQPlugin
from plugins.screen import helper, session
from plugins.screen.helper import HelperScreenCapture, HelperTextRecognizer, JsonHelper
from plugins.screen.layout import QQ_LAYOUT, WECHAT_LAYOUT
from plugins.screen.plugin_base import validate_config
from plugins.screen.reader import ChatScreenReader, whitelisted
from plugins.screen.session import DesktopState, console_state
from plugins.wechat_screen.plugin import Plugin as WechatPlugin
from tests.screen_render import block
from tests.test_doctor import FakeProbe, by_name
from tests.test_screen import GROUP, NOW

OTHER = "另一个授权群"
CURSOR = {"anchor": "unreached", "sent_at": "2026-10-15T08:00:00+08:00"}


def frame(text="请在周五前交报告", time="20:30", **values):
    return {
        "blocks": [
            block(time, 0.45, 0.1, 0.02),
            block("老师", 0.1, 0.2, 0.015),
            block(text, 0.12, 0.24, 0.025),
        ],
        **values,
    }


def preview(*chats):
    blocks = []
    for index, chat in enumerate(chats):
        y = 0.05 + index * 0.2
        blocks.extend(
            [
                block(chat, 0.1, y, 0.016),
                block("20:41", 0.88, y + 0.002, 0.012, char_w=0.016),
                block("@全体成员 请周五前交报告…", 0.1, y + 0.03, 0.014),
            ]
        )
    return {"blocks": blocks}


@pytest.fixture(params=[("qq_screen", QQ_LAYOUT), ("wechat_screen", WECHAT_LAYOUT)])
def protocol(service, monkeypatch, request, tmp_path):
    plugin, layout = request.param
    config = {"whitelist": [GROUP, OTHER], "preview_only": True, "open_on_demand": [GROUP, OTHER]}
    data = {
        "preview": preview(GROUP, OTHER),
        "chats": {GROUP: [frame(at_top=True)], OTHER: [frame(at_top=True)]},
        "calls": [],
        "desktop": DesktopState(),
        "cleanup_error": None,
    }
    for item in data["preview"]["blocks"]:
        item["text"] = item["text"].replace("@全体成员", layout.everyone_markers[0])
    blocks_by_path = {}

    async def desktop():
        return data["desktop"]

    async def transport(argv, raw, timeout):
        # The only permitted executable is a nonexistent synthetic path, intercepted here.
        assert argv == [str(tmp_path / "fake-helper")]
        payload = json.loads(raw)
        data["calls"].append(payload)
        action = payload["action"]
        if action == "recognize":
            value = blocks_by_path[payload["path"]]
            if isinstance(value, str):
                return 1, json.dumps({"error": value}).encode()
            return 0, json.dumps({"blocks": value}).encode()
        if action == "preview":
            if len([c for c in data["calls"] if c["action"] == "preview"]) > 1:
                if data["cleanup_error"]:
                    return 1, json.dumps({"error": data["cleanup_error"]}).encode()
            item = data["preview"]
        elif action == "capture_chat":
            frames = data["chats"][payload["chat"]]
            item = frames[min(payload["page"], len(frames) - 1)]
        elif action == "capture_announcement":
            return 0, b'{"screenshot": null}'
        else:
            pytest.fail("unexpected helper action")
        if item.get("desktop_after"):
            data["desktop"] = item["desktop_after"]
        if item.get("error"):
            return 1, json.dumps({"error": item["error"]}).encode()
        path = f"/synthetic/{len(data['calls'])}.png"
        blocks_by_path[path] = item.get("ocr_error", item.get("blocks", []))
        return 0, json.dumps(
            {
                "path": path,
                "captured_at": NOW.isoformat(),
                "chat": item.get("chat", payload.get("chat")),
                "at_top": item.get("at_top", False),
            }
        ).encode()

    monkeypatch.setattr(helper, "desktop_state", desktop)
    monkeypatch.setattr(helper.helper_process, "run", transport)
    monkeypatch.setattr("sys.platform", "darwin")
    json_helper = JsonHelper(tmp_path / "fake-helper", app=layout.app)
    context = PluginContext(service.store, service.settings.data_dir, plugin)
    reader = ChatScreenReader(
        HelperScreenCapture(json_helper), HelperTextRecognizer(json_helper), layout, context, config
    )
    reader._now = lambda: NOW
    return reader, data


def operations(data):
    return [(c["chat"], c["page"]) for c in data["calls"] if c["action"] == "capture_chat"]


def ingest(reader, messages):
    return reader.context.store.ingest(
        reader.context.plugin_id,
        [MessageInput.model_validate(m) for m in messages],
        reader.config["whitelist"],
    )


async def test_disconnected_pages_save_both_sides_without_inventing_a_bridge(protocol):
    reader, data = protocol
    data["chats"][GROUP] = [frame("新消息", "20:30"), frame("较旧消息", "19:00")]
    reader.context.put(f"screen_cursor:{GROUP}", CURSOR)
    reader.context.put("screen_open_requests", {GROUP: "request"})
    batch = await reader.fetch()
    assert {m["text"] for m in batch if not m.get("preview") and m["chat_name"] == GROUP} == {
        "新消息",
        "较旧消息",
    }
    assert reader.outcomes[GROUP]["state"] == "partial"
    assert reader.outcomes[OTHER]["state"] == "full"
    assert reader.context.get(f"screen_gap:{GROUP}")["error"] == "screen_history_gap"
    first_count = ingest(reader, batch)
    await reader.commit_fetch()
    assert reader.cursor(GROUP) == CURSOR
    assert reader.context.get("screen_open_requests") == {GROUP: "request"}
    assert reader.context.get(f"screen_open_hint:{GROUP}") is None
    assert reader._budget(GROUP)["count"] == 1
    # Repeated partial results are deduplicated; a later contiguous read clears the gap.
    assert ingest(reader, batch) == 0 and first_count > 0
    reader._now = lambda: NOW + timedelta(hours=1)
    data["chats"][GROUP] = [frame("新消息", "20:30", at_top=True)]
    batch = await reader.fetch()
    ingest(reader, batch)
    await reader.commit_fetch()
    assert reader.context.get(f"screen_gap:{GROUP}") is None
    assert reader.cursor(GROUP) != CURSOR
    assert not reader.context.get("screen_open_requests")


@pytest.mark.parametrize(
    "failure",
    [
        "screen_locked",
        "screen_chat_not_open",
        "screen_helper_timeout",
        "screen_empty_capture",
        "screen_recording_denied",
    ],
)
async def test_later_page_failure_keeps_confirmed_content(protocol, failure):
    reader, data = protocol
    second = {"blocks": []} if failure == "screen_empty_capture" else {"error": failure}
    data["chats"][GROUP] = [frame(), second]
    reader.context.put(f"screen_cursor:{GROUP}", CURSOR)
    batch = await reader.fetch()
    assert any(m["text"] == "请在周五前交报告" and not m.get("preview") for m in batch)
    code = "screen_chat_mismatch" if failure == "screen_chat_not_open" else failure
    assert reader.outcomes[GROUP]["error"] == code
    assert reader._budget(GROUP)["count"] == 1
    ingest(reader, batch)
    await reader.commit_fetch()
    assert reader.cursor(GROUP) == CURSOR
    if failure == "screen_locked":
        assert operations(data) == [(GROUP, 0), (GROUP, 1)]
        assert len([c for c in data["calls"] if c["action"] == "preview"]) == 1
    else:
        assert (OTHER, 0) in operations(data)


async def test_wrong_title_on_later_page_is_never_recognized(protocol):
    reader, data = protocol
    data["chats"][GROUP] = [frame(), frame("不该读取的模拟内容", chat=GROUP + "家长群")]
    reader.context.put(f"screen_cursor:{GROUP}", CURSOR)
    batch = await reader.fetch()
    assert "不该读取" not in json.dumps(batch, ensure_ascii=False, default=str)
    assert reader.outcomes[GROUP]["error"] == "screen_chat_mismatch"
    index = next(
        i
        for i, c in enumerate(data["calls"])
        if c["action"] == "capture_chat" and c["chat"] == GROUP and c["page"] == 1
    )
    assert data["calls"][index + 1]["action"] == "preview"


@pytest.mark.parametrize(
    "failure",
    [
        "screen_chat_not_in_list",
        "screen_chat_not_open",
        "screen_window_obscured",
        "screen_capture_failed",
    ],
)
async def test_zero_message_failures_have_independent_durable_backoff(protocol, failure):
    reader, data = protocol
    reader.config.update(max_opens_per_chat_per_day=1, max_failed_opens_per_chat_per_day=2)
    data["chats"][GROUP] = [{"error": failure}]
    await reader.fetch()
    assert reader._budget(GROUP)["count"] == 0
    assert reader.can_open(GROUP) == "screen_open_backoff"
    # Reconstruct the reader (a backend restart), preserving only plugin storage.
    fresh = ChatScreenReader(
        reader.capture, reader.recognizer, reader.layout, reader.context, reader.config
    )
    fresh._now = lambda: NOW + timedelta(minutes=59)
    assert fresh.request_open(GROUP)["reason"] == "screen_open_backoff"
    fresh._now = lambda: NOW + timedelta(hours=1)
    assert fresh.can_open(GROUP) is None
    await fresh.fetch()
    assert fresh.can_open(GROUP) == "screen_open_retry_limit"
    assert fresh._budget(GROUP)["count"] == 0
    assert fresh._retry(GROUP)["next_at"] == (NOW + timedelta(hours=3)).isoformat()
    fresh._now = lambda: NOW + timedelta(days=1)
    assert fresh.can_open(GROUP) is None


@pytest.mark.parametrize(
    "desktop",
    [
        DesktopState(locked=True),
        DesktopState(login_done=False),
        DesktopState(on_console=False),
        DesktopState(display_asleep=True),
    ],
)
async def test_unavailable_desktop_skips_whole_round_without_helper_or_quota(protocol, desktop):
    reader, data = protocol
    data["desktop"] = desktop
    with pytest.raises(RuntimeError, match=desktop.code):
        await reader.fetch()
    assert not data["calls"]
    assert all(
        reader._budget(chat)["count"] == 0 and reader._retry(chat)["attempts"] == 0
        for chat in reader.config["whitelist"]
    )
    assert reader.context.get("screen_reading")["error"] == desktop.code


async def test_cleanup_error_does_not_discard_read_content(protocol):
    reader, data = protocol
    data["cleanup_error"] = "screen_capture_failed"
    batch = await reader.fetch()
    assert sum(not m.get("preview") for m in batch) == 2
    assert reader.outcomes[GROUP]["cleanup_error"] == "screen_capture_failed"
    assert ingest(reader, batch) > 0


async def test_full_mode_isolates_chat_failures_and_stops_after_mid_round_lock(protocol):
    reader, data = protocol
    reader.config["preview_only"] = False
    data["chats"][GROUP] = [frame(at_top=True)]
    data["chats"][OTHER] = [{"error": "screen_locked"}]
    third = "不应点开的后续群"
    reader.config["whitelist"].append(third)
    batch = await reader.fetch()
    assert batch and {m["chat_name"] for m in batch} == {GROUP}
    assert operations(data) == [(GROUP, 0), (OTHER, 0)]
    assert reader.outcomes[third]["state"] == "missing"
    assert ingest(reader, batch) > 0
    await reader.commit_fetch()
    assert reader.cursor(GROUP)
    assert not reader.cursor(OTHER)


async def test_cards_distinguish_partial_preview_failed_and_today_without_raw_text(protocol):
    reader, data = protocol
    data["chats"][GROUP] = [{"error": "screen_chat_not_in_list"}]
    instance = QQPlugin() if reader.context.plugin_id == "qq_screen" else WechatPlugin()
    instance.context, instance.config, instance.reader = reader.context, reader.config, reader
    batch = await reader.fetch()
    ingest(reader, batch)
    await reader.commit_fetch()
    cards = [Card.model_validate(c) for c in await instance.cards()]
    text = json.dumps([c.model_dump() for c in cards], ensure_ascii=False)
    assert "全文未读全" in text and "全文 1 个" in text and "点开失败 1 个" in text
    # In words, not as a code, and still without any chat text.
    assert "会话列表里没找到" in text and "screen_chat_not_in_list" not in text
    assert "@全体成员" not in text
    history = reader.context.get("screen_reading_history")
    assert history[-1]["committed"] and "blocks" not in json.dumps(history)
    assert reader.context.get("screen_reading")["committed"]
    reader._now = lambda: NOW + timedelta(days=1)
    assert "今天还没有读取结果" in json.dumps(await instance.cards(), ensure_ascii=False)


async def test_never_parks_in_a_chat_without_on_demand_authorization(protocol):
    reader, data = protocol
    reader.config.update(open_on_demand=[GROUP], park_chat=OTHER)
    await reader.fetch()
    assert operations(data) == [(GROUP, 0)]
    with pytest.raises(ValueError, match="screen_park_not_allowed"):
        validate_config(reader.config)


@pytest.mark.parametrize("name", [" 软件 工程２班 ", "《软件工程2班》", "软件工程2班👩‍💻️"])
def test_name_equivalence_is_bounded_to_whole_name(name):
    assert whitelisted(name, [GROUP]) == GROUP
    assert whitelisted(name + "家长群", [GROUP]) is None
    assert whitelisted(name + "…", [GROUP]) is None
    assert whitelisted("软件工程3班", [GROUP]) is None
    assert whitelisted("软件工程2班", [GROUP, "软件工程 2 班"]) is None
    with pytest.raises(ValueError, match="screen_whitelist_ambiguous"):
        validate_config({"whitelist": [GROUP, "软件工程 2 班"]})


def user(uid=501, **values):
    return {
        "kCGSSessionUserIDKey": uid,
        "kCGSSessionOnConsoleKey": True,
        "kCGSessionLoginDoneKey": True,
        "CGSSessionScreenIsLocked": False,
        **values,
    }


@pytest.mark.parametrize(
    "users, root, code",
    [
        (
            [user(), user(502, kCGSSessionOnConsoleKey=False, CGSSessionScreenIsLocked=True)],
            {},
            None,
        ),
        (
            [user(kCGSSessionOnConsoleKey=False, CGSSessionScreenIsLocked=True), user(502)],
            {},
            "screen_session_inactive",
        ),
        ([user(CGSSessionScreenIsLocked=True)], {}, "screen_locked"),
        ([user()], {"IOConsoleLocked": True}, "screen_locked"),
        ([user(0, kCGSessionLoginDoneKey=False)], {}, "screen_login_window"),
        ([user(502)], {}, "screen_session_inactive"),
        ([user(CGSSessionScreenIsLocked="false")], {"IOConsoleLocked": "false"}, None),
    ],
)
def test_console_probe_separates_active_user_lock_login_and_background_sessions(users, root, code):
    state = console_state(plistlib.dumps({**root, "IOConsoleUsers": users}), uid=501)
    assert state.code == code
    assert "501" not in json.dumps(state.details())


@pytest.mark.parametrize("output", [b"not plist", b"", plistlib.dumps([]), plistlib.dumps(["bad"])])
def test_unreadable_console_is_unknown_instead_of_locked(output):
    assert console_state(output, uid=501).code is None


@pytest.mark.parametrize(
    "powers, error, expected",
    [([0, 0], 0, False), ([1, 0], 0, False), ([1, 1], 0, True), ([], 0, None), ([1], 1, None)],
)
def test_display_sleep_uses_all_online_displays_without_helper(
    monkeypatch, powers, error, expected
):
    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    class CoreGraphics:
        def __init__(self):
            def online(limit, displays, count):
                ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = len(powers)
                for i in range(len(powers)):
                    displays[i] = i
                return error

            self.CGGetOnlineDisplayList = Function(online)
            self.CGDisplayIsAsleep = Function(lambda display: powers[display])

    monkeypatch.setattr(session.sys, "platform", "darwin")
    monkeypatch.setattr(session.ctypes, "CDLL", lambda _: CoreGraphics())
    assert session.displays_asleep() is expected


@pytest.mark.parametrize(
    "state, asleep, expected",
    [
        (user(), False, "ok"),
        (user(CGSSessionScreenIsLocked=True), False, "fail"),
        (user(kCGSessionLoginDoneKey=False), False, "fail"),
        (user(kCGSSessionOnConsoleKey=False), False, "fail"),
        (user(), True, "ok"),
        ({}, None, "manual"),
    ],
)
def test_doctor_policy_off_does_not_override_actual_desktop_state(state, asleep, expected):
    from scripts.doctor import Report, Result, check_system

    probe = FakeProbe()
    probe.commands[tuple(session.CONSOLE_COMMAND)] = Result(
        0, plistlib.dumps({"IOConsoleUsers": [state]}).decode()
    )
    probe.displays_asleep = lambda: asleep
    report = Report(probe)
    check_system(report)
    checks = by_name({"checks": [c.as_dict() for c in report.checks]})
    assert checks["lock_screen"]["status"] == "ok"
    assert checks["lock_screen"]["fix"] == ""
    assert checks["desktop_session"]["status"] == expected
    assert checks["display_power"]["status"] == (
        "manual" if asleep is None else "fail" if asleep else "ok"
    )
    if state.get("CGSSessionScreenIsLocked"):
        assert "解锁" in checks["desktop_session"]["fix"]
        assert "永不" not in checks["desktop_session"]["fix"]


async def test_a_retry_an_hour_out_is_due_at_the_round_an_hour_later(protocol, monkeypatch):
    # Rounds run every 30 minutes and a chat may be opened minutes into one, after others.
    # Its retry an hour out belongs to the round an hour after that round, not the one half
    # an hour later still (2026-11-14: every QQ retry waited 90 minutes).
    reader, data = protocol
    data["chats"][GROUP] = [{"error": "screen_chat_not_in_list"}]
    clock = {"now": NOW}
    reader._now = lambda: clock["now"]
    capture_chat = reader.capture.capture_chat

    async def minutes_in(chat, page):
        clock["now"] = NOW + timedelta(minutes=2, seconds=15)
        return await capture_chat(chat, page)

    monkeypatch.setattr(reader.capture, "capture_chat", minutes_in)
    await reader.fetch()
    assert reader.can_open(GROUP) == "screen_open_backoff"
    clock["now"] = NOW + timedelta(minutes=59)
    assert reader.can_open(GROUP) == "screen_open_backoff"
    # The round an hour later began a moment early by the millisecond clock.
    clock["now"] = NOW + timedelta(minutes=59, seconds=59, milliseconds=990)
    assert reader.can_open(GROUP) is None


async def test_cancelled_open_is_bounded_without_spending_success_quota(protocol, monkeypatch):
    reader, data = protocol

    async def cancelled(*_):
        raise asyncio.CancelledError()

    monkeypatch.setattr(reader.capture, "capture_chat", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await reader.fetch()
    assert reader._budget(GROUP)["count"] == 0
    assert reader.can_open(GROUP) == "screen_open_backoff"


async def test_round_deadline_returns_partial_batch_before_outer_cancellation(
    protocol, monkeypatch
):
    from plugins.screen import reader as module

    reader, data = protocol
    reader.context.put(f"screen_cursor:{GROUP}", CURSOR)
    original = reader.capture.capture_chat

    async def slow(chat, page):
        if page == 1:
            await asyncio.sleep(10)
        return await original(chat, page)

    monkeypatch.setattr(module, "ROUND_TIMEOUT", 0.05)
    monkeypatch.setattr(reader.capture, "capture_chat", slow)
    data["chats"][GROUP] = [frame()]
    batch = await reader.fetch()
    assert any(not m.get("preview") for m in batch)
    assert reader.outcomes[GROUP]["error"] == "screen_read_timeout"
    assert operations(data) == [(GROUP, 0)]
    assert reader.outcomes[OTHER]["error"] == "screen_read_timeout"
    ingest(reader, batch)
    await reader.commit_fetch()
    assert reader.cursor(GROUP) == CURSOR


async def test_lock_after_first_capture_is_caught_before_next_helper_process(protocol):
    reader, data = protocol
    data["chats"][GROUP] = [frame(desktop_after=DesktopState(locked=True))]
    reader.context.put(f"screen_cursor:{GROUP}", CURSOR)
    batch = await reader.fetch()
    assert any(not m.get("preview") for m in batch)
    assert operations(data) == [(GROUP, 0)]
    assert reader.outcomes[GROUP]["error"] == "screen_locked"
    assert reader.outcomes[OTHER]["error"] == "screen_locked"


async def test_old_date_partial_gap_retries_even_after_some_messages_are_stored(protocol):
    reader, data = protocol
    for b in data["preview"]["blocks"]:
        if b["text"] == "20:41":
            b["text"] = "星期二"
    data["chats"][GROUP] = [frame("新消息", "20:30"), frame("较旧消息", "19:00")]
    reader.context.put(f"screen_cursor:{GROUP}", CURSOR)
    batch = await reader.fetch()
    ingest(reader, batch)
    await reader.commit_fetch()
    assert reader.outcomes[GROUP]["state"] == "partial"
    reader._now = lambda: NOW + timedelta(hours=1)
    data["calls"].clear()
    await reader.fetch()
    assert (GROUP, 0) in operations(data)
    assert ":missed:" in reader.context.store.runs()["items"][0]["job"]


async def test_retained_gap_remains_visible_in_a_later_preview_round(protocol):
    reader, data = protocol
    data["chats"][GROUP] = [frame(), frame("较旧消息", "19:00")]
    reader.context.put(f"screen_cursor:{GROUP}", CURSOR)
    batch = await reader.fetch()
    ingest(reader, batch)
    await reader.commit_fetch()
    await reader.fetch()  # Still in backoff; previews alone cannot clear a saved gap.
    assert reader.outcomes[GROUP]["state"] == "preview"
    assert reader.context.get("screen_reading")["chats"][GROUP]["gap"] == "screen_history_gap"


async def test_unfinished_attempt_reservations_are_bounded_after_restarts(protocol):
    reader, _ = protocol
    state = reader._retry(GROUP)
    state["attempts"] = reader.config.get("max_opens_per_chat_per_day", 4) + 4
    reader.context.put(f"screen_open_retry:{GROUP}", state)
    assert reader.can_open(GROUP) == "screen_open_retry_limit"
    assert reader._budget(GROUP)["count"] == 0


def test_explicitly_empty_console_users_means_login_window():
    assert console_state(plistlib.dumps({"IOConsoleUsers": []}), 501).code == "screen_login_window"


def test_duplicate_uid_prefers_foreground_session_over_stale_locked_entry():
    users = [user(kCGSSessionOnConsoleKey=False, CGSSessionScreenIsLocked=True), user()]
    state = console_state(plistlib.dumps({"IOConsoleUsers": users}), 501)
    assert state.code is None and state.on_console and state.locked is False


async def test_all_full_cards_require_commit_and_gaps_clear_only_after_ingestion(protocol):
    reader, _ = protocol
    instance = QQPlugin() if reader.context.plugin_id == "qq_screen" else WechatPlugin()
    instance.context, instance.config, instance.reader = reader.context, reader.config, reader
    reader.context.put(f"screen_gap:{GROUP}", {"error": "screen_history_gap"})
    batch = await reader.fetch()
    assert all(o["state"] == "full" for o in reader.outcomes.values())
    assert "本轮授权范围已读全" not in json.dumps(await instance.cards(), ensure_ascii=False)
    assert reader.context.get(f"screen_gap:{GROUP}")
    ingest(reader, batch)
    await reader.commit_fetch()
    assert not reader.context.get(f"screen_gap:{GROUP}")
    assert "本轮授权范围已读全" in json.dumps(await instance.cards(), ensure_ascii=False)


async def test_unlisted_parking_target_is_not_clicked_even_if_on_demand_config_is_stale(protocol):
    reader, data = protocol
    reader.config.update(whitelist=[GROUP], open_on_demand=[GROUP, OTHER], park_chat=OTHER)
    await reader.fetch()
    assert operations(data) == [(GROUP, 0)]


@pytest.mark.parametrize("raw", [b'{"error": ["private"]}', b'{"error": null}', b"not-json"])
async def test_malformed_helper_error_never_exposes_arbitrary_output(protocol, monkeypatch, raw):
    reader, _ = protocol

    async def failed(*_args, **_kwargs):
        return 1, raw

    monkeypatch.setattr(helper.helper_process, "run", failed)
    with pytest.raises(RuntimeError, match="^screen_helper_failed$"):
        await reader.capture.preview()


async def test_timeout_is_reclassified_if_desktop_disappears(protocol, monkeypatch):
    reader, data = protocol

    async def timed_out(*_args, **_kwargs):
        data["desktop"] = DesktopState(on_console=False)
        raise TimeoutError()

    monkeypatch.setattr(helper.helper_process, "run", timed_out)
    with pytest.raises(RuntimeError, match="^screen_session_inactive$"):
        await reader.capture.preview()


async def test_partial_full_text_is_not_counted_as_preview_only(protocol):
    reader, data = protocol
    data["chats"][GROUP] = [frame(), frame("较旧消息", "19:00")]
    reader.context.put(f"screen_cursor:{GROUP}", CURSOR)
    await reader.fetch()
    instance = QQPlugin() if reader.context.plugin_id == "qq_screen" else WechatPlugin()
    instance.context, instance.config, instance.reader = reader.context, reader.config, reader
    text = json.dumps(await instance.cards(), ensure_ascii=False)
    assert "部分全文 1 个" in text and "仅有预览 0 个" in text
    assert "本轮尚未确认入库" in text
    assert "已保存部分全文" not in text


def test_the_coverage_card_says_codes_in_words():
    from plugins.screen.plugin_base import READING_WORDS, reading_words
    from plugins.screen.session import DESKTOP_ERRORS

    assert reading_words("screen_chat_mismatch") == "打开的不是这个会话"
    # Every desktop error the round can stop on has words, and none is another code.
    assert DESKTOP_ERRORS <= set(READING_WORDS)
    assert not any(words.startswith("screen_") for words in READING_WORDS.values())
    # A code nobody wrote words for is shown rather than hidden.
    assert reading_words("screen_something_new") == "screen_something_new"
