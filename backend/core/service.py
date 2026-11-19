import asyncio
from pathlib import Path

from core.apple import AppleSync, FakeAppleBridge, MacRequiredBridge, SwiftAppleBridge
from core.chat import Chat
from core.daily import DailyNotes
from core.db import Database
from core.jobs import Jobs
from core.knowledge import Knowledge
from core.lock import InstanceLock
from core.memory import Memory
from core.platforms import platform_name
from core.plugins import PluginManager
from core.store import Store
from core.tokens import DeviceTokens
from core.watchdog import Watchdog


class Service:
    def __init__(self, settings, plugin_dir=None, platform=None, apple_bridge=None):
        self.settings = settings
        settings.data_dir = settings.data_dir.resolve()
        self.db = Database(settings.data_dir / "assistant.sqlite3")
        self.instance_lock = InstanceLock(settings.data_dir / "service.lock")
        self.store = Store(self.db)
        self.tokens = DeviceTokens(self.db)
        self.memory = Memory(self.db, settings.vault_dir or settings.data_dir / "vault")
        self.knowledge = Knowledge(self)
        self.memory.knowledge = self.knowledge
        windows = (platform or platform_name()) == "windows"
        if not windows and settings.apple_mode == "real" and not settings.apple_binary:
            raise ValueError("real apple mode requires ASSISTANT_APPLE_BINARY")
        bridge = apple_bridge or (
            MacRequiredBridge()
            if windows
            else SwiftAppleBridge(settings.apple_binary)
            if settings.apple_mode == "real"
            else FakeAppleBridge(settings.data_dir / "fake-apple")
        )
        self.apple = AppleSync(self.db, bridge)
        self.agent_edit = asyncio.Lock()
        self.plugins = PluginManager(
            self.store,
            plugin_dir or Path(__file__).resolve().parents[1] / "plugins",
            settings.data_dir,
            platform,
        )
        self.jobs = Jobs(self)
        self.store.on_attention = self.request_extraction
        self.chat = Chat(self)
        self.daily = DailyNotes(self)
        self.watchdog = Watchdog(self)
        self.refresh_tools = None
        self.plugins.on_change = self.reconfigure
        self.plugins.cancel_jobs = self.jobs.cancel_plugin
        self.plugins.memory = self.memory
        self.plugins.run_agent = self.jobs.run_agent
        self.plugins.apple_call = self.call_apple
        self.plugins.redact_chat = self.chat.redact
        self.plugins.chat = self.chat

    def request_extraction(self):
        if "extract_todos" in self.jobs.callbacks:
            self.jobs.submit("extract_todos", rerun=True)

    async def call_apple(self, resource, action, payload):
        return await self.apple.call(resource, action, payload)

    def reconfigure(self):
        self.jobs.configure()
        if self.refresh_tools:
            self.refresh_tools()

    async def start(self):
        self.instance_lock.acquire()
        try:
            self.db.recover_runs()
            self.chat.recover()
            await asyncio.to_thread(self.memory.reindex)
            await self.plugins.discover()
            self.reconfigure()
            if self.settings.scheduler_enabled:
                self.jobs.scheduler.start()
                if self.jobs.backup_due():
                    self.jobs.submit("backup")
        except BaseException:
            self.instance_lock.release()
            raise

    async def close(self):
        try:
            await self.chat.close()
            await self.jobs.close()
            await self.plugins.close()
        finally:
            self.instance_lock.release()

    async def add_todo(self, data):
        return await self._add_todo(data, self.store.add_todo)

    async def new_todo(self, data):
        return await self._add_todo(data, self.store.new_todo)

    async def _add_todo(self, data, save):
        async with self.apple.lock:
            result = save(data)
            await self.apple._flush()
            if result["todo"]:
                result["todo"] = self.store.todo(result["todo"]["id"])
            return result

    async def update_todo(self, identifier, patch, *, from_agent=False):
        # An Agent reads the user's latest edits first, so nothing may land between its read and
        # its write. Other edits only wait for that one row: a sync can hold apple.lock for a long
        # time, and the user's change is saved before the wait for the phone, not after it.
        flush = {}
        if from_agent:
            async with self.apple.lock, self.agent_edit:
                flush = await self._read_for_agent_edit(identifier, patch)
                self.store.update_todo(identifier, patch, from_agent=True)
        else:
            async with self.agent_edit:
                self.store.update_todo(identifier, patch)
        await self.apple.flush(**flush)
        await self.plugins.notify("todo_changed", identifier)
        return self.store.todo(identifier)

    async def _read_for_agent_edit(self, identifier, patch):
        """What flush needs to know. Only a read can tell whether the user changed the title or
        date on the phone, so without one those stay refused. A status or a note is kept, and its
        write to an existing reminder waits for a read that works."""
        try:
            read = await self.apple._reconcile(todo_ids={identifier})
            read_ok, flush = not read["failed"], {"failed_ids": read.get("failed_ids", [])}
        except RuntimeError:
            read_ok, flush = False, {"readback": False, "read_failed": True}
        if not read_ok and patch.model_fields_set & {"title", "due_at", "has_time"}:
            raise RuntimeError("apple_sync_failed")
        return flush
