"""Stitch overlapping screenshots of one chat and assemble its messages.

Screenshots are taken from the newest screen upward, each scrolled so that part of the
previous screen stays visible. Stitching works on classified lines: the bottom lines of an
older screenshot must equal the top lines of the newer timeline, at one constant scroll
offset. Messages are only assembled after stitching, so a long message cut in two by the
screen edge comes back whole, and lines seen twice in the overlap are kept once.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from statistics import median

from plugins.screen.layout import HOVER_TIME, body_height, normalize, sender_name


class ScreenGapError(RuntimeError):
    """Two consecutive screenshots do not overlap; messages in between could be missing."""


@dataclass
class ScreenMessage:
    sender: str
    sent_at: datetime | None  # None: no time separator above it in the stitched timeline
    lines: list[str] = field(default_factory=list)
    outgoing: bool = False
    screenshot: str = ""
    external_id: str = ""
    priority: bool = False
    announcement: bool = False

    @property
    def text(self):
        return "\n".join(self.lines)


@dataclass
class AnnouncementHint:
    """A notice such as「管理员发布了新的群公告」or an announcement card in a bubble."""

    text: str
    sent_at: datetime | None
    position: int  # index of the next message (notices) or of the card message itself


def stitch(timeline, older, max_offset_spread=0.02):
    """Prepend an older screenshot's lines to the timeline (newest screen first).

    Returns (timeline, added) where added is the number of new lines. Raises ScreenGapError
    when the screenshots share no lines.
    """
    if not timeline:
        return [replace(line, pos=line.top) for line in older], len(older)
    limit = min(len(older), len(timeline))
    for size in range(limit, 0, -1):
        tail, head = older[-size:], timeline[:size]
        if [line.key for line in tail] != [line.key for line in head]:
            continue
        offsets = [a.top - b.pos for a, b in zip(tail, head, strict=True)]
        if max(offsets) - min(offsets) > max_offset_spread:
            # Same text by coincidence, not the same place on screen. Half a line of slack:
            # Vision boxes the same label up to about 1% higher or lower between screenshots.
            continue
        shift = median(offsets)
        # The older copies of the overlapping lines know the gap above them; the newer
        # screenshot cut them off at its top edge.
        moved = [replace(line, pos=line.top - shift) for line in older]
        return moved + list(timeline[size:]), len(older) - size
    raise ScreenGapError("screen_overlap_missing")


def message_id(app, chat, message, occurrence):
    """Stable across runs: same chat, sender, minute, text and position among duplicates."""
    parts = [
        app,
        chat,
        message.sender,
        message.sent_at.isoformat() if message.sent_at else "",
        normalize(message.text),
        str(occurrence),
    ]
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]
    return f"{app}:{digest}"


def assemble(lines, chat, layout):
    """Messages (oldest first) and announcement hints from a stitched timeline."""
    messages, hints = [], []
    current = None
    when = None
    body = body_height(lines)

    def small(line):
        return line.height < layout.name_height_ratio * body

    def name_like(line):
        # OCR boxes a name with its hover time taller, about as tall as message text.
        return line.kind == "left" and (small(line) or HOVER_TIME.search(line.text) is not None)

    # Names start right of the avatars, left of anything in a bubble. A card's rows (QQ's
    # homework card has a table) are as small as names but set in inside the bubble.
    name_x = min((line.x for line in lines if name_like(line)), default=0.0)
    # Names and card rows can be most lines of a short timeline, which makes ordinary text look
    # small too; measure it on the lines set in from the names.
    inset = [
        line
        for line in lines
        if line.kind == "right" or (line.kind == "left" and line.x > name_x + layout.name_indent)
    ]
    if inset:
        body = body_height(inset)

    def classify(line):
        if line.kind == "left":
            if not name_like(line):
                return "incoming"
            return "name" if line.x <= name_x + layout.name_indent else "card"
        if line.kind == "right":
            return "own_name" if small(line) else "outgoing"
        return line.kind

    kinds = [classify(line) for line in lines]
    has_names = "name" in kinds
    last_sender = None

    def close():
        nonlocal current
        if current and current.lines:
            messages.append(current)
        current = None

    def start(sender, outgoing, line):
        nonlocal current
        close()
        current = ScreenMessage(sender, when, outgoing=outgoing, screenshot=line.screenshot)

    for line, kind in zip(lines, kinds, strict=True):
        if kind == "time":
            close()
            when = line.when
        elif kind == "notice":
            close()
            if any(marker in line.text for marker in layout.announcement_markers):
                hints.append(AnnouncementHint(line.text, when, len(messages)))
        elif kind == "name":
            last_sender = sender_name(line.text, layout)
            start(last_sender, False, line)
        elif kind == "own_name":
            start(layout.self_name, True, line)
        else:  # incoming / card / outgoing / other
            outgoing = kind == "outgoing" or (
                kind == "other" and current is not None and current.outgoing
            )
            # A card's rows are spaced wider than lines of text but stay one message.
            near = kind == "card" or (line.gap is not None and line.gap < layout.bubble_gap * body)
            continues = (
                current is not None and current.outgoing == outgoing and (not current.lines or near)
            )
            if not continues:
                if outgoing:
                    sender = layout.self_name
                elif has_names:
                    # Consecutive bubbles from one person in a group; None if the name is
                    # above the stitched timeline.
                    sender = last_sender
                else:
                    sender = chat  # one-to-one chat: every incoming bubble is the other person
                start(sender, outgoing, line)
            current.lines.append(line.text)
    close()

    counts = Counter()
    for message in messages:
        text = message.text
        message.priority = any(marker in text for marker in layout.everyone_markers)
        if any(text.startswith(marker) for marker in layout.announcement_markers):
            message.announcement = message.priority = True
            hints.append(AnnouncementHint(text, message.sent_at, messages.index(message)))
        if message.sent_at is None or message.sender is None:
            continue
        key = (message.sender, message.sent_at, normalize(text))
        message.external_id = message_id(layout.app, chat, message, counts[key])
        counts[key] += 1
    return messages, hints
