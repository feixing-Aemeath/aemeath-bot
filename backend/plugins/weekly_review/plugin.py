"""助理的本周回顾: code counts, an Agent writes, Markdown keeps the notes."""

import asyncio
from datetime import date, datetime, time, timedelta

from apscheduler.triggers.cron import CronTrigger

from core.bots import context as bot_context
from core.daily import keep_notes
from core.db import uid
from core.plugins import Plugin as BasePlugin
from core.prompts import render
from plugins.school.common import arguments_of, text, week_start, zone_of
from plugins.weekly_review.inputs import Inputs

BOT = "助理Bot"
REVIEWS = "reviews"
WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat", "sun")


def review_schedule(cron):
    """Unix crontab uses Sunday=0/7; APScheduler uses Monday=0. Expand numeric days before
    passing the schedule to the core, including lists, ranges and steps."""
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError("invalid review_cron")
    days = []
    for part in fields[4].split(","):
        if any(c.isalpha() for c in part):
            days.append(part)
            continue
        span, _, step = part.partition("/")
        if span == "*":
            first, last = 0, 6
        elif "-" in span:
            first, last = map(int, span.split("-"))
        else:
            first = int(span)
            last = 7 if step else first
        if not 0 <= first <= last <= 7 or (step and int(step) <= 0):
            raise ValueError("invalid review_cron")
        days.extend(WEEKDAYS[n] for n in range(first, last + 1, int(step or 1)))
    fields[4] = ",".join(dict.fromkeys(days))
    result = " ".join(fields)
    CronTrigger.from_crontab(result)
    return result


def week_id(monday):
    year, week, _ = monday.isocalendar()
    return f"{year}-W{week:02d}"


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        self.review_agent = config.get("review_agent", "deepseek")
        self.review_cron = review_schedule(config.get("review_cron", "0 21 * * 0"))

    def now(self):
        return datetime.now(zone_of(self.context))

    def jobs(self):
        return {"review": {"cron": self.review_cron, "callback": self.review}}

    def mcp_tools(self):
        return {"weekly_review_input": self.review_input, "weekly_review_write": self.review_write}

    def monday(self, arguments):
        value = text(arguments, "date", 10)
        return week_start(date.fromisoformat(value) if value else self.now().date())

    async def review_input(self, arguments):
        """Weekly aggregates and titles only: completed/overdue todos, homework, classes,
        activities, sleep/readiness, tokens and next week's deadlines/exam titles. Missing or
        disabled plugins say 没有数据. No raw chats. arguments: {"date": "YYYY-MM-DD"}
        (optional, any day of the Monday-Sunday week; default today)."""
        arguments_of(arguments, {"date"})
        monday, now = self.monday(arguments), self.now()
        return {
            "week": week_id(monday),
            "from": monday.isoformat(),
            "to": (monday + timedelta(days=6)).isoformat(),
            "timezone": self.context.store.settings.timezone,
            "as_of": now.isoformat(),
            "completion_note": "完成数按已完成项的最后更新时间统计；修改旧完成项也可能计入。作业包含在待办中，不要相加。",
            **await Inputs(self.context, monday, now).read(),
        }

    async def review(self):
        monday = week_start(self.now().date())
        week = week_id(monday)
        previous = self.context.get(REVIEWS, {}).get(week, {}).get("revision")
        if self.context.run_agent is None:
            raise RuntimeError("agent_runner_unavailable")
        prompt = render(
            "weekly_review",
            date=monday.isoformat(),
            week=week,
            local_now=self.now().isoformat(),
            **bot_context(self.context.memory, "助理"),
        )
        await self.context.run_agent(
            "plugin:weekly_review:review",
            prompt,
            succeeded=lambda: (
                self.context.get(REVIEWS, {}).get(week, {}).get("revision") not in (None, previous)
            ),
            incomplete_error="review_not_written",
            harness=self.review_agent,
        )

    async def review_write(self, arguments):
        """Write the review and its 周报/YYYY-Www.md memory file, preserving ## 笔记. A rerun
        replaces the same week. arguments: {"date": "YYYY-MM-DD" (any day of the reviewed
        week), "text": "Markdown, 1-6000 characters"}."""
        arguments_of(arguments, {"date", "text"}, required=("date", "text"))
        monday, body = self.monday(arguments), text(arguments, "text", 6000)
        if not body:
            raise ValueError("text must not be blank")
        # The final notes section belongs to the user/Bot, not generated report text.
        if any(line.rstrip() == "## 笔记" for line in body.splitlines()):
            raise ValueError("text must not contain the notes section")
        return await asyncio.to_thread(self.write, monday, body, self.now())

    def write(self, monday, body, now):
        week, memory = week_id(monday), self.context.memory
        relative = f"周报/{week}.md"
        path = memory.path(relative)
        old = path.read_text(encoding="utf-8") if path.is_file() else None
        generated = (
            f"# 本周回顾 · {week}\n\n"
            f"{monday}–{monday + timedelta(days=6)} · {self.context.store.settings.timezone}\n\n"
            f"写于 {now.isoformat()}。\n\n{body}"
        )
        # The plugin owns this generated path; the caller's memory_write stays scoped.
        path.parent.mkdir(parents=True, exist_ok=True)
        memory.save(path, keep_notes(old, generated))
        reviews = self.context.get(REVIEWS, {})
        reviews[week] = {
            "monday": monday.isoformat(),
            "text": body,
            "written_at": now.isoformat(),
            "path": relative,
            "revision": uid(),
        }
        self.context.put(REVIEWS, reviews)
        return {"accepted": True, "week": week, "path": relative}

    async def cards(self):
        now, cards = self.now(), []
        for review in self.context.get(REVIEWS, {}).values():
            sunday = date.fromisoformat(review["monday"]) + timedelta(days=6)
            start = datetime.combine(sunday, time(), now.tzinfo)
            end = datetime.combine(sunday + timedelta(days=3), time(), now.tzinfo)
            if start <= now < end and datetime.fromisoformat(review["written_at"]) <= now:
                cards.append(
                    {"type": "text", "title": "本周回顾", "data": {"text": review["text"]}}
                )
        return cards

    async def daily(self, day):
        reviews = [
            (week, review)
            for week, review in sorted(self.context.get(REVIEWS, {}).items())
            if datetime.fromisoformat(review["written_at"]).astimezone(zone_of(self.context)).date()
            == day
        ]
        if not reviews:
            return None
        return {
            "file": "本周回顾.md",
            "bot": BOT,
            "summary": "本周回顾写好了：" + "、".join(week for week, _ in reviews),
            "body": "\n\n".join(
                f"[{week} 的回顾](../../{review['path']})\n\n{review['text']}"
                for week, review in reviews
            ),
        }
