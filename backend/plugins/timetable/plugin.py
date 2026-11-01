"""课表: the courses of each day, read from the school's 个人日程 page that the user keeps open in
Chrome (docs/timetable.md).

The backend never logs in to the school site and never clicks it. The user logs in and leaves
the page on its week view (周历); every 30 minutes the read-only screen helper captures that one
window, Apple Vision reads it on this Mac and page.py turns the text into the week's entries.
Every 10 minutes the backend reloads that tab through Chrome's AppleScript (`refresh`), so the
site does not log the user out for being idle; a logged-out page has another title and is left
alone. The screenshot stays in the plugin's data folder (a week);
only times and course names are stored. Each read replaces the week it shows.

The page keeps showing the week it was opened on until it is reloaded, so a day of a week
never read is taken from the latest week read before it, on the same weekday, marked 推测
(for up to four weeks).
"""

import asyncio
import contextlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from core import helper_process
from core.db import dump
from core.plugins import Plugin as BasePlugin
from plugins.school.common import WEEKDAYS, arguments_of, day_text, text, zone_of
from plugins.timetable import page

BOT = "作业Bot"  # the character that looks after courses and homework (the same sticker)
PAGE_TITLE = "学校门户-个人日程"
STATUS = "status"
REFRESH = "refresh"
# Reloads the tabs titled argv's first item; never starts Chrome or brings it forward. Prints how
# many it reloaded, -1 when Chrome is not running.
RELOAD = """on run argv
    set wanted to item 1 of argv
    if application "Google Chrome" is not running then return "-1"
    set reloaded to 0
    tell application "Google Chrome"
        repeat with w in windows
            repeat with t in tabs of w
                if title of t is wanted then
                    tell t to reload
                    set reloaded to reloaded + 1
                end if
            end repeat
        end repeat
    end tell
    return reloaded as text
end run"""
GUESS_WEEKS = 4
SCHEMA = """
CREATE TABLE IF NOT EXISTS timetable_entries (
  day TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, title TEXT NOT NULL,
  kind TEXT NOT NULL, PRIMARY KEY (day, start_time, title));
CREATE TABLE IF NOT EXISTS timetable_weeks (monday TEXT PRIMARY KEY, read_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS timetable_calendar_events (
  client_id TEXT PRIMARY KEY, event_id TEXT, payload_json TEXT NOT NULL, synced_json TEXT);
"""
# A failed read on the cards; the codes come from the helper and page.py.
PROBLEMS = {
    "screen_page_not_open": "Chrome 里没有找到「{title}」这一页。在 Chrome 里打开学校网站的个人日程，切到「周历」，"
    "让它留在一个窗口的当前标签页（可以被别的窗口挡住，别最小化）。如果网站让你重新登录，登录后回到这一页。",
    "screen_app_not_running": "Chrome 没有打开。打开学校网站的个人日程（周历）后，半小时内会读到。",
    "screen_recording_denied": "屏幕读取助手没有屏幕录制权限：在 系统设置 → 隐私与安全性 → 屏幕与系统录音 里打开 "
    "screen-helper。",
    "screen_locked": "Mac mini 锁屏了（显示器关掉后要密码），截不了图；解锁后会自己恢复。"
    "想让夜里也能读，到 系统设置 → 锁定屏幕 把「屏幕保护程序启动或显示器关闭后需要密码」选「永不」。",
    "screen_capture_failed": "截图没成功，多半是 Mac mini 的显示器关了或锁屏了；亮屏解锁后会自己恢复。",
    "timetable_not_week_view": "页面不在「周历」，或者表头的星期没露出来（窗口被最小化、页面往下滚了）。点回「周历」就好。",
    "timetable_week_unreadable": "读不出页面显示的是哪一周：日期框和星期上面的日期都没读清。",
}


