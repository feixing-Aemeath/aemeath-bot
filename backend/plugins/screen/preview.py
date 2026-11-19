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
# A clock with one more digit than a clock has: OCR adds a stray 「1」 after some labels
# (「昨天20:411」 for 「昨天20:41」, seen on 2026-11-14), which would drop the whole row.
STRAY_DIGIT = re.compile(r"^(.*\d{1,2}[:：]\d{2})\d$")
TAG = re.compile(r"^(\[[^\[\]]{1,8}\])\s*")
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
    top: float = 0.0  # where the preview line sits, as fractions of the captured height
    bottom: float = 0.0


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


def list_label(text):
    """The list's time label as QQ shows it, or None when the text is no time label."""
    label = text.strip()
    if LIST_TIME.match(label):
        return label
    stray = STRAY_DIGIT.match(label)
    return stray[1] if stray and LIST_TIME.match(stray[1]) else None


def conversation_rows(blocks, layout):
    """Every visible header, including rows whose preview is absent or a draft."""
    entries = []
    for row in _rows(blocks, layout):
        last = row[-1]
        if len(row) >= 2 and last.right > 0.85 and (label := list_label(last.text)):
            entries.append(("".join(b.text.strip() for b in row[:-1]), label, []))
        elif entries and not entries[-1][2]:
            entries[-1][2].extend(row)
    return entries


def parse_preview(blocks, layout, reference):
    return preview_items(conversation_rows(blocks, layout), layout, reference)


def preview_items(entries, layout, reference):
    items = []
    for chat, label, row in entries:
        if len(row) >= 2 and row[-1].right > 0.85 and is_badge(row[-1]):
            row = row[:-1]  # unread count, or a muted / pinned icon read as a letter
        text = " ".join(b.text.strip() for b in row).strip()
        tags, content_tags = [], []
        while match := TAG.match(text):
            tags.append(match[1])
            if match[1] not in MENTION_TAGS:
                content_tags.append(match[0])
            text = text[match.end() :]
        text = "".join(content_tags) + text
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
                chat,
                sender,
                text,
                parse_list_time(label, reference),
                priority,
                everyone,
                label,
                min(b.y for b in row),
                max(b.bottom for b in row),
            )
        )
    return items
