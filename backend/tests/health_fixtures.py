"""Builders for synthetic health observations; no real person's data."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from plugins.health.observations import STAGE_CODES, Observation

SHANGHAI = ZoneInfo("Asia/Shanghai")
WATCH = "示例用户的Apple Watch"
PHONE = "示例用户的iPhone"


def at(text, zone=SHANGHAI):
    """Local "YYYY-MM-DD HH:MM[:SS]" to UTC epoch seconds."""
    pattern = "%Y-%m-%d %H:%M:%S" if text.count(":") == 2 else "%Y-%m-%d %H:%M"
    return int(datetime.strptime(text, pattern).replace(tzinfo=zone).timestamp())


def obs(kind, start, end=None, value=1.0, label="", source=WATCH, device="watch", origin="export"):
    start = at(start) if isinstance(start, str) else start
    end = start if end is None else (at(end) if isinstance(end, str) else end)
    return Observation(kind, start, end, float(value), label, source, device, origin)


def stage(start, end, label, **kwargs):
    return obs("sleep", start, end, STAGE_CODES[label], label, **kwargs)


def entry(value, quality="ok", method=None):
    result = {"value": value, "quality": quality}
    if method:
        result["method"] = method
    return result


def rows_with(metric, values, last=date(2026, 10, 12), quality="ok", method=None):
    """health_daily-like rows: values[-1] lands on `last`; None leaves the day out."""
    rows = {}
    for offset, value in enumerate(reversed(values)):
        day = last - timedelta(days=offset)
        if value is None:
            continue
        rows[day] = {
            "status": "complete",
            "issues": [],
            "wear_hours": 20,
            "metrics": {metric: entry(value, quality, method)},
            "extras": {},
        }
    return rows
