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

The month view (月历) is read too, so a whole month can be saved at once:

    ‹ › [2026-11-01]                      周历 月历 列表
    周一       │ 周二         │ … │ 周日
    26 十七    │ 27 十八      │ … │ 11 01 廿三     (days of other months: no entries)
    02 廿四    │ 03 廿五      │ … │ 08 三十
    09:55-11:30│ 08:00-09:35  │   │
    俱乐部体育1│ 大学英语（1）│   │

The picker gives the month, each row of day numbers a week. The month view shows nothing on
days of the months before and after, so only the weeks wholly inside the month are kept. Its
entries all count as courses: unlike the week view it has no row for the user's own.
"""

import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, timedelta

from plugins.screen.helper import HelperScreenCapture, HelperTextRecognizer, JsonHelper

WEEKDAYS = "一二三四五六日"
WEEKDAY = re.compile(r"周([一二三四五六日天])")
TIME = re.compile(r"(\d{1,2})[:：](\d{2})\s*[-–—~～至]\s*(\d{1,2})[:：](\d{2})")
TIME_HINT = re.compile(r"^[\dOoＯ]{1,2}[:：]\d{2}(?:[-–—~～至]|$)")
PICKER = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
NUMBER = re.compile(r"\d{1,2}")
# A day number in the month view: "02", or month and day on a month's first ("11 01").
DAY_NUMBER = re.compile(r"\d{1,4}")
# 高等数学I is read as 高等数学| or 高等数学丨: a bar after Chinese is a Roman one.
ROMAN_ONE = re.compile(r"(?<=[一-鿿])[|丨](?=$|[（(])")


class PageError(ValueError):
    """The window is not the week or month view, or its week or month cannot be told. The
    message is a fixed code (runs and the cards show it), never text from the page."""


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


def columns_of(labels):
    xs = [center(label)[0] for label in labels]
    step = (xs[-1] - xs[0]) / 6
    return [(x - step / 2, x + step / 2) for x in xs]


def collect(column, day_of, kind_of, stop=lambda block: False):
    """The entries in one column, top to bottom: a time range, then the lines of its title."""
    found, current = [], None
    skip = -1
    for index, block in enumerate(column):
        if index == skip:
            continue
        value = compact(block.text)
        match = TIME.fullmatch(value)
        last = block
        if not match and TIME_HINT.search(value):
            # Narrow columns wrap the range into two lines, e.g. "09:55-" / "11:30".
            following = column[index + 1] if index + 1 < len(column) else None
            if following and not stop(following) and following.y - block.bottom < 2 * block.h:
                match = TIME.fullmatch(value + compact(following.text))
                if match:
                    last, skip = following, index + 1
            if not match:
                raise PageError("timetable_entries_unreadable")
        if match:
            start, end = clock(*match.groups()[:2]), clock(*match.groups()[2:])
            if not start or not end or start >= end:
                raise PageError("timetable_entries_unreadable")
            current = {"start": start, "end": end, "lines": [], "last": last}
            current.update(day=day_of(block), kind=kind_of(block))
            found.append(current)
        elif (
            current and not stop(block) and block.y - current["last"].bottom < 2 * current["last"].h
        ):
            current["lines"].append(block.text)
            current["last"] = block
        else:
            current = None  # something else in the column, not an entry's title
    return found


def entries_of(found):
    if any(not tidy(e["lines"]) for e in found):
        raise PageError("timetable_entries_unreadable")
    return tuple(
        sorted(
            (Entry(e["day"], e["start"], e["end"], tidy(e["lines"]), e["kind"]) for e in found),
            key=lambda e: (e.day, e.start, e.title),
        )
    )


def parse_week(blocks, today):
    """The week the page shows, from its recognized text blocks; `today` picks the week when
    only the day numbers were read."""
    labels = weekday_labels(blocks)
    if labels is None or not any(compact(block.text) == "日历名称" for block in blocks):
        raise PageError("timetable_not_week_view")
    columns = columns_of(labels)
    monday = week_of(blocks, labels, columns, today)
    header = max(label.bottom for label in labels)
    personal = next(
        (b for b in blocks if compact(b.text) == "个人日程" and center(b)[0] < columns[0][0]),
        None,
    )

    def kind_of(block):
        return "日程" if personal and block.y < personal.bottom else "课程"

    found = []
    for index, (left, right) in enumerate(columns):
        column = sorted(
            (b for b in blocks if left <= center(b)[0] < right and b.y > header),
            key=lambda b: b.y,
        )
        day = monday + timedelta(days=index)
        found += collect(column, lambda _, day=day: day, kind_of)
    return Week(monday, entries_of(found))


def settle_spelling(entries):
    """Correct a one-Han-character misread against repeated titles in the same weekly slot;
    distinct weekdays, times and course numbers are not evidence of a spelling mistake."""

    def slot(entry):
        return entry.day.weekday(), entry.start, entry.end, entry.kind

    counts = Counter((slot(e), e.title) for e in entries)

    def best(entry):
        title, where = entry.title, slot(entry)
        near = [
            other
            for (other_slot, other), n in counts.items()
            if other_slot == where
            and n > counts[where, title]
            and n >= 2
            and len(other) == len(title)
            and len(differences := [(a, b) for a, b in zip(other, title, strict=True) if a != b])
            == 1
            # Course numbers and Roman numerals distinguish real courses, not OCR spellings.
            and all("一" <= c <= "鿿" for pair in differences for c in pair)
        ]
        return max(near, key=lambda other: counts[where, other]) if near else title

    return tuple(replace(e, title=best(e)) for e in entries)


def parse_month(blocks):
    """The weeks of the month view that lie wholly inside the month it shows, oldest first."""
    labels = weekday_labels(blocks)
    if labels is None or any(compact(block.text) == "日历名称" for block in blocks):
        raise PageError("timetable_not_week_view")
    columns = columns_of(labels)
    header = max(label.bottom for label in labels)
    shown = None
    for block in blocks:
        match = PICKER.search(compact(block.text))
        if match and block.bottom <= labels[0].y:
            try:
                shown = date(*(int(part) for part in match.groups()))
            except ValueError:
                continue
            break
    if shown is None:
        raise PageError("timetable_month_unreadable")
    first = shown.replace(day=1)
    start = first - timedelta(days=first.weekday())

    def column_of(block):
        x = center(block)[0]
        return next((i for i, (left, right) in enumerate(columns) if left <= x < right), None)

    # Rows of day numbers, top to bottom; each row is a week from `start` on.
    numbers = sorted(
        (
            b
            for b in blocks
            if b.y > header and DAY_NUMBER.fullmatch(compact(b.text)) and column_of(b) is not None
        ),
        key=lambda b: b.y,
    )
    tops = []
    for block in numbers:
        if not tops or block.y - tops[-1] > 1.5 * block.h:
            tops.append(block.y)

    def row_of(block):
        return max((i for i, top in enumerate(tops) if top <= block.y + block.h / 2), default=None)

    def day_of(block):
        return start + timedelta(weeks=row_of(block), days=column_of(block))

    # The numbers must be the days the rows stand for, or the month was misread.
    if len(tops) < 4:
        raise PageError("timetable_month_unreadable")
    for row in range(len(tops)):
        labels_in_row = [b for b in numbers if row_of(b) == row]
        right = sum(int(compact(b.text)[-2:]) == day_of(b).day for b in labels_in_row)
        if len({column_of(b) for b in labels_in_row}) < 3 or right < 0.8 * len(labels_in_row):
            raise PageError("timetable_month_unreadable")

    def is_number(block):
        return DAY_NUMBER.fullmatch(compact(block.text)) is not None

    found = []
    for left, right_edge in columns:
        column = sorted(
            (b for b in blocks if left <= center(b)[0] < right_edge and b.y > tops[0]),
            key=lambda b: b.y,
        )
        found += collect(column, day_of, lambda _: "课程", stop=is_number)
    entries = settle_spelling(entries_of(found))
    weeks = []
    for row in range(len(tops)):
        monday = start + timedelta(weeks=row)
        days = [monday + timedelta(days=i) for i in range(7)]
        if all(day.month == first.month for day in days):
            weeks.append(Week(monday, tuple(e for e in entries if monday <= e.day <= days[-1])))
    return weeks
