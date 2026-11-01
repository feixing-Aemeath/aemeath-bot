import asyncio
import importlib.util
import inspect
import json
import re
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml
from apscheduler.triggers.cron import CronTrigger

from core.db import dump, now
from core.models import Card, MessageInput, PluginManifest
from core.platforms import platform_name
from core.store import ConflictError


@dataclass
class PluginContext:
    store: object
    data_dir: Path
    plugin_id: str
    # Long-term Markdown memory and the core's Agent runner (Jobs.run_agent); plugins never
    # start an Agent CLI themselves, so harness order, fallback and run records stay central.
    memory: object = None
    run_agent: object = None
    apple_call: object = None
    get_plugin: object = None  # enabled instances only, for shared public plugin data
    # (reader, secret): hides something the user typed to that Bot, e.g. a zip password
    # (Chat.redact).
    redact_chat: object = None

    def get(self, key, default=None):
        return self.store.db.get(f"plugin:{self.plugin_id}:{key}", default)

    def put(self, key, value):
        self.store.db.put(f"plugin:{self.plugin_id}:{key}", value)


class Plugin:
    """Optional async hooks. Only trusted local Python plugins may be installed."""

    async def setup(self, context, config):
        self.context, self.config = context, config

    async def fetch(self):
        return []

    def jobs(self):
        return {}  # name -> {cron: five-field-cron, callback: async callable}

    def mcp_tools(self):
        return {}  # name -> async callable(arguments: dict)

    async def cards(self):
        return []

    async def teardown(self):
        pass


