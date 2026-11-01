"""Turning OCR blocks of one screenshot into classified lines.

The chat area of QQ and WeChat has a fixed geometry:

    ┌────────────────────────────────────────────┐
    │                  20:15                     │  time separator, centered
    │ [头像] 李老师                               │  sender name: left, smaller font
    │        @全体成员 周五前交实验报告            │  incoming bubble: left
    │                            收到 [头像]      │  own message: right aligned
    │           王同学撤回了一条消息               │  system notice, centered
    └────────────────────────────────────────────┘

Thresholds are fractions of the captured area (or of the body text height) and can be
overridden per machine in the plugin config after calibrating on the Mac.
"""

import re
import unicodedata
from dataclasses import dataclass, field, fields, replace

from plugins.screen.model import TextBlock
from plugins.screen.timeparse import is_time_label, parse_separator

PLACEHOLDERS = (
    "[图片]",
    "[表情]",
    "[动画表情]",
    "[语音]",
    "[视频]",
    "[文件]",
    "[链接]",
    "[位置]",
    "[聊天记录]",
)


@dataclass(frozen=True)
class ChatLayout:
    app: str
    everyone_markers: tuple[str, ...]
    self_name: str = "我"
    announcement_markers: tuple[str, ...] = ("群公告",)
    # Group titles shown before a sender name, removed from `sender`.
    badge_pattern: str = r"^(?:(?:群主|管理员|LV\s?\d+|Lv\s?\d+)\s*)+"
    incoming_max_x: float = 0.2  # incoming lines (name and bubble) start left of this
    outgoing_min_right: float = 0.84  # own bubbles end right of this
    center_tolerance: float = 0.12  # |line center - 0.5| for time separators and notices
    name_height_ratio: float = 0.85  # names are shorter than this × body text height
    name_indent: float = 0.006  # names start within this of the leftmost; card rows sit further in
    bubble_gap: float = 0.8  # a vertical gap above this × body height starts a new bubble
    row_overlap: float = 0.5  # blocks overlapping this much vertically form one line
    edge_margin: float = 0.004  # blocks this close to the top/bottom edge are cut off
    space_gap: float = 0.6  # horizontal gap (× height) between blocks that becomes a space
    overrides: dict = field(default_factory=dict, compare=False, repr=False)

    def with_overrides(self, values):
        """Apply the numeric calibration from the plugin config (`layout` object)."""
        numeric = {f.name for f in fields(self) if f.type is float}
        unknown = set(values) - numeric
        if unknown:
            raise ValueError(f"unknown layout settings: {sorted(unknown)}")
        return replace(self, **{k: float(v) for k, v in values.items()})


QQ_LAYOUT = ChatLayout(app="qq", everyone_markers=("@全体成员",))
WECHAT_LAYOUT = ChatLayout(app="wechat", everyone_markers=("@所有人",))


@dataclass(frozen=True)
class Line:
    """A visual line: blocks on the same row merged left to right."""

    kind: str  # time / notice / left / right / other
    text: str
    x: float
    right: float
    top: float
    bottom: float
    gap: float | None  # space above this line (fraction of the screen); None at a frame's top
    when: object = None  # resolved datetime for time separators
    screenshot: str = ""
    pos: float = 0.0  # top in the stitched timeline's coordinates (set by stitch)

    @property
    def height(self):
        return self.bottom - self.top

    @property
    def key(self):
        # Side only: whether a left line is a name is decided on the whole stitched timeline,
        # so one screenshot's statistics can never make the same line look different.
        return (self.kind, normalize(self.text))


def normalize(text):
    """Compare OCR text across screenshots: width-folded, without spaces and punctuation."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        c for c in folded if not c.isspace() and not unicodedata.category(c).startswith(("P", "Z"))
    )


def merge_rows(blocks, layout):
    """Group blocks that share a row; drop empty blocks and rows cut off by the screen edge.

    The edge test applies to whole rows: if one block of a row touches the edge the row is
    dropped as a whole, so a screenshot never keeps half a line that another one has whole.
    """
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
    merged = []
    for row in rows:
        if min(b.y for b in row) <= layout.edge_margin:
            continue
        if max(b.bottom for b in row) >= 1 - layout.edge_margin:
            continue
        row.sort(key=lambda b: b.x)
        text = row[0].text.strip()
        for previous, block in zip(row, row[1:], strict=False):
            spacer = " " if block.x - previous.right > layout.space_gap * block.h else ""
            text += spacer + block.text.strip()
        merged.append(
            TextBlock(
                text=text,
                x=row[0].x,
                y=min(b.y for b in row),
                w=max(b.right for b in row) - row[0].x,
                h=max(b.bottom for b in row) - min(b.y for b in row),
            )
        )
    return merged


def frame_lines(blocks, layout, captured_at, screenshot=""):
    """Rows of one chat screenshot, top to bottom, sorted by position on screen."""
    lines, previous = [], None
    for row in merge_rows(blocks, layout):
        middle = row.x + row.w / 2
        when = None
        if row.x <= layout.incoming_max_x:
            kind = "left"
        elif row.right >= layout.outgoing_min_right:
            kind = "right"
        elif abs(middle - 0.5) <= layout.center_tolerance:
            kind = "time" if is_time_label(row.text) else "notice"
            if kind == "time":
                when = parse_separator(row.text, captured_at)
        else:
            kind = "other"
        gap = None if previous is None else row.y - previous.bottom
        lines.append(
            Line(kind, row.text, row.x, row.right, row.y, row.bottom, gap, when, screenshot, row.y)
        )
        previous = row
    return lines


def body_height(lines):
    """Height of ordinary message text: the upper quartile of side line heights, since sender
    names (smaller font) can make up half of the lines in a group chat."""
    heights = sorted(line.height for line in lines if line.kind in {"left", "right"})
    if not heights:
        return 1.0
    return heights[round(0.75 * (len(heights) - 1))]


def strip_badges(name, layout):
    return re.sub(layout.badge_pattern, "", name).strip() or name.strip()


# QQ shows the send time beside the name of the message under the pointer: "10-27 09:22:50".
HOVER_TIME = re.compile(r"\s*(?:\d{4}-)?(?:\d{1,2}-\d{1,2}\s+)?\d{1,2}:\d{2}:\d{1,2}$")


def sender_name(line, layout):
    return strip_badges(HOVER_TIME.sub("", line), layout)