async def reload_tabs(title):
    """How many Chrome tabs titled `title` were reloaded; -1 when Chrome is not running."""
    if sys.platform == "win32":
        raise RuntimeError("chrome_refresh_requires_macos")
    try:
        code, output = await helper_process.run(
            ["/usr/bin/osascript", "-e", RELOAD, title], None, 30, capture_error=True
        )
    except TimeoutError:
        # The first time, macOS holds the call until the user answers whether the backend may
        # control Chrome.
        raise RuntimeError("chrome_refresh_timeout") from None
    if code:
        # -1743: the backend may not control Chrome yet (系统设置 → 隐私与安全性 → 自动化).
        raise RuntimeError(
            "chrome_automation_denied" if b"-1743" in output else "chrome_refresh_failed"
        )
    return int(output.strip() or 0)


def code_of(error):
    """Fixed error codes only, never text from the page (as core.jobs.error_code)."""
    message = str(error)
    return message if re.fullmatch(r"[a-z][a-z0-9_]{2,60}", message) else "timetable_read_failed"


def span(row):
    return f"{row['start_time']}–{row['end_time']}"


def day_entries(db, day):
    """(entries of `day`, the Monday of the week they were taken from when guessed). Other
    school plugins use it too (campus: does an activity clash with a class); it raises
    sqlite3.OperationalError where the timetable was never enabled."""
    monday = day - timedelta(days=day.weekday())
    weeks = db.rows(
        "SELECT monday FROM timetable_weeks WHERE monday<=? ORDER BY monday DESC LIMIT 1",
        (monday.isoformat(),),
    )
    if not weeks:
        return [], None
    source = date.fromisoformat(weeks[0]["monday"])
    if (monday - source).days > 7 * GUESS_WEEKS:
        return [], None
    rows = db.rows(
        "SELECT * FROM timetable_entries WHERE day=? ORDER BY start_time, title",
        ((source + timedelta(days=day.weekday())).isoformat(),),
    )
    return rows, (source if source != monday else None)


def state(row, clock):
    """On today's timeline: over, or going on at `clock` ("14:20"); nothing for another day."""
    if clock is None or clock < row["start_time"]:
        return {}
    return {"state": "done" if clock >= row["end_time"] else "now"}


