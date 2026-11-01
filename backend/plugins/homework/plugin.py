"""Homework: a view over the todos, not a second list.

A todo is homework when the Agent marked it with homework_mark (which also names the course),
when it came from one of `course_chats`, or when its title contains a homework keyword.
Ticking it off in the App or in Reminders completes the todo, and with it the homework.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from core.plugins import Plugin as BasePlugin
from core.store import todo_row
from plugins.school.common import (
    arguments_of,
    choice,
    day_text,
    local,
    noon,
    text,
    week_start,
    when_text,
    zone_of,
)

BOT = "作业Bot"  # the character this plugin shows as in the App
KEYWORDS = [
    "作业",
    "习题",
    "练习",
    "实验报告",
    "课程报告",
    "读书报告",
    "读书笔记",
    "课程设计",
    "课设",
    "大作业",
    "论文",
    "预习",
    "小测",
    "测验",
    "网课",
    "慕课",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS homework_marks (
  todo_id TEXT PRIMARY KEY, course TEXT, created_at TEXT NOT NULL);
"""


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        self.db = context.store.db
        with self.db.connect() as conn:
            conn.executescript(SCHEMA)
        self.course_chats = set(config.get("course_chats", []))
        self.keywords = config.get("keywords") or KEYWORDS

    def now(self):
        return datetime.now(zone_of(self.context))

    def homework(self):
        """Every homework todo with its course, as stored todos (with `sources`)."""
        marks = {
            row["todo_id"]: row["course"]
            for row in self.db.rows("SELECT todo_id, course FROM homework_marks")
        }
        rows = self.db.rows("SELECT * FROM todos WHERE status != 'deleted'")
        result = []
        for todo in self.context.store.with_sources([todo_row(row) for row in rows]):
            chats = [source["chat_name"] for source in todo["sources"]]
            course_chat = next((chat for chat in chats if chat in self.course_chats), None)
            if (
                todo["id"] not in marks
                and not course_chat
                and not any(word in todo["title"] for word in self.keywords)
            ):
                continue
            todo["course"] = marks.get(todo["id"]) or course_chat
            result.append(todo)
        return result

    async def cards(self):
        return await asyncio.to_thread(self._cards, self.now())

    async def daily(self, day):
        """作业.md in the day's folder (core/daily.py): due that day, overdue, done, new."""
        return await asyncio.to_thread(self._daily, day, self.now().tzinfo)

    def _daily(self, day, zone):
        at = noon(day, zone)

        def due(todo):
            return local(todo["due_at"], zone).date() if todo["due_at"] else None

        def on_day(value):
            return local(value, zone).date() == day

        sections = {
            "今天要交": [],
            "已经逾期": [],
            "今天交完的": [],
            "今天新布置的": [],
            "之后一周要交": [],
        }
        for todo in sorted(self.homework(), key=lambda t: (t["due_at"] is None, t["due_at"] or "")):
            pending = todo["status"] == "pending"
            if pending and due(todo) == day:
                sections["今天要交"].append(todo)
            elif pending and due(todo) and due(todo) < day:
                sections["已经逾期"].append(todo)
            elif not pending and on_day(todo["updated_at"]):
                sections["今天交完的"].append(todo)
            elif on_day(todo["created_at"]):
                sections["今天新布置的"].append(todo)
            elif pending and due(todo) and due(todo) <= day + timedelta(days=7):
                sections["之后一周要交"].append(todo)
        lines = []
        for title, todos in sections.items():
            if todos:
                lines += ["", f"## {title}"]
                for todo in todos:
                    course = f"{todo['course']}：" if todo["course"] else ""
                    when = (
                        f" · {when_text(todo['due_at'], todo['has_time'], at)} 截止"
                        if todo["due_at"]
                        else ""
                    )
                    box = "[ ]" if todo["status"] == "pending" else "[x]"
                    lines.append(f"- {box} {course}{todo['title']}{when}")
        counts = [
            f"{label} {len(sections[key])} 项"
            for label, key in (
                ("今天要交", "今天要交"),
                ("逾期", "已经逾期"),
                ("一周内", "之后一周要交"),
            )
            if sections[key]
        ]
        return {
            "file": "作业.md",
            "bot": BOT,
            "summary": "，".join(counts) if counts else "今天没有要交的作业",
            "body": "\n".join(lines).strip() or "这一天没有和作业有关的事。",
        }

    def _cards(self, now):
        items = self.homework()
        pending = sorted(
            (t for t in items if t["status"] == "pending"),
            key=lambda t: (t["due_at"] is None, t["due_at"] or "", t["created_at"]),
        )
        week_end = datetime.combine(
            week_start(now.date()) + timedelta(days=7), datetime.min.time(), now.tzinfo
        )
        this_week = [
            t for t in pending if t["due_at"] and now <= local(t["due_at"], now.tzinfo) < week_end
        ]
        overdue = [t for t in pending if t["due_at"] and local(t["due_at"], now.tzinfo) < now]
        if not items:
            return [
                {
                    "type": "text",
                    "title": "作业",
                    "data": {
                        "text": "还没有作业。提炼消息时，课程群里布置的作业会成为待办，并出现在这里；"
                        "也可以在设置里把课程群加进 course_chats。"
                    },
                }
            ]
        if this_week:
            first = this_week[0]
            label = (
                f"最近一项：{when_text(first['due_at'], first['has_time'], now)} · {first['title']}"
            )
        else:
            label = "这周没有要交的作业"
        stat = {"value": len(this_week), "unit": "项", "label": label}
        if overdue:
            stat["delta"] = f"已过截止 {len(overdue)} 项"
            stat["trend"] = "up"
        cards = [
            {"type": "stat", "title": "这周要交的作业", "data": stat},
            {
                "type": "todo_list",
                "title": "还没交",
                "data": {"todos": [strip(t) for t in pending[:20]]},
            },
        ]
        weeks = [week_start(now.date()) - timedelta(weeks=n) for n in (3, 2, 1, 0)]
        counts = dict.fromkeys(weeks, 0)
        for todo in items:
            moment = local(todo["due_at"] or todo["created_at"], now.tzinfo)
            start = week_start(moment.date())
            if start in counts:
                counts[start] += 1
        cards.append(
            {
                "type": "chart",
                "title": "最近 4 周的作业",
                "data": {
                    "kind": "bar",
                    "unit": "项",
                    "points": [
                        {
                            "x": "本周" if start == weeks[-1] else f"{start.month}/{start.day}",
                            "y": n,
                        }
                        for start, n in counts.items()
                    ],
                },
            }
        )
        done = sorted(
            (t for t in items if t["status"] == "completed"),
            key=lambda t: t["updated_at"],
            reverse=True,
        )[:5]
        if done:
            cards.append(
                {
                    "type": "list",
                    "title": "最近交完的",
                    "data": {
                        "items": [
                            {
                                "title": t["title"],
                                **({"subtitle": t["course"]} if t["course"] else {}),
                                "detail": day_text(
                                    local(t["updated_at"], now.tzinfo).date(), now.date()
                                ),
                            }
                            for t in done
                        ]
                    },
                }
            )
        return cards

    def mcp_tools(self):
        return {"homework_mark": self.homework_mark, "homework_list": self.homework_list}

    async def homework_mark(self, arguments):
        """Mark an existing todo as homework, optionally with its course, after todos_add
        created or merged it. Use it for homework a keyword would not catch. arguments:
        {"todo_id": "...", "course": "数据结构" (optional)}."""
        arguments_of(arguments, {"todo_id", "course"}, required=("todo_id",))
        todo_id, course = arguments["todo_id"], text(arguments, "course", 60)
        self.context.store.todo(todo_id)  # KeyError for a missing todo
        self.db.execute(
            "INSERT INTO homework_marks VALUES(?,?,?) ON CONFLICT(todo_id) DO UPDATE SET "
            "course=COALESCE(excluded.course, homework_marks.course)",
            (todo_id, course, datetime.now(UTC).isoformat()),
        )
        return {"todo_id": todo_id, "course": course}

    async def homework_list(self, arguments):
        """Homework todos with their course, due time and status; pending first, soonest due
        first. arguments: {"status": "pending" | "completed" (optional, default both)}."""
        arguments_of(arguments, {"status"})
        status = choice(arguments, "status", ("pending", "completed"))
        items = await asyncio.to_thread(self.homework)
        items = [t for t in items if status is None or t["status"] == status]
        items.sort(key=lambda t: (t["status"] != "pending", t["due_at"] is None, t["due_at"] or ""))
        return {
            "items": [
                {
                    key: t[key]
                    for key in ("id", "title", "course", "due_at", "has_time", "status", "notes")
                }
                for t in items
            ]
        }


def strip(todo):
    """A Todo as the API returns it, without the plugin's extra `course`."""
    return {key: value for key, value in todo.items() if key != "course"}
