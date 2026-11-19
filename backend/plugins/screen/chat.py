"""Stitch overlapping screenshots of one chat and assemble its messages.

Screenshots are taken from the newest screen upward, each scrolled so that part of the
previous screen stays visible. Stitching works on classified lines: the bottom lines of an
older screenshot must equal the top lines of the newer timeline, at one constant scroll
offset (a minority read slightly differently by OCR, or seen on one screen only, is
tolerated: see tolerant_overlap; where the text cannot show the overlap, the pixels at the
scroll distance can: see pixel_overlap). Messages are only assembled after stitching, so a long
message cut in two by the screen edge comes back whole, and lines seen twice in the overlap
are kept once.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from difflib import SequenceMatcher
from functools import partial
from itertools import takewhile
from statistics import median

from plugins.screen import pixels
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
    has_separator: bool = False  # distinguishes invalid OCR from a page starting mid-conversation

    @property
    def text(self):
        return "\n".join(self.lines)


@dataclass
class AnnouncementHint:
    """A notice such as「管理员发布了新的群公告」or an announcement card in a bubble."""

    text: str
    sent_at: datetime | None
    position: int  # index of the next message (notices) or of the card message itself


def stitch(timeline, older, max_offset_spread=0.02, scroll=None):
    """Prepend an older screenshot's lines to the timeline (newest screen first).

    `scroll` is how far the capture scrolled between the two screens, as a fraction of the
    chat area's height, when it is known. Returns (timeline, added) where added is the number
    of new lines. Raises ScreenGapError when the screenshots share no lines.
    """
    if not timeline:
        return [replace(line, pos=line.top) for line in older], len(older)
    base = timeline[0].top - timeline[0].pos
    # Only the preceding viewport can overlap this screen, even after a 60-page read.
    adjacent = list(takewhile(lambda line: line.pos + base < 1, timeline))
    limit = min(len(older), len(adjacent))
    for size in range(limit, 1, -1):
        tail, head = older[-size:], timeline[:size]
        if [line.key for line in tail] != [line.key for line in head]:
            continue
        if len({line.key for line in tail if line.key[1]}) < 2:
            continue
        offsets = [a.top - b.pos for a, b in zip(tail, head, strict=True)]
        if max(offsets) - min(offsets) > max_offset_spread:
            # Same text by coincidence, not the same place on screen. Half a line of slack:
            # Vision boxes the same label up to about 1% higher or lower between screenshots.
            continue
        shift = median(offsets)
        if shift < base - max_offset_spread:
            continue
        # The older copies of the overlapping lines know the gap above them; the newer
        # screenshot cut them off at its top edge.
        moved = [replace(line, pos=line.top - shift) for line in older]
        return moved + list(timeline[size:]), len(older) - size
    aligned = tolerant_overlap(adjacent, older, max_offset_spread)
    if aligned is None and scroll is not None:
        aligned = scrolled_overlap(adjacent, older, scroll, max_offset_spread)
    if aligned is None and scroll is not None:
        aligned = pixel_overlap(timeline, older, scroll, max_offset_spread)
    if aligned:
        shift, size = aligned
        moved = [replace(line, pos=line.top - shift) for line in older]
        # The same lines with two different resolved dates (a daily notice repeated on
        # another day) are not one place in the chat.
        pairs, _ = align(older, timeline[:size], shift, 1.5 * max_offset_spread)
        if any(o.when and t.when and o.when != t.when for o, t in pairs):
            raise ScreenGapError("screen_overlap_missing")
        return moved + list(timeline[size:]), len(older) - size
    raise ScreenGapError("screen_overlap_missing")


# A line Vision read a little differently on the two screens (one character of 18 on a real
# QQ group, 2026-11-14) still counts as the same line when everything around it matches; so
# does a very short one, which is mostly OCR of a sticker, emoji or picture.
SIMILAR_TEXT = 0.8
MOSTLY_EXACT = 0.6
SHORT_TEXT = 4


def tolerant_overlap(timeline, older, max_offset_spread):
    """(shift, size) when the older screen's bottom lines are the timeline's top lines at one
    scroll offset, allowing a minority of them to be read differently; else None.

    Lines in the overlap pair up, in order, with a line of the same kind at the same place on
    the other screen. Most of them must be the same line for sure: exact (at one offset) or,
    if longer than SHORT_TEXT, close in text (SIMILAR_TEXT). The rest may be very short lines
    read differently, or read on one screen only (a sticker Vision saw once). A screen that
    merely repeats some words elsewhere, such as a run of「收到」under different names, does
    not pair up like that.
    """
    near = 1.5 * max_offset_spread
    base = timeline[0].top - timeline[0].pos
    shifts = sorted(
        {
            o.top - t.pos
            for o in older
            for t in timeline
            if o.key == t.key and o.top - t.pos >= base - max_offset_spread
        }
    )
    best = None
    for shift in shifts:
        # The overlap: older lines from the timeline's first line down, and timeline lines
        # down to the older screen's last line that fit on it whole (its bottom edge cut off
        # the rest).
        tail = [o for o in older if o.top - shift >= timeline[0].pos - max_offset_spread / 2]
        if len(tail) < 2 or older[-len(tail) :] != tail:
            continue
        head = list(takewhile(partial(on_older, older[-1], shift, near), timeline))
        pairs, unmatched = align(tail, head, shift, near)
        if any(len(line.key[1]) > SHORT_TEXT for line in unmatched):
            continue
        exact = [o.top - t.pos for o, t in pairs if o.key == t.key]
        distinct = {o.key for o, t in pairs if o.key == t.key and o.key[1]}
        if len(distinct) < 2 or max(exact) - min(exact) > max_offset_spread:
            continue
        offset = median(exact)
        sure = 0
        for o, t in pairs:
            if o.key != t.key and (
                o.kind != t.kind
                or abs(o.top - t.pos - offset) > near
                or not close(o.key[1], t.key[1])
            ):
                break
            if o.key == t.key or min(len(o.key[1]), len(t.key[1])) > SHORT_TEXT:
                sure += 1
        else:
            enough = sure >= MOSTLY_EXACT * (len(pairs) + len(unmatched))
            if enough and (best is None or len(exact) > best[2]):
                best = (offset, len(head), len(exact))
    return best[:2] if best else None


# The capture scrolls by a fixed fraction of the chat area; measured on real QQ screens
# (2026-11-14) it moved the content by that fraction to within 0.1 % of the area's height.
SCROLL_TOLERANCE = 0.005


def scrolled_overlap(timeline, older, scroll, max_offset_spread):
    """(shift, size) when at least two different long lines match exactly at the distance the
    capture scrolled, however differently the rest of the overlap was read; else None.

    A picture with small text comes back with other text on every screen while it loads, so
    an overlap that is mostly picture pairs up line by line with nothing (QQ, 2026-11-14).
    Two whole lines landing exactly where the scroll put them is still proof the screens
    overlap; if the scroll went anywhere else, nothing lands there.
    """
    near = 1.5 * max_offset_spread
    newest = timeline[0].screenshot
    sure = [
        (o, t)
        for o in older
        if len(o.key[1]) > SHORT_TEXT
        for t in timeline
        if t.screenshot == newest
        and o.key == t.key
        and abs(o.top - t.top - scroll) <= SCROLL_TOLERANCE
    ]
    if len({o.key for o, _ in sure}) < 2:
        return None
    offsets = [o.top - t.pos for o, t in sure]
    if max(offsets) - min(offsets) > max_offset_spread:
        return None
    shift = median(offsets)
    return shift, len(list(takewhile(partial(on_older, older[-1], shift, near), timeline)))


def pixel_overlap(timeline, older, scroll, max_offset_spread):
    """(shift, size) when the two screenshots' pixels overlap where the scroll put them, however
    the text in the overlap was read; else None (see plugins/screen/pixels.py)."""
    newest, path = timeline[0].screenshot, older[0].screenshot
    if not newest or not path:
        return None
    measured = pixels.overlap(newest, path, scroll, SCROLL_TOLERANCE)
    if measured is None:
        return None
    # A line keeps the top it had on its own screen; pos moved it into the timeline by that
    # screen's shift, the same for every line of it.
    shift = measured + timeline[0].top - timeline[0].pos
    near = 1.5 * max_offset_spread
    return shift, len(list(takewhile(partial(on_older, older[-1], shift, near), timeline)))


def on_older(last, shift, near, line):
    """Whether a timeline line, moved by shift, is down to the older screen's last line and
    fits on that screen whole."""
    return line.pos + shift <= last.top + near and line.pos + shift + line.height <= 1


def align(tail, head, shift, near):
    """Pairs of lines at the same place on both screens, in order, and the lines left over."""

    def same(o, t):
        return o.key == t.key and abs(o.top - shift - t.pos) <= near

    pairs, unmatched = [], []
    i = j = 0
    while i < len(tail) and j < len(head):
        o, t = tail[i], head[j]
        below = o.top - shift - t.pos
        if same(o, t):
            pairs.append((o, t))
            i, j = i + 1, j + 1
        elif i + 1 < len(tail) and same(tail[i + 1], t):
            unmatched.append(o)
            i += 1
        elif j + 1 < len(head) and same(o, head[j + 1]):
            unmatched.append(t)
            j += 1
        elif abs(below) <= near and o.kind == t.kind:
            pairs.append((o, t))
            i, j = i + 1, j + 1
        elif below < 0:
            unmatched.append(o)
            i += 1
        else:
            unmatched.append(t)
            j += 1
    return pairs, unmatched + tail[i:] + head[j:]


def close(a, b):
    if max(len(a), len(b)) <= SHORT_TEXT:
        return True
    return SequenceMatcher(None, a, b).ratio() >= SIMILAR_TEXT


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
    has_separator = False
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

    def labels_bubble(index):
        # Names do not all start at one x: on a real QQ group (2026-11-14) they spread over
        # 3 % of the width, so most of them sat further in than the leftmost one. What every
        # name has is the bubble it labels right below it, set in by the bubble's padding.
        line, below = lines[index], lines[index + 1] if index + 1 < len(lines) else None
        return (
            below is not None
            and below.kind == "left"
            and below.gap is not None
            and layout.name_setin_min <= below.x - line.x <= layout.name_setin_max
            and layout.name_gap_min * body <= below.gap <= layout.name_gap_max * body
        )

    def classify(index, line):
        if line.kind == "left":
            if not name_like(line):
                return "incoming"
            if line.x <= name_x + layout.name_indent or labels_bubble(index):
                return "name"
            return "card"
        if line.kind == "right":
            return "own_name" if small(line) else "outgoing"
        return line.kind

    kinds = [classify(index, line) for index, line in enumerate(lines)]
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
        current = ScreenMessage(
            sender, when, outgoing=outgoing, screenshot=line.screenshot, has_separator=has_separator
        )

    for line, kind in zip(lines, kinds, strict=True):
        if kind == "time":
            close()
            when = line.when
            has_separator = True
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