def week_label(monday):
    sunday = monday + timedelta(days=6)
    end = f"{sunday.day}日" if sunday.month == monday.month else f"{sunday.month}月{sunday.day}日"
    return f"{monday.month}月{monday.day}日–{end}"


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        helper = config.get("helper")
        if not (sys.platform == "win32" and not helper) and (
            not helper or not Path(helper).is_absolute()
        ):
            raise ValueError("timetable_requires_helper")
        self.helper = Path(helper) if helper else None
        self.shots = context.data_dir / "screens" / context.plugin_id
        self.title = config.get("page_title", PAGE_TITLE)
        self.db = context.store.db
        self.read_lock = asyncio.Lock()
        self.calendar_lock = asyncio.Lock()
        with self.db.connect() as conn:
            conn.executescript(SCHEMA)
        self.reading = asyncio.create_task(self.first_read())

    async def first_read(self):
        # Best effort: the job reads again every 30 minutes and records a failure in runs.
        with contextlib.suppress(Exception):
            await self.read()

    async def teardown(self):
        task = getattr(self, "reading", None)
        if task:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    def now(self):
        return datetime.now(zone_of(self.context))

    def status(self):
        return self.context.get(STATUS, {})

    def jobs(self):
        jobs = {"read": {"cron": "*/30 * * * *", "callback": self.read}}
        if sys.platform != "win32" and self.config.get("keep_alive", True):
            # Between the reads, so a reloaded page has long finished loading when it is read.
            jobs["refresh"] = {"cron": "5-59/10 * * * *", "callback": self.refresh}
        return jobs

    async def watch(self):
        """For the watchdog (core/watchdog.py): the page not read for three hours (whatever the
        reason, so it covers the read job), and a page read fine but on a week before this
        one since this Monday began."""
        status, now = self.status(), self.now()
        error = status.get("error")
        if error or not status.get("read_at"):
            problem = PROBLEMS.get(error, "在 Chrome 里打开学校网站的个人日程（周历）。")
        else:
            problem = "截图助手没能读完页面；后台会继续重试，一直不好的话让 Claude 查一下运行记录。"
        found = [
            {
                "key": "page",
                "title": "课表读不到了",
                "ok_at": status.get("read_at"),
                "hours": 3,
                "hint": problem.format(title=self.title, code=error),
                "jobs": ["read"],
            }
        ]
        monday = now.date() - timedelta(days=now.weekday())
        if not error and status.get("week") and date.fromisoformat(status["week"]) < monday:
            found.append(
                {
                    "key": "week",
                    "title": "课表页面停在旧的一周",
                    "ok_at": datetime.combine(monday, datetime.min.time(), now.tzinfo).isoformat(),
                    "hours": 3,
                    "hint": "在 Chrome 的个人日程里点到这一周（或刷新一下），半小时内就能读到。",
                }
            )
        refresh = self.context.get(REFRESH, {})
        hints = {
            "chrome_automation_denied": "系统设置 → 隐私与安全性 → 自动化：在后端（python）下面打开「Google Chrome」。",
            "chrome_refresh_timeout": "可能在等你允许：屏幕上有「想要控制 Google Chrome」的提示的话点「好」；"
            "没有的话到 系统设置 → 隐私与安全性 → 自动化 里打开。",
        }
        if self.config.get("keep_alive", True) and refresh.get("error") in hints:
            found.append(
                {
                    "key": "refresh",
                    "title": "课表页面没法自动刷新",
                    "ok_at": refresh.get("ok_at"),
                    "hours": 1,
                    "hint": hints[refresh["error"]] + "不刷新的话，学校网站放久了会让你重新登录。",
                    "jobs": ["refresh"],
                }
            )
        return found

    def mcp_tools(self):
        return {"timetable_days": self.timetable_days}

    # Reading ------------------------------------------------------------------------------

    async def read(self):
        async with self.read_lock:
            try:
                await self.read_page()
            except Exception:
                # Keep the rolling window and retry failed writes even with the page closed.
                await self.sync_calendar()
                raise
            await self.sync_calendar()

    async def refresh(self):
        """Reload the timetable tab, so the site does not log the user out for being idle (the
        page then also shows the current week). Not while it is being read."""
        async with self.read_lock:
            now = self.now()
            previous = self.context.get(REFRESH, {})
            try:
                reloaded = await reload_tabs(self.title)
            except Exception as error:
                code = code_of(error)
                self.context.put(
                    REFRESH, {**previous, "checked_at": now.isoformat(), "error": code}
                )
                # As with reading: runs get each problem once.
                if code != previous.get("error"):
                    raise RuntimeError(code) from None
                return
            self.context.put(
                REFRESH,
                {
                    "checked_at": now.isoformat(),
                    "error": None,
                    "reloaded": reloaded,
                    "ok_at": now.isoformat() if reloaded > 0 else previous.get("ok_at"),
                },
            )

    async def read_page(self):
        """Capture the page, read the week it shows and replace that week's entries."""
        now = self.now()
        previous = self.status()
        try:
            _, blocks = await page.read_window(self.helper, self.shots, self.title)
            week = page.parse_week(blocks, now.date())
        except Exception as error:
            code = code_of(error)
            self.context.put(STATUS, {**previous, "checked_at": now.isoformat(), "error": code})
            # A page left closed would fail every half hour; runs get each problem once, the
            # cards show it until it is gone.
            if code != previous.get("error"):
                raise RuntimeError(code) from None
            return
        await asyncio.to_thread(self.store_week, week, now)
        self.context.put(
            STATUS,
            {
                "checked_at": now.isoformat(),
                "error": None,
                "read_at": now.isoformat(),
                "week": week.monday.isoformat(),
                "entries": len(week.entries),
            },
        )

    def store_week(self, week, now):
        days = [(week.monday + timedelta(days=i)).isoformat() for i in range(7)]
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM timetable_entries WHERE day>=? AND day<=?", (days[0], days[-1])
            )
            conn.executemany(
                "INSERT OR REPLACE INTO timetable_entries VALUES(?,?,?,?,?)",
                [(e.day.isoformat(), e.start, e.end, e.title, e.kind) for e in week.entries],
            )
            conn.execute(
                "INSERT OR REPLACE INTO timetable_weeks VALUES(?,?)", (days[0], now.isoformat())
            )

    def calendar_entries(self):
        minutes = self.config.get("class_alarm_minutes", 15)
        if not minutes:
            return {}
        now = self.now()
        desired = {}
        for offset in range(7):
            day = now.date() + timedelta(days=offset)
            rows, guessed = self.day_entries(day)
            for row in rows:
                if row["kind"] != "课程":
                    continue
                key = dump(["aemeath:timetable", day.isoformat(), row["start_time"], row["title"]])
                client_id = str(uuid5(NAMESPACE_URL, key))
                desired[client_id] = {
                    "client_id": client_id,
                    "calendar": "AI 课表",
                    "title": row["title"] + ("（推测）" if guessed else ""),
                    "notes": "来源：学校个人日程"
                    + (f"\n按 {guessed.isoformat()} 那周的课表推测。" if guessed else ""),
                    "due_at": datetime.combine(
                        day, datetime.strptime(row["start_time"], "%H:%M").time(), now.tzinfo
                    ).isoformat(),
                    "end_at": datetime.combine(
                        day, datetime.strptime(row["end_time"], "%H:%M").time(), now.tzinfo
                    ).isoformat(),
                    "has_time": True,
                    "completed": False,
                    "timezone": self.context.store.settings.timezone,
                    "alarms": [minutes],
                }
        return desired

    async def sync_calendar(self):
        async with self.calendar_lock:
            desired = self.calendar_entries()
            saved = {
                row["client_id"]: row
                for row in self.db.rows("SELECT * FROM timetable_calendar_events")
            }
            failures = 0
            for client_id, payload in desired.items():
                encoded = dump(payload)
                previous = saved.get(client_id, {})
                if previous.get("synced_json") == encoded:
                    continue
                # Save intent before calling EventKit: a crash before the returned ID is
                # committed still leaves a UUID and start date for marker lookup/deletion.
                self.db.execute(
                    "INSERT INTO timetable_calendar_events(client_id,payload_json) VALUES(?,?) "
                    "ON CONFLICT(client_id) DO UPDATE SET "
                    "payload_json=excluded.payload_json,synced_json=NULL",
                    (client_id, encoded),
                )
                try:
                    result = await self.context.apple_call(
                        "calendar", "upsert", {**payload, "id": previous.get("event_id")}
                    )
                    if not result.get("id"):
                        raise RuntimeError("apple_missing_identifier")
                    self.db.execute(
                        "UPDATE timetable_calendar_events SET event_id=?,synced_json=? "
                        "WHERE client_id=?",
                        (result["id"], encoded, client_id),
                    )
                except Exception:
                    failures += 1
            for client_id, previous in saved.items():
                if client_id in desired:
                    continue
                try:
                    await self.context.apple_call(
                        "calendar",
                        "delete",
                        {**json.loads(previous["payload_json"]), "id": previous["event_id"]},
                    )
                    self.db.execute(
                        "DELETE FROM timetable_calendar_events WHERE client_id=?", (client_id,)
                    )
                except Exception:
                    failures += 1
            if failures:
                raise RuntimeError("timetable_calendar_failed")

    def day_entries(self, day):
        """(entries of `day`, the Monday of the week they were taken from when guessed)."""
        return day_entries(self.db, day)

    def known(self, day):
        """Whether anything is known about `day`: a week read on or up to four weeks before."""
        monday = day - timedelta(days=day.weekday())
        rows = self.db.rows(
            "SELECT 1 FROM timetable_weeks WHERE monday<=? AND monday>=? LIMIT 1",
            (monday.isoformat(), (monday - timedelta(weeks=GUESS_WEEKS)).isoformat()),
        )
        return bool(rows)

    # The day's file, the cards and the Agent's tool ---------------------------------------

    async def daily(self, day):
        """课程.md in the day's folder (core/daily.py)."""
        return await asyncio.to_thread(self._daily, day)

    def _daily(self, day):
        if not self.known(day):
            return {
                "file": "课程.md",
                "bot": BOT,
                "summary": "还没有读到这一周的课表",
                "body": "还没有读到这一周的课表。在 Chrome 里打开学校网站的个人日程（周历），半小时内会读到。",
            }
        rows, guessed = self.day_entries(day)
        courses = [r for r in rows if r["kind"] == "课程"]
        own = [r for r in rows if r["kind"] != "课程"]
        lines = []
        if courses:
            lines += ["## 课程", *(f"- {span(r)} {r['title']}" for r in courses)]
        if own:
            lines += ["", "## 自己的日程", *(f"- {span(r)} {r['title']}" for r in own)]
        if not rows:
            lines.append("这一天没有课。")
        if guessed:
            lines += ["", f"按 {week_label(guessed)} 那周的课表推测：页面还没显示过这一周。"]
        summary = (
            f"{len(courses)} 节课：" + "、".join(f"{r['start_time']} {r['title']}" for r in courses)
            if courses
            else "没有课"
        )
        if own:
            summary += f"，自己的日程 {len(own)} 项"
        return {
            "file": "课程.md",
            "bot": BOT,
            "summary": summary + ("（推测）" if guessed else ""),
            "body": "\n".join(lines).strip(),
        }

    async def cards(self):
        return await asyncio.to_thread(self._cards, self.now())

    def _cards(self, now):
        status, cards = self.status(), []
        if status.get("error"):
            problem = PROBLEMS.get(status["error"], "读取失败（{code}）。")
            cards.append(
                {
                    "type": "text",
                    "title": "课表没读到",
                    "data": {
                        "text": problem.format(title=self.title, code=status["error"]),
                        "first": True,
                    },
                }
            )
        if not status.get("read_at"):
            if not cards:
                cards.append(
                    {
                        "type": "text",
                        "title": "课表",
                        "data": {
                            "text": "还没有读到课表。在 Chrome 里打开学校网站的个人日程（周历）。"
                        },
                    }
                )
            return cards
        today = now.date()
        shown = date.fromisoformat(status["week"]) if status.get("week") else None
        if not status.get("error") and shown and shown < today - timedelta(days=today.weekday()):
            # Read fine, but an old week: the page does not turn to the new week by itself.
            cards.append(
                {
                    "type": "text",
                    "title": "课表页面停在旧的一周",
                    "data": {
                        "text": f"Chrome 里的个人日程还停在 {week_label(shown)} 那周，页面不会自己翻到新的一周。"
                        "在页面上点到这一周（或刷新一下），半小时内就能读到；在那之前，这周的课按那一周推测。",
                        "first": True,
                    },
                }
            )
        for day in (today, today + timedelta(days=1)):
            rows, guessed = self.day_entries(day)
            title = f"{day_text(day, today)}的课" + (" · 推测" if guessed else "")
            if rows:
                clock = now.strftime("%H:%M") if day == today else None
                cards.append(
                    {
                        "type": "list",
                        "title": title,
                        "data": {
                            "style": "timeline",
                            # Today's classes before the homework on 作业Bot's page.
                            **({"first": True} if day == today else {}),
                            "items": [
                                {
                                    "title": r["title"],
                                    "subtitle": span(r),
                                    **({"detail": "自己的日程"} if r["kind"] != "课程" else {}),
                                    **state(r, clock),
                                }
                                for r in rows
                            ],
                        },
                    }
                )
            else:
                data = {"text": "没有课。", **({"first": True} if day == today else {})}
                cards.append({"type": "text", "title": title, "data": data})
        cards.append(self.week_card(today, status))
        return cards

    def week_card(self, today, status):
        """The week as a table (a row per start time, a column per weekday, Saturday and
        Sunday only when they have something), with the same week as lines of text for Apps
        that draw no tables."""
        monday = today - timedelta(days=today.weekday())
        _, guessed = self.day_entries(monday)
        days = [self.day_entries(monday + timedelta(days=offset))[0] for offset in range(7)]
        lines = [
            f"**周{WEEKDAYS[offset]}** "
            + "；".join(f"{r['start_time']} {r['title']}" for r in rows)
            for offset, rows in enumerate(days)
            if rows
        ]
        read_at = datetime.fromisoformat(status["read_at"]).astimezone(zone_of(self.context))
        footnote = (
            f"{day_text(read_at.date(), today)} {read_at:%H:%M} 读到 "
            f"{week_label(date.fromisoformat(status['week']))} 那周。"
        )
        title = "这周课表" + (f" · 按 {week_label(guessed)} 推测" if guessed else "")
        if not lines:
            empty = "这周没有课。" if self.known(monday) else "这周的课表还没读到。"
            return {"type": "text", "title": title, "data": {"text": f"{empty}\n\n{footnote}"}}
        shown = [offset for offset in range(7) if offset < 5 or days[offset]]
        starts = sorted({r["start_time"] for rows in days for r in rows})
        table = {
            "columns": ["", *(f"周{WEEKDAYS[offset]}" for offset in shown)],
            "rows": [
                [
                    start,
                    *(
                        "／".join(r["title"] for r in days[offset] if r["start_time"] == start)
                        for offset in shown
                    ),
                ]
                for start in starts
            ],
        }
        if today.weekday() in shown:
            table["highlight"] = shown.index(today.weekday()) + 1
        text = "\n".join(lines) + f"\n\n{footnote}"
        return {
            "type": "text",
            "title": title,
            "data": {"text": text, "table": table, "footnote": footnote},
        }

    async def timetable_days(self, arguments):
        """The courses of some days, as the school's timetable page showed them. arguments:
        {"from": "2026-10-26" (optional, default today), "days": 1-14 (optional, default 7)}.
        A day whose week the page has not shown yet is taken from the latest week read before
        it and has "estimated_from" (that week's Monday)."""
        arguments_of(arguments, {"from", "days"})
        start = text(arguments, "from", 10)
        try:
            first = date.fromisoformat(start) if start else self.now().date()
        except ValueError as error:
            raise ValueError("from must be a date such as 2026-10-26") from error
        count = arguments.get("days", 7)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 14:
            raise ValueError("days must be 1-14")
        return await asyncio.to_thread(self._days, first, count)

    def _days(self, first, count):
        days = []
        for offset in range(count):
            day = first + timedelta(days=offset)
            known = self.known(day)
            rows, guessed = self.day_entries(day) if known else ([], None)
            days.append(
                {
                    "date": day.isoformat(),
                    "weekday": f"周{WEEKDAYS[day.weekday()]}",
                    "known": known,
                    "entries": [
                        {
                            "start": r["start_time"],
                            "end": r["end_time"],
                            "title": r["title"],
                            "kind": r["kind"],
                        }
                        for r in rows
                    ],
                    **({"estimated_from": guessed.isoformat()} if guessed else {}),
                }
            )
        status = self.status()
        return {
            "days": days,
            "last_read": status.get("read_at"),
            "week_read": status.get("week"),
            "problem": status.get("error"),
        }
