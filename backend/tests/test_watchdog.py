"""数据看门狗 (core/watchdog.py): a job that keeps failing, or data a plugin has not got for hours,
becomes one of 助理Bot's todos for today, and is checked off when it works again."""

from datetime import UTC, datetime, timedelta

import pytest

from core.config import Settings
from core.models import HealthBatch, PluginPatch, TodoPatch
from core.service import Service
from plugins.screen.plugin_base import ScreenPlugin
from plugins.token_usage.plugin import LIMITS_KEY
from tests.test_plugins_jobs import make_plugin

NOW = datetime.now(UTC)
WATCHED = """from core.plugins import Plugin as Base
class Plugin(Base):
    def jobs(self):
        return {"read": {"cron": "*/30 * * * *", "callback": self.read}}
    async def read(self):
        pass
    async def watch(self):
        if self.context.get("broken"):
            raise RuntimeError("bug")
        return self.context.get("watch", [])
"""


@pytest.fixture
async def watched(tmp_path):
    make_plugin(tmp_path / "plugins", "watched", body=WATCHED, provides=["cards"])
    service = Service(
        Settings(
            token="test-token-000000000000000000000000",
            data_dir=tmp_path / "state",
            scheduler_enabled=False,
            _env_file=None,
        ),
        plugin_dir=tmp_path / "plugins",
        platform="linux",
    )
    await service.start()
    await service.plugins.patch("watched", PluginPatch(enabled=True))
    yield service
    await service.close()


def run(service, job, status, at, error=None):
    run_id = service.db.start_run(job)
    service.db.finish_run(run_id, status, error)
    service.db.execute(
        "UPDATE runs SET started_at=?,finished_at=? WHERE id=?",
        (at.isoformat(), at.isoformat(), run_id),
    )


def todos(service):
    return service.db.rows("SELECT * FROM todos ORDER BY created_at")


async def test_a_job_failing_for_hours_becomes_a_todo_until_it_works(watched):
    run(watched, "apple_sync", "succeeded", NOW - timedelta(hours=2))
    run(watched, "apple_sync", "failed", NOW - timedelta(hours=1), "apple_sync_failed")
    await watched.watchdog.check(NOW)
    assert todos(watched) == []  # two hours without success: not yet

    run(watched, "apple_sync", "failed", NOW - timedelta(minutes=10), "apple_sync_failed")
    await watched.watchdog.check(NOW + timedelta(hours=1))
    [todo] = todos(watched)
    assert todo["title"] == "同步提醒事项一直失败" and todo["status"] == "pending"
    assert todo["source_plugin"] == "watchdog" and todo["created_by"] == "watchdog"
    assert "起就没成功过" in todo["notes"] and "apple-sync" in todo["notes"]
    local = datetime.fromisoformat(todo["due_at"]).astimezone(watched.watchdog.zone)
    assert (local.hour, local.minute, todo["has_time"]) == (0, 0, 0)
    # The note it cites is the backend's own, not a message a source read.
    [source] = watched.store.todo(todo["id"])["sources"]
    assert source["chat_name"] == "数据看门狗"
    assert watched.store.unprocessed() == []
    day = (NOW + timedelta(hours=1)).astimezone(watched.watchdog.zone).date()
    assert "数据看门狗" not in watched.daily.messages_file(day, watched.daily.messages(day))

    await watched.watchdog.check(NOW + timedelta(hours=2))
    assert len(todos(watched)) == 1  # still the same one

    run(watched, "apple_sync", "succeeded", NOW)
    await watched.watchdog.check(NOW + timedelta(hours=2))
    assert todos(watched)[0]["status"] == "completed"
    assert watched.db.get("watchdog") == {}


async def test_a_job_that_never_worked_counts_from_when_it_was_seen(watched):
    run(watched, "plugin:watched:read", "failed", NOW - timedelta(days=2), "job_failed")
    await watched.watchdog.check(NOW)
    assert todos(watched) == []
    await watched.watchdog.check(NOW + timedelta(hours=3))
    [todo] = todos(watched)
    assert todo["title"] == "watched的「read」任务一直失败" and "发现的" in todo["notes"]
    assert "job_failed" in todo["notes"]


async def test_a_locked_screen_says_unlocking_fixes_it(watched):
    run(watched, "plugin:watched:read", "failed", NOW - timedelta(days=1), "screen_locked")
    await watched.watchdog.check(NOW)
    await watched.watchdog.check(NOW + timedelta(hours=3))
    [todo] = todos(watched)
    assert "锁屏" in todo["notes"] and "解锁后会自己恢复" in todo["notes"]
    assert "错误代码" not in todo["notes"]


