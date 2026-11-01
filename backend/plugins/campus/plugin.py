"""Leave requests and school activities.

The Agent records them with campus_event_add and campus_leave_record while it reads
messages (an activity notice is not a todo, so before this plugin it only reached the
digest) or when the user tells a Bot in chat. Both deduplicate, so seeing the same notice in
two groups, or a leave request moving from 审批中 to 已批准, updates one record.

An activity can carry the 第二课堂 credits its notice promises. 「推荐参加」 ranks the ones still
open: those giving credits first, then the nearest sign-up deadline; one that clashes with a
class on the timetable goes last and says so.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta

from core.db import dump, uid
from core.plugins import Plugin as BasePlugin
from plugins.school.common import (
    arguments_of,
    choice,
    day_text,
    key,
    local,
    moment,
    noon,
    source_refs,
    text,
    week_start,
    when_text,
    zone_of,
)
from plugins.timetable.plugin import day_entries

BOT = "校园Bot"  # the character this plugin shows as in the App
EVENT_KINDS = ("讲座", "比赛", "报名", "会议", "考试", "文体", "志愿", "其他")
LEAVE_KINDS = ("事假", "病假", "公假", "其他")
LEAVE_STATUSES = ("待提交", "审批中", "已批准", "已驳回", "已销假")
# Kinds worth recommending: what one chooses to go to, not exams or meetings one is called to.
OPTIONAL_KINDS = ("讲座", "比赛", "报名", "文体", "志愿", "其他")
RECOMMENDED = 5
# An activity with a start time but no end is taken to last this long for clashes.
ASSUMED_HOURS = 1.5

SCHEMA = """
CREATE TABLE IF NOT EXISTS campus_events (
  id TEXT PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL, kind TEXT NOT NULL,
  starts_at TEXT, ends_at TEXT, has_time INTEGER NOT NULL, place TEXT, signup_deadline TEXT,
  note TEXT, source_refs TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  credits REAL, credit_kind TEXT);
