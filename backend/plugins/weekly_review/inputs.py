"""Weekly numbers from the core and enabled plugins; no messages or source excerpts."""

import asyncio
from datetime import datetime, time, timedelta

from plugins.school.common import local, zone_of
from plugins.token_usage.usage import Ledger

MISSING = "没有数据"


def missing():
    return {"status": MISSING}


def item(todo):
    return {key: todo[key] for key in ("title", "due_at", "has_time")}


class Inputs:
    def __init__(self, context, monday, now):
        self.context, self.monday, self.now = context, monday, now
        self.zone = zone_of(context)
        self.start = datetime.combine(monday, time(), self.zone)
        self.end = datetime.combine(monday + timedelta(days=7), time(), self.zone)
        self.cutoff = min(now, self.end)

    def within(self, value):
        return value is not None and self.start <= local(value, self.zone) < self.end

    def known(self, todos):
        return [
            t
            for t in todos
            if local(t["created_at"], self.zone) < self.end
            and local(t["created_at"], self.zone) <= self.now
        ]

    def next_week(self, todos):
        return [
            item(t)
            for t in self.known(todos)
            if t["status"] == "pending"
            and t["due_at"]
            and self.end <= local(t["due_at"], self.zone) < self.end + timedelta(days=7)
        ]

    def tasks(self, todos):
        todos = self.known(todos)
        done = [
            t
            for t in todos
            if t["status"] == "completed"
            and self.within(t["updated_at"])
            and local(t["updated_at"], self.zone) <= self.now
        ]
        pending = [t for t in todos if t["status"] == "pending"]
        overdue = []
        for todo in pending:
            due = local(todo["due_at"], self.zone)
            if due is not None:
                # A date without a clock lasts all day, as in the core's todo convention.
                deadline = (
                    due
                    if todo["has_time"]
                    else datetime.combine(due.date() + timedelta(days=1), time(), self.zone)
                )
                if deadline <= self.cutoff:
                    overdue.append(todo["title"])
        if not done and not pending:
            return missing()
        return {
            "status": "ok",
            "completed": len(done),
            "overdue": overdue,
            "pending": [item(t) for t in pending],
        }

    async def read(self):
        todos = await asyncio.to_thread(
            self.context.store.db.rows,
            "SELECT title,due_at,has_time,status,created_at,updated_at FROM todos "
            "WHERE status!='deleted' ORDER BY due_at,title",
        )
        result = {"todos": self.tasks(todos)}
        upcoming = self.next_week(todos)
        result["next_week"] = {
            "from": self.end.date().isoformat(),
            "to": (self.end.date() + timedelta(days=6)).isoformat(),
            "todos": upcoming if todos else MISSING,
            "homework": MISSING,
            "exams": [
                t
                for t in upcoming
                if any(w in t["title"] for w in ("考试", "期中", "期末", "小测", "测验"))
            ]
            if todos
            else MISSING,
        }
        for identifier in ("homework", "timetable", "campus", "health", "token_usage"):
            plugin = self.context.get_plugin(identifier) if self.context.get_plugin else None
            key = {"campus": "activities", "token_usage": "tokens"}.get(identifier, identifier)
            result[key] = missing()
            if plugin is None:
                continue
            try:
                result[key] = await getattr(self, identifier)(plugin)
                if identifier == "homework":
                    result["next_week"]["homework"] = result[key].pop("next_week", MISSING)
            except Exception:
                # One unavailable plugin must not prevent the other sections being read.
                result[key] = missing()
        return result

    async def homework(self, plugin):
        todos = await asyncio.to_thread(plugin.homework)
        return {**self.tasks(todos), "next_week": self.next_week(todos) if todos else MISSING}

    async def timetable(self, plugin):
        data = await plugin.timetable_days({"from": self.monday.isoformat(), "days": 7})
        days = [day for day in data["days"] if day["known"]]
        if not days:
            return missing()
        return {
            "status": "ok",
            "classes": sum(sum(e["kind"] == "课程" for e in day["entries"]) for day in days),
            "days": [
                {
                    "date": day["date"],
                    "classes": sum(e["kind"] == "课程" for e in day["entries"]),
                    "estimated_from": day.get("estimated_from"),
                }
                for day in days
            ],
        }

    async def campus(self, plugin):
        data = await plugin.campus_list({"what": "events"})
        titles = [
            e["title"]
            for e in data["items"]
            if self.within(e["starts_at"]) or self.within(e["signup_deadline"])
        ]
        # campus stores notices, not attendance or the user's registration state.
        return {
            "status": MISSING,
            "attended_or_registered": MISSING,
            "recorded_titles": titles,
            "note": "活动只记录了通知，没有已参加或已报名状态；不能把通知当作参加记录。",
        }

    async def health(self, plugin):
        arguments = {"date": (self.monday + timedelta(days=6)).isoformat(), "days": 7}
        daily = await plugin.health_get_daily(arguments)
        sleep = await plugin.health_get_sleep(arguments)
        scores = {d["date"]: d["readiness"]["score"] for d in daily["days"]}
        nights = {n["date"]: n for n in sleep["nights"]}
        days = []
        for offset in range(7):
            day = (self.monday + timedelta(days=offset)).isoformat()
            night = nights.get(day, {})
            quality = night.get("quality", "missing")
            days.append(
                {
                    "date": day,
                    "sleep_minutes": night.get("total_minutes") if quality == "ok" else None,
                    "sleep_quality": quality,
                    "readiness": scores.get(day),
                }
            )
        slept = [d["sleep_minutes"] for d in days if d["sleep_minutes"] is not None]
        ready = [d for d in days if d["readiness"] is not None]
        return {
            "status": "ok" if slept or ready else MISSING,
            "days": days,
            "average_sleep_minutes": round(sum(slept) / len(slept), 1) if slept else MISSING,
            "sleep_days": len(slept),
            "readiness_change": round(ready[-1]["readiness"] - ready[0]["readiness"], 1)
            if len(ready) >= 2
            else MISSING,
            "readiness_from": ready[0]["date"] if ready else None,
            "readiness_to": ready[-1]["date"] if ready else None,
        }

    async def token_usage(self, plugin):
        last = self.monday + timedelta(days=6)
        ledger = await asyncio.to_thread(
            Ledger, self.context.store.db, self.zone, last, plugin.config
        )
        if not any(self.monday <= row["day"] <= last for row in ledger.rows):
            return missing()
        return {
            "status": "ok",
            "total": ledger.total(self.monday, last),
            "assistant": ledger.total(self.monday, last, own=False),
            "own": ledger.total(self.monday, last, own=True),
            "note": "输入（含缓存）加输出；仅统计有日期的记录，不拆分手填的累计用量。",
        }
