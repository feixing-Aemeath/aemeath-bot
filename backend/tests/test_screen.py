"""Platform-independent screen reading, driven by synthetic OCR blocks (no macOS needed)."""

import json
import plistlib
import random
import struct
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema
import pytest
import yaml

from core.models import MessageInput
from core.plugins import PluginContext
from plugins.screen import pixels
from plugins.screen.chat import ScreenGapError, assemble, stitch
from plugins.screen.fake import load_fake_screen
from plugins.screen.helper import HelperScreenCapture, JsonHelper
from plugins.screen.layout import QQ_LAYOUT, WECHAT_LAYOUT, frame_lines, merge_rows
from plugins.screen.model import TextBlock
from plugins.screen.preview import list_label, parse_preview
from plugins.screen.reader import ChatScreenReader, whitelisted
from plugins.screen.session import DesktopState
from plugins.screen.timeparse import parse_separator
from tests.screen_render import block, document, random_transcript

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 15, 21, 0, tzinfo=SHANGHAI)  # a Thursday
FIXTURES = Path(__file__).parent / "fixtures" / "screen"
GROUP = "软件工程2班"
PLUGINS = Path(__file__).resolve().parents[1] / "plugins"


def reader_for(service, doc, layout=QQ_LAYOUT, plugin="qq_screen", **config):
    capture, recognizer = load_fake_screen(doc)
    context = PluginContext(service.store, service.settings.data_dir, plugin)
    chats = list(doc.get("chats", {})) or config.pop("whitelist", [])
    settings = {"whitelist": chats, "preview_only": False, **config}
    return ChatScreenReader(capture, recognizer, layout, context, settings), capture


async def read(reader):
    messages = await reader.fetch()
    return [MessageInput.model_validate(m) for m in messages]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("20:15", datetime(2026, 10, 15, 20, 15)),
        ("昨天 08:30", datetime(2026, 10, 14, 8, 30)),
        ("昨天08:30", datetime(2026, 10, 14, 8, 30)),
        ("前天 23:59", datetime(2026, 10, 13, 23, 59)),
        ("星期一 09:00", datetime(2026, 10, 12, 9, 0)),
        ("周四 09:00", datetime(2026, 10, 8, 9, 0)),  # a weekday label is never today
        ("10月12日 08:30", datetime(2026, 10, 12, 8, 30)),
        ("12月30日 08:30", datetime(2025, 12, 30, 8, 30)),  # in the future → last year
        ("2025年10月12日 08:30", datetime(2025, 10, 12, 8, 30)),
        ("2025/10/12 08:30", datetime(2025, 10, 12, 8, 30)),
        ("下午 3:05", datetime(2026, 10, 15, 15, 5)),
        ("晚上 8:00", datetime(2026, 10, 15, 20, 0)),
        ("凌晨 12:30", datetime(2026, 10, 15, 0, 30)),
        ("中午 12:10", datetime(2026, 10, 15, 12, 10)),
        ("21:30", None),  # later than the screenshot: never guessed
        ("25:00", None),
        ("2月30日 08:00", None),
        ("2月29日 08:00", None),  # an undated leap day cannot be assigned to 2026
        ("王同学撤回了一条消息", None),
    ],
)
def test_time_separators(label, expected):
    resolved = parse_separator(label, NOW)
    assert resolved == (expected.replace(tzinfo=SHANGHAI) if expected else None)


def test_leap_day_before_it_occurs_does_not_crash_the_reader():
    reference = datetime(2024, 1, 1, 21, tzinfo=SHANGHAI)
    assert parse_separator("2月29日 08:00", reference) is None


async def test_one_screen_of_a_group_chat(service):
    doc = json.loads((FIXTURES / "qq_group_one_screen.json").read_text(encoding="utf-8"))
    chat = next(iter(doc["chats"]))
    reader, capture = reader_for(service, doc)
    messages = await read(reader)
    rows = [(m.sender, m.sent_at.strftime("%m-%d %H:%M"), m.text, m.priority) for m in messages]
    assert rows == [
        (
            "李老师",  # the 管理员 badge is not part of the name
            "10-14 21:30",
            "@全体成员 本周五（10月17日）18:00 前\n把实验报告提交到课程平台，逾期不收",
            True,
        ),
        ("王同学", "10-14 21:30", "收到", False),
        ("陈同学", "10-15 08:05", "[图片]", False),
        ("陈同学", "10-15 08:05", "上面是期中考试安排[表情]", False),  # two OCR blocks, one line
        ("我", "10-15 08:05", "好的，谢谢", False),
        ("群公告", "10-15 21:00", "【群公告】\n实验室开放时间调整为每周一至周五 14:00-17:00，进\n门请刷卡签到。", True),
    ]  # fmt: skip
    # The recall notice and the half-visible line at the top edge are not messages.
    assert not any("撤回" in m.text or m.text == "高数作业" for m in messages)
    assert all(m.screenshot_path.startswith("fake/") for m in messages)
    assert len({m.external_id for m in messages}) == len(messages)
    # Only read-only operations reached the (fake) helper.
    assert {op[0] for op in capture.operations} == {"capture_chat", "capture_announcement"}
    assert service.store.ingest("qq_screen", messages, [chat]) == len(messages)
    assert service.store.ingest("qq_screen", messages, [chat]) == 0  # stable IDs: no duplicates


def long_transcript(lines=100):
    text = "\n".join(f"第{i}项：实验课安排编号{i:03d}，请按时到场" for i in range(1, lines + 1))
    return [
        {"time": "08:05"},
        {"from": "李老师", "text": "下面是本学期实验课安排，请大家保存"},
        {"from": "李老师", "text": text},
        {"from": "王同学", "text": "收到"},
    ]


async def test_long_message_across_screenshots_comes_back_whole(service):
    items = long_transcript()
    doc = document({GROUP: items}, jitter=0.002)
    assert len(doc["chats"][GROUP]["frames"]) >= 3  # taller than two screens
    reader, capture = reader_for(service, doc, initial_pages=10)
    messages = await read(reader)
    assert [m.sender for m in messages] == ["李老师", "李老师", "王同学"]
    assert messages[1].text == items[2]["text"]
    assert len(messages[1].text.splitlines()) == 100
    assert capture.operations[-1] == ("capture_chat", GROUP, len(doc["chats"][GROUP]["frames"]) - 1)


async def test_overlapping_screenshots_keep_each_message_once(service):
    items = [{"time": "09:00"}] + [
        {"from": f"同学{i % 4}", "text": f"第{i}条消息" + ("，内容较长" * (i % 3))}
        for i in range(80)
    ]
    doc = document({GROUP: items}, step=0.3)  # 70 % of every screen seen twice
    reader, _ = reader_for(service, doc, initial_pages=50, max_pages=50)
    messages = await read(reader)
    assert [m.text.replace("\n", "") for m in messages] == [
        f"第{i}条消息" + ("，内容较长" * (i % 3)) for i in range(80)
    ]


async def test_same_minute_messages_get_distinct_stable_ids(service):
    items = [
        {"time": "20:30"},
        {"from": "王同学", "text": "收到"},
        {"from": "王同学", "text": "收到"},
        {"from": "赵同学", "text": "收到"},
        {"from": "王同学", "text": "收到"},
        {"me": "收到"},
    ]
    first, _ = reader_for(service, document({GROUP: items}))
    second, _ = reader_for(service, document({GROUP: items}, jitter=0.002, seed=3))
    ids = [m.external_id for m in await read(first)]
    assert len(ids) == len(set(ids)) == 5
    assert [m.external_id for m in await read(second)] == ids  # OCR jitter does not change IDs


