"""Regression cases from the screen reader review. All screenshots and chats are synthetic."""

import asyncio
from datetime import timedelta

import pytest

from plugins.screen import pixels
from plugins.screen.chat import ScreenGapError, stitch
from tests.screen_render import block, document
from tests.test_screen import (
    GROUP,
    NOW,
    placed_lines,
    read,
    reader_for,
    write_bmp,
)
from tests.test_screen_signals import preview_document


@pytest.mark.parametrize("duplicate_body", ["[草稿]私人草稿", None])
async def test_all_visible_headers_disambiguate_preview(service, duplicate_body):
    doc = preview_document("老师：请查收附件")
    blocks = doc["preview"]["blocks"]
    blocks.extend([block(GROUP, 0.2, 0.3, 0.016), block("昨天", 0.91, 0.3, 0.015)])
    if duplicate_body:
        blocks.append(block(duplicate_body, 0.2, 0.33, 0.016))
    reader, capture = reader_for(service, doc, preview_only=True, open_on_demand=[GROUP])
    reader.request_open(GROUP)
    assert await read(reader) == []
    assert capture.operations == [("preview",)]
    assert reader.context.get("ambiguous_chats") == [GROUP]


@pytest.mark.parametrize(
    "text, expected",
    [("[文件]", "[文件]"), ("[作业]明早截止", "[作业]明早截止"), ("[草稿]明早截止", None)],
)
async def test_preview_keeps_content_tags_and_recognizes_draft_only_list(service, text, expected):
    reader, _ = reader_for(service, preview_document(text), preview_only=True)
    assert [m.text for m in await read(reader)] == ([expected] if expected else [])
    assert not reader.context.get("not_conversation_list")


@pytest.mark.parametrize("repeated", [["收到"], ["收到", "收到"]])
def test_repeated_reply_alone_cannot_bridge_pages(repeated):
    new = placed_lines([(text, 0.1 + i * 0.05) for i, text in enumerate([*repeated, "新通知"])])
    old = placed_lines(
        [("旧通知", 0.55), *[(text, 0.75 + i * 0.05) for i, text in enumerate(repeated)]]
    )
    with pytest.raises(ScreenGapError):
        stitch(new, old, scroll=0.65)


@pytest.mark.parametrize("changed", [False, True])
def test_reverse_scroll_is_not_a_text_overlap(changed):
    new = placed_lines([("张老师", 0.55), ("请于周五前交实验报告", 0.60), ("好的", 0.65)])
    old = placed_lines(
        [
            ("张老师", 0.15),
            ("请于周五前交实验报吿" if changed else "请于周五前交实验报告", 0.20),
            ("好的", 0.25),
        ]
    )
    with pytest.raises(ScreenGapError):
        stitch(new, old, scroll=0.65)


async def test_unresolved_message_inside_read_range_keeps_anchor(service):
    items = [
        {"time": "20:30"},
        {"from": "甲", "text": "已入库的消息"},
        {"time": "28:13"},
        {"from": "乙", "text": "丢失时间的通知"},
        {"time": "20:40"},
        {"from": "甲", "text": "之后的通知"},
    ]
    reader, _ = reader_for(service, document({GROUP: items[:2]}))
    service.store.ingest("qq_screen", await read(reader), [GROUP])
    await reader.commit_fetch()
    anchor = reader.cursor(GROUP)
    reader, _ = reader_for(service, document({GROUP: items}))
    batch = await read(reader)
    assert "之后的通知" in [m.text for m in batch]
    service.store.ingest("qq_screen", batch, [GROUP])
    await reader.commit_fetch()
    assert reader.cursor(GROUP) == anchor
    assert reader.outcomes[GROUP]["state"] == "partial"


async def test_older_only_view_cannot_replace_cursor_with_an_unstored_anchor(service):
    reader, _ = reader_for(
        service, document({GROUP: [{"time": "19:00"}, {"from": "甲", "text": "更旧的消息"}]})
    )
    anchor = {"anchor": "qq:latest", "sent_at": "2026-10-15T20:00:00+08:00"}
    reader.context.put(f"screen_cursor:{GROUP}", anchor)
    batch = await read(reader)
    assert [m.text for m in batch] == ["更旧的消息"]
    service.store.ingest("qq_screen", batch, [GROUP])
    await reader.commit_fetch()
    assert reader.cursor(GROUP) == anchor
    assert reader.outcomes[GROUP]["error"] == "screen_history_gap"


