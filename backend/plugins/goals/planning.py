"""Local timetable gaps and counts; the model never supplies progress totals."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.proactive import DEFAULTS, quiet

DAYS = "一二三四五六日"
INTERNAL = ("证据", "验收", "回执", "版本", "唤醒", "失败", "中断")
COUNTABLE = {"app", "speech", "workout", "homework"}


def visible(text, limit=80):
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def effective(profile, state, day):
    change = state.get("week_override", {})
    if change.get("week") != week(day).isoformat():
        return profile
    return {
        **profile,
        "weekly_target": change["target"],
        "schedule": profile["schedule"][: change["target"]],
        "weekly_title": f"这周先做 {change['target']} 次",
    }


def local(value, zone):
    return datetime.fromisoformat(value).astimezone(zone)


def week(day):
    return day - timedelta(days=day.weekday())


def label(moment):
    return f"周{DAYS[moment.weekday()]} {moment.hour}:{moment.minute:02d}"


def counted(records):
    return {r["step_id"] or (r["kind"], r["ref"]) for r in records if r["kind"] in COUNTABLE}


def progress(records, day, zone):
    monday = week(day)
    rows = [r for r in records if monday <= local(r["happened_at"], zone).date() <= day]
    return len(counted(rows)), len(
        {local(r["happened_at"], zone).date() for r in rows if r["kind"] in COUNTABLE}
    )


def satisfied(profile, records):
    results = []
    for criterion in profile["criteria"]:
        if criterion["kind"] == "steps":
            results.append(len(counted(records)) >= criterion["target"])
            continue
        values = [
            r["value"]
            for r in records
            if r["criterion"] == criterion["id"] and r["value"] is not None
        ]
        compare = (
            (lambda v, target=criterion["target"]: v >= target)
            if criterion["comparison"] == "gte"
            else (lambda v, target=criterion["target"]: v <= target)
        )
        results.append(
            len(values) >= criterion["samples"]
            and all(compare(v) for v in values[-criterion["samples"] :])
        )
    return results


def entries(context, day):
    timetable = context.get_plugin("timetable") if context.get_plugin else None
    return timetable.day_entries(day)[0] if timetable else []


def busy_entries(context, day):
    rows = list(entries(context, day))
    homework = context.get_plugin("homework") if context.get_plugin else None
    if homework:
        zone = ZoneInfo(context.store.settings.timezone)
        for todo in homework.homework():
            if (
                todo["status"] != "pending"
                or not todo["due_at"]
                or not todo["has_time"]
                or "goals" in todo["source_plugin"].split(",")
            ):
                continue
            due = local(todo["due_at"], zone)
            if due.date() == day:
                # Reserve the final hand-in minutes; suggest a change, never accept it silently.
                start = max(datetime.combine(day, time(), zone), due - timedelta(minutes=15))
                rows.append(
                    {
                        "start_time": start.strftime("%H:%M"),
                        "end_time": due.strftime("%H:%M"),
                        "title": todo["title"],
                        "kind": "作业",
                    }
                )
    return sorted(rows, key=lambda row: (row["start_time"], row["end_time"]))


def conflicts(context, day, at, minutes):
    end = at + timedelta(minutes=minutes)
    return [
        row
        for row in busy_entries(context, day)
        if at < datetime.combine(day, time.fromisoformat(row["end_time"]), at.tzinfo)
        and end > datetime.combine(day, time.fromisoformat(row["start_time"]), at.tzinfo)
    ]


def gap(context, day, clock, minutes, policy=None):
    """Nearest allowed gap at or after the preferred time."""
    zone = ZoneInfo(context.store.settings.timezone)
    at = datetime.combine(day, time.fromisoformat(clock), zone)
    rows = busy_entries(context, day)
    policy = DEFAULTS | (policy or {})
    for _ in range(24 * 12):
        if at.date() != day:
            return None, "今天课比较满"
        if quiet(at, policy) or quiet(at + timedelta(minutes=minutes - 1), policy):
            at += timedelta(minutes=5)
            continue
        end = at + timedelta(minutes=minutes)
        collision = [
            r
            for r in rows
            if at < datetime.combine(day, time.fromisoformat(r["end_time"]), zone)
            and end > datetime.combine(day, time.fromisoformat(r["start_time"]), zone)
        ]
        if collision:
            until = max(time.fromisoformat(r["end_time"]) for r in collision)
            at = datetime.combine(day, until, zone) + timedelta(minutes=15)
            continue
        previous = [r for r in rows if r["end_time"] <= at.strftime("%H:%M")]
        reason = (
            (
                "交完作业后有空"
                if previous[-1].get("kind") == "作业"
                else f"{previous[-1]['end_time']} 下课后有空"
            )
            if previous
            else ("这时没课" if rows else "按你定的时间")
        )
        return at, reason
    return None, "今天课比较满"


def week_input(goal, records, day, zone, steps):
    if not goal or goal["status"] != "active":
        return None
    monday = week(day)
    profile = effective(goal["profile"], goal["state"], day)
    relevant = [r for r in records if monday <= local(r["happened_at"], zone).date() <= day]
    n, days = progress(records, day, zone)
    return {
        "title": goal["profile"]["title"],
        "weekly_title": profile["weekly_title"],
        "done": n,
        "target": profile["weekly_target"],
        "days_advanced": days,
        "records": [
            {k: r[k] for k in ("id", "kind", "happened_at", "text", "value")} for r in relevant
        ],
        "missed": [
            s["day"]
            for s in steps
            if s["state"] == "missed" and monday <= date.fromisoformat(s["day"]) <= day
        ],
        "blocker": goal["state"].get("blocker"),
        "waiting_for_user": goal["state"].get("silent", False),
        "next_week": "保持本周的量；没回复就不加码",
    }