def chat_history(first_hour=8, count=40):
    items = []
    for index in range(count):
        if index % 5 == 0:
            items.append({"time": f"{first_hour + index // 5:02d}:{index % 60:02d}"})
        items.append({"from": f"同学{index % 3}", "text": f"旧消息{index}：今天的安排第{index}项"})
    return items


async def test_cursor_resumes_where_the_last_read_stopped(service):
    old = chat_history()
    reader, capture = reader_for(service, document({GROUP: old}), initial_pages=2)
    first = await read(reader)
    assert first and first[-1].text == "旧消息39：今天的安排第39项"
    assert len(capture.operations) == 2  # first run: only the initial pages
    assert reader.cursor(GROUP) is None  # not committed before the core stored the batch
    await reader.commit_fetch()
    assert reader.cursor(GROUP)["sent_at"] == "2026-10-15T15:35:00+08:00"

    new = [
        {"time": "20:40"},
        {"from": "李老师", "text": "@全体成员 明天上午 9 点开班会"},
        {"from": "王同学", "text": "收到"},
    ]
    updated, capture = reader_for(service, document({GROUP: old + new}), initial_pages=2)
    messages = await read(updated)
    assert [m.text for m in messages] == ["@全体成员 明天上午 9 点开班会", "收到"]
    assert messages[0].priority
    assert len(capture.operations) < len(document({GROUP: old + new})["chats"][GROUP]["frames"])
    await updated.commit_fetch()
    assert updated.cursor(GROUP)["anchor"] == messages[-1].external_id
    assert await read(updated) == []  # nothing new


async def test_unreachable_cursor_is_a_gap_and_keeps_the_cursor(service):
    items = chat_history(count=60)
    reader, _ = reader_for(service, document({GROUP: items}), max_pages=2)
    cursor = {"anchor": "qq:not-on-screen", "sent_at": "2026-10-15T07:00:00+08:00"}
    reader.context.put(f"screen_cursor:{GROUP}", cursor)
    assert await reader.fetch()
    assert reader.context.get("screen_gap:" + GROUP)["error"] == "screen_history_gap"
    await reader.commit_fetch()
    assert reader.cursor(GROUP) == cursor


async def test_vanished_anchor_falls_back_to_its_time(service):
    items = chat_history(count=40)
    reader, _ = reader_for(service, document({GROUP: items}), max_pages=20)
    # The anchor message was recalled; its minute (12:20) is still known.
    reader.context.put(
        f"screen_cursor:{GROUP}", {"anchor": "qq:recalled", "sent_at": "2026-10-15T12:20:00+08:00"}
    )
    messages = await read(reader)
    assert messages[0].text == "旧消息20：今天的安排第20项"
    assert all(m.sent_at >= datetime(2026, 10, 15, 12, 20, tzinfo=SHANGHAI) for m in messages)


async def test_screens_without_overlap_are_a_gap(service):
    doc = document({GROUP: chat_history(count=60)}, step=1.2)  # scrolled too far each time
    reader, _ = reader_for(service, doc, initial_pages=5)
    assert await reader.fetch()
    assert reader.outcomes[GROUP]["error"] == "screen_history_gap"
    assert reader.outcomes[GROUP]["state"] == "partial"


async def test_repeated_ocr_does_not_claim_the_history_top(service):
    doc = document({GROUP: chat_history(count=60)})
    newest = doc["chats"][GROUP]["frames"][0]
    assert not newest.get("at_top")
    doc["chats"][GROUP]["frames"] = [newest, newest]
    reader, _ = reader_for(service, doc, max_pages=2)
    cursor = {"anchor": "qq:not-on-screen", "sent_at": "2026-10-15T07:00:00+08:00"}
    reader.context.put(f"screen_cursor:{GROUP}", cursor)
    assert await reader.fetch()
    assert reader.outcomes[GROUP]["error"] == "screen_history_gap"
    await reader.commit_fetch()
    assert reader.cursor(GROUP) == cursor


async def test_empty_chat_ocr_is_a_failure_and_keeps_the_cursor(service):
    doc = {
        "captured_at": NOW.isoformat(),
        "chats": {GROUP: {"frames": [{"blocks": [], "at_top": True}]}},
    }
    reader, _ = reader_for(service, doc)
    cursor = {"anchor": "qq:old", "sent_at": "2026-10-15T07:00:00+08:00"}
    reader.context.put(f"screen_cursor:{GROUP}", cursor)
    with pytest.raises(RuntimeError, match="^screen_empty_capture$"):
        await reader.fetch()
    await reader.commit_fetch()
    assert reader.cursor(GROUP) == cursor


def stitch_lines(texts, top):
    blocks = [TextBlock(text, 0.105, top + i * 0.05, 0.1, 0.016) for i, text in enumerate(texts)]
    return frame_lines(blocks, QQ_LAYOUT, NOW)


def test_stitch_rejects_same_text_at_a_different_place():
    timeline, _ = stitch([], stitch_lines(["收到", "好的", "明天见"], 0.1))
    # Same words, but the spacing does not match a single scroll offset.
    older = stitch_lines(["收到", "好的"], 0.5)
    older[1] = replace(older[1], top=older[1].top + 0.2)
    with pytest.raises(ScreenGapError):
        stitch(timeline, older)


def test_stitch_allows_a_box_a_little_off():
    # As read in a real QQ group: the time label's box sat 1.2% lower on the newer screen.
    timeline, _ = stitch([], stitch_lines(["收到", "好的", "明天见"], 0.1))
    older = stitch_lines(["早上好", "收到", "好的"], 0.5)
    older[2] = replace(older[2], top=older[2].top + 0.012)
    timeline, added = stitch(timeline, older)
    assert added == 1
    assert [line.text for line in timeline] == ["早上好", "收到", "好的", "明天见"]


def test_stitch_ignores_the_hover_time_beside_a_name():
    # The pointer stays where the last scroll left it, so QQ shows a name's send time on one
    # screen and not on the next (2026-11-14, every careful read stopped after two screens).
    timeline, _ = stitch(
        [], stitch_lines(["李老师 11-13 20:37:05", "第三章习题明天交", "收到"], 0.1)
    )
    older = stitch_lines(["早上好", "李老师", "第三章习题明天交"], 0.5)
    timeline, added = stitch(timeline, older)
    assert added == 1
    assert [line.text for line in timeline][:2] == ["早上好", "李老师"]


def test_stitch_survives_one_line_read_a_little_differently():
    # As on a real QQ group: one line of the overlap came back with one character changed.
    newer = ["收到", "第三章习题1到12题周五交", "好的", "明天见", "晚安"]
    older = ["早上好", "收到", "第三章习题l到12题周五交", "好的", "明天见"]
    timeline, _ = stitch([], stitch_lines(newer, 0.1))
    timeline, added = stitch(timeline, stitch_lines(older, 0.5))
    assert added == 1
    assert [line.text for line in timeline] == ["早上好", *older[1:], "晚安"]