@pytest.mark.parametrize("interruption", ["ocr_failure", "cancelled", "before_commit"])
async def test_unread_retry_survives_badge_clear_and_restart(service, monkeypatch, interruption):
    doc = preview_document("老师：普通消息")
    config = dict(preview_only=True, open_on_demand=[GROUP])
    reader, _ = reader_for(service, doc, **config)
    reader._now = lambda: NOW
    monkeypatch.setattr(pixels, "badge", lambda *_: True)
    recognize = reader.recognizer.recognize

    async def interrupted(shot):
        if shot.kind == "chat":
            if interruption == "cancelled":
                raise asyncio.CancelledError()
            if interruption == "ocr_failure":
                raise RuntimeError("screen_helper_failed")
        return await recognize(shot)

    monkeypatch.setattr(reader.recognizer, "recognize", interrupted)
    if interruption == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await reader.fetch()
    else:
        batch = await read(reader)
        if interruption == "ocr_failure":
            service.store.ingest("qq_screen", batch, [GROUP])
            await reader.commit_fetch()
    assert reader.cursor(GROUP) is None
    fresh, capture = reader_for(service, doc, **config)
    fresh._now = lambda: NOW + timedelta(hours=1)
    monkeypatch.setattr(pixels, "badge", lambda *_: False)
    recovered = await read(fresh)
    assert any(not m.preview for m in recovered)
    assert fresh._budget(GROUP)["count"] == 0
    service.store.ingest("qq_screen", recovered, [GROUP])
    await fresh.commit_fetch()
    capture.operations.clear()
    assert all(m.preview for m in await read(fresh))
    assert capture.operations == [("preview",)]


@pytest.mark.parametrize("signal", ["[@全体成员]请查收", "老师：普通消息"])
async def test_unread_budget_available_with_an_exhausted_general_reason(
    service, monkeypatch, signal
):
    reader, capture = reader_for(
        service,
        preview_document(signal),
        preview_only=True,
        open_on_demand=[GROUP],
        max_opens_per_chat_per_day=0,
    )
    if signal.startswith("老师"):
        reader.context.put("screen_open_requests", {GROUP: "queued"})
    monkeypatch.setattr(pixels, "badge", lambda *_: True)
    batch = await read(reader)
    assert any(not m.preview for m in batch)
    assert ("capture_chat", GROUP, 0) in capture.operations
    assert reader._budget(GROUP)["unread"] == 1
    assert reader._budget(GROUP)["count"] == 0
    service.store.ingest("qq_screen", batch, [GROUP])
    await reader.commit_fetch()
    assert not reader.context.get("screen_open_requests")


async def test_existing_unread_gap_obeys_setting_after_restart(service):
    reader, capture = reader_for(
        service,
        preview_document("普通消息"),
        preview_only=True,
        open_on_demand=[GROUP],
        open_on_unread=False,
    )
    reader.context.put(
        f"screen_gap:{GROUP}", {"error": "screen_history_gap", "open_reason": "unread"}
    )
    await reader.fetch()
    assert capture.operations == [("preview",)]


async def test_overnight_open_is_charged_to_attempt_day(service, monkeypatch):
    reader, capture = reader_for(
        service,
        preview_document("[@全体成员]请查收"),
        preview_only=True,
        open_on_demand=[GROUP],
        max_opens_per_chat_per_day=1,
    )
    moment = NOW.replace(hour=23, minute=59)
    reader._now = lambda: moment
    take = capture.capture_chat

    async def overnight(*args):
        nonlocal moment
        moment = moment.replace(hour=0, minute=1) + timedelta(days=1)
        return await take(*args)

    monkeypatch.setattr(capture, "capture_chat", overnight)
    await reader.fetch()
    assert reader.can_open(GROUP) is None
    assert reader.context.get(f"screen_opens:{GROUP}") == {
        "day": NOW.date().isoformat(),
        "count": 1,
    }


async def test_overnight_backoff_survives_daily_counter_reset(service):
    reader, _ = reader_for(service, preview_document(), open_on_demand=[GROUP])
    end = NOW.replace(hour=23, minute=58)
    reader.context.put(
        f"screen_open_retry:{GROUP}",
        {
            "day": end.date().isoformat(),
            "attempts": 4,
            "failures": 4,
            "next_at": (end + timedelta(hours=1)).isoformat(),
        },
    )
    reader._now = lambda: end + timedelta(minutes=3)
    assert reader._retry(GROUP)["attempts"] == 0
    assert reader.can_open(GROUP) == "screen_open_backoff"