async def test_a_plugins_watch_hook_reports_data_it_has_not_got(watched):
    context = watched.plugins.context("watched")
    entry = {"key": "page", "title": "页面读不到了", "ok_at": None, "hours": 3, "hint": "打开页面"}
    context.put("watch", [{**entry, "jobs": ["read"]}])
    # Its own job's failures are what the entry already says.
    run(watched, "plugin:watched:read", "failed", NOW - timedelta(hours=9), "screen_page_not_open")
    await watched.watchdog.check(NOW)
    assert todos(watched) == []
    await watched.watchdog.check(NOW + timedelta(hours=3))
    [todo] = todos(watched)
    assert todo["title"] == "页面读不到了" and "打开页面" in todo["notes"]

    # A new reason updates the notes of the same todo.
    context.put("watch", [{**entry, "hint": "先登录"}])
    await watched.watchdog.check(NOW + timedelta(hours=4))
    assert len(todos(watched)) == 1  # the job, no longer covered, is only just seen failing
    assert "先登录" in watched.store.todo(todo["id"])["notes"]

    # Data that came in recently is fine; the entry closes.
    context.put("watch", [{**entry, "ok_at": NOW.isoformat()}])
    run(watched, "plugin:watched:read", "succeeded", NOW)
    await watched.watchdog.check(NOW + timedelta(hours=1))
    assert {t["status"] for t in todos(watched)} == {"completed"}


async def test_a_todo_checked_off_by_hand_stays_off_until_it_comes_back(watched):
    context = watched.plugins.context("watched")
    entry = {"key": "page", "title": "页面读不到了", "ok_at": None, "hours": 0, "hint": "打开页面"}
    context.put("watch", [entry])
    await watched.watchdog.check(NOW)
    [todo] = todos(watched)
    await watched.update_todo(todo["id"], TodoPatch(status="completed"))
    await watched.watchdog.check(NOW + timedelta(minutes=10))
    assert todos(watched)[0]["status"] == "completed"
    # Fixed, then broken again the same day: the same todo opens again.
    context.put("watch", [])
    await watched.watchdog.check(NOW + timedelta(minutes=20))
    context.put("watch", [entry])
    await watched.watchdog.check(NOW + timedelta(minutes=30))
    [again] = todos(watched)
    assert again["id"] == todo["id"] and again["status"] == "pending"


async def test_a_broken_hook_and_a_plugin_that_cannot_start(watched):
    watched.plugins.context("watched").put("broken", True)
    watched.plugins.errors["watched"] = "plugin_setup_failed"
    await watched.watchdog.check(NOW)
    assert [t["title"] for t in todos(watched)] == ["watched没能启动"]
    [failed] = [r for r in watched.store.runs()["items"] if r["job"] == "plugin:watched:watch"]
    assert failed["error"] == "plugin_watch_failed"


# The plugins' hooks ----------------------------------------------------------------------------


async def test_health_watches_the_iphones_uploads(service):
    await service.start()
    try:
        await service.plugins.patch("health", PluginPatch(enabled=True))
        plugin = service.plugins.instances["health"]
        [entry] = await plugin.watch()
        assert entry["ok_at"] is None and entry["hours"] == 36
        sample = {
            "id": "s1",
            "type": "step_count",
            "value": 10,
            "unit": "count",
            "start_at": "2026-10-28T08:00:00+08:00",
            "end_at": "2026-10-28T08:01:00+08:00",
            "source": "iPhone",
        }
        service.store.health(HealthBatch.model_validate({"samples": [sample]}))
        [entry] = await plugin.watch()
        assert datetime.now(UTC) - datetime.fromisoformat(entry["ok_at"]) < timedelta(minutes=1)
    finally:
        await service.close()


async def test_token_usage_watches_claudes_login(service):
    await service.start()
    try:
        await service.plugins.patch("token_usage", PluginPatch(enabled=True))
        plugin = service.plugins.instances["token_usage"]
        await plugin.limiting  # its first read, which would overwrite the state below
        context = service.plugins.context("token_usage")
        context.put(LIMITS_KEY, {"claude": {"status": "ok", "windows": []}})
        assert await plugin.watch() == []
        as_of = int(NOW.timestamp())
        context.put(LIMITS_KEY, {"claude": {"status": "expired", "as_of": as_of, "stale": True}})
        [entry] = await plugin.watch()
        assert entry["title"] == "Claude 的用量读不到了" and "运行一次 claude" in entry["hint"]
        assert datetime.fromisoformat(entry["ok_at"]).timestamp() == as_of
    finally:
        await service.close()


async def test_screen_plugins_watch_cut_off_chats(service, tmp_path):
    plugin = ScreenPlugin()
    plugin.app_name = "QQ"
    plugin.context, plugin.config = service.plugins.context("qq_screen"), {}
    assert await plugin.watch() == []
    plugin.context.put("cut_off_chats", ["人工智能学院2026…"])
    [entry] = await plugin.watch()
    assert entry["title"] == "QQ有群名被截断，没有读"
    assert entry["hint"].endswith("人工智能学院2026…") and entry["ok_at"] is None


def test_the_watchdog_runs_every_ten_minutes(service):
    service.jobs.configure()
    assert str(service.jobs.scheduler.get_job("watchdog").trigger).startswith("cron[")
    assert "watchdog" in service.jobs.callbacks