def test_stitch_still_refuses_an_overlap_that_is_mostly_different():
    timeline, _ = stitch(
        [], stitch_lines(["收到", "第三章习题周五交", "实验报告下周一", "明天见"], 0.1)
    )
    older = stitch_lines(["早上好", "收到", "第四章习题周六交", "实验预习下周二"], 0.5)
    with pytest.raises(ScreenGapError):
        stitch(timeline, older)


def test_stitch_needs_more_than_one_exact_line_among_short_ones():
    timeline, _ = stitch([], stitch_lines(["收到", "好的", "嗯", "明天见"], 0.1))
    older = stitch_lines(["早上好", "收到", "好滴", "哦"], 0.5)
    with pytest.raises(ScreenGapError):
        stitch(timeline, older)


def test_stitch_lets_a_sticker_read_differently_on_each_screen():
    # As on a real QQ group (2026-11-14): 6 of 7 overlapping lines matched; a sticker came
    # back as one character on one screen and three on the other, its box 2 % lower.
    newer = ["王同学", "11月13日 20:00", "李老师", "😀", "11月13日 21:00", "明天交作业", "晚安"]
    older = ["早上好", "王同学", "11月13日 20:00", "李老师", "Oo0", "11月13日 21:00", "明天交作业"]
    timeline, _ = stitch([], stitch_lines(newer, 0.1))
    shifted = stitch_lines(older, 0.5)
    shifted[4] = replace(shifted[4], top=shifted[4].top + 0.019)
    timeline, added = stitch(timeline, shifted)
    assert added == 1
    assert [line.text for line in timeline] == [*older, "晚安"]


def placed_lines(rows):
    """Lines from (text, top) pairs, in the style of stitch_lines."""
    return frame_lines(
        [TextBlock(text, 0.105, top, 0.1, 0.016) for text, top in rows], QQ_LAYOUT, NOW
    )


def test_stitch_lets_one_screen_read_a_sticker_the_other_missed():
    # As on a real QQ group (2026-11-14): Vision read three characters on a sticker on the
    # older screen and nothing at all on the newer one; every other line matched.
    newer = [("王同学", 0.1), ("11月13日 20:00", 0.15), ("李老师", 0.2), ("11月13日 21:00", 0.25),
             ("明天交作业", 0.3), ("晚安", 0.35)]  # fmt: skip
    older = [("早上好", 0.5), ("王同学", 0.55), ("11月13日 20:00", 0.6), ("李老师", 0.65),
             ("哈哈哈", 0.675), ("11月13日 21:00", 0.7), ("明天交作业", 0.75)]  # fmt: skip
    timeline, _ = stitch([], placed_lines(newer))
    timeline, added = stitch(timeline, placed_lines(older))
    assert added == 2
    assert [line.text for line in timeline] == [text for text, _ in older] + ["晚安"]


def test_stitch_counts_long_lines_read_a_little_differently():
    # As on a real QQ group (2026-11-14): 4 of 7 overlapping lines matched exactly; the other
    # three were long lines with one character read differently.
    newer = [("今天下午三点在三教开班会", 0.1), ("张同学", 0.15), ("第三章习题周五前交给学委", 0.2),
             ("李同学", 0.25), ("实验报告下周一之前提交", 0.3), ("王同学", 0.35), ("收到", 0.4),
             ("晚安", 0.45)]  # fmt: skip
    older = [("早上好", 0.5), ("今天下午三点在三教开班曾", 0.55), ("张同学", 0.6),
             ("第三章习题周五前交给学娄", 0.65), ("李同学", 0.7), ("实验报告下周一之前提父", 0.75),
             ("王同学", 0.8), ("收到", 0.85)]  # fmt: skip
    timeline, _ = stitch([], placed_lines(newer))
    timeline, added = stitch(timeline, placed_lines(older))
    assert added == 1
    assert [line.text for line in timeline] == [text for text, _ in older] + ["晚安"]


def test_stitch_does_not_pair_a_run_of_replies_under_other_names():
    # A run of「收到」lines repeats on every screen; names of two or three characters differ.
    timeline, _ = stitch([], stitch_lines(["张三", "收到", "李四", "收到", "王五", "收到"], 0.1))
    older = stitch_lines(["早上好", "孙八", "收到", "周九", "收到"], 0.45)
    with pytest.raises(ScreenGapError):
        stitch(timeline, older)


def test_stitch_does_not_miss_a_line_the_older_screen_cut_off():
    # The newer screen's line just below the older screen's last one was cut off by the older
    # screen's bottom edge; it is not a line one screen failed to read.
    newer = [("今天下午三点在三教开班会", 0.115), ("收到", 0.165), ("好的", 0.215), ("明天见", 0.265),
             ("第三章习题周五前交给学委", 0.288), ("晚安", 0.34)]  # fmt: skip
    older = [("早上好", 0.765), ("今天下午三点在三教开班曾", 0.815), ("收到", 0.865), ("好的", 0.915),
             ("明天见", 0.965)]  # fmt: skip
    timeline, _ = stitch([], placed_lines(newer))
    timeline, added = stitch(timeline, placed_lines(older))
    assert added == 1
    assert [line.text for line in timeline] == [text for text, _ in older] + [
        "第三章习题周五前交给学委",
        "晚安",
    ]


# A real QQ overlap (2026-11-14) that was mostly a picture of a notice: its small text came
# back differently on each screen while the picture loaded; whole lines around it matched.
PICTURE_NEWER = [("今天下午三点在三教开班会", 0.05), ("一、实验报告格式要求如下", 0.10),
                 ("二、提交截止时间为周五晚", 0.15), ("第三章习题周五前交给学委", 0.20),
                 ("三、迟交按缺交处理请注意", 0.25), ("晚安", 0.45)]  # fmt: skip


def picture_older(scroll):
    return [("早上好", 0.05 + scroll - 0.3), ("今天下午三点在三教开班会", 0.05 + scroll),
            ("—`实验很告格戒耍求妇下", 0.10 + scroll), ("二`提文截正时间为周丑晩", 0.15 + scroll),
            ("第三章习题周五前交给学委", 0.20 + scroll), ("三`迟文按缺文处埋请注薏", 0.25 + scroll)]  # fmt: skip


def test_stitch_trusts_whole_lines_where_the_scroll_put_them():
    timeline, _ = stitch([], placed_lines(PICTURE_NEWER))
    older = placed_lines(picture_older(0.65))
    with pytest.raises(ScreenGapError):
        stitch(timeline, older)  # nothing pairs up line by line
    timeline, added = stitch(timeline, older, scroll=0.65)
    assert added == 1
    assert [line.text for line in timeline] == [t for t, _ in picture_older(0.65)] + ["晚安"]


def test_stitch_does_not_trust_lines_away_from_the_scroll():
    # The same screens, but the content moved by 0.6 rather than the 0.65 the capture
    # scrolled: QQ jumped (loading history, say), so the lines do not prove an overlap.
    timeline, _ = stitch([], placed_lines(PICTURE_NEWER))
    with pytest.raises(ScreenGapError):
        stitch(timeline, placed_lines(picture_older(0.6)), scroll=0.65)


def write_bmp(path, rows, width=40):
    """A top-down 32-bit BMP of the given pixel rows (each width * 4 bytes)."""
    size = len(rows) * width * 4
    header = struct.pack("<2sIHHI", b"BM", 54 + size, 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, -len(rows), 1, 32, 0, size, 2835, 2835, 0, 0)
    path.write_bytes(header + info + b"".join(rows))
    return str(path)


