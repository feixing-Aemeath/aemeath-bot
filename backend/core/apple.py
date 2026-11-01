import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Protocol

from core import helper_process
from core.db import Database, dump
from core.models import RuntimeSettings
from core.store import enqueue

CALENDARS = {"AI 待办", "AI 课表"}
# Todos from these sources stay in the App and never reach Reminders or the calendar: the
# watchdog's notes about a broken data source are for the App, not the phone's alarms.
APP_ONLY = {"watchdog"}


def calendar_name(resource, payload):
    name = payload.get("calendar", "AI 待办")
    if name not in CALENDARS or (resource == "reminders" and name != "AI 待办"):
        raise ValueError("invalid apple calendar")
    return name


class AppleBridge(Protocol):
    async def call(self, resource: str, action: str, payload: dict) -> dict: ...


class FakeAppleBridge:
    """Durable fake. Stable client IDs model retries after a crash before DB commit."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    async def call(self, resource, action, payload):
        if resource not in {"reminders", "calendar"} or action not in {"upsert", "delete"}:
            raise ValueError("invalid apple operation")
        name = calendar_name(resource, payload)
        path = self.directory / f"{resource}.json"
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        key = payload["client_id"]
        if name != "AI 待办":
            key = f"{name}:{key}"
        if action == "delete":
            items.pop(key, None)
            identifier = None
        else:
            identifier = f"fake-{resource}-{key}"
            previous = items.get(key, {})
            items[key] = {**payload, "id": identifier, "list": name}
            if "alarms" not in payload and "alarms" in previous:
                items[key]["alarms"] = previous["alarms"]
        temp = path.with_suffix(".tmp")
        temp.write_text(dump(items), encoding="utf-8")
        os.replace(temp, path)
        return {"id": identifier}


class SwiftAppleBridge:
    def __init__(self, binary: Path, timeout=30):
        self.binary, self.timeout = binary, timeout

    async def call(self, resource, action, payload):
        calendar_name(resource, payload)
        if sys.platform != "darwin":
            raise RuntimeError("macos_required")
        returncode, output = await helper_process.run(
            [str(self.binary), resource, action], dump(payload).encode(), self.timeout
        )
        if returncode:
            raise RuntimeError("apple_helper_failed")
        result = json.loads(output)
        if action == "upsert" and not result.get("id"):
            raise RuntimeError("apple_missing_identifier")
        return result


class MacRequiredBridge:
    """Keep the sync queue and IDs intact for a future Mac bridge; never simulate success."""

    error = "macos_required"

    async def call(self, resource, action, payload):
        raise RuntimeError(self.error)


class AppleSync:
    def __init__(self, db: Database, bridge: AppleBridge):
        self.db, self.bridge = db, bridge
        self.lock = asyncio.Lock()

    async def call(self, resource, action, payload):
        """Plugins share the bridge and its lock with the todo outbox."""
        async with self.lock:
            return await self.bridge.call(resource, action, payload)

    def queue_alarm_changes(self):
        settings = RuntimeSettings.model_validate(self.db.get("settings", {}))
        policy = settings.model_dump(
            include={"timezone", "todo_alarm_minutes", "todo_date_alarm_minutes"}
        )
        key = "apple:alarm_settings:v1"
        with self.db.connect() as conn:
            previous = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            if previous and json.loads(previous["value"]) == policy:
                return settings
            # Also covers the first upgrade: already-synced todos acquire alarms once.
            for row in conn.execute("SELECT id FROM todos WHERE status!='deleted'").fetchall():
                enqueue(conn, row["id"])
            conn.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, dump(policy)))
        return settings

    def queue_app_only(self):
        """Take back what reached Reminders before its source became App-only."""
        marks = ",".join("?" for _ in APP_ONLY)
        with self.db.connect() as conn:
            for row in conn.execute(
                f"SELECT id FROM todos WHERE source_plugin IN ({marks}) "
                "AND (reminder_id IS NOT NULL OR calendar_event_id IS NOT NULL) "
                "AND id NOT IN (SELECT todo_id FROM outbox)",
                tuple(APP_ONLY),
            ).fetchall():
                enqueue(conn, row["id"])

    async def flush(self):
        async with self.lock:
            settings = self.queue_alarm_changes()
            self.queue_app_only()
            entries = self.db.rows("SELECT todo_id,revision FROM outbox")
            failures = 0
            for entry in entries:
                row = self.db.rows("SELECT * FROM todos WHERE id=?", (entry["todo_id"],))[0]
                app_only = row["source_plugin"] in APP_ONLY
                payload = {
                    "client_id": row["id"],
                    "title": row["title"],
                    "notes": row["notes"]
                    + "\n来源："
                    + row["source_plugin"]
                    + "\n"
                    + row["source_excerpt"]
                    + "\n消息 ID："
                    + row["source_refs"],
                    "due_at": row["due_at"],
                    "has_time": bool(row["has_time"]),
                    "completed": row["status"] == "completed",
                    "timezone": settings.timezone,
                    "alarms": (
                        settings.todo_alarm_minutes
                        if row["has_time"]
                        else [-settings.todo_date_alarm_minutes]
                    )
                    if row["status"] == "pending" and row["due_at"]
                    else [],
                }
                try:
                    deleted = row["status"] == "deleted" or app_only
                    reminder = {"id": None}
                    if not (app_only and row["reminder_id"] is None):
                        reminder = await self.bridge.call(
                            "reminders",
                            "delete" if deleted else "upsert",
                            {**payload, "id": row["reminder_id"]},
                        )
                    # Persist each identifier immediately, even if the next operation fails.
                    self.db.execute(
                        "UPDATE todos SET reminder_id=? WHERE id=?", (reminder["id"], row["id"])
                    )
                    # The reminder rings; its copy in the calendar stays silent, or every
                    # timed todo would ring twice on the iPhone and the watch.
                    calendar = {"id": None}
                    if not (app_only and row["calendar_event_id"] is None):
                        calendar = await self.bridge.call(
                            "calendar",
                            "upsert"
                            if not deleted and row["has_time"] and row["due_at"]
                            else "delete",
                            {**payload, "id": row["calendar_event_id"], "alarms": []},
                        )
                    with self.db.connect() as conn:
                        conn.execute(
                            "UPDATE todos SET calendar_event_id=? WHERE id=?",
                            (calendar["id"], row["id"]),
                        )
                        removed = conn.execute(
                            "DELETE FROM outbox WHERE todo_id=? AND revision=?",
                            (row["id"], entry["revision"]),
                        ).rowcount
                        if removed:
                            conn.execute(
                                "UPDATE todos SET sync_status='synced',sync_error=NULL WHERE id=?",
                                (row["id"],),
                            )
                except Exception:
                    failures += 1
                    self.db.execute(
                        "UPDATE todos SET sync_error=? WHERE id=?",
                        (getattr(self.bridge, "error", "apple_sync_failed"), row["id"]),
                    )
            return {"attempted": len(entries), "failed": failures}