class PluginManager:
    def __init__(self, store, directory, data_dir, platform=None):
        self.store, self.directory, self.data_dir = store, directory, data_dir
        self.platform = platform or platform_name()
        self.manifests, self.instances, self.errors, self.locks = {}, {}, {}, {}
        self.on_change = None
        self.cancel_jobs = None
        self.memory = None
        self.run_agent = None
        self.apple_call = None
        self.redact_chat = None

    def context(self, identifier):
        return PluginContext(
            self.store,
            self.data_dir,
            identifier,
            self.memory,
            self.run_agent,
            apple_call=self.apple_call,
            get_plugin=self.instances.get,
            redact_chat=self.redact_chat,
        )

    async def discover(self):
        for manifest_path in sorted(self.directory.glob("*/manifest.yaml")):
            identifier = manifest_path.parent.name
            try:
                # Exact keys and value types; later code and PluginView rely on both.
                manifest = PluginManifest.model_validate(
                    yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                ).model_dump()
                if manifest["id"] != identifier:
                    raise ValueError("invalid manifest")
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", identifier):
                    raise ValueError("invalid plugin id")
                if not set(manifest["platforms"]) <= {"any", "linux", "macos", "windows"}:
                    raise ValueError("invalid platforms")
                if manifest["schedule"]:
                    CronTrigger.from_crontab(manifest["schedule"])
                jsonschema.Draft202012Validator.check_schema(manifest["config_schema"])
                self.manifests[identifier] = manifest
                self.locks[identifier] = asyncio.Lock()
                self.store.db.execute("INSERT OR IGNORE INTO plugins(id) VALUES(?)", (identifier,))
                row = self.row(identifier)
                if row["enabled"] and not row["deleted"] and self.supported(identifier):
                    await self._enable(identifier, json.loads(row["config_json"]))
            except Exception:
                run = self.store.db.start_run(f"plugin:{identifier}:setup")
                self.store.db.finish_run(run, "failed", "plugin_setup_failed")
                self.errors[identifier] = "plugin_setup_failed"

    def row(self, identifier):
        rows = self.store.db.rows("SELECT * FROM plugins WHERE id=?", (identifier,))
        if not rows or identifier not in self.manifests:
            raise KeyError("plugin_not_found")
        return rows[0]

    def supported(self, identifier):
        platforms = self.manifests[identifier]["platforms"]
        return "any" in platforms or self.platform in platforms

    def view(self, identifier):
        row = self.row(identifier)
        if row["deleted"]:
            raise KeyError("plugin_not_found")
        state = "enabled" if identifier in self.instances else "disabled"
        if not self.supported(identifier):
            state = "unsupported"
        elif identifier in self.errors:
            state = "error"
        instance = self.instances.get(identifier)
        try:
            dynamic_error = (
                instance.state_error() if instance and hasattr(instance, "state_error") else None
            )
        except Exception:
            dynamic_error = "plugin_status_failed"
        if dynamic_error:
            state = "error"
        return {
            **self.manifests[identifier],
            "enabled": bool(row["enabled"]),
            "config": json.loads(row["config_json"]),
            "last_run_at": row["last_run_at"],
            "state": state,
            "error": dynamic_error or self.errors.get(identifier),
        }

    def list(self):
        return [self.view(i) for i in self.manifests if not self.row(i)["deleted"]]

    async def _enable(self, identifier, config):
        jsonschema.validate(config, self.manifests[identifier]["config_schema"])
        module_path = self.directory / identifier / "plugin.py"
        spec = importlib.util.spec_from_file_location(f"assistant_plugin_{identifier}", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        instance = module.Plugin()
        try:
            await asyncio.wait_for(instance.setup(self.context(identifier), config), 30)
        except BaseException:
            try:
                await instance.teardown()
            except Exception:
                pass
            raise
        self.instances[identifier] = instance

    async def _disable(self, identifier):
        if self.cancel_jobs:
            await self.cancel_jobs(identifier)
        instance = self.instances.pop(identifier, None)
        if instance:
            try:
                await asyncio.wait_for(instance.teardown(), 10)
            except Exception:
                run = self.store.db.start_run(f"plugin:{identifier}:teardown")
                self.store.db.finish_run(run, "failed", "plugin_teardown_failed")

    async def patch(self, identifier, patch):
        self.view(identifier)
        async with self.locks[identifier]:
            row = self.row(identifier)
            config = patch.config if patch.config is not None else json.loads(row["config_json"])
            enabled = patch.enabled if patch.enabled is not None else bool(row["enabled"])
            jsonschema.validate(config, self.manifests[identifier]["config_schema"])
            if identifier in {"qq_screen", "wechat_screen"}:
                # Cross-field constraint cannot be expressed by the manifest's JSON schema.
                from plugins.screen.plugin_base import validate_config

                validate_config(config)
            if enabled and not self.supported(identifier):
                raise ConflictError("unsupported_platform")
            await self._disable(identifier)
            self.errors.pop(identifier, None)
            self.store.db.execute(
                "UPDATE plugins SET enabled=?,config_json=? WHERE id=?",
                (int(enabled), dump(config), identifier),
            )
            if enabled:
                try:
                    await self._enable(identifier, config)
                except Exception:
                    self.errors[identifier] = "plugin_setup_failed"
                    run = self.store.db.start_run(f"plugin:{identifier}:setup")
                    self.store.db.finish_run(run, "failed", "plugin_setup_failed")
            if self.on_change:
                self.on_change()
            return self.view(identifier)

    async def delete(self, identifier):
        self.view(identifier)
        async with self.locks[identifier]:
            await self._disable(identifier)
            self.store.db.execute(
                "UPDATE plugins SET enabled=0,deleted=1 WHERE id=?", (identifier,)
            )
            if self.on_change:
                self.on_change()

    async def fetch(self, identifier):
        instance = self.instances.get(identifier)
        if not instance:
            raise KeyError("plugin_disabled")
        # Reading QQ chats screen by screen (capture, scroll, OCR) takes minutes, not seconds.
        batch = await asyncio.wait_for(instance.fetch(), 600)
        # Reject a stale fetch result after disable/reconfiguration.
        if self.instances.get(identifier) is not instance:
            raise ConflictError("plugin_changed")
        messages = [MessageInput.model_validate(m) for m in batch]
        config = json.loads(self.row(identifier)["config_json"])
        count = self.store.ingest(identifier, messages, config.get("whitelist", []))
        # Source checkpoints commit only after the whole batch was durably ingested.
        if hasattr(instance, "commit_fetch"):
            await instance.commit_fetch()
        self.store.db.execute("UPDATE plugins SET last_run_at=? WHERE id=?", (now(), identifier))
        return count

    async def cards(self, identifier):
        self.view(identifier)
        instance = self.instances.get(identifier)
        if not instance:
            return []
        try:
            return [
                Card.model_validate(c).model_dump()
                for c in await asyncio.wait_for(instance.cards(), 10)
            ]
        except Exception:
            run = self.store.db.start_run(f"plugin:{identifier}:cards")
            self.store.db.finish_run(run, "failed", "plugin_cards_failed")
            return []

    def hooks(self, kind):
        result = {}
        for identifier, instance in list(self.instances.items()):
            try:
                for name, hook in getattr(instance, kind)().items():
                    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
                        raise ValueError("invalid hook name")
                    if kind == "jobs":
                        CronTrigger.from_crontab(hook["cron"])
                        if not inspect.iscoroutinefunction(hook["callback"]):
                            raise ValueError("job must be async")
                    elif not inspect.iscoroutinefunction(hook):
                        raise ValueError("tool must be async")
                    result[(identifier, name)] = hook
            except Exception:
                run = self.store.db.start_run(f"plugin:{identifier}:{kind}")
                self.store.db.finish_run(run, "failed", "plugin_hook_failed")
        return result

    async def close(self):
        for identifier in list(self.instances):
            await self._disable(identifier)
