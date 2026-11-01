"""The school's 个人日程 page (portal.example.edu.cn/#/schedule) in its week view (周历), read from the
text Apple Vision recognized in a screenshot of the browser window that shows it.

    ‹ › [2026-10-25]                      周历 月历 列表
    日历名称 │ 19 周一 │ 20 周二      │ … │ 25 周日
    个人日程 │         │              │   │            (entries the user added)
    我的课程 │ 09:55-  │ 08:00-09:35  │   │            (courses)
             │ 11:30   │ 大学英语     │   │
             │ 俱乐部… │ （1）        │   │

The weekday labels give the columns; the date picker gives the week (checked against the day
numbers above the labels, which alone give it when the picker was not read). An entry is a
time range followed by the lines of its title in the same column. Entries that start above
the bottom of the 个人日程 label are the user's own; the rest are courses.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from plugins.screen.helper import HelperScreenCapture, HelperTextRecognizer, JsonHelper

WEEKDAYS = "一二三四五六日"
WEEKDAY = re.compile(r"周([一二三四五六日天])")
TIME = re.compile(r"(\d{1,2})[:：](\d{2})\s*[-–—~～至]\s*(\d{1,2})[:：](\d{2})")
PICKER = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
NUMBER = re.compile(r"\d{1,2}")
# 高等数学I is read as 高等数学| or 高等数学丨: a bar after Chinese is a Roman one.
ROMAN_ONE = re.compile(r"(?<=[一-鿿])[|丨](?=$|[（(])")


class PageError(ValueError):
    """The window is not the week view, or its week cannot be told. The message is a fixed
    code (runs and the cards show it), never text from the page."""


@dataclass(frozen=True)
class Entry:
    day: date
    start: str  # "08:00"
    end: str
    title: str
    kind: str  # 课程 or 日程 (the user's own)


@dataclass(frozen=True)
class Week:
    monday: date
    entries: tuple[Entry, ...]


async def read_window(helper, out_dir, title):
    """Capture the browser window titled `title` and recognize its text, on this Mac.
    Returns (screenshot, text blocks)."""
    json_helper = JsonHelper(helper, app="chrome", out_dir=out_dir)
    screenshot = await HelperScreenCapture(json_helper).capture_page(title)
    return screenshot, await HelperTextRecognizer(json_helper).recognize(screenshot)


def compact(text):
    return re.sub(r"\s+", "", text)


def center(block):
    return block.x + block.w / 2, block.y + block.h / 2


def weekday_labels(blocks):
    """The seven weekday labels on one row, left to right, or None."""
    found = []
    for block in blocks:
        match = WEEKDAY.fullmatch(compact(block.text))
        if match:
            index = 6 if match.group(1) == "天" else WEEKDAYS.index(match.group(1))
            found.append((index, block))
    for _, first in (item for item in found if item[0] == 0):
        _, row_y = center(first)
        row = {}
        for index, block in found:
            if abs(center(block)[1] - row_y) <= first.h:
                row.setdefault(index, block)
        labels = [row.get(index) for index in range(7)]
        if None not in labels and all(
            center(a)[0] < center(b)[0] for a, b in zip(labels, labels[1:], strict=False)
        ):
            return labels
    return None


def week_of(blocks, labels, columns, today):
    """Monday of the week shown: from the date picker, else from the day numbers."""
    numbers = {}
    for index, label in enumerate(labels):
        for block in blocks:
            x, y = center(block)
            if (
                NUMBER.fullmatch(compact(block.text))
                and columns[index][0] <= x < columns[index][1]
                and label.y - 3 * label.h <= y < label.y
            ):
                numbers[index] = int(compact(block.text))
                break

    def fits(monday):
        wrong = sum((monday + timedelta(days=i)).day != n for i, n in numbers.items())
        return wrong <= 1 if len(numbers) >= 3 else wrong == 0

    for block in blocks:
        match = PICKER.search(compact(block.text))
        if match and block.bottom <= labels[0].y:
            try:
                shown = date(*(int(part) for part in match.groups()))
            except ValueError:
                continue
            monday = shown - timedelta(days=shown.weekday())
            if fits(monday):
                return monday
    if len(numbers) >= 3:
        this = today - timedelta(days=today.weekday())
        candidates = [this + timedelta(weeks=k) for k in range(-10, 11)]
        matching = [m for m in candidates if fits(m)]
        if matching:
            return min(matching, key=lambda m: abs((m - this).days))
    raise PageError("timetable_week_unreadable")


def tidy(lines):
    title = "".join(line.strip() for line in lines)
    title = title.replace("(", "（").replace(")", "）")
    return ROMAN_ONE.sub("I", title)


def clock(hours, minutes):
    hours, minutes = int(hours), int(minutes)
    if hours > 23 or minutes > 59:
        return None
    return f"{hours:02d}:{minutes:02d}"


def parse_week(blocks, today):
    """The week the page shows, from its recognized text blocks; `today` picks the week when
    only the day numbers were read."""
    labels = weekday_labels(blocks)
    if labels is None or not any(compact(block.text) == "日历名称" for block in blocks):
        raise PageError("timetable_not_week_view")
    xs = [center(label)[0] for label in labels]
    step = (xs[-1] - xs[0]) / 6
    columns = [(x - step / 2, x + step / 2) for x in xs]
    monday = week_of(blocks, labels, columns, today)
    header = max(label.bottom for label in labels)
    personal = next(
        (b for b in blocks if compact(b.text) == "个人日程" and center(b)[0] < columns[0][0]),
        None,
    )

    entries = []
    for index, (left, right) in enumerate(columns):
        column = sorted(
            (b for b in blocks if left <= center(b)[0] < right and b.y > header),
            key=lambda b: b.y,
        )
        current = None
        for block in column:
            match = TIME.fullmatch(compact(block.text))
            if match:
                start, end = clock(*match.groups()[:2]), clock(*match.groups()[2:])
                current = None
                if start and end and start < end:
                    kind = "日程" if personal and block.y < personal.bottom else "课程"
                    current = {"start": start, "end": end, "kind": kind, "lines": [], "last": block}
                    current["day"] = monday + timedelta(days=index)
                    entries.append(current)
            elif current and block.y - current["last"].bottom < 2 * current["last"].h:
                current["lines"].append(block.text)
                current["last"] = block
            else:
                current = None  # something else in the column, not an entry's title
    return Week(
        monday,
        tuple(
            sorted(
                (
                    Entry(e["day"], e["start"], e["end"], tidy(e["lines"]), e["kind"])
                    for e in entries
                    if tidy(e["lines"])
                ),
                key=lambda e: (e.day, e.start, e.title),
            )
        ),
    )
