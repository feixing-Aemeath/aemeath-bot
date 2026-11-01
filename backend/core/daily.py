"""每日文件夹: 每日/<YYYY-MM-DD>/ in the long-term memory, one day split up by Bot, for the user
(in Obsidian or Finder) and for the Agents (memory_read / memory_search).

The database stays the record; the files are written from it every 10 minutes and after each
extraction, and only when their content changed. Each file has the part the backend writes and,
below it, a 「## 笔记」 section that Bots and the user write and that rewriting keeps.

The core writes 今天.md (who does what today, and the day's summaries), 消息.md (what the
sources read that day) and 待办.md (助理Bot's todos). A plugin with an async `daily(day)` hook
adds its own file and so claims its share of the day: it returns {"file": "作业.md", "bot":
"作业Bot", "summary": one line for 今天.md, "body": Markdown}, or None.
"""

import asyncio
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.apple import APP_ONLY
from core.message_signals import redact_self
from core.store import todo_row

FOLDER = "每日"
NOTES = "## 笔记"
NOTES_HINT = "（Bot 和你写在这一节，后台重新整理时不会改动。）"
CORE_FILES = ("今天.md", "消息.md", "待办.md")
WEEKDAYS = "一二三四五六日"
# Files in 今天.md in this order; plugins not listed follow by id.
ORDER = ("weather", "timetable", "homework", "campus", "grades", "health")
FILE_NAME = re.compile(r"[^/\\:]{1,40}\.md")


def day_title(day):
    return f"{day.month}月{day.day}日 周{WEEKDAYS[day.weekday()]}"


def keep_notes(old, generated):
    """The new file: what the backend writes, then the old file's 「## 笔记」 section."""
    notes = f"{NOTES}\n\n{NOTES_HINT}\n"
    if old:
        index = old.find(f"\n{NOTES}\n")
        if index >= 0:
            notes = old[index + 1 :]
    return f"{generated.rstrip()}\n\n{notes}"


def one_line(text):
    return " ".join(text.split())