def list_image(path, scale, selected, dark, badge=False):
    width, height = 240 * scale, 150 * scale
    base = (38, 38, 38) if dark else (249, 249, 249)
    highlight = (130, 80, 40) if selected else base
    red = (55, 55, 225)
    rows = []
    for y in range(height):
        yy = y // scale
        row = bytearray(bytes((*(highlight if 43 <= yy < 70 else base), 255)) * width)
        for x in range(width):
            xx = x // scale
            if 44 <= yy < 56 and (4 <= xx < 16 or 38 <= xx < 55):
                row[x * 4 : x * 4 + 3] = bytes(red)  # avatar decoration and inline @ tag
            if badge and (xx - 222) ** 2 + (yy - 53) ** 2 <= 7**2:
                row[x * 4 : x * 4 + 3] = bytes((155, 155, 155) if dark else red)
                if xx == 222 and 50 <= yy < 57:
                    row[x * 4 : x * 4 + 3] = bytes([250] * 3)
        rows.append(bytes(row))
    return write_bmp(path, rows, width)


# Retina only: QQ's list on this Mac is captured at 2x, where the detector was checked against
# real lists (2026-11-19: 869 rows, 114 badges incl. grey 1, 9 and 99+ pills after a cut-off
# preview); 1x badges are smaller than its 7 x 7 solid patches need.
@pytest.mark.parametrize("scale", [2])
@pytest.mark.parametrize("selected,dark", [(False, True), (True, True), (True, False)])
@pytest.mark.parametrize("has_badge", [False, True])
def test_badge_depends_on_a_local_pill(tmp_path, scale, selected, dark, has_badge):
    path = list_image(tmp_path / "list.bmp", scale, selected, dark, has_badge)
    assert pixels.badge(path, 48 / 150, 58 / 150) is has_badge


def test_static_banner_cannot_hide_a_scrolling_chat(tmp_path):
    width, height = 40, 200

    def shot(name, offset):
        rows = [
            (
                (y if y < 65 else (200 + y + offset if y % 5 == 0 else 0)).to_bytes(3, "little")
                + b"\xff"
            )
            * width
            for y in range(height)
        ]
        return write_bmp(tmp_path / name, rows, width)

    assert not pixels.unmoved(shot("current.bmp", 130), shot("previous.bmp", 0))


def test_chain_identity_cannot_span_two_numbered_people():
    from core.message_signals import message_flags

    assert message_flags("全班接龙\n1. 张\n2. 三", ["张三"]) == ["self_missing"]


async def test_a_new_badge_can_repeat_the_last_preview(service, monkeypatch):
    reader, capture = reader_for(
        service, preview_document("老师：收到"), preview_only=True, open_on_demand=[GROUP]
    )
    monkeypatch.setattr(pixels, "badge", lambda *_: True)
    service.store.ingest("qq_screen", await read(reader), [GROUP])
    await reader.commit_fetch()
    capture.operations.clear()
    await reader.fetch()
    assert ("capture_chat", GROUP, 0) in capture.operations


async def test_old_preview_already_in_store_can_still_have_unread_messages(service, monkeypatch):
    doc = preview_document("老师：收到")
    doc["preview"]["blocks"][1]["text"] = "昨天20:41"
    reader, capture = reader_for(service, doc, preview_only=True, open_on_demand=[GROUP])
    monkeypatch.setattr(pixels, "badge", lambda *_: False)
    service.store.ingest("qq_screen", await read(reader), [GROUP])
    await reader.commit_fetch()
    capture.operations.clear()
    monkeypatch.setattr(pixels, "badge", lambda *_: True)
    await reader.fetch()
    assert ("capture_chat", GROUP, 0) in capture.operations


async def test_empty_unread_reads_do_not_exhaust_the_agent_attempt_budget(service, monkeypatch):
    doc = preview_document("老师：收到")
    reader, _ = reader_for(
        service,
        doc,
        preview_only=True,
        open_on_demand=[GROUP],
        max_failed_opens_per_chat_per_day=1,
        max_opens_per_chat_per_day=1,
    )
    monkeypatch.setattr(pixels, "badge", lambda *_: True)
    for i in range(4):
        doc["preview"]["blocks"][1]["text"] = f"20:4{i}"
        service.store.ingest("qq_screen", await read(reader), [GROUP])
        await reader.commit_fetch()
    assert reader.request_open(GROUP)["status"] == "scheduled"


def test_midnight_stitch_matches_the_resolved_day():
    from plugins.screen.layout import QQ_LAYOUT, frame_lines
    from plugins.screen.model import TextBlock

    midnight = NOW.replace(hour=0, minute=1) + timedelta(days=1)

    def lines(reference, label, start):
        blocks = [
            TextBlock(label, 0.45, start, 0.12, 0.016),
            TextBlock("请查收", 0.1, start + 0.05, 0.1, 0.016),
        ]
        return frame_lines(blocks, QQ_LAYOUT, reference)

    new = lines(NOW.replace(hour=23, minute=59), "23:58", 0.1)
    old = lines(midnight, "昨天23:58", 0.75)
    merged, added = stitch(new, old, scroll=0.65)
    assert len(merged) == 2 and added == 0