def chat_canvas(height, width=40, seed=7):
    """Pixel rows of a long chat: every row different, like rows of text; a blank row every
    tenth, like the space between messages."""
    rng = random.Random(seed)
    return [
        bytes(width * 4) if i % 10 == 0 else bytes(rng.randrange(256) for _ in range(width * 4))
        for i in range(height)
    ]


def screens(tmp_path, scroll, screen=200, top=400):
    """The newest screen and the one a scroll of `scroll` screens above it, as BMP files; the
    scroll bar column differs between them, as it moves with the content."""
    canvas = chat_canvas(1000)
    up = round(scroll * screen)

    def shot(name, start, bar):
        rows = [row[:-8] + bytes([bar]) * 8 for row in canvas[start : start + screen]]
        return write_bmp(tmp_path / name, rows)

    return shot("newer.bmp", top, 1), shot("older.bmp", top - up, 2)


def test_pixels_show_where_two_screens_overlap(tmp_path):
    newer, older = screens(tmp_path, 0.65)
    assert pixels.overlap(newer, older, 0.65, 0.005) == pytest.approx(0.65)
    # QQ moved the content elsewhere (loading history, say): nothing lines up near the scroll.
    jumped = tmp_path / "jumped"
    jumped.mkdir()
    newer, older = screens(jumped, 0.55)
    assert pixels.overlap(newer, older, 0.65, 0.005) is None
    # Not a screenshot, or not one that can be read here.
    (tmp_path / "shot.png").write_bytes(b"not an image")
    assert pixels.overlap(newer, str(tmp_path / "shot.png"), 0.65, 0.005) is None
    assert pixels.overlap(newer, str(tmp_path / "missing.bmp"), 0.65, 0.005) is None


def test_pixels_allow_a_few_rows_that_changed(tmp_path):
    # The pointer's hover and an animated sticker repaint a few rows of the overlap.
    newer, older = screens(tmp_path, 0.65)
    data = bytearray(Path(older).read_bytes())
    for row in range(140, 146):
        start = 54 + row * 160
        data[start : start + 40] = bytes(40)
    Path(older).write_bytes(bytes(data))
    pixels.rows.cache_clear()
    pixels.bitmap.cache_clear()
    assert pixels.overlap(newer, older, 0.65, 0.005) == pytest.approx(0.65)


def unmoved_screens(tmp_path, changed=range(20, 70)):
    """One screen of a chat, then the same place again with some rows repainted (an animated
    sticker), as two BMP files."""
    canvas = chat_canvas(1000)
    still = canvas[400:600]
    moved = [bytes(reversed(row)) if index in changed else row for index, row in enumerate(still)]
    return write_bmp(tmp_path / "first.bmp", still), write_bmp(tmp_path / "again.bmp", moved)


def test_pixels_see_a_screen_that_did_not_scroll(tmp_path):
    first, again = unmoved_screens(tmp_path)
    assert pixels.unmoved(again, first)
    scrolled = tmp_path / "scrolled"
    scrolled.mkdir()
    newer, older = screens(scrolled, 0.65)
    assert not pixels.unmoved(older, newer)
    # Most of the screen changed: not the same place, whatever the cause.
    repainted = tmp_path / "repainted"
    repainted.mkdir()
    first, again = unmoved_screens(repainted, changed=range(10, 150))
    assert not pixels.unmoved(again, first)
    assert not pixels.unmoved(again, str(tmp_path / "missing.bmp"))


async def test_a_screen_that_did_not_scroll_is_the_top_of_the_history(service, tmp_path):
    # 2026-11-18: a young group reached the top of its history, but an animated sticker kept
    # the helper from seeing that the screen stayed put; every night's read ended in a gap.
    doc = document({GROUP: chat_history(count=60)})
    newest = doc["chats"][GROUP]["frames"][0]
    assert not newest.get("at_top")
    first, again = unmoved_screens(tmp_path)
    doc["chats"][GROUP]["frames"] = [{**newest, "path": first}, {**newest, "path": again}]
    reader, capture = reader_for(service, doc, max_pages=2)
    cursor = {"anchor": "qq:not-on-screen", "sent_at": "2026-10-15T07:00:00+08:00"}
    reader.context.put(f"screen_cursor:{GROUP}", cursor)
    messages = await read(reader)
    assert messages
    assert reader.outcomes[GROUP]["error"] is None
    assert [op[2] for op in capture.operations if op[0] == "capture_chat"] == [0, 1]
    await reader.commit_fetch()
    assert reader.cursor(GROUP) != cursor


def conversation_list(tmp_path, pill=True, ring=False):
    """A conversation-list screenshot: one row whose preview line spans y 0.08 to 0.094, with
    a filled grey unread pill at its right edge, or the thin ring of a muted bell."""
    width, height = 400, 1000
    background, grey = bytes([245, 245, 245, 255]), bytes([196, 196, 196, 255])
    rows = []
    for y in range(height):
        row = bytearray(background * width)
        for x in range(width):
            inside = 360 <= x < 388 and 78 <= y < 96
            edge = inside and (x in (360, 361, 386, 387) or y in (78, 79, 94, 95))
            if (pill and inside) or (ring and edge):
                row[x * 4 : x * 4 + 4] = grey
        rows.append(bytes(row))
    return write_bmp(tmp_path / "list.bmp", rows, width=width)


def unread_document(path):
    doc = document(
        {GROUP: chat_history(count=5)},
        preview=[
            block(GROUP, 0.1, 0.05, 0.016),
            block("20:41", 0.88, 0.052, 0.012, char_w=0.016),
            block("李同学：明天上午交一下作业", 0.1, 0.08, 0.014),
        ],
    )
    doc["preview"]["path"] = path
    return doc


def test_pixels_find_an_unread_badge_but_not_a_thin_icon(tmp_path):
    assert pixels.badge(conversation_list(tmp_path), 0.08, 0.094)
    pixels.bitmap.cache_clear()
    assert not pixels.badge(conversation_list(tmp_path, pill=False, ring=True), 0.08, 0.094)
    pixels.bitmap.cache_clear()
    assert not pixels.badge(conversation_list(tmp_path), 0.5, 0.514)  # another row
    assert not pixels.badge(str(tmp_path / "missing.bmp"), 0.08, 0.094)


@pytest.mark.parametrize(
    ("config", "pill", "opened"),
    [
        ({}, True, True),
        ({}, False, False),
        ({"open_on_unread": False}, True, False),
        ({"open_on_demand": []}, True, False),
        ({"max_unread_opens_per_chat_per_day": 0}, True, False),
    ],
)
async def test_an_unread_badge_opens_an_authorized_chat(service, tmp_path, config, pill, opened):
    # Vision reads no digits on QQ's badges; the preview shows only the newest message, so a
    # badge on a chat the user allowed to be opened is the sign that more came.
    pixels.bitmap.cache_clear()
    doc = unread_document(conversation_list(tmp_path, pill=pill))
    settings = {"preview_only": True, "open_on_demand": [GROUP], **config}
    reader, capture = reader_for(service, doc, **settings)
    await reader.fetch()
    chats = [op[1] for op in capture.operations if op[0] == "capture_chat"]
    assert bool(chats) == opened
    budget = reader._budget(GROUP)
    assert budget.get("unread", 0) == (1 if opened else 0)
    assert budget["count"] == 0  # the opens kept for 「@全体成员」 and the Agent are untouched


