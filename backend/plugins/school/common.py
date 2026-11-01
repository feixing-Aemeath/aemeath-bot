"""Shared by the homework, campus and grades plugins.

The Agent writes school records through plugin MCP tools while it reads messages (see
prompts/extract_todos.md) or chats with a Bot. Like a todo, every record it writes cites the
messages it came from; the checks here keep it from inventing references, keys or dates.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.store import text_key

WEEKDAYS = "一二三四五六日"


def arguments_of(arguments, allowed, required=()):
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    unknown = set(arguments) - set(allowed)
    if unknown:
        raise ValueError(f"unknown arguments: {', '.join(sorted(unknown))}")
    missing = [key for key in required if arguments.get(key) in (None, "")]
    if missing:
        raise ValueError(f"missing arguments: {', '.join(missing)}")
    return arguments


def text(arguments, key, limit=200, default=None):
    value = arguments.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ValueError(f"{key} must be text of at most {limit} characters")
    return value.strip() or None


def choice(arguments, key, options, default=None):
    value = arguments.get(key, default)
    if value is not None and value not in options:
        raise ValueError(f"{key} must be one of {', '.join(options)}")
    return value


def source_refs(db, arguments, required=True):
    """Message IDs the record came from; each must exist."""
    refs = arguments.get("source_refs")
    if refs is None and not required:
        return []
    if (
        not isinstance(refs, list)
        or not 1 <= len(refs) <= 50
        or not all(isinstance(ref, str) and ref for ref in refs)
    ):
        raise ValueError("source_refs must list 1-50 message IDs")
    refs = list(dict.fromkeys(refs))
    placeholders = ",".join("?" for _ in refs)
    found = db.rows(f"SELECT id FROM messages WHERE id IN ({placeholders})", refs)
    if len(found) != len(refs):
        raise ValueError("source_reference_missing")
    return refs


def moment(arguments, key, zone):
    """An ISO 8601 time with offset, or a bare date (local midnight, no time of day).

    Returns (UTC ISO string, has_time) or (None, False)."""
    value = arguments.get(key)
    if value is None:
        return None, False
    if not isinstance(value, str):
        raise ValueError(f"{key} must be an ISO 8601 date or time")
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return datetime.combine(day, time(), zone).astimezone(UTC).isoformat(), False
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{key} must be an ISO 8601 date or time") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{key} needs a time zone offset, e.g. +08:00")
    return parsed.astimezone(UTC).isoformat(), True


def key(*parts):
    """Deduplication key: case, width, spaces and punctuation do not count."""
    return "|".join(text_key(part or "") for part in parts)


def local(value, zone):
    return datetime.fromisoformat(value).astimezone(zone) if value else None


def zone_of(context):
    return ZoneInfo(context.store.settings.timezone)


def day_text(day, today):
    """今天 / 明天 / 昨天 / 周四 (this week) / 10月30日 / 2027年1月5日."""
    delta = (day - today).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == -1:
        return "昨天"
    if delta == 2:
        return "后天"
    monday = today - timedelta(days=today.weekday())
    if monday <= day < monday + timedelta(days=7):
        return f"周{WEEKDAYS[day.weekday()]}"
    if day.year == today.year:
        return f"{day.month}月{day.day}日"
    return f"{day.year}年{day.month}月{day.day}日"


def when_text(value, has_time, now):
    """A stored UTC time for people: 「明天 18:00」, 「周四」, 「10月30日 14:00」."""
    at = local(value, now.tzinfo)
    label = day_text(at.date(), now.date())
    return f"{label} {at:%H:%M}" if has_time else label


def noon(day, zone):
    """A moment on `day`, for wording times relative to that day (the daily folder's files)."""
    return datetime.combine(day, time(12), zone)


def week_start(day):
    return day - timedelta(days=day.weekday())
