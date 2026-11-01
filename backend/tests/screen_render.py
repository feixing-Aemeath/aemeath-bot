"""Render a synthetic chat transcript into the OCR blocks a Mac helper would return.

Geometry mimics the QQ / WeChat chat area: names and bubbles on the left, own messages right
aligned, time separators and notices centered. The transcript is laid out on one tall canvas
and cut into overlapping windows (page 0 = newest). Lines crossing a window edge come back
clipped and half-recognized, as real OCR would return them. All names and texts are invented.
"""

import random

BODY_H, NAME_H, CENTER_H = 0.016, 0.012, 0.012
CHAR_W, CENTER_CHAR_W = 0.021, 0.013
LINE_GAP, NAME_GAP, MESSAGE_GAP = 0.005, 0.007, 0.024
MAX_CHARS = 24


def wrap(text, width=MAX_CHARS):
    lines = []
    for paragraph in text.split("\n"):
        lines.extend(paragraph[i : i + width] for i in range(0, max(len(paragraph), 1), width))
    return lines


def block(text, x, y, h, w=None, char_w=CHAR_W):
    return {"text": text, "x": x, "y": y, "w": w or len(text) * char_w, "h": h}


def layout(items, top=0.02):
    """Canvas blocks (y grows downward, may exceed 1) and the canvas height."""
    blocks, y = [], top
    for item in items:
        if "time" in item or "notice" in item:
            text = item.get("time") or item["notice"]
            width = len(text) * CENTER_CHAR_W
            blocks.append(block(text, 0.5 - width / 2, y, CENTER_H, width))
            y += CENTER_H + MESSAGE_GAP
            continue
        if item.get("from"):
            x = 0.09
            if item.get("badge"):
                blocks.append(block(item["badge"], x, y, NAME_H, char_w=0.015))
                x += len(item["badge"]) * 0.015 + 0.01
            blocks.append(block(item["from"], x, y, NAME_H, char_w=0.015))
            y += NAME_H + NAME_GAP
        lines = wrap(item.get("text") or item["me"])
        for index, line in enumerate(lines):
            width = len(line) * CHAR_W
            x = 0.895 - width if "me" in item else 0.105
            if item.get("split") and index == 0 and len(line) > 4:
                # OCR sometimes returns one visual line as two blocks.
                cut = len(line) // 2
                blocks.append(block(line[:cut], x, y, BODY_H))
                blocks.append(block(line[cut:], x + cut * CHAR_W + 0.004, y + 0.0007, BODY_H))
            else:
                blocks.append(block(line, x, y, BODY_H, width))
            y += BODY_H + (LINE_GAP if index < len(lines) - 1 else 0)
        y += MESSAGE_GAP
    return blocks, y


def frames(items, step=0.6, jitter=0.0, seed=7):
    """Overlapping screenshots, newest first; the last one is marked at_top."""
    blocks, height = layout(items)
    rng = random.Random(seed)
    if height <= 1:
        tops = [0.0]
    else:
        tops, top = [], height - 1
        while top > 0:
            tops.append(top)
            top -= step
        tops.append(0.0)
    result = []
    for top in tops:
        visible = []
        for item in blocks:
            start, end = item["y"] - top, item["y"] + item["h"] - top
            if end <= 0 or start >= 1:
                continue
            clipped = dict(item)
            if start < 0 or end > 1:
                # Half a line at the edge: clipped box, garbled text.
                clipped["text"] = item["text"][: max(1, len(item["text"]) // 2)]
                clipped["y"] = max(start, 0.0)
                clipped["h"] = min(end, 1.0) - clipped["y"]
            else:
                clipped["y"] = start
            if jitter:
                clipped["x"] = max(0.0, clipped["x"] + rng.uniform(-jitter, jitter))
                if 0 < clipped["y"] and clipped["y"] + clipped["h"] < 1:
                    clipped["y"] += rng.uniform(-jitter, jitter) / 4
            visible.append(clipped)
        result.append({"blocks": visible})
    result[-1]["at_top"] = True
    return result


def document(chats, captured_at="2026-10-15T21:00:00+08:00", preview=None, **options):
    """A FakeScreenCapture document: {chat: transcript items or (items, announcement text)}."""
    data = {"captured_at": captured_at, "chats": {}}
    for chat, items in chats.items():
        announcement = None
        if isinstance(items, tuple):
            items, announcement = items
        entry = {"frames": frames(items, **options)}
        if announcement:
            lines = wrap(announcement, 30)
            entry["announcement"] = {
                "blocks": [
                    block(line, 0.08, 0.05 + i * 0.03, BODY_H) for i, line in enumerate(lines)
                ]
            }
        data["chats"][chat] = entry
    if preview is not None:
        data["preview"] = {"blocks": preview}
    return data


WORDS = (
    "收到",
    "好的",
    "明天交作业",
    "@全体成员 周五前提交实验报告",
    "[图片]",
    "[表情]",
    "谢谢老师",
    "第三章习题 1-10 题，下周一交到课代表处，逾期扣分",
    "OK",
)


def random_transcript(rng):
    """Random group chat: (items, expected [(sender, text)]) with repeats and long bubbles."""
    items, expected, minute = [], [], 0
    for index in range(rng.randint(5, 70)):
        if index == 0 or rng.random() < 0.15:
            minute += rng.randint(3, 50)
            items.append({"time": f"{8 + minute // 60:02d}:{minute % 60:02d}"})
        roll = rng.random()
        text = rng.choice(WORDS) * rng.choice([1, 1, 1, 2, 5])
        if roll < 0.1:
            items.append({"notice": "某同学撤回了一条消息"})
        elif roll < 0.3:
            items.append({"me": text})
            expected.append(("我", text))
        else:
            sender = rng.choice(["李老师", "王同学", "陈同学"])
            items.append({"from": sender, "text": text, "split": rng.random() < 0.2})
            expected.append((sender, text))
    return items, expected
