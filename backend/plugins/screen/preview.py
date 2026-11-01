"""Conversation list ("preview only" mode): read the latest line of each chat without opening it.

    ┌──────────────────────────────────────┐
    │ [头像] 软件工程2班              21:05 │  header: chat name, time column at the right
    │        李老师：@全体成员 周五前交…  3 │  preview text, unread badge at the right
    └──────────────────────────────────────┘

Previews with a bare clock (today) are used. Older ones carry no minute and were normally read
on their day; the reader only catches up on them when nothing from that day was stored (see
ChatScreenReader.fetch_preview). The preview is truncated by the app, so it is a hint, not the
full message.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from plugins.screen.timeparse import parse_list_time

# A bare clock, or a day with or without its time: QQ 7 writes 「昨天20:59」 without a space.
LIST_TIME = re.compile(
    r"^(?:\d{1,2}[:：]\d{2}|(?:昨天|前天|(?:星期|周)[一二三四五六日天]"
    r"|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}|\d{1,2}[/.-]\d{1,2}|\d{1,2}月\d{1,2}日)"
    r"(?:\s*\d{1,2}[:：]\d{2})?)$"
)
BADGE = re.compile(r"^(?:\d{1,3}\+?|…)$")
TAG = re.compile(r"^\[[^\[\]]{1,8}\]")
SENDER = re.compile(r"^(?P<sender>[^：:\s][^：:]{0,19})[：:]\s*(?P<text>.+)$", re.S)
MENTION_TAGS = ("[有人@我]", "[@全体成员]", "[@所有人]")


@dataclass
class PreviewItem:
    chat: str
    sender: str
    text: str
    sent_at: datetime | None
    priority: bool
    everyone: bool = False
    label: str = ""  # the time column as read: 「20:41」「星期二」


def is_badge(block):
    """An unread count, or one or two characters on their own at the right edge: OCR reads
    the muted bell and similar icons as a stray letter."""
    text = block.text.strip()
    return bool(BADGE.match(text)) or (block.x >= 0.8 and len(text) <= 2)


def _rows(blocks, layout):
    rows = []
    for block in sorted((b for b in blocks if b.text.strip()), key=lambda b: (b.y, b.x)):
        for row in rows:
            top, bottom = min(b.y for b in row), max(b.bottom for b in row)
            overlap = min(bottom, block.bottom) - max(top, block.y)
            if overlap >= layout.row_overlap * min(block.h, bottom - top):
                row.append(block)
                break
        else:
            rows.append([block])
    return [
        sorted(row, key=lambda b: b.x)
        for row in rows
        if min(b.y for b in row) > layout.edge_margin
        and max(b.bottom for b in row) < 1 - layout.edge_margin
    ]


def parse_preview(blocks, layout, reference):
    items, header = [], None
    for row in _rows(blocks, layout):
        last = row[-1]
        if len(row) >= 2 and last.right > 0.85 and LIST_TIME.match(last.text.strip()):
            header = ("".join(b.text.strip() for b in row[:-1]), last.text.strip())
            continue
        if header is None:
            continue
        parts = [b.text.strip() for b in row]
        if len(row) >= 2 and row[-1].right > 0.85 and is_badge(row[-1]):
            parts = parts[:-1]  # unread count, or a muted / pinned icon read as a letter
        text = " ".join(parts).strip()
        chat, label = header
        header = None
        tags = []
        while match := TAG.match(text):
            tags.append(match.group(0))
            text = text[match.end() :].strip()
        if "[草稿]" in tags or not text:
            continue  # the user's own unsent draft
        # Group previews read "李老师：…"; one-to-one chats have no prefix. The full preview
        # stays the message text either way, so a misread prefix never loses words.
        match = SENDER.match(text)
        sender = match["sender"].strip() if match else chat
        priority = any(tag in MENTION_TAGS for tag in tags) or any(
            marker in text for marker in layout.everyone_markers
        )
        everyone = any(tag in ("[@全体成员]", "[@所有人]") for tag in tags) or any(
            marker in text for marker in layout.everyone_markers
        )
        items.append(
            PreviewItem(
                chat, sender, text, parse_list_time(label, reference), priority, everyone, label
            )
        )
    return items
