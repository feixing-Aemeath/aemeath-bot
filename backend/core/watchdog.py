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
so it shows on her page (only in the App: apple.REMINDERS_EXCLUDED keeps it out of Reminders); when it
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
from plugins.screen.session import DESKTOP_HINTS

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
    "screen_window_not_found": "在后端电脑上打开{app}的主窗口，别最小化（被别的窗口挡住没关系）。",
    "screen_app_not_running": "在后端电脑上打开并登录{app}。",
    "screen_not_conversation_list": "{app}没停在消息列表：在左侧点回「消息」。",
    "screen_recording_denied": "后端电脑不允许截图：检查读屏程序的权限。",
    "screen_accessibility_denied": "后端电脑不允许操作窗口：检查读屏程序的权限。",
    "screen_helper_timeout": "截图助手一直卡住：重启一下{app}；还不行就让 Claude 看看。",
    **DESKTOP_HINTS,
    "screen_capture_failed": "截图失败，尚未确认原因。请运行 doctor 区分桌面会话、显示器状态和权限，并检查窗口是否可用。",
    "all_harnesses_failed": "Claude Code、Codex 和 DeepSeek 都没跑成，多半是额度用完或登录过期："
    "看看用量Bot的用量页。",
    "authentication_required": "Agent 的登录过期了：在后端电脑的终端运行一次 claude 或 codex 登录。",
    "quota_exhausted": "Agent 的额度用完了：等额度重置，或者在设置里把别的 Agent 排到前面。",
    "apple_sync_failed": "写不进提醒事项：在运行后端的电脑上检查 apple-sync 的运行记录和权限。",
    "apple_reminder_edit_rejected": "提醒事项里有一条改过的标题或日期存不下（太长，或者和另一条待办完全一样）："
    "在提醒事项里改一下那条就会恢复。",
    "apple_reminders_mass_missing": "提醒事项里一下子少了很多条，可能是 iCloud 重新同步，也可能是你自己删的。"
    "助理先不动这些待办；确实是你删的，在 App 里也删掉就好。",
    "macos_required": "提醒事项和日历同步需要 Mac 后端；当前没有跨机器的 Apple 同步桥，"
    "可在 App 内继续管理待办。",
    "plugin_setup_failed": "在 App 的插件设置里检查它的配置（比如 helper 的路径），改好后它会重新启动。",
}
MACOS_HINTS = {
    "screen_window_not_found": "在 Mac mini 上打开{app}的主窗口，别最小化（被别的窗口挡住没关系）。",
    "screen_app_not_running": "在 Mac mini 上打开并登录{app}。",
    "screen_recording_denied": "系统设置 → 隐私与安全性 → 屏幕与系统录音，打开 screen-helper。",
    "screen_accessibility_denied": "系统设置 → 隐私与安全性 → 辅助功能，打开 screen-helper。",
    # A locked session is not a display that is off (see plugins/screen/session.py).
    "screen_locked": DESKTOP_HINTS["screen_locked"],
    "screen_capture_failed": HINTS["screen_capture_failed"],
    "authentication_required": "Agent 的登录过期了：在 Mac mini 的终端运行一次 claude 或 codex 登录。",
    "apple_sync_failed": "提醒事项同步失败：系统设置 → 隐私与安全性 → 提醒事项 和 日历，打开 apple-sync，并确认「AI 待办」列表可见。",
}
WINDOWS_HINTS = {
    "screen_window_not_found": "在 Windows 电脑上打开{app}的主窗口，别最小化（被别的窗口挡住没关系）。",
    "screen_app_not_running": "在 Windows 电脑上打开并登录{app}。",
    "screen_recording_denied": "确认 Windows 桌面已登录且未锁定，{app}窗口没有最小化；"
    "{app}和后端应使用相同的普通权限。",
    "screen_accessibility_denied": "{app}和后端应使用相同的普通权限；不要单独以管理员身份运行客户端。",
    "screen_locked": "Windows 电脑锁屏了，截不了图；解锁后会自己恢复。"
    "想让夜里也能读，到「设置 → 系统 → 电源」检查休眠和显示器超时，"
    "再到「账户 → 登录选项」检查动态锁、恢复后要求登录和屏幕保护程序。",
    "screen_capture_failed": "截图没成功：确认 Windows 桌面已登录、亮屏解锁，{app}窗口没有最小化。"
    "如果刚断开 RDP，先确认桌面仍可访问；GPU 黑图也可能导致截图失败。",
    "authentication_required": "Agent 的登录过期了：在 Windows 电脑的终端运行一次 claude 或 codex 登录。",
    "apple_sync_failed": HINTS["macos_required"],
    "screen_ocr_language_missing": "在「设置 → 时间和语言 → 语言和区域 → 中文(简体) → 语言选项」"
    "安装「光学字符识别」，然后重试。",
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
            from core.source_views import SOURCE_NAMES

            name = plugins.manifests.get(plugin, {}).get("name", SOURCE_NAMES.get(plugin, plugin))
            if job.startswith("plugin:"):
                name += f"的「{job.split(':')[2]}」任务"
        instance = plugins.instances.get(plugin)
        app = getattr(instance, "app_name", "") or name
        platform_hints = {"macos": MACOS_HINTS, "windows": WINDOWS_HINTS}.get(plugins.platform, {})
        hint = platform_hints.get(error, HINTS.get(error, OTHER_HINT))
        return {
            "key": f"job:{job}",
            "plugin": plugin,
            "title": f"{name}一直失败",
            "ok_at": parse(ok_at),
            "hours": HOURS,
            "hint": hint.format(app=app, code=error),
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
        plugins, entries, failed = self.service.plugins, [], set()
        for identifier, instance in list(plugins.instances.items()):
            if not hasattr(instance, "watch"):
                continue
            try:
                found_entries = []
                for found in await asyncio.wait_for(instance.watch(), 10):
                    if not KEY.fullmatch(found["key"]):
                        raise ValueError("invalid watch key")
                    ok_at, hours = parse(found["ok_at"]), float(found["hours"])
                    if ok_at is not None and ok_at.utcoffset() is None:
                        raise ValueError("watch time needs an offset")
                    if hours < 0:
                        raise ValueError("invalid watch hours")
                    timedelta(hours=hours)  # rejects NaN, infinity and overflowing durations
                    found_entries.append(
                        {
                            "key": f"{identifier}:{found['key']}",
                            "plugin": identifier,
                            "title": str(found["title"]),
                            "ok_at": ok_at,
                            "hours": hours,
                            "hint": str(found["hint"]),
                            "covers": {
                                f"job:plugin:{identifier}:{job}" for job in found.get("jobs", [])
                            },
                        }
                    )
                if plugins.instances.get(identifier) is instance:
                    entries.extend(found_entries)
            except Exception:
                run = self.service.db.start_run(f"plugin:{identifier}:watch")
                self.service.db.finish_run(run, "failed", "plugin_watch_failed")
                if plugins.instances.get(identifier) is instance:
                    failed.add(identifier)
                    entry = self.job_entry(
                        f"plugin:{identifier}:watch", "plugin_watch_failed", None
                    )
                    entries.append({**entry, "covers": set()})
        return entries, failed

    def from_setup(self):
        plugins, entries = self.service.plugins, []
        for identifier in list(plugins.errors):
            if identifier not in plugins.manifests:
                continue  # a rejected manifest has no registered plugin state
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
        hooks, failed = await self.from_hooks()
        covered = set().union(*(e.pop("covers") for e in hooks))
        runs = [e for e in self.from_runs() if e["key"] not in covered]
        return self.from_setup() + hooks + runs, failed

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
        entries, failed = await self.entries()
        for entry in entries:
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
            if key.split(":", 1)[0] in failed and key not in kept:
                kept[key] = old  # a broken hook cannot confirm that its data recovered
            if old.get("todo") and kept.get(key, {}).get("todo") != old["todo"]:
                await self.close(old["todo"])
        self.service.db.put(STATE, kept)
        return kept
