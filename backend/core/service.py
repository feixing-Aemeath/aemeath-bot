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
        result = self.store.add_todo(data)
        await self.apple.flush()
        if result["todo"]:
            result["todo"] = self.store.todo(result["todo"]["id"])
        return result

    async def update_todo(self, identifier, patch):
        self.store.update_todo(identifier, patch)
        await self.apple.flush()
        return self.store.todo(identifier)
