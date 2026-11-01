"""数据看门狗: tells the user when a data source has stopped working, so a closed QQ window or a
timetable page left on the wrong tab is not found out days later (docs/watchdog.md).

Every 10 minutes it looks at

- each scheduled job's runs: a job whose latest finished run failed and that has not succeeded
  for `HOURS` hours (counted from when the watchdog first saw it failing, if it never did);
- the plugins' `watch()` hooks, for data a job can fetch "successfully" without getting it (the
  timetable page left closed, no health upload, Claude's expired login). A hook returns a list
  of {"key", "title", "ok_at", "hours", "hint"}: when the plugin last got that data (ISO time,
  or None when it does not know, counted from when the watchdog first saw the entry), after
  how many hours without it to tell the user, and what the user can do; optionally "jobs", the
  plugin's jobs whose failures the entry already reports (the run-based check skips them). An
  entry the plugin stops returning counts as fixed;
- plugins that could not start.

Something down that long becomes a todo of 助理Bot's due today, with what to do in its notes,
so it shows on her page (only in the App: apple.APP_ONLY keeps it out of Reminders); when it
works again the watchdog checks the todo off.
The todo cites a message the watchdog writes (plugin "watchdog", already processed), like a todo
from a Bot chat cites the user's line.
"""

import asyncio
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.db import now as stamp
from core.db import uid
from core.models import TodoCreate, TodoPatch

HOURS = 3
SOURCE = "watchdog"
STATE = "watchdog"
CORE_JOBS = {
    "extract_todos": "提炼待办",
    "apple_sync": "同步提醒事项",
    "backup": "每日备份",
    "memory_index": "记忆索引",
    "daily_notes": "每日文件夹",
}
# What the user can do about a job's error code; {app} is the plugin's app (QQ) or name.
HINTS = {
    "screen_window_not_found": "在 Mac mini 上打开{app}的主窗口，别最小化（被别的窗口挡住没关系）。",
    "screen_app_not_running": "在 Mac mini 上打开并登录{app}。",
    "screen_not_conversation_list": "{app}没停在消息列表：在左侧点回「消息」。",
    "screen_recording_denied": "系统设置 → 隐私与安全性 → 屏幕与系统录音，打开 screen-helper。",
    "screen_accessibility_denied": "系统设置 → 隐私与安全性 → 辅助功能，打开 screen-helper。",
    "screen_helper_timeout": "截图助手一直卡住：重启一下{app}；还不行就让 Claude 看看。",
    "screen_locked": "Mac mini 锁屏了（显示器关掉后要密码），截不了图；解锁后会自己恢复。"
    "想让夜里也能读，到 系统设置 → 锁定屏幕 把「屏幕保护程序启动或显示器关闭后需要密码」选「永不」。",
    "screen_capture_failed": "截图没成功，多半是 Mac mini 的显示器关了或锁屏了；亮屏解锁后会自己恢复。",
    "all_harnesses_failed": "Claude Code、Codex 和 DeepSeek 都没跑成，多半是额度用完或登录过期："
    "看看用量Bot的用量页。",
    "authentication_required": "Agent 的登录过期了：在 Mac mini 的终端运行一次 claude 或 codex 登录。",
    "quota_exhausted": "Agent 的额度用完了：等额度重置，或者在设置里把别的 Agent 排到前面。",
    "apple_sync_failed": "写不进提醒事项：系统设置 → 隐私与安全性 → 提醒事项 和 日历，打开 apple-sync。",
    "plugin_setup_failed": "在 App 的插件设置里检查它的配置（比如 helper 的路径），改好后它会重新启动。",
}
OTHER_HINT = "后台会继续重试；一直不好的话，让 Claude 查一下运行记录（错误代码 {code}）。"
KEY = re.compile(r"[a-z][a-z0-9_]{0,40}")


