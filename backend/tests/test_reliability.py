"""Failure paths with synthetic data, clocks and blocked workers; no live services."""

import asyncio
import errno
import os
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.db import Database
from core.models import RuntimeSettings
from core.store import ConflictError
from core.tokens import DeviceTokens
from harness.base import RunResult


async def test_background_harness_credentials_expire_with_the_attempt(service):
    root = service.settings.token.get_secret_value()
    credentials = []

    class Agent:
        async def run(self, _prompt, config, _timeout):
            token = config["environment"]["ASSISTANT_TOKEN"]
            credentials.append(token)
            if token != root:
                assert service.tokens.check(token) is not None
                assert DeviceTokens(service.db).check(token) is None  # a restarted core
            return RunResult(
                len(credentials) == 2, "quota_exhausted" if len(credentials) == 1 else None
            )

    service.jobs.harnesses = {"claude_code": Agent(), "codex": Agent()}
    await service.jobs.run_agent("extract_todos", "synthetic prompt")
    assert len(set(credentials)) == 2 and root not in credentials
    assert all(service.tokens.check(token) is None for token in credentials)


@pytest.mark.parametrize("age_hours,expected", [(None, True), (25, True), (1, False)])
async def test_startup_catches_up_overdue_backups(service, monkeypatch, age_hours, expected):
    if age_hours is not None:
        complete = service.settings.data_dir / "backups" / "old" / "COMPLETE"
        complete.parent.mkdir(parents=True)
        complete.write_text("synthetic backup")
        at = time.time() - age_hours * 3600
        os.utime(complete, (at, at))
    called = []

    async def backup():
        called.append(True)

    service.settings.scheduler_enabled = True
    monkeypatch.setattr(service.jobs.scheduler, "start", lambda: None)
    monkeypatch.setattr(service.jobs, "backup", backup)
    await service.start()
    try:
        task = service.jobs.tasks.get("backup")
        if task:
            await task
        assert bool(called) == expected
    finally:
        await service.close()


def test_concurrent_digest_appends_keep_both_updates(service, monkeypatch):
    service.store.digest("2026-11-04", "fixture", "原有摘要")
    readers = threading.Barrier(2)
    connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql.startswith("SELECT summary FROM digests") and not self.in_transaction:
                readers.wait(5)  # both unprotected reads finish before either writes
            return cursor

    def concurrent_connect(*args, **kwargs):
        return connect(*args, **kwargs, factory=Connection)

    monkeypatch.setattr("core.db.sqlite3.connect", concurrent_connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writes = [
            pool.submit(service.store.digest, "2026-11-04", "fixture", text, True)
            for text in ("第一条新增", "第二条新增")
        ]
        for write in writes:
            write.result()
    summary = service.store.digests("2026-11-04")["items"][0]["summary"]
    assert all(text in summary for text in ("原有摘要", "第一条新增", "第二条新增"))


def test_legacy_metadata_upgrade_preserves_data_and_is_repeatable(service):
    service.db.execute(
        "INSERT INTO messages(id,plugin,external_id,chat_name,sender,sent_at,text,processed) "
        "VALUES('old','fixture','old','test','test','2026-11-04T00:00:00+00:00','旧消息',1)"
    )
    for column in ("preview", "revised", "flags", "outgoing"):
        service.db.execute(f"ALTER TABLE messages DROP COLUMN {column}")
    service.db.execute("ALTER TABLE device_tokens DROP COLUMN reader")
    service.db.execute("ALTER TABLE knowledge_sources DROP COLUMN organize_offset")
    for _ in range(2):
        migrated = Database(service.db.path)
        [row] = migrated.rows("SELECT * FROM messages")
        assert (row["text"], row["processed"], row["flags"]) == ("旧消息", 1, "[]")
        assert row["preview"] == row["revised"] == row["outgoing"] == 0


async def test_backup_keeps_the_event_loop_available(service, monkeypatch):
    released = threading.Event()
    loop = asyncio.get_running_loop()
    copytree = shutil.copytree

    def copy(*args, **kwargs):
        loop.call_soon_threadsafe(released.set)
        assert released.wait(2), "backup blocked the event loop"
        return copytree(*args, **kwargs)

    monkeypatch.setattr("core.jobs.shutil.copytree", copy)
    await service.jobs.backup()


async def test_backup_cancellation_joins_the_worker(service, monkeypatch):
    entered, released = threading.Event(), threading.Event()

    def copy(*_args, **_kwargs):
        entered.set()
        assert released.wait(5)

    monkeypatch.setattr("core.jobs.shutil.copytree", copy)
    service.jobs.callbacks["backup"] = service.jobs.backup
    task = service.jobs.submit("backup")
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(ConflictError):
            service.jobs.submit("backup")
    finally:
        released.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


@pytest.mark.parametrize("job", ["memory_index", "daily_notes"])
async def test_cancelled_file_jobs_wait_for_the_worker(service, monkeypatch, job):
    entered, released = threading.Event(), threading.Event()

    def blocked(*_args):
        entered.set()
        assert released.wait(5)

    if job == "memory_index":
        monkeypatch.setattr(service.memory, "reindex", blocked)
        callback = service.jobs.index
    else:
        monkeypatch.setattr(service.daily, "_write", blocked)
        callback = service.jobs.daily_notes
    task = asyncio.create_task(callback())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        released.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


async def test_disk_full_backup_removes_partial_copy(service, monkeypatch):
    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "synthetic disk full")

    monkeypatch.setattr("core.jobs.shutil.copytree", full)
    with pytest.raises(OSError) as error:
        await service.jobs.backup()
    assert error.value.errno == errno.ENOSPC
    assert list((service.settings.data_dir / "backups").iterdir()) == []


async def test_job_timeout_allows_the_full_harness_fallback(service, monkeypatch):
    settings = RuntimeSettings(agent_timeout=300, retry_attempts=1)
    service.db.put("settings", settings.model_dump())
    budgets = []

    async def wait_for(awaitable, timeout):
        budgets.append(timeout)
        return await awaitable

    class Agent:
        async def run(self, *_args):
            return RunResult(False, "timeout")

    monkeypatch.setattr("core.jobs.asyncio.wait_for", wait_for)
    service.jobs.harnesses = {name: Agent() for name in ("deepseek", "claude_code", "codex")}

    async def review():
        await service.jobs.run_agent("review", "test", harness="deepseek")

    service.jobs.callbacks["review"] = review
    await service.jobs.submit("review")
    assert len(budgets) == 4  # the job, followed by all three harness attempts
    assert budgets[0] >= sum(budgets[1:])
