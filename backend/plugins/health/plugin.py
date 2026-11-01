import asyncio
from datetime import date, datetime

from core.plugins import Plugin as BasePlugin
from core.prompts import render
from plugins.health.cards import health_cards
from plugins.health.config import HealthConfig
from plugins.health.engine import HealthEngine
from plugins.health.reports import KINDS, ReportConfig, Reports, hours_minutes

BOT = "健康Bot"  # the character this plugin shows as in the App


def parse_arguments(arguments, default_days=None):
    allowed = {"date", "days"} if default_days else {"date"}
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise ValueError("invalid arguments")
    day = arguments.get("date")
    result = {"day": date.fromisoformat(day) if day else None}
    if default_days:
        days = arguments.get("days", default_days)
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 31:
            raise ValueError("days must be 1-31")
        result["days"] = days
    return result


# Sleep comes in after waking up, so a day and a half covers a late morning.
UPLOAD_HOURS = 36


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        settings = HealthConfig.from_dict({k: v for k, v in config.items() if k != "reports"})
        self.report_config = ReportConfig.from_dict(config.get("reports"))
        self.engine = await asyncio.to_thread(
            HealthEngine, context.store.db, context.data_dir, settings
        )
        self.reports = Reports(context.store.db, self.engine, self.report_config, context.memory)

    def jobs(self):
        # Picks up new uploads; after a settings change it rebuilds the stored history.
        jobs = {"refresh": {"cron": "*/15 * * * *", "callback": self.refresh}}
        config = self.report_config
        if config.enabled:
            hours = f"{config.morning_earliest_hour}-{config.morning_deadline_hour}"
            jobs["morning_report"] = {"cron": f"*/15 {hours} * * *", "callback": self.morning}
            jobs["weekly_report"] = {
                "cron": f"0 {config.weekly_hour} * * {config.weekly_weekday}",
                "callback": self.weekly,
            }
        return jobs

    def now(self):
        return datetime.now(self.engine.zone)

    async def watch(self):
        """For the watchdog (core/watchdog.py): when the iPhone App last uploaded."""
        return [
            {
                "key": "upload",
                "title": "健康数据没有上传",
                "ok_at": self.context.store.db.get("health_uploaded_at"),
                "hours": UPLOAD_HOURS,
                "hint": "打开 iPhone 上的 Aemeath Bot，它会补传一次。还不行的话，看看 设置 → 健康 → "
                "数据访问与设备 里它的读取权限，以及 设置 → 通用 → 后台 App 刷新；免费签名的 App "
                "7 天后打不开，要连上 Mac 重新安装。",
            }
        ]

    async def morning(self):
        """After last night's sleep has been uploaded; at the deadline hour without it."""
        now = self.now()
        day = now.date()
        if not self._due("morning", day):
            return
        await asyncio.to_thread(self.engine.refresh, incremental_only=True)
        has_night = await asyncio.to_thread(self.reports.has_night, day)
        if not has_night and now.hour < self.report_config.morning_deadline_hour:
            return
        await self.generate("morning", day, now)

    async def weekly(self):
        now = self.now()
        if self._due("weekly", now.date()):
            await asyncio.to_thread(self.engine.refresh, incremental_only=True)
            await self.generate("weekly", now.date(), now)

    def _due(self, kind, day):
        # Ready, or failed after max_attempts generations (each with the full harness
        # fallback), means nothing more to do today.
        if self.reports.status(kind, day) == "ready":
            return False
        return self.context.get(f"report_attempts:{kind}:{day}", 0) < (
            self.report_config.max_attempts
        )

    async def generate(self, kind, day, now):
        if self.context.run_agent is None:
            raise RuntimeError("agent_runner_unavailable")
        key = f"report_attempts:{kind}:{day}"
        self.context.put(key, self.context.get(key, 0) + 1)
        await asyncio.to_thread(self.reports.prepare, kind, day, now.date())
        prompt = render(
            f"health_{kind}",
            date=day.isoformat(),
            local_now=now.isoformat(),
            timezone=self.engine.config.timezone,
        )
        try:
            harness = await self.context.run_agent(
                f"plugin:health:{kind}_report",
                prompt,
                succeeded=lambda: self.reports.status(kind, day) == "ready",
                incomplete_error="report_not_written",
            )
        except RuntimeError as error:
            row = self.reports.row(kind, day)
            reason = row["error"] if row and row["error"] else str(error)
            self.reports.finish(kind, day, error=reason)
            raise
        self.reports.finish(kind, day, harness=harness)

    async def cards(self):
        return await asyncio.to_thread(self._cards, self.now().date())

    async def daily(self, day):
        """健康.md in the day's folder (core/daily.py): readiness, the night, alerts, briefing."""
        return await asyncio.to_thread(self._daily, day, self.now().date())

    def _daily(self, day, today):
        if self.engine.data_dates() is None:
            return {
                "file": "健康.md",
                "bot": BOT,
                "summary": "还没有健康数据",
                "body": "还没有健康数据：等 iPhone App 上传，或导入「健康」App 的导出文件。",
            }
        summary = self.engine.daily(day, 1, today=today)["days"][-1]
        night = self.engine.sleep(day, 1, today=today)["nights"][-1]
        score = summary["readiness"]["score"]
        lines = [
            "## 准备度",
            "",
            f"{score} / 10" if score is not None else "数据不足，暂时算不出来",
        ]
        lines += ["", "## 昨晚睡眠", ""]
        if night["quality"] == "missing":
            lines.append("没有记录")
            slept = None
        else:
            slept = hours_minutes(night["total_minutes"])
            parts = [f"一共 {slept}"]
            if night["naps"]["count"]:
                parts.append(f"主睡眠 {hours_minutes(night['asleep_minutes'])}")
                parts.append(
                    f"小睡 {night['naps']['count']} 次 {hours_minutes(night['naps']['minutes'])}"
                )
            parts.append(f"{night['onset']} 入睡，{night['wake']} 醒来")
            lines.append("，".join(parts))
        if summary["alerts"]:
            lines += ["", "## 预警（只表示建议关注，不是诊断）", ""]
            lines += [f"- {alert['message']}" for alert in summary["alerts"]]
        report = self.reports.row("morning", day)
        if report and report["status"] == "ready":
            lines += ["", "## 晨间简报", "", f"### {report['title']}", "", report["body"]]
        words = [f"准备度 {score}" if score is not None else "准备度数据不足"]
        if slept:
            words.append(f"昨晚睡了 {slept}")
        return {
            "file": "健康.md",
            "bot": BOT,
            "summary": "，".join(words),
            "body": "\n".join(lines),
        }

    def _cards(self, today):
        # Metric cards first (alerts on top), then the latest briefing and weekly report.
        return health_cards(self.engine, today) + self.reports.cards(today)

    async def refresh(self):
        await asyncio.to_thread(self.engine.refresh)

    def mcp_tools(self):
        return {
            "health_get_daily": self.health_get_daily,
            "health_get_sleep": self.health_get_sleep,
            "health_get_baselines": self.health_get_baselines,
            "health_report_input": self.health_report_input,
            "health_report_write": self.health_report_write,
        }

    async def _read(self, method, **kwargs):
        # Include uploads from the last minutes without waiting for a running rebuild.
        await asyncio.to_thread(self.engine.refresh, incremental_only=True, wait=False)
        return await asyncio.to_thread(method, **kwargs)

    async def health_get_daily(self, arguments):
        """Daily health summary: per-day aggregates (sleep, resting HR, HRV as ln SDNN and ln
        RMSSD - separate measures, each against its own baseline, `hrv_preferred` names the
        one a day is judged by - steps, TRIMP...), their deviation from 7- and 30-day personal
        baselines, data-quality flags, training load, alert flags and a 0-10 readiness score
        with its four parts (an engineering estimate, see readiness_note). Aggregates only,
        never raw samples. Describes data and trends; it is not a diagnosis. arguments:
        {"date": "YYYY-MM-DD" (default today), "days": 1-31 (default 2)}."""
        return await self._read(self.engine.daily, **parse_arguments(arguments, 2))

    async def health_get_sleep(self, arguments):
        """Nightly sleep (onset, wake, total and stage minutes, segments, naps) with baseline
        deviations, Sleep Regularity Index and onset/wake variability. Aggregates only; not a
        diagnosis. arguments: {"date": "YYYY-MM-DD" (default today), "days": 1-31 (default 7)}."""
        return await self._read(self.engine.sleep, **parse_arguments(arguments, 7))

    async def health_get_baselines(self, arguments):
        """7- and 30-day personal baselines (median, MAD, sample count, reliability) of every
        daily metric as of a date, excluding that date. arguments: {"date": "YYYY-MM-DD"
        (default today)}."""
        return await self._read(self.engine.baselines, **parse_arguments(arguments))

    async def health_report_input(self, arguments):
        """All data a morning briefing or weekly report may cite, computed by code: the
        daily / sleep / baseline summaries, values already converted for reading (display,
        week), and the training-goal note. Every number in the report must come from here.
        arguments: {"kind": "morning" | "weekly", "date": "YYYY-MM-DD"}."""
        kind, day = parse_report_arguments(arguments, {"kind", "date"})
        return await asyncio.to_thread(self.reports.input, kind, day, self.now().date())

    async def health_report_write(self, arguments):
        """Submit a finished report. It is accepted only if every number appears in
        health_report_input, no quantity is written in Chinese numerals and there is no
        diagnosis or medication wording; otherwise the reply lists the problems to fix.
        arguments: {"kind": "morning" | "weekly", "date": "YYYY-MM-DD", "title": "...",
        "body": "Markdown"}."""
        kind, day = parse_report_arguments(arguments, {"kind", "date", "title", "body"})
        title, body = arguments.get("title"), arguments.get("body")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
            raise ValueError("title must be 1-80 characters")
        if not isinstance(body, str) or not 1 <= len(body.strip()) <= 6000:
            raise ValueError("body must be 1-6000 characters")
        return await asyncio.to_thread(
            self.reports.submit, kind, day, title.strip(), body.strip(), self.now().date()
        )


def parse_report_arguments(arguments, allowed):
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise ValueError("invalid arguments")
    kind = arguments.get("kind")
    if kind not in KINDS:
        raise ValueError("kind must be morning or weekly")
    return kind, date.fromisoformat(arguments["date"])