CREATE TABLE IF NOT EXISTS campus_leaves (
  id TEXT PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
  starts_at TEXT NOT NULL, ends_at TEXT, has_time INTEGER NOT NULL, reason TEXT,
  status TEXT NOT NULL, approver TEXT, source_refs TEXT NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
"""


def deadline_of(arguments, zone):
    """A sign-up deadline given as a bare date lasts until the end of that day."""
    value, has_time = moment(arguments, "signup_deadline", zone)
    if value and not has_time:
        end = local(value, zone).replace(hour=23, minute=59)
        value = end.astimezone(UTC).isoformat()
    return value


def credits_of(arguments):
    value = arguments.get("credits")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 10:
        raise ValueError("credits must be a number above 0, at most 10")
    return float(value)


def deadline_text(value, now):
    at = local(value, now.tzinfo)
    return when_text(value, (at.hour, at.minute) != (23, 59), now)


def row_view(row):
    item = dict(row)
    item["source_refs"] = json.loads(item["source_refs"])
    item["has_time"] = bool(item["has_time"])
    item.pop("dedup_key", None)
    return item


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        self.db = context.store.db
        with self.db.connect() as conn:
            conn.executescript(SCHEMA)
            # Tables from before activities carried 第二课堂 credits get the two columns.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(campus_events)")}
            for column, kind in (("credits", "REAL"), ("credit_kind", "TEXT")):
                if column not in columns:
                    conn.execute(f"ALTER TABLE campus_events ADD COLUMN {column} {kind}")
        self.past_days = config.get("past_days", 3)

    def now(self):
        return datetime.now(zone_of(self.context))

    def mcp_tools(self):
        return {
            "campus_event_add": self.campus_event_add,
            "campus_leave_record": self.campus_leave_record,
            "campus_list": self.campus_list,
        }

    async def campus_event_add(self, arguments):
        """Record a school activity (lecture, competition, sign-up, meeting, exam, sports or
        arts event, volunteering) from a notice, even when it is only a notice and not a todo.
        The same title on the same day updates one record and merges its sources. Times are
        ISO 8601 with offset, or YYYY-MM-DD for a day without a time. `credits` and
        `credit_kind` only when the notice says how many 第二课堂 (素拓) credits it gives and in
        which category, as it words it. arguments:
        {"title": "...", "kind": "讲座|比赛|报名|会议|考试|文体|志愿|其他", "starts_at"?,
        "ends_at"?, "place"?, "signup_deadline"?, "note"?, "credits"?: 0.5,
        "credit_kind"?: "文体艺术", "source_refs": [message IDs]}."""
        allowed = {
            "title", "kind", "starts_at", "ends_at", "place", "signup_deadline", "note",
            "credits", "credit_kind",
        }  # fmt: skip
        arguments_of(arguments, allowed | {"source_refs"}, required=("title", "source_refs"))
        zone = zone_of(self.context)
        title = text(arguments, "title", 80)
        kind = choice(arguments, "kind", EVENT_KINDS, "其他")
        starts_at, has_time = moment(arguments, "starts_at", zone)
        ends_at, _ = moment(arguments, "ends_at", zone)
        deadline = deadline_of(arguments, zone)
        refs = source_refs(self.db, arguments)
        day = local(starts_at, zone).date().isoformat() if starts_at else ""
        values = {
            "title": title,
            "kind": kind,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "has_time": int(has_time),
            "place": text(arguments, "place", 80),
            "signup_deadline": deadline,
            "note": text(arguments, "note", 300),
            "credits": credits_of(arguments),
            "credit_kind": text(arguments, "credit_kind", 20),
        }
        return await asyncio.to_thread(
            self._upsert, "campus_events", key(title, day), values, refs, set(arguments)
        )

    async def campus_leave_record(self, arguments):
        """Record the user's own leave request, or update its status (审批中 -> 已批准...).
        The same kind starting on the same day is one record; pass "id" from campus_list to
        change one whose dates changed. arguments: {"kind": "事假|病假|公假|其他",
        "starts_at": ISO 8601 or YYYY-MM-DD, "ends_at"?, "reason"?, "status":
        "待提交|审批中|已批准|已驳回|已销假", "approver"?, "id"?, "source_refs": [message IDs]}."""
        allowed = {"kind", "starts_at", "ends_at", "reason", "status", "approver", "id"}
        arguments_of(
            arguments, allowed | {"source_refs"}, required=("kind", "starts_at", "source_refs")
        )
        zone = zone_of(self.context)
        kind = choice(arguments, "kind", LEAVE_KINDS)
        starts_at, has_time = moment(arguments, "starts_at", zone)
        ends_at, _ = moment(arguments, "ends_at", zone)
        refs = source_refs(self.db, arguments)
        values = {
            "kind": kind,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "has_time": int(has_time),
            "reason": text(arguments, "reason", 120),
            "status": choice(arguments, "status", LEAVE_STATUSES, "审批中"),
            "approver": text(arguments, "approver", 40),
        }
        dedup = key(kind, local(starts_at, zone).date().isoformat())
        return await asyncio.to_thread(
            self._upsert,
            "campus_leaves",
            dedup,
            values,
            refs,
            set(arguments),
            arguments.get("id"),
        )

    def _upsert(self, table, dedup, values, refs, given, identifier=None):
        timestamp = datetime.now(UTC).isoformat()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if identifier:
                existing = conn.execute(
                    f"SELECT * FROM {table} WHERE id=?", (identifier,)
                ).fetchone()
                if existing is None:
                    raise KeyError("record_not_found")
            else:
                existing = conn.execute(
                    f"SELECT * FROM {table} WHERE dedup_key=?", (dedup,)
                ).fetchone()
            if existing:
                # Only the fields the Agent passed change; the rest keep what was known. The
                # first wording of a title stays: a later notice spelling it differently is
                # the same activity.
                changed = {
                    name: value
                    for name, value in values.items()
                    if name != "title"
                    and (name in given or (name == "has_time" and "starts_at" in given))
                }
                merged = list(dict.fromkeys(json.loads(existing["source_refs"]) + refs))
                assignments = ", ".join(f"{name}=?" for name in changed)
                conn.execute(
                    f"UPDATE {table} SET {assignments + ', ' if assignments else ''}"
                    "dedup_key=?, source_refs=?, updated_at=? WHERE id=?",
                    (*changed.values(), dedup, dump(merged), timestamp, existing["id"]),
                )
                record_id, disposition = existing["id"], "updated"
            else:
                record_id, disposition = uid(), "added"
                columns = ["id", "dedup_key", *values, "source_refs", "created_at", "updated_at"]
                conn.execute(
                    f"INSERT INTO {table}({','.join(columns)}) "
                    f"VALUES({','.join('?' for _ in columns)})",
                    (record_id, dedup, *values.values(), dump(refs), timestamp, timestamp),
                )
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        return {"disposition": disposition, "record": row_view(row)}

    async def campus_list(self, arguments):
        """Recorded activities or leave requests, to find one before adding or updating it.
        "recommended" lists the activities still open, best first, each with "clash" (the
        class it overlaps, or null) as on 校园Bot's 「推荐参加」 card. arguments: {"what":
        "events" | "leaves" | "recommended", "upcoming_only": true (optional; events that have
        not ended, leaves that have not ended)}."""
        arguments_of(arguments, {"what", "upcoming_only"}, required=("what",))
        what = choice(arguments, "what", ("events", "leaves", "recommended"))
        upcoming = arguments.get("upcoming_only", False)
        if not isinstance(upcoming, bool):
            raise ValueError("upcoming_only must be true or false")
        if what == "recommended":
            picks = await asyncio.to_thread(self.recommend, self.now())
            return {"items": [{**row, "clash": clash} for row, clash in picks]}
        rows = await asyncio.to_thread(
            self.events if what == "events" else self.leaves, upcoming, self.now()
        )
        return {"items": rows}

    def recommend(self, now):
        """[(activity, the class it clashes with or None)]: optional activities that have not
        started and whose sign-up is still open, those giving 第二课堂 credits first (more
        first), then by sign-up deadline and start; clashing ones last."""
        picks = []
        for row in self.events(True, now):
            start, deadline = (
                local(row["starts_at"], now.tzinfo),
                local(row["signup_deadline"], now.tzinfo),
            )
            if row["kind"] not in OPTIONAL_KINDS or not (start or deadline):
                continue
            if (start and start <= now) or (deadline and deadline < now):
                continue
            picks.append((row, self.clash(row, now.tzinfo)))
        picks.sort(
            key=lambda pick: (
                pick[1] is not None,
                -(pick[0]["credits"] or 0),
                pick[0]["signup_deadline"] or "~",
                pick[0]["starts_at"] or "~",
            )
        )
        return picks

    def clash(self, row, zone):
        """The class (title) a timed activity overlaps, from the timetable, guessed weeks
        included; None when it overlaps none or nothing is known."""
        start = local(row["starts_at"], zone)
        if not start or not row["has_time"]:
            return None
        end = local(row["ends_at"], zone) or start + timedelta(hours=ASSUMED_HOURS)
        try:
            classes, _ = day_entries(self.db, start.date())
        except sqlite3.OperationalError:  # the timetable was never enabled
            return None
        first, last = (
            start.strftime("%H:%M"),
            end.strftime("%H:%M") if end.date() == start.date() else "24:00",
        )
        for entry in classes:
            if entry["start_time"] < last and first < entry["end_time"]:
                return entry["title"]
        return None

    def events(self, upcoming, now):
        rows = [row_view(r) for r in self.db.rows("SELECT * FROM campus_events")]
        if upcoming:
            since = (now - timedelta(days=self.past_days)).astimezone(UTC).isoformat()
            rows = [r for r in rows if self._event_end(r) is None or self._event_end(r) >= since]
        # Dated activities soonest first, undated ones after them, newest first.
        dated = sorted((r for r in rows if r["starts_at"]), key=lambda r: r["starts_at"])
        undated = sorted(
            (r for r in rows if not r["starts_at"]), key=lambda r: r["created_at"], reverse=True
        )
        return dated + undated

    @staticmethod
    def _event_end(row):
        return row["ends_at"] or row["starts_at"] or row["signup_deadline"]

    def leaves(self, upcoming, now):
        rows = [row_view(r) for r in self.db.rows("SELECT * FROM campus_leaves")]
        if upcoming:
            start_of_day = datetime.combine(now.date(), datetime.min.time(), now.tzinfo)
            since = start_of_day.astimezone(UTC).isoformat()
            rows = [r for r in rows if (r["ends_at"] or r["starts_at"]) >= since]
        return sorted(rows, key=lambda r: r["starts_at"], reverse=True)

    async def cards(self):
        return await asyncio.to_thread(self._cards, self.now())

    async def daily(self, day):
        """活动与请假.md in the day's folder (core/daily.py)."""
        return await asyncio.to_thread(self._daily, day, self.now().tzinfo)

    def _daily(self, day, zone):
        at = noon(day, zone)

        def covers(row):
            first = local(row["starts_at"], zone)
            last = local(row["ends_at"], zone) or first
            return first is not None and first.date() <= day <= last.date()

        def starts_within_week(row):
            first = local(row["starts_at"], zone)
            return first is not None and day < first.date() <= day + timedelta(days=7)

        events = self.events(False, at)
        today = [e for e in events if covers(e)]
        signups = [
            e
            for e in events
            if e not in today
            and e["signup_deadline"]
            and local(e["signup_deadline"], zone).date() == day
        ]
        sections = (
            ("今天的活动", today, self._event_item),
            ("今天报名截止", signups, self._event_item),
            ("今天的请假", [r for r in self.leaves(False, at) if covers(r)], self._leave_item),
            ("之后一周的活动", [e for e in events if starts_within_week(e)], self._event_item),
        )
        lines = []
        for title, rows, describe in sections:
            if rows:
                lines += ["", f"## {title}"]
                for row in rows:
                    item = describe(row, at)
                    text = item["title"] + (f"（{item['detail']}）" if item.get("detail") else "")
                    lines.append(
                        f"- {text}" + (f" · {item['subtitle']}" if item.get("subtitle") else "")
                    )
        # The summary counts today's part only (not the coming week).
        labels = (("今天活动", "个"), ("报名截止", "个"), ("请假", "条"))
        counts = [
            f"{label} {len(rows)} {unit}"
            for (label, unit), (_, rows, _) in zip(labels, sections[:3], strict=True)
            if rows
        ]
        return {
            "file": "活动与请假.md",
            "bot": BOT,
            "summary": "，".join(counts) if counts else "今天没有活动和请假",
            "body": "\n".join(lines).strip() or "这一天没有活动和请假。",
        }

    def _cards(self, now):
        events = self.events(True, now)
        leaves = self.leaves(False, now)[:6]
        if not events and not leaves:
            return [
                {
                    "type": "text",
                    "title": "请假与学校活动",
                    "data": {
                        "text": "还没有记录。Agent 读 QQ 群消息时会把讲座、比赛、报名这些活动和请假审批记到这里；"
                        "也可以直接在对话里说，比如「我明天上午请了病假，辅导员已经批了」。"
                    },
                }
            ]
        cards = []
        picks = self.recommend(now)[:RECOMMENDED]
        if picks:
            cards.append(
                {
                    "type": "list",
                    "title": "推荐参加",
                    "data": {"items": [self._pick_item(row, clash, now) for row, clash in picks]},
                }
            )
        if leaves:
            cards.append(
                {
                    "type": "list",
                    "title": "请假记录",
                    "data": {"items": [self._leave_item(r, now) for r in leaves]},
                }
            )
        if events:
            cards.append(
                {
                    "type": "list",
                    "title": "近期活动",
                    "data": {"items": [self._event_item(r, now) for r in events[:8]]},
                }
            )
        week_end = datetime.combine(
            week_start(now.date()) + timedelta(days=7), datetime.min.time(), now.tzinfo
        )
        this_week = [
            r
            for r in events
            if r["starts_at"]
            and now.date() <= local(r["starts_at"], now.tzinfo).date()
            and local(r["starts_at"], now.tzinfo) < week_end
        ]
        signups = [
            r
            for r in events
            if r["signup_deadline"] and local(r["signup_deadline"], now.tzinfo) >= now
        ]
        stat = {"value": len(this_week), "unit": "个", "label": "这周剩下的活动"}
        if signups:
            first = min(signups, key=lambda r: r["signup_deadline"])
            stat["label"] = (
                f"{len(signups)} 个还能报名，最早{deadline_text(first['signup_deadline'], now)}截止："
                f"{first['title']}"
            )
        cards.append({"type": "stat", "title": "这周的活动", "data": stat})
        return cards

    @staticmethod
    def _leave_item(row, now):
        start = local(row["starts_at"], now.tzinfo)
        end = local(row["ends_at"], now.tzinfo)
        span = day_text(start.date(), now.date())
        if end and end.date() > start.date():
            span += f"–{day_text(end.date(), now.date())}"
        item = {"title": f"{span} {row['kind']}", "detail": row["status"]}
        subtitle = " · ".join(part for part in (row["reason"], row["approver"]) if part)
        if subtitle:
            item["subtitle"] = subtitle
        return item

    @staticmethod
    def _pick_item(row, clash, now):
        parts = []
        if row["credits"]:
            kind = f"（{row['credit_kind']}）" if row["credit_kind"] else ""
            parts.append(f"加 {row['credits']:g} 分第二课堂{kind}")
        if row["signup_deadline"]:
            parts.append(f"{deadline_text(row['signup_deadline'], now)}前报名")
        if row["starts_at"]:
            parts.append(when_text(row["starts_at"], row["has_time"], now))
        if clash:
            parts.append(f"和「{clash}」冲突")
        item = {"title": row["title"], "detail": row["kind"]}
        if parts:
            item["subtitle"] = " · ".join(parts)
        return item

    @staticmethod
    def _event_item(row, now):
        parts = []
        if row["starts_at"]:
            parts.append(when_text(row["starts_at"], row["has_time"], now))
        if row["place"]:
            parts.append(row["place"])
        if row["signup_deadline"]:
            parts.append(f"{deadline_text(row['signup_deadline'], now)}前报名")
        item = {"title": row["title"], "detail": row["kind"]}
        if parts:
            item["subtitle"] = " · ".join(parts)
        return item
