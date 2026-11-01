import asyncio
import re
import shutil
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.prompts import render
from core.store import ConflictError
from core.tokens import current_grant
from harness.claude_code import ClaudeCodeHarness
from harness.codex import CodexHarness
from harness.deepseek import DeepSeekHarness


def error_code(exc):
    """A failed job's error for runs: the exception's own fixed code (screen_history_gap,
    apple_sync_failed...) when it is one, never free text that could hold private data."""
    text = str(exc)
    return text if re.fullmatch(r"[a-z][a-z0-9_]{2,60}", text) else "job_failed"


class Jobs:
    def __init__(self, service):
        self.service = service
        self.scheduler = AsyncIOScheduler()
        self.tasks = {}
        self.pending = set()
        self.self_timed = {"knowledge_organize"}
        self.harnesses = {
            "claude_code": ClaudeCodeHarness(service.settings.data_dir / "agent-work"),
            "codex": CodexHarness(service.settings.data_dir / "agent-work"),
            "deepseek": DeepSeekHarness(service.settings.data_dir / "agent-work"),
        }
        self.callbacks = {}

    def configure(self):
        settings = self.service.store.settings
        zone = ZoneInfo(settings.timezone)
        self.scheduler.remove_all_jobs()
        self.callbacks = {
            "extract_todos": self.extract,
            "apple_sync": self.sync,
            "backup": self.backup,
            "memory_index": self.index,
            "daily_notes": self.daily_notes,
            "watchdog": self.watch,
            "knowledge_organize": self.service.knowledge.organize,
        }
        schedules = {
            "extract_todos": settings.extraction_cron,
            "apple_sync": "*/5 * * * *",
            "backup": "0 3 * * *",
            "memory_index": "*/10 * * * *",
            "daily_notes": "*/10 * * * *",
            "watchdog": "*/10 * * * *",
            "knowledge_organize": settings.knowledge_organize_cron,
        }
        if getattr(self.service.apple.bridge, "error", None):
            # No bridge on this platform (Windows): it would only fail every five minutes. The
            # queue stays for a later bridge, and /status shows why; a manual run still reports it.
            del schedules["apple_sync"]
        # Jobs that enforce their own time budgets run without the outer timeout.
        self.self_timed = {"knowledge_organize"}
        for identifier in self.service.plugins.instances:
            if "source" not in self.service.plugins.manifests[identifier]["provides"]:
                continue
            job_id = f"fetch:{identifier}"

            async def fetch(plugin_id=identifier):
                return await self.service.plugins.fetch(plugin_id)

            self.callbacks[job_id] = fetch
            schedules[job_id] = (
                self.service.plugins.manifests[identifier]["schedule"] or settings.source_cron
            )
        for (identifier, name), hook in self.service.plugins.hooks("jobs").items():
            job_id = f"plugin:{identifier}:{name}"
            self.callbacks[job_id] = hook["callback"]
            schedules[job_id] = hook["cron"]
            if hook.get("self_timed"):
                self.self_timed.add(job_id)
        for job_id, cron in schedules.items():
            self.scheduler.add_job(
                self.scheduled,
                CronTrigger.from_crontab(cron, timezone=zone),
                args=[job_id],
                id=job_id,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=120,
            )

    async def scheduled(self, job_id):
        try:
            task = self.submit(job_id)
        except (ConflictError, KeyError):
            return
        await task

    def submit(self, job_id, *, rerun=False):
        if job_id not in self.callbacks:
            raise KeyError("job_not_found")
        if job_id in self.tasks and not self.tasks[job_id].done():
            if rerun:
                self.pending.add(job_id)
                return self.tasks[job_id]
            raise ConflictError("job_already_running")
        callback = self.callbacks[job_id]
        task = asyncio.create_task(self._drain(job_id, callback), name=job_id)
        self.tasks[job_id] = task
        task.add_done_callback(
            lambda done: self.tasks.pop(job_id, None) if self.tasks.get(job_id) is done else None
        )
        return task

    async def _drain(self, job_id, callback):
        try:
            while True:
                await self._run(job_id, callback)
                if job_id not in self.pending or job_id not in self.callbacks:
                    return
                self.pending.discard(job_id)
                callback = self.callbacks[job_id]
        finally:
            self.pending.discard(job_id)

    async def _run(self, job_id, callback):
        settings, db = self.service.store.settings, self.service.db
        for attempt in range(1, settings.retry_attempts + 1):
            run_id = db.start_run(job_id, attempt=attempt)
            try:
                if job_id in self.self_timed:
                    await callback()
                else:
                    await asyncio.wait_for(callback(), settings.agent_timeout * 2 + 60)
                db.finish_run(run_id)
                return
            except asyncio.CancelledError:
                db.finish_run(run_id, "cancelled", "cancelled")
                raise
            except Exception as exc:
                db.finish_run(run_id, "failed", error_code(exc))
            if attempt < settings.retry_attempts:
                await asyncio.sleep(settings.retry_base_seconds * 2 ** (attempt - 1))

    async def cancel_plugin(self, identifier):
        tasks = [
            task
            for job, task in list(self.tasks.items())
            if job == f"fetch:{identifier}" or job.startswith(f"plugin:{identifier}:")
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def mcp_config(self, token=None):
        """How an Agent CLI reaches this core: the stdio bridge plus local credentials."""
        backend = Path(__file__).resolve().parents[1]
        return {
            "stdio": {
                "command": sys.executable,
                "args": ["-m", "core.mcp_stdio"],
                "cwd": str(backend),
            },
            "environment": {
                "ASSISTANT_TOKEN": token or self.service.settings.token.get_secret_value(),
                "ASSISTANT_BASE_URL": self.service.settings.base_url,
            },
        }

    async def run_agent(
        self,
        job,
        prompt,
        succeeded=None,
        incomplete_error="agent_incomplete",
        harness=None,
        model=None,
        grant=None,
    ):
        """Run a prompt through the harness order for `job`, falling back on failure.

        `harness` (with an optional `model` for it) goes first when given, as the user picked
        it for one chat line; the others still follow if it fails. Every attempt gets its own
        runs row. A zero exit only counts when `succeeded()` (if given) confirms the work was
        written. Returns the harness that succeeded.
        """
        settings, db = self.service.store.settings, self.service.db
        order = list(settings.harness_order)
        preferred = harness if harness in self.harnesses else settings.job_harness.get(job)
        if preferred:
            order = [preferred, *[name for name in order if name != preferred]]
        for name in order:
            if grant is not None and grant.kind == "organizer":
                # Permissions may change while another topic or fallback is running.
                # Check again immediately before sending the prompt to any model.
                self.service.memory.access.require_read(grant.area + "/_", grant)
            run_id = db.start_run(job, name)
            chosen = {"model": model} if model and name == harness else {}
            credential, context = None, None
            if grant is not None:
                attempt_grant = replace(grant, harness=name)
                credential = self.service.tokens.temporary(
                    attempt_grant, settings.agent_timeout + 30
                )
                context = current_grant.set(attempt_grant)
            try:
                result = await asyncio.wait_for(
                    self.harnesses[name].run(
                        prompt, self.mcp_config(credential), settings.agent_timeout, **chosen
                    ),
                    settings.agent_timeout + 5,
                )
                completed = not result.success or succeeded is None or succeeded()
            except asyncio.CancelledError:
                db.finish_run(run_id, "cancelled", "cancelled")
                raise
            except Exception:
                db.finish_run(run_id, "failed", "harness_exception")
                continue
            finally:
                if credential:
                    self.service.tokens.discard(credential)
                if context is not None:
                    current_grant.reset(context)
            db.finish_run(run_id, "succeeded" if result.success else "failed", result.error)
            if result.usage:
                db.record_usage(run_id, job, name, result.usage)
            if result.success:
                # A zero CLI exit without the expected writes is not a success.
                if not completed:
                    db.finish_run(run_id, "failed", incomplete_error)
                    continue
                return name
        raise RuntimeError("all_harnesses_failed")

    async def extract(self):
        if not self.service.store.unprocessed(limit=1):
            return
        settings = self.service.store.settings
        prompt = render(
            "extract_todos",
            local_now=datetime.now(ZoneInfo(settings.timezone)).isoformat(),
            timezone=settings.timezone,
            confidence_threshold=settings.confidence_threshold,
        )
        await self.run_agent(
            "extract_todos",
            prompt,
            succeeded=lambda: not self.service.store.unprocessed(limit=1),
            incomplete_error="messages_remaining",
        )
        await self.service.daily.write()  # what was just sorted out shows in today's folder

    async def sync(self):
        if error := getattr(self.service.apple.bridge, "error", None):
            raise RuntimeError(error)
        result = await self.service.apple.flush()
        if result["failed"]:
            raise RuntimeError("apple_sync_failed")

    async def index(self):
        await asyncio.to_thread(self.service.memory.reindex)

    async def daily_notes(self):
        await self.service.daily.write_recent()

    async def watch(self):
        await self.service.watchdog.check()

    async def backup(self):
        directory = (
            self.service.settings.data_dir / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        )
        directory.mkdir(parents=True)
        with (
            self.service.db.connect() as source,
            sqlite3.connect(directory / "assistant.sqlite3") as target,
        ):
            source.backup(target)
        shutil.copytree(self.service.memory.root, directory / "vault", symlinks=True)
        (directory / "COMPLETE").write_text(
            "SQLite online backup and Markdown vault\n", encoding="utf-8"
        )

    async def close(self):
        self.pending.clear()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