def test_stitch_takes_the_pixels_where_the_text_was_read_differently(tmp_path):
    # A screen of homework cards (QQ, 2026-11-15): Vision read the cards' small text differently
    # on each screen, so not one line of the overlap matches, but the pixels do.
    newer_shot, older_shot = screens(tmp_path, 0.65)
    newer = [("作业卡片一", 0.05), ("张三 已交", 0.10), ("李四 未交", 0.15), ("晚安", 0.45)]
    older = [("早上好", 0.10), ("作业卡片—", 0.70), ("张三 己交", 0.75), ("李四 末交", 0.80)]

    def lines(rows, shot):
        blocks = [TextBlock(text, 0.105, top, 0.1, 0.016) for text, top in rows]
        return frame_lines(blocks, QQ_LAYOUT, NOW, shot)

    timeline, _ = stitch([], lines(newer, newer_shot))
    with pytest.raises(ScreenGapError):
        stitch(timeline, lines(older, older_shot))  # the scroll unknown: no proof
    with pytest.raises(ScreenGapError):
        stitch(timeline, lines(older, newer_shot), scroll=0.65)  # the same screen twice
    timeline, added = stitch(timeline, lines(older, older_shot), scroll=0.65)
    assert added == 1
    assert [line.text for line in timeline] == [t for t, _ in older] + ["晚安"]
    assert timeline[0].pos == pytest.approx(0.10 - 0.65, abs=0.01)


@pytest.mark.parametrize("hover", [False, True])
def test_a_homework_card_stays_one_message(hover):
    # Two screens of a real QQ group as read (x, right, top, height), with invented texts: two
    # older posts, then a homework card. Its table rows are as small as names but set in; the
    # newest name came back boxed almost as tall as text, and with the pointer over the message
    # QQ adds its send time beside the name.
    name = "群主 张老师" + (" 10-13 09:22:50" if hover else "")
    rows = [
        ("2026/10/11 10:23", 0.404, 0.596, -0.560, 0.041),
        ("群主 张老师", 0.121, 0.307, -0.466, 0.046),
        ("[文件] 第1次作业", 0.310, 0.742, -0.370, 0.126),
        ("2026/10/12 09:58", 0.402, 0.600, -0.148, 0.041),
        ("群主 张老师", 0.121, 0.307, -0.052, 0.044),
        ("[文件] 第2次作业", 0.312, 0.742, 0.043, 0.120),
        ("09:22", 0.425, 0.573, 0.265, 0.041),
        (name, 0.121, 0.485 if hover else 0.307, 0.387, 0.056),
        ("名称 类型 时间", 0.148, 0.621, 0.539, 0.049),
        ("作业03 作业 2026/10/15 9:20:09", 0.146, 0.747, 0.640, 0.047),
        ("2026/10/20 23:59:00", 0.579, 0.747, 0.690, 0.042),
        ("各位同学，第3次作业已发布，请在要求时间", 0.132, 0.726, 0.771, 0.074),
        ("（2026/10/20 23:59:00之前）内完成。", 0.145, 0.589, 0.839, 0.080),
    ]
    # Both screens on one canvas.
    blocks = [
        TextBlock(text, x, (top + 0.6) / 1.6, right - x, height / 1.6)
        for text, x, right, top, height in rows
    ]
    messages, _ = assemble(frame_lines(blocks, QQ_LAYOUT, NOW), GROUP, QQ_LAYOUT)
    assert [(m.sender, m.lines) for m in messages] == [
        (
            "张老师",
            [
                "名称 类型 时间",
                "作业03 作业 2026/10/15 9:20:09",
                "2026/10/20 23:59:00",
                "各位同学，第3次作业已发布，请在要求时间",
                "（2026/10/20 23:59:00之前）内完成。",
            ],
        )
    ]


TEXT_H = 0.0165  # body text height of a real QQ group chat area (2026-11-14)


def group_lines(rows):
    """Left lines from (text, x, height, gap above) rows, as body-height multiples."""
    blocks, top = [], 0.05
    for text, x, height, gap in rows:
        top += gap * TEXT_H
        blocks.append(TextBlock(text, x, top, 0.2, height * TEXT_H))
        top += height * TEXT_H
    return frame_lines(blocks, QQ_LAYOUT, NOW)


def test_names_further_in_than_the_leftmost_still_name_their_senders():
    # A real QQ group (2026-11-14): names started anywhere from 0.093 to 0.123 of the width,
    # message text at 0.130, so most names sat further in than the leftmost one.
    rows = [
        ("张同学", 0.093, 0.83, 0.0),
        ("第三章习题周五交", 0.130, 1.0, 0.66),
        ("李同学", 0.111, 0.75, 1.7),
        ("实验报告下周一交", 0.130, 1.0, 0.6),
        ("下午三点开班会", 0.130, 1.0, 0.3),
        ("王同学", 0.120, 0.83, 1.6),
        ("收到", 0.130, 1.0, 0.8),
    ]
    messages, _ = assemble(group_lines(rows), GROUP, QQ_LAYOUT)
    assert [(m.sender, m.lines) for m in messages] == [
        ("张同学", ["第三章习题周五交"]),
        ("李同学", ["实验报告下周一交", "下午三点开班会"]),
        ("王同学", ["收到"]),
    ]


def test_a_short_bubble_before_the_next_one_is_not_a_name():
    # A bubble of one small line starting a little left of the text (a numbered line), then
    # the same sender's next bubble: bubble to bubble is wider than name to bubble.
    rows = [
        ("张同学", 0.111, 0.83, 0.0),
        ("1. 李四", 0.124, 0.82, 0.66),
        ("明天交", 0.130, 1.0, 1.2),
    ]
    messages, _ = assemble(group_lines(rows), GROUP, QQ_LAYOUT)
    assert [(m.sender, m.lines) for m in messages] == [
        ("张同学", ["1. 李四"]),
        ("张同学", ["明天交"]),
    ]


async def test_announcement_opened_when_hinted(service):
    items = [
        {"time": "19:00"},
        {"from": "李老师", "text": "大家看一下新公告"},
        {"notice": "李老师发布了新的群公告"},
    ]
    doc = document({GROUP: (items, "期中考试 11 月 3 日上午 8:30，地点三教 201，带学生证。")})
    reader, capture = reader_for(service, doc)
    messages = await read(reader)
    assert messages[-1].sender == "群公告" and messages[-1].priority
    assert messages[-1].text.startswith("【群公告】\n期中考试")
    assert capture.operations[-1] == ("capture_announcement", GROUP)

    await reader.commit_fetch()
    again, capture = reader_for(service, doc)
    assert await read(again) == []  # an old hint does not reopen the announcement
    assert ("capture_announcement", GROUP) not in capture.operations