@pytest.mark.parametrize("height", [0.012, 0.016])
async def test_hover_clock_on_a_sender_label_still_names_the_bubble(service, height):
    doc = document({GROUP: [{"time": "20:00"}, {"from": "甲", "text": "请查收"}]})
    rows = doc["chats"][GROUP]["frames"][0]["blocks"]
    rows[1].update(text="甲 18:30:00", h=height)
    rows[2]["y"] = rows[1]["y"] + height + 0.009
    reader, _ = reader_for(service, doc)
    assert [(m.sender, m.text) for m in await read(reader)] == [("甲", "请查收")]


@pytest.mark.parametrize("name", ["软件…工程2班", "软件．．．工程2班"])
async def test_middle_ellipsis_cannot_identify_a_whitelisted_chat(service, name):
    doc = preview_document("老师：私人通知")
    doc["preview"]["blocks"][0]["text"] = name
    reader, capture = reader_for(service, doc, preview_only=True, open_on_demand=[GROUP])
    reader.request_open(GROUP)
    assert await read(reader) == []
    assert capture.operations == [("preview",)]
    assert reader.context.get("cut_off_chats") == [name]
    reader.config["preview_only"] = False
    capture.confirmed_chat = name
    with pytest.raises(RuntimeError, match="screen_chat_mismatch"):
        await reader.fetch()
    assert reader.recognizer.calls == ["fake/preview.png"]


async def test_initial_read_keeps_gap_after_an_explicit_invalid_separator(service):
    doc = document(
        {
            GROUP: [
                {"time": "28:03"},
                {"from": "甲", "text": "需补读的通知"},
                {"time": "20:40"},
                {"from": "乙", "text": "最后一条"},
            ]
        }
    )
    reader, _ = reader_for(service, doc)
    batch = await read(reader)
    assert [m.text for m in batch] == ["最后一条"]
    service.store.ingest("qq_screen", batch, [GROUP])
    await reader.commit_fetch()
    assert reader.cursor(GROUP) is None
    assert reader.outcomes[GROUP]["error"] == "screen_history_gap"


async def test_exhausted_unread_budget_does_not_hide_missed_day(service, monkeypatch):
    doc = preview_document("请查收")
    doc["preview"]["blocks"][1]["text"] = "星期二"
    reader, _ = reader_for(
        service, doc, preview_only=True, open_on_demand=[GROUP], max_unread_opens_per_chat_per_day=0
    )
    monkeypatch.setattr(pixels, "badge", lambda *_: True)
    assert any(not m.preview for m in await read(reader))
    assert reader._budget(GROUP)["count"] == 1


def test_same_daily_notice_with_different_resolved_dates_is_a_gap():
    from plugins.screen.layout import QQ_LAYOUT, frame_lines
    from plugins.screen.model import TextBlock

    def notice(day, offset):
        blocks = [
            TextBlock(day + " 09:00", 0.42, 0.05 + offset, 0.16, 0.02),
            TextBlock("班级每日例行工作通知", 0.1, 0.10 + offset, 0.25, 0.02),
            TextBlock("下午六点前登记签到", 0.1, 0.15 + offset, 0.25, 0.02),
            TextBlock("收到", 0.1, 0.20 + offset, 0.08, 0.02),
        ]
        return frame_lines(blocks, QQ_LAYOUT, NOW)

    with pytest.raises(ScreenGapError):
        stitch(notice("昨天", 0), notice("前天", 0.65), scroll=0.65)


async def test_draft_behind_another_preview_tag_stays_private(service):
    reader, _ = reader_for(service, preview_document("[置顶][草稿]私人草稿"), preview_only=True)
    assert await read(reader) == []


async def test_announcement_without_a_confirmed_title_is_not_recognized(service, monkeypatch):
    from dataclasses import replace

    doc = document(
        {GROUP: ([{"time": "20:00"}, {"from": "甲", "text": "群公告已更新"}], "模拟未核对的公告")}
    )
    reader, capture = reader_for(service, doc)
    take = capture.capture_announcement

    async def unconfirmed(chat):
        return replace(await take(chat), chat=None)

    monkeypatch.setattr(capture, "capture_announcement", unconfirmed)
    batch = await read(reader)
    assert [m.text for m in batch] == ["群公告已更新"]
    assert reader.outcomes[GROUP]["error"] == "screen_chat_mismatch"
    assert not any("announcement" in path for path in reader.recognizer.calls)