class DailyNotes:
    def __init__(self, service):
        self.service = service

    @property
    def zone(self):
        return ZoneInfo(self.service.store.settings.timezone)

    def today(self):
        return datetime.now(self.zone).date()

    def local(self, value):
        return datetime.fromisoformat(value).astimezone(self.zone)

    def bounds(self, day):
        """The local day as UTC ISO times, the form every stored time has (core.store.stamp)."""
        start = datetime.combine(day, time(), self.zone).astimezone(UTC)
        return start.isoformat(), (start + timedelta(days=1)).isoformat()

    def on(self, value, day):
        return value is not None and self.local(value).date() == day

    def clock(self, value, has_time=True, day=None):
        """ "14:20" on `day`, "10月27日 14:20" (or "10月27日" without a time) otherwise."""
        moment = self.local(value)
        if moment.date() == day:
            return moment.strftime("%H:%M") if has_time else "今天"
        text = f"{moment.month}月{moment.day}日"
        return f"{text} {moment.strftime('%H:%M')}" if has_time else text

    # 消息.md ------------------------------------------------------------------------------

    def messages(self, day):
        # The Bot chats are conversations with the user and the watchdog's notes are the
        # backend's own, not something a source read.
        rows = self.service.db.rows(
            "SELECT plugin,chat_name,sender,sent_at,text,preview,priority FROM messages "
            "WHERE plugin NOT IN ('chat','watchdog') AND sent_at>=? AND sent_at<? "
            "ORDER BY plugin,chat_name,sent_at",
            self.bounds(day),
        )
        for row in rows:
            identities = self.service.store.local_identities(row["plugin"])
            for field in ("text", "sender", "chat_name"):
                row[field] = redact_self(row[field], identities)
        return rows

    def messages_file(self, day, rows):
        lines = [
            f"# 消息 · {day_title(day)}",
            "",
            "白名单里的会话这一天读到的消息（识别出的文字，不含截图）。",
        ]
        if not rows:
            lines += ["", "这一天还没有读到消息。"]
        chat = None
        for row in rows:
            if (row["plugin"], row["chat_name"]) != chat:
                chat = (row["plugin"], row["chat_name"])
                lines += ["", f"## {self.source_name(row['plugin'])} · {row['chat_name']}"]
            marks = "（@全体成员）" if row["priority"] else ""
            marks += "（预览，可能不全）" if row["preview"] else ""
            sender = f"{row['sender']}：" if row["sender"] else ""
            lines.append(
                f"- {self.clock(row['sent_at'], day=day)} {sender}{one_line(row['text'])}{marks}"
            )
        return "\n".join(lines)

    def source_name(self, plugin):
        manifest = self.service.plugins.manifests.get(plugin)
        return manifest["name"] if manifest else plugin

    # 待办.md ------------------------------------------------------------------------------

    def todos(self, day):
        store = self.service.store
        rows = self.service.db.rows("SELECT * FROM todos WHERE status!='deleted'")
        items = store.with_sources([todo_row(r) for r in rows])
        start = self.bounds(day)[0]
        due = {"today": [], "overdue": [], "new": [], "done": [], "system": []}
        for todo in sorted(items, key=lambda t: (t["due_at"] is None, t["due_at"] or "")):
            pending = todo["status"] == "pending"
            if todo["source_plugin"] in APP_ONLY:
                # The watchdog's alerts are not the user's tasks: kept apart, so a Bot does not
                # list 「课表读不到了」 among the day's deadlines; fixed ones are left out.
                if pending:
                    due["system"].append(todo)
            elif self.on(todo["due_at"], day):
                due["today"].append(todo)
            elif pending and todo["due_at"] and todo["due_at"] < start:
                due["overdue"].append(todo)
            elif self.on(todo["created_at"], day):
                due["new"].append(todo)
            elif not pending and self.on(todo["updated_at"], day):
                due["done"].append(todo)
        return due

    def todo_line(self, todo, day):
        box = "[x]" if todo["status"] == "completed" else "[ ]"
        parts = [todo["title"]]
        if todo["due_at"]:
            parts.append(f"{self.clock(todo['due_at'], todo['has_time'], day)} 截止")
        if todo["sources"]:
            source = todo["sources"][0]
            parts.append(f"来自 {self.source_name(source['plugin'])} {source['chat_name']}")
        return f"- {box} " + " · ".join(parts)

    @staticmethod
    def system_line(todo):
        """「课表读不到了：从 10月31日 01:30 起就没成功过。怎么处理：…」, without the closing line
        about the watchdog checking it off."""
        notes = [line for line in (todo["notes"] or "").splitlines() if line.strip()]
        detail = "".join(line for line in notes if not line.startswith("（"))
        return f"- {todo['title']}" + (f"：{detail}" if detail else "")

    def todos_file(self, day, due):
        lines = [
            f"# 待办 · {day_title(day)}",
            "",
            "负责：助理Bot。所有来源的待办里和这一天有关的。",
        ]
        sections = (
            ("今天截止", "today"),
            ("已经逾期", "overdue"),
            ("今天新记下的", "new"),
            ("今天完成的", "done"),
        )
        for title, key in sections:
            if due[key]:
                lines += ["", f"## {title}", *(self.todo_line(t, day) for t in due[key])]
        if due["system"]:
            lines += [
                "",
                "## 系统提醒",
                "数据看门狗报的后台问题，不是要用户去做的待办；好了以后会自己消失。",
                *(self.system_line(t) for t in due["system"]),
            ]
        if not any(due.values()):
            lines += ["", "这一天没有相关的待办。"]
        return "\n".join(lines)

    @staticmethod
    def todos_summary(due):
        parts = [
            f"{label} {len(due[key])} 件"
            for label, key in (("今天截止", "today"), ("逾期", "overdue"), ("新记下", "new"))
            if due[key]
        ]
        text = "，".join(parts) if parts else "今天没有要处理的待办"
        if due["system"]:
            text += f"；另有 {len(due['system'])} 条系统提醒（不是待办）"
        return text

    # Plugins and 今天.md ------------------------------------------------------------------

    async def plugin_parts(self, day):
        plugins, parts = self.service.plugins, []
        rank = {identifier: index for index, identifier in enumerate(ORDER)}
        for identifier in sorted(plugins.instances, key=lambda i: (rank.get(i, len(ORDER)), i)):
            instance = plugins.instances.get(identifier)
            if instance is None or not hasattr(instance, "daily"):
                continue
            try:
                part = await asyncio.wait_for(instance.daily(day), 20)
                if part is None:
                    continue
                if (
                    not FILE_NAME.fullmatch(part["file"])
                    or part["file"] in CORE_FILES
                    or any(part["file"] == p["file"] for p in parts)
                ):
                    raise ValueError("invalid daily file")
                parts.append({key: str(part[key]) for key in ("file", "bot", "summary", "body")})
            except Exception:
                run = self.service.db.start_run(f"plugin:{identifier}:daily")
                self.service.db.finish_run(run, "failed", "plugin_daily_failed")
        return parts

    def today_file(self, day, due, messages, parts):
        lines = [
            f"# {day_title(day)}",
            "",
            "后台按数据库整理这一天，每 10 分钟更新；每个文件最后的「笔记」一节不会被改动。",
            "",
            "## 分工",
            f"- 助理Bot · [待办](待办.md)：{self.todos_summary(due)}",
        ]
        for part in parts:
            name = part["file"].removesuffix(".md")
            lines.append(f"- {part['bot']} · [{name}]({part['file']})：{one_line(part['summary'])}")
        chats = {(m["plugin"], m["chat_name"]) for m in messages}
        lines.append(
            f"- 消息来源 · [消息](消息.md)：{len(chats)} 个会话 {len(messages)} 条"
            if messages
            else "- 消息来源 · [消息](消息.md)：还没有读到消息"
        )
        digests = self.service.store.digests(day=day.isoformat(), limit=50)["items"]
        if digests:
            lines += ["", "## 这一天的摘要"]
            for digest in digests:
                lines += ["", f"### {self.source_name(digest['plugin'])}", "", digest["summary"]]
        return "\n".join(lines)

    # Writing ------------------------------------------------------------------------------

    async def write(self, day=None):
        """Write one day's folder; the paths whose content changed."""
        day = day or self.today()
        parts = await self.plugin_parts(day)
        return await asyncio.to_thread(self._write, day, parts)

    def _write(self, day, parts):
        due, messages = self.todos(day), self.messages(day)
        files = {
            "今天.md": self.today_file(day, due, messages, parts),
            "消息.md": self.messages_file(day, messages),
            "待办.md": self.todos_file(day, due),
        }
        for part in parts:
            header = (
                f"# {part['file'].removesuffix('.md')} · {day_title(day)}\n\n负责：{part['bot']}。"
            )
            files[part["file"]] = f"{header}\n\n{part['body'].strip()}"
        memory, changed = self.service.memory, []
        for name, content in files.items():
            relative = f"{FOLDER}/{day.isoformat()}/{name}"
            path = memory.path(relative)
            old = path.read_text(encoding="utf-8") if path.is_file() else None
            new = keep_notes(old, content)
            if new != old:
                memory.write(relative, new)
                changed.append(relative)
        return changed

    async def write_recent(self):
        """Today, and yesterday for what arrives after midnight about it."""
        today = self.today()
        return await self.write(today - timedelta(days=1)) + await self.write(today)