async def test_preview_mode_reads_the_list_without_opening_chats(service):
    preview = [
        block("软件工程2班", 0.2, 0.05, 0.016),
        block("20:41", 0.88, 0.052, 0.012, char_w=0.016),
        block("[有人@我]李老师：@全体成员 明天上午 9 点开班会…", 0.2, 0.08, 0.014, char_w=0.014),
        block("3", 0.93, 0.08, 0.014, char_w=0.014),
        block("篮球社（虚构）", 0.2, 0.15, 0.016),
        block("20:30", 0.88, 0.152, 0.012, char_w=0.016),
        block("周六约球吗", 0.2, 0.18, 0.014),
        block("王同学", 0.2, 0.25, 0.016),
        block("昨天", 0.9, 0.252, 0.012, char_w=0.016),
        block("作业写完了吗", 0.2, 0.28, 0.014),
        block("实验课群（虚构）", 0.2, 0.35, 0.016),
        block("19:02", 0.88, 0.352, 0.012, char_w=0.016),
        block("[草稿]我明天", 0.2, 0.38, 0.014),
        # QQ 7: an older day with its time and no space; it must not swallow the next chat.
        block("课程通知群（虚构）", 0.2, 0.42, 0.016),
        block("昨天20:59", 0.8, 0.422, 0.012, char_w=0.016),
        block("张老师：周一交报告", 0.2, 0.45, 0.014),
        block("社团群（虚构）", 0.2, 0.52, 0.016),
        block("18:00", 0.88, 0.522, 0.012, char_w=0.016),
        block("明天下午活动改到三点", 0.2, 0.55, 0.014),
        block("Q", 0.92, 0.55, 0.014, char_w=0.014),  # the muted bell, read as a letter
    ]
    doc = {"captured_at": NOW.isoformat(), "preview": {"blocks": preview}, "chats": {}}
    reader, capture = reader_for(
        service,
        doc,
        preview_only=True,
        whitelist=[
            "软件工程2班",
            "王同学",
            "实验课群（虚构）",
            "课程通知群（虚构）",
            "社团群（虚构）",
        ],
    )
    messages = await read(reader)
    assert capture.operations == [("preview",)]
    assert [(m.chat_name, m.sender, m.sent_at.strftime("%H:%M"), m.priority) for m in messages] == [
        ("软件工程2班", "李老师", "20:41", True),
        # Yesterday with its minute and nothing of yesterday stored: the reader missed it.
        ("课程通知群（虚构）", "张老师", "20:59", False),
        ("社团群（虚构）", "社团群（虚构）", "18:00", False),
    ]  # not whitelisted, the user's own draft and a bare 「昨天」 are skipped
    assert reader.context.get("missed_chats") == ["王同学"]  # no minute, not allowed to open
    assert messages[2].text == "明天下午活动改到三点"
    assert messages[0].text == "李老师：@全体成员 明天上午 9 点开班会…"
    assert messages[0].preview  # stored as a preview whose text may be truncated


async def test_a_page_that_is_not_the_conversation_list_is_reported(service):
    # QQ left on its contacts page: group folders with member counts, no times.
    contacts = [
        block("群聊", 0.67, 0.16, 0.025),
        block("我创建的群", 0.05, 0.22, 0.026),
        block("0/0", 0.87, 0.22, 0.02, char_w=0.03),
        block("我加入的群", 0.05, 0.39, 0.026),
        block("60/79", 0.82, 0.39, 0.02, char_w=0.028),
    ]
    doc = {"captured_at": NOW.isoformat(), "preview": {"blocks": contacts}, "chats": {}}
    reader, _ = reader_for(service, doc, preview_only=True, whitelist=["软件工程2班"])
    with pytest.raises(RuntimeError, match="^screen_not_conversation_list$"):
        await reader.fetch()
    assert reader.context.get("not_conversation_list") is True


def test_failed_jobs_keep_fixed_error_codes_only():
    from core.jobs import error_code

    assert (
        error_code(RuntimeError("screen_not_conversation_list")) == "screen_not_conversation_list"
    )
    assert error_code(ValueError("周五前交实验报告")) == "job_failed"
    assert error_code(KeyError("x")) == "job_failed"


def test_whitelist_matches_names_as_the_screen_shows_them():
    whitelist = [
        "《高等数学》26软件",
        "2026级algo/lab实验室招新",
        "信息学院2026级软件工程专业1-4班",
        "软件1班",
    ]
    assert whitelisted("高等数学》26软件", whitelist) == "《高等数学》26软件"
    assert whitelisted("2026级ALGO / LAB实验室招新", whitelist) == "2026级algo/lab实验室招新"
    assert whitelisted("软件1班", whitelist) == "软件1班"
    # A different chat whose whole name starts an entry, and a longer name, do not match.
    assert whitelisted("信息学院2026级", whitelist) is None
    assert whitelisted("软件1班家长群", whitelist) is None
    # Cut off, a chat outside the whitelist can look the same, so it never matches; not even
    # when it starts only one entry.
    assert whitelisted("信息学院2026级软件…", whitelist) is None
    assert whitelisted("软件1班…", whitelist) is None
    assert whitelisted("", whitelist) is None


async def test_preview_skips_cut_off_names_and_says_so(service):
    preview = [
        block("信息学院2026级软件…", 0.2, 0.05, 0.016),
        block("20:41", 0.88, 0.052, 0.012, char_w=0.016),
        block("辅导员：家长会改到周六", 0.2, 0.08, 0.014),
        block("篮球社…", 0.2, 0.15, 0.016),
        block("20:30", 0.88, 0.152, 0.012, char_w=0.016),
        block("周六约球吗", 0.2, 0.18, 0.014),
    ]
    doc = {"captured_at": NOW.isoformat(), "preview": {"blocks": preview}, "chats": {}}
    reader, _ = reader_for(
        service, doc, preview_only=True, whitelist=["信息学院2026级软件工程专业1-4班"]
    )
    assert await read(reader) == []
    # Only the cut-off name that could be a whitelisted chat is reported.
    assert reader.context.get("cut_off_chats") == ["信息学院2026级软件…"]


async def test_preview_skips_same_named_chats_even_with_a_pending_open_request(service):
    preview = []
    for y, text in [(0.05, "白名单群的消息"), (0.25, "同名的另一个群的消息")]:
        preview.extend(
            [
                block(GROUP, 0.2, y, 0.016),
                block("20:41", 0.88, y + 0.002, 0.012, char_w=0.016),
                block(text, 0.2, y + 0.03, 0.014),
            ]
        )
    doc = document({GROUP: chat_history(count=5)}, preview=preview)
    reader, capture = reader_for(service, doc, preview_only=True, open_on_demand=[GROUP])
    assert reader.request_open(GROUP)["status"] == "scheduled"
    assert await read(reader) == []
    assert capture.operations == [("preview",)]
    assert reader.context.get("ambiguous_chats") == [GROUP]


async def test_wrong_chat_on_screen_is_rejected(service):
    capture_doc = document({GROUP: chat_history(count=5)})
    reader, capture = reader_for(service, capture_doc)
    for title in ("别的群", GROUP + "家长群", GROUP + "…"):
        capture.confirmed_chat = title
        with pytest.raises(RuntimeError, match="screen_chat_mismatch"):
            await reader.fetch()
    capture.confirmed_chat = "软件工程 2 班"  # as OCR reads the title: the same chat
    assert await reader.fetch()


async def test_wechat_private_chat_and_everyone_mention(service):
    items = [
        {"time": "星期二 18:20"},
        {"text": "@所有人 周五的聚餐改到周六"},
        {"me": "好"},
        {"text": "地点不变"},
    ]
    doc = document({"室友（虚构）": items})
    reader, _ = reader_for(service, doc, layout=WECHAT_LAYOUT, plugin="wechat_screen")
    messages = await read(reader)
    assert [(m.sender, m.text, m.priority) for m in messages] == [
        ("室友（虚构）", "@所有人 周五的聚餐改到周六", True),
        ("我", "好", False),
        ("室友（虚构）", "地点不变", False),
    ]
    assert messages[0].sent_at == datetime(2026, 10, 13, 18, 20, tzinfo=SHANGHAI)
    assert messages[0].external_id.startswith("wechat:")


