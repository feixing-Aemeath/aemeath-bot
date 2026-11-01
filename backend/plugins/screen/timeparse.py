"""Chat time separators ("20:15", "昨天 08:30", "星期一 09:00", "10月12日 08:30" ...).

Chat apps only print relative labels, so every label is resolved against the moment the
screenshot was taken, in the user's time zone. A label that cannot be resolved returns None;
the reader never guesses a time.
"""

import re
from datetime import date, datetime, timedelta

PERIOD = r"(?:(?P<period>上午|下午|中午|晚上|凌晨|早上)\s*)?"
CLOCK = PERIOD + r"(?P<hour>\d{1,2})[:：](?P<minute>\d{2})"
WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
PATTERNS = [
    ("today", re.compile(rf"^{CLOCK}$")),
    ("yesterday", re.compile(rf"^昨天\s*{CLOCK}$")),
    ("before_yesterday", re.compile(rf"^前天\s*{CLOCK}$")),
    ("weekday", re.compile(rf"^(?:星期|周)(?P<weekday>[一二三四五六日天])\s*{CLOCK}$")),
    (
        "full",
        re.compile(
            rf"^(?P<year>\d{{4}})(?:年|[/.-])(?P<month>\d{{1,2}})(?:月|[/.-])"
            rf"(?P<day>\d{{1,2}})日?\s*{CLOCK}$"
        ),
    ),
    (
        "month_day",
        re.compile(rf"^(?P<month>\d{{1,2}})(?:月|[/.-])(?P<day>\d{{1,2}})日?\s*{CLOCK}$"),
    ),
]


def _clock(match):
    hour, minute = int(match["hour"]), int(match["minute"])
    period = match["period"]
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    elif period in {"凌晨", "上午", "早上"} and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _day(kind, match, today):
    if kind == "today":
        return today
    if kind == "yesterday":
        return today - timedelta(days=1)
    if kind == "before_yesterday":
        return today - timedelta(days=2)
    if kind == "weekday":
        # A weekday label is never today (today shows a bare clock).
        delta = (today.weekday() - WEEKDAYS[match["weekday"]]) % 7 or 7
        return today - timedelta(days=delta)
    year = int(match["year"]) if kind == "full" else today.year
    try:
        day = date(year, int(match["month"]), int(match["day"]))
    except ValueError:
        return None
    if kind == "month_day" and day > today:
        day = date(year - 1, day.month, day.day)  # "12月30日" seen on 1 January
    return day


def parse_separator(text, reference):
    """Resolve a separator label against `reference` (an aware datetime); None if not a time."""
    label = re.sub(r"\s+", " ", text.strip())
    for kind, pattern in PATTERNS:
        match = pattern.match(label)
        if not match:
            continue
        clock = _clock(match)
        day = _day(kind, match, reference.date())
        if clock is None or day is None:
            return None
        moment = datetime(day.year, day.month, day.day, *clock, tzinfo=reference.tzinfo)
        # Labels never point to the future; "23:50" read at 00:05 is an app-side quirk.
        return moment if moment <= reference + timedelta(minutes=1) else None
    return None


def is_time_label(text):
    """The label has the shape of a time separator, whether or not it resolves."""
    label = re.sub(r"\s+", " ", text.strip())
    return any(pattern.match(label) for _, pattern in PATTERNS)


def parse_list_time(text, reference):
    """Conversation-list time column: only a bare clock means a message from today."""
    label = text.strip()
    match = PATTERNS[0][1].match(label)
    return parse_separator(label, reference) if match else None


LIST_DAY = re.compile(
    r"^(?:(?P<relative>昨天|前天)|(?:星期|周)(?P<weekday>[一二三四五六日天]))\s*(?:\d{1,2}[:：]\d{2})?$"
)


def parse_list_day(text, reference):
    """The day of an older list label from the last week (「昨天」「星期二」, with or without a
    clock), or None. Dates further back are not caught up on."""
    match = LIST_DAY.match(re.sub(r"\s+", " ", text.strip()))
    if not match:
        return None
    today = reference.date()
    if match["relative"]:
        return today - timedelta(days=1 if match["relative"] == "昨天" else 2)
    return _day("weekday", match, today)
