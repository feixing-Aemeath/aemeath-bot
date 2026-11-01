import asyncio
import contextlib
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from core.plugins import Plugin as BasePlugin
from plugins.token_usage import limits
from plugins.token_usage.sessions import (
    COUNTING,
    COUNTING_KEY,
    FILES_KEY,
    import_sessions,
    recount_claude,
)
from plugins.token_usage.usage import Ledger

PLUGIN_ID = "token_usage"
SESSION_DIRS = ["~/.claude/projects", "~/.codex/sessions"]
LIMITS_KEY = "limits"
RENEWED_KEY = "claude_renewed_at"  # when the plugin last ran Claude Code to renew its login


class Plugin(BasePlugin):
    """Cards and a daily summary from the token counts the core records for every Agent run,
    plus your own use of Claude Code and Codex read from their session files.

    Counting Agent runs happens in the core (Jobs.run_agent), so it goes on while this plugin is
    disabled; the plugin only reads it. Session files are read hourly, and once on enabling.
    Claude's and Codex's 5-hour and weekly limits are read every 10 minutes (limits.py).
    """

    async def setup(self, context, config):
        await super().setup(context, config)
        self.importing = asyncio.create_task(self.first_import())
        self.limiting = asyncio.create_task(self.first_limits()) if self.reads_limits else None

    @property
    def reads_limits(self):
        return self.config.get("read_limits", True)

    async def first_import(self):
        # Best effort: the hourly check job imports again and records a failure in runs.
        with contextlib.suppress(Exception):
            await self.import_sessions()

    async def first_limits(self):
        # Best effort: the job reads again every 10 minutes and records a failure in runs.
        with contextlib.suppress(Exception):
            await self.refresh_limits()

    async def teardown(self):
        for task in (getattr(self, "importing", None), getattr(self, "limiting", None)):
            if task:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task

    def jobs(self):
        jobs = {"check": {"cron": "0 * * * *", "callback": self.check}}
        if self.reads_limits:
            jobs["limits"] = {"cron": "*/10 * * * *", "callback": self.refresh_limits}
        return jobs

    async def watch(self):
        """For the watchdog (core/watchdog.py): Claude's limits unread, mostly an expired
        login that renewing did not fix."""
        entry = self.context.get(LIMITS_KEY, {}).get("claude") if self.reads_limits else None
        if not entry or entry["status"] == "ok":
            return []
        if entry["status"] in limits.STATUS_NOTES:
            hint = limits.STATUS_NOTES[entry["status"]][1]
        else:
            hint = f"{limits.failure_text(entry.get('error', ''))}；后台每 10 分钟再读一次。"
        as_of = entry.get("as_of")
        return [
            {
                "key": "claude",
                "title": "Claude 的用量读不到了",
                "ok_at": datetime.fromtimestamp(as_of, UTC).isoformat() if as_of else None,
                "hours": 3,
                "hint": hint + "（Claude Code 登录过期时，用 Claude Code 的任务也会失败。）",
            }
        ]

    async def refresh_limits(self):
        def run():
            state = self.context.get(LIMITS_KEY, {})
            dirs = self.session_dirs()
            state["codex"] = limits.update(state.get("codex"), lambda: limits.read_codex(dirs))

            def claude():
                if not self.config.get("renew_claude_login", True):
                    return limits.read_claude()
                entry, renewed = limits.read_claude_renewing(self.context.get(RENEWED_KEY))
                self.context.put(RENEWED_KEY, renewed)
                return entry

            state["claude"] = limits.update(state.get("claude"), claude)
            self.context.put(LIMITS_KEY, state)

        await asyncio.to_thread(run)

    def session_dirs(self):
        """The CLIs' own folders, plus a folder to copy another computer's session files into."""
        if not self.config.get("read_sessions", True):
            return []
        imports = self.context.data_dir / PLUGIN_ID / "imports"
        imports.mkdir(parents=True, exist_ok=True)
        return [*self.config.get("session_dirs", SESSION_DIRS), str(imports)]

    async def import_sessions(self):
        def run():
            db, state = self.context.store.db, self.context.get(FILES_KEY, {})
            if recount_claude(db, self.context.get(COUNTING_KEY)):
                state = {}
            self.context.put(FILES_KEY, import_sessions(db, self.session_dirs(), state))
            self.context.put(COUNTING_KEY, COUNTING)

        await asyncio.to_thread(run)

    def ledger(self):
        zone = ZoneInfo(self.context.store.settings.timezone)
        return Ledger(self.context.store.db, zone, datetime.now(zone).date(), self.config)

    async def cards(self):
        def build():
            ledger = self.ledger()
            state = self.context.get(LIMITS_KEY, {}) if self.reads_limits else {}
            return limits.limit_cards(state, ledger.zone) + ledger.cards()

        return await asyncio.to_thread(build)

    async def check(self):
        await self.import_sessions()

        def write():
            ledger = self.ledger()
            summary = ledger.summary()
            if summary:
                self.context.store.digest(ledger.today.isoformat(), PLUGIN_ID, summary)

        await asyncio.to_thread(write)