def test_rows_merge_split_blocks_and_drop_cut_off_ones():
    blocks = [
        TextBlock("截止", 0.105, 0.0, 0.04, 0.008),  # cut by the top edge
        TextBlock("请在周五", 0.105, 0.30, 0.08, 0.016),
        TextBlock("前提交", 0.19, 0.3006, 0.06, 0.016),
        TextBlock("Lab 3", 0.31, 0.3, 0.05, 0.016),
        TextBlock("下一行", 0.105, 0.985, 0.06, 0.015),  # cut by the bottom edge
    ]
    assert [row.text for row in merge_rows(blocks, QQ_LAYOUT)] == ["请在周五前提交 Lab 3"]


def test_message_before_the_first_separator_has_no_time():
    blocks = [
        TextBlock("王同学", 0.09, 0.10, 0.045, 0.012),
        TextBlock("这条看不到时间", 0.105, 0.12, 0.15, 0.016),
        TextBlock("20:30", 0.47, 0.20, 0.06, 0.012),
        TextBlock("王同学", 0.09, 0.24, 0.045, 0.012),
        TextBlock("这条有时间", 0.105, 0.26, 0.1, 0.016),
    ]
    messages, _ = assemble(frame_lines(blocks, QQ_LAYOUT, NOW), GROUP, QQ_LAYOUT)
    assert [(m.text, bool(m.external_id)) for m in messages] == [
        ("这条看不到时间", False),
        ("这条有时间", True),
    ]


async def test_helper_is_read_only_and_mac_only(tmp_path):
    helper = JsonHelper(tmp_path / "helper")
    for action in ("type", "send", "click", None):
        with pytest.raises(ValueError, match="not_allowed"):
            await helper.call({"action": action})
    if __import__("sys").platform not in {"darwin", "win32"}:
        with pytest.raises(RuntimeError, match="macos_required"):
            await helper.call({"action": "preview"})


async def test_helper_gets_the_app_and_folder_and_reports_fixed_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")

    async def unlocked():
        return DesktopState()

    monkeypatch.setattr("plugins.screen.helper.desktop_state", unlocked)
    script = tmp_path / "helper.py"
    script.write_text(
        "import sys,json\nfrom pathlib import Path\n"
        "raw = sys.stdin.buffer.read()\nPath(__file__).with_suffix('.request').write_bytes(raw)\n"
        "action = json.loads(raw)['action']\n"
        "if action == 'recognize':\n    print('{\"error\":\"screen_recording_denied\"}'); sys.exit(1)\n"
        "if action == 'capture_chat':\n    print('{\"error\":\"see /Users/me/secret\"}'); sys.exit(1)\n"
        'print(\'{"path":"/x.png","captured_at":"2026-10-24T08:00:00+08:00","chat":null}\')\n',
        encoding="utf-8",
    )
    helper = JsonHelper(script, app="qq", out_dir=tmp_path / "shots")
    shot = await HelperScreenCapture(helper).preview()
    assert shot.path == "/x.png" and shot.kind == "preview"
    sent = json.loads((tmp_path / "helper.request").read_text(encoding="utf-8"))
    assert sent == {"action": "preview", "app": "qq", "out_dir": str(tmp_path / "shots")}
    with pytest.raises(RuntimeError, match="^screen_recording_denied$"):
        await helper.call({"action": "recognize", "path": "/x.png"})
    with pytest.raises(RuntimeError, match="^screen_helper_failed$"):  # not a fixed code
        await helper.call({"action": "capture_chat", "chat": "班级群", "page": 0})


async def test_a_capture_failing_on_a_locked_screen_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    script = tmp_path / "helper.py"
    script.write_text(
        'import sys; sys.stdin.buffer.read(); print(\'{"error":"screen_capture_failed"}\'); sys.exit(1)\n',
        encoding="utf-8",
    )
    helper = JsonHelper(script, app="qq", out_dir=tmp_path / "shots")
    locked = False

    async def is_locked():
        return DesktopState(locked=locked)

    monkeypatch.setattr("plugins.screen.helper.desktop_state", is_locked)
    with pytest.raises(RuntimeError, match="^screen_capture_failed$"):
        await helper.call({"action": "preview"})
    locked = True
    with pytest.raises(RuntimeError, match="^screen_locked$"):
        await helper.call({"action": "capture_page", "title": "个人日程"})
    # Reading text from a saved screenshot does not need the screen.
    with pytest.raises(RuntimeError, match="^screen_capture_failed$"):
        await helper.call({"action": "recognize", "path": "/x.png"})


async def test_a_locked_screen_never_runs_a_capture_helper(tmp_path, monkeypatch):
    from plugins.screen import helper as module

    monkeypatch.setattr("sys.platform", "darwin")
    calls = []

    async def locked():
        return DesktopState(locked=True)

    async def run(argv, data, timeout):
        calls.append(json.loads(data))
        return 0, b'{"blocks": []}'

    monkeypatch.setattr(module, "desktop_state", locked)
    monkeypatch.setattr(module.helper_process, "run", run)
    helper = JsonHelper(tmp_path / "helper", app="qq")
    for payload in [
        {"action": "preview"},
        {"action": "capture_chat", "chat": GROUP, "page": 0},
        {"action": "capture_page", "title": "个人日程"},
    ]:
        with pytest.raises(RuntimeError, match="^screen_locked$"):
            await helper.call(payload)
    assert calls == []
    assert await helper.call({"action": "recognize", "path": "/fake.png"}) == {"blocks": []}


async def test_the_lock_is_read_from_the_io_registry(monkeypatch):
    from plugins.screen import helper as module

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)

    answers = {}

    async def run(argv, data, timeout, **_):
        assert argv == module.CONSOLE_COMMAND
        if isinstance(answers["root"], Exception):
            raise answers["root"]
        return 0, plistlib.dumps(answers["root"])

    monkeypatch.setattr(module.helper_process, "run", run)
    monkeypatch.setattr(module, "displays_asleep", lambda: None)
    answers["root"] = {"IOConsoleUsers": [{"kCGSSessionOnConsoleKey": True}]}
    assert await module.screen_is_locked() is False
    answers["root"] = {"IOConsoleUsers": [{"CGSSessionScreenIsLocked": True}]}
    assert await module.screen_is_locked() is True
    answers["root"] = {"IOConsoleLocked": True}
    assert await module.screen_is_locked() is True
    answers["root"] = FileNotFoundError("no ioreg on Linux")
    assert await module.screen_is_locked() is False


@pytest.mark.parametrize(
    "root, locked",
    [
        ([{"IOConsoleUsers": [{"CGSSessionScreenIsLocked": True}]}], True),
        ([{"IOConsoleUsers": [{"CGSSessionScreenIsLocked": False}]}], False),
        ([], False),
        (["invalid"], False),
        ({"IOConsoleUsers": True}, False),
    ],
)
async def test_lock_probe_handles_ioreg_array_and_unreadable_shapes(monkeypatch, root, locked):
    from plugins.screen import helper as module

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)

    async def run(*_args, **_kwargs):
        return 0, plistlib.dumps(root)

    monkeypatch.setattr(module.helper_process, "run", run)
    monkeypatch.setattr(module, "displays_asleep", lambda: None)
    assert await module.screen_is_locked() is locked