def parse(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class Watchdog:
    def __init__(self, service):
        self.service = service

    @property
    def zone(self):
        return ZoneInfo(self.service.store.settings.timezone)

    def clock(self, moment):
        local = moment.astimezone(self.zone)
        return f"{local.month}月{local.day}日 {local:%H:%M}"

    # What is down --------------------------------------------------------------------------

    def plugin_of(self, job):
        """The plugin a job belongs to (fetch:qq_screen, plugin:weather:forecast), or None."""
        parts = job.split(":")
        return parts[1] if parts[0] in ("fetch", "plugin") and len(parts) > 1 else None

    def job_entry(self, job, error, ok_at):
        plugins = self.service.plugins
        plugin = self.plugin_of(job)
        if plugin is None:
            name = CORE_JOBS.get(job, job)
        else:
            name = plugins.manifests[plugin]["name"]
            if job.startswith("plugin:"):
                name += f"的「{job.split(':')[2]}」任务"
        instance = plugins.instances.get(plugin)
        app = getattr(instance, "app_name", "") or name
        return {
            "key": f"job:{job}",
            "plugin": plugin,
            "title": f"{name}一直失败",
            "ok_at": parse(ok_at),
            "hours": HOURS,
            "hint": HINTS.get(error, OTHER_HINT).format(app=app, code=error),
        }

    def from_runs(self):
        db, entries = self.service.db, []
        for job in self.service.jobs.callbacks:
            if job == "watchdog":
                continue
            last = db.rows(
                "SELECT status,error FROM runs WHERE job=? AND status IN ('succeeded','failed') "
                "ORDER BY started_at DESC LIMIT 1",
                (job,),
            )
            if not last or last[0]["status"] != "failed":
                continue
            ok_at = db.rows(
                "SELECT max(finished_at) AS t FROM runs WHERE job=? AND status='succeeded'", (job,)
            )[0]["t"]
            entries.append(self.job_entry(job, last[0]["error"], ok_at))
        return entries

    async def from_hooks(self):
        plugins, entries = self.service.plugins, []
        for identifier, instance in list(plugins.instances.items()):
            if not hasattr(instance, "watch"):
                continue
            try:
                for found in await asyncio.wait_for(instance.watch(), 10):
                    if not KEY.fullmatch(found["key"]):
                        raise ValueError("invalid watch key")
                    entries.append(
                        {
                            "key": f"{identifier}:{found['key']}",
                            "plugin": identifier,
                            "title": str(found["title"]),
                            "ok_at": parse(found["ok_at"]),
                            "hours": float(found["hours"]),
                            "hint": str(found["hint"]),
                            "covers": {
                                f"job:plugin:{identifier}:{job}" for job in found.get("jobs", [])
                            },
                        }
                    )
            except Exception:
                run = self.service.db.start_run(f"plugin:{identifier}:watch")
                self.service.db.finish_run(run, "failed", "plugin_watch_failed")
        return entries

    def from_setup(self):
        plugins, entries = self.service.plugins, []
        for identifier in list(plugins.errors):
            row = plugins.row(identifier)
            if not row["enabled"] or row["deleted"]:
                continue
            entries.append(
                {
                    "key": f"setup:{identifier}",
                    "plugin": identifier,
                    "title": f"{plugins.manifests[identifier]['name']}没能启动",
                    "ok_at": None,
                    "hours": 0,
                    "hint": HINTS["plugin_setup_failed"],
                }
            )
        return entries

    async def entries(self):
        hooks = await self.from_hooks()
        covered = set().union(*(e.pop("covers") for e in hooks))
        runs = [e for e in self.from_runs() if e["key"] not in covered]
        return self.from_setup() + hooks + runs

    # Todos ---------------------------------------------------------------------------------

    def notes(self, entry, since):
        if entry["ok_at"] is None:
            when = f"{self.clock(since)} 发现的。"
        else:
            when = f"从 {self.clock(since)} 起就没成功过。"
        return (
            f"{when}\n怎么处理：{entry['hint']}\n"
            "（助理每 10 分钟看一次，好了以后会自己勾掉这一条。）"
        )

    async def open(self, entry, since, moment):
        notes = self.notes(entry, since)
        message_id = uid()
        self.service.db.execute(
            "INSERT INTO messages(id,plugin,external_id,chat_name,sender,sent_at,text,processed) "
            "VALUES(?,?,?,?,?,?,?,1)",
            (
                message_id,
                SOURCE,
                uid(),
                "数据看门狗",
                "助理Bot",
                stamp(),
                f"{entry['title']}\n{notes}",
            ),
        )
        day = moment.astimezone(self.zone).date()
        result = await self.service.add_todo(
            TodoCreate(
                title=entry["title"],
                notes=notes,
                due_at=datetime.combine(day, time(), self.zone),
                has_time=False,
                source_plugin=SOURCE,
                source_refs=[message_id],
                source_excerpt=entry["title"],
                confidence=1,
                created_by=SOURCE,
            )
        )
        todo = result["todo"]
        # The same problem earlier today, checked off since, is back; one the user deleted
        # today stays deleted.
        if todo["status"] == "completed" or (
            todo["status"] == "pending" and todo["notes"] != notes
        ):
            await self.service.update_todo(todo["id"], TodoPatch(status="pending", notes=notes))
        return todo["id"]

    async def close(self, identifier):
        rows = self.service.db.rows("SELECT status FROM todos WHERE id=?", (identifier,))
        if rows and rows[0]["status"] == "pending":
            await self.service.update_todo(identifier, TodoPatch(status="completed"))

    async def check(self, moment=None):
        moment = moment or datetime.now(UTC)
        state = self.service.db.get(STATE, {})
        kept = {}
        for entry in await self.entries():
            old = state.get(entry["key"], {})
            seen = parse(old.get("seen")) or moment
            since = entry["ok_at"] or seen
            if moment - since < timedelta(hours=entry["hours"]):
                if entry["ok_at"] is None:
                    kept[entry["key"]] = {"seen": seen.isoformat()}
                continue
            item = {"seen": seen.isoformat(), "todo": old.get("todo"), "hint": entry["hint"]}
            if item["todo"] is None:
                item["todo"] = await self.open(entry, since, moment)
            elif old.get("hint") != entry["hint"]:
                rows = self.service.db.rows("SELECT status FROM todos WHERE id=?", (item["todo"],))
                if rows and rows[0]["status"] == "pending":
                    await self.service.update_todo(
                        item["todo"], TodoPatch(notes=self.notes(entry, since))
                    )
            kept[entry["key"]] = item
        for key, old in state.items():
            if old.get("todo") and kept.get(key, {}).get("todo") != old["todo"]:
                await self.close(old["todo"])
        self.service.db.put(STATE, kept)
        return kept