def test_layout_calibration_is_validated():
    assert QQ_LAYOUT.with_overrides({"bubble_gap": 1.1}).bubble_gap == 1.1
    with pytest.raises(ValueError):
        QQ_LAYOUT.with_overrides({"app": "other"})
    for plugin in ("qq_screen", "wechat_screen"):
        schema = yaml.safe_load((PLUGINS / plugin / "manifest.yaml").read_text())["config_schema"]
        jsonschema.validate({"whitelist": ["a"], "layout": {"bubble_gap": 1.2}}, schema)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"layout": {"self_name": "x"}}, schema)
        # Every calibration key in the schema exists on the layout.
        QQ_LAYOUT.with_overrides({key: 0.5 for key in schema["properties"]["layout"]["properties"]})


async def test_screen_plugins_are_listed_and_mac_only(service):
    await service.start()
    try:
        views = {view["id"]: view for view in service.plugins.list()}
        assert views["qq_screen"]["state"] == views["wechat_screen"]["state"] == "unsupported"
        assert views["wechat_screen"]["permissions"] == ["screen_recording", "accessibility"]
    finally:
        await service.close()


@pytest.mark.parametrize("seed", range(30))
async def test_random_transcripts_round_trip(service, seed):
    rng = random.Random(seed)
    items, expected = random_transcript(rng)
    doc = document(
        {GROUP: items},
        step=rng.choice([0.3, 0.5, 0.7]),
        jitter=rng.choice([0, 0.001, 0.002]),
        seed=seed,
    )
    reader, _ = reader_for(service, doc, initial_pages=100, max_pages=100)
    messages = await read(reader)

    def plain(text):
        return text.replace("\n", "").replace(" ", "")

    assert [(m.sender, plain(m.text)) for m in messages] == [
        (sender, plain(text)) for sender, text in expected
    ]


async def test_plugin_setup_wires_helper_layout_and_timezone(service, tmp_path):
    import importlib.util
    import sys

    for plugin, layout in (("qq_screen", QQ_LAYOUT), ("wechat_screen", WECHAT_LAYOUT)):
        spec = importlib.util.spec_from_file_location(
            f"test_{plugin}", PLUGINS / plugin / "plugin.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        context = PluginContext(service.store, service.settings.data_dir, plugin)
        instance = module.Plugin()
        with pytest.raises(ValueError, match="calibration"):
            await instance.setup(context, {"helper": "relative/helper"})
        config = {"helper": str(tmp_path / "helper"), "layout": {"bubble_gap": 1.1}}
        await instance.setup(context, config)
        assert instance.reader.layout.app == layout.app
        assert instance.reader.layout.bubble_gap == 1.1
        assert str(instance.reader.zone) == "Asia/Shanghai"
        assert "仅预览模式" in (await instance.cards())[0]["data"]["text"]
        if sys.platform not in {"darwin", "win32"}:
            with pytest.raises(RuntimeError, match="macos_required"):
                await instance.fetch()


@pytest.mark.parametrize("plugin", ["qq_screen", "wechat_screen"])
@pytest.mark.parametrize("preview", [False, True])
async def test_screen_reader_uses_runtime_timezone(service, tmp_path, plugin, preview):
    from plugins.qq_screen.plugin import Plugin as QQPlugin
    from plugins.wechat_screen.plugin import Plugin as WechatPlugin

    instance = QQPlugin() if plugin == "qq_screen" else WechatPlugin()
    context = PluginContext(service.store, service.settings.data_dir, plugin)
    await instance.setup(
        context,
        {"helper": str(tmp_path / "fake-helper"), "whitelist": [GROUP], "preview_only": preview},
    )
    doc = document(
        {GROUP: [{"time": "昨天 08:30"}, {"from": "老师", "text": "明天交报告"}]},
        captured_at="2026-10-15T01:00:00+08:00",
        preview=[
            block(GROUP, 0.2, 0.05, 0.016),
            block("08:30", 0.88, 0.052, 0.012, char_w=0.016),
            block("老师：明天交报告", 0.2, 0.08, 0.014, char_w=0.014),
        ],
    )
    capture, recognizer = load_fake_screen(doc)
    instance.reader.capture, instance.reader.recognizer = capture, recognizer
    service.db.put("settings", {"timezone": "America/Los_Angeles"})
    [message] = await read(instance.reader)
    expected_day = "2026-10-14" if preview else "2026-10-13"
    assert message.sent_at.isoformat() == expected_day + "T08:30:00-07:00"
    assert str(instance.reader.zone) == "America/Los_Angeles"


async def test_preview_is_replaced_by_the_full_message(service):
    full = "@全体成员 明天上午 9 点开班会，带好学生证，迟到的同学请提前和班长说明"
    preview = [
        block(GROUP, 0.2, 0.05, 0.016),
        block("20:41", 0.88, 0.052, 0.012, char_w=0.016),
        block("李老师：" + full[:16] + "…", 0.2, 0.08, 0.014, char_w=0.014),
    ]
    doc = document({GROUP: [{"time": "20:38"}, {"from": "李老师", "text": full}]}, preview=preview)
    store = service.store
    list_reader, _ = reader_for(service, doc, preview_only=True)
    assert store.ingest("qq_screen", await read(list_reader), [GROUP]) == 1
    [row] = store.unprocessed()
    assert row["preview"] and row["note"] == "预览，内容可能被截断"
    store.mark_processed([row["id"]])  # the Agent already handled the preview

    chat_reader, _ = reader_for(service, doc)
    assert store.ingest("qq_screen", await read(chat_reader), [GROUP]) == 1
    [again] = store.unprocessed()
    assert again["id"] == row["id"]  # todos citing the preview stay valid
    assert again["text"].replace("\n", "") == full  # the wrapped bubble, line by line
    assert not again["preview"] and again["revised"]
    assert again["note"] == "之前按预览处理过，现在是完整内容"
    # Reading the chat or the list again adds nothing.
    assert store.ingest("qq_screen", await read(chat_reader), [GROUP]) == 0
    assert store.ingest("qq_screen", await read(list_reader), [GROUP]) == 0
    assert len(service.db.rows("SELECT id FROM messages")) == 1


def test_a_list_time_with_a_stray_digit_keeps_its_row():
    # OCR read 「昨天20:41」 as 「昨天20:411」 twice on 2026-11-14, and the row was dropped.
    assert list_label("昨天20:411") == "昨天20:41"
    assert list_label("16:091") == "16:09"
    assert list_label("昨天20:41") == "昨天20:41"
    assert list_label("星期四") == "星期四"
    assert (
        list_label("20:4") is None and list_label("好的1") is None and list_label("20:4112") is None
    )
    blocks = [
        block("学院通知群（虚构）", 0.2, 0.05, 0.016),
        block("昨天20:411", 0.8, 0.052, 0.012, char_w=0.016),
        block("周老师：@全体成员", 0.2, 0.08, 0.014),
    ]
    [item] = parse_preview([TextBlock.from_dict(b) for b in blocks], QQ_LAYOUT, NOW)
    assert (item.chat, item.label, item.sender) == ("学院通知群（虚构）", "昨天20:41", "周老师")
