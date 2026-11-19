import asyncio
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, StrictBool, model_validator

from core import helper_process
from core.db import Database, dump, now
from core.models import Model, RuntimeSettings, TodoPatch
from core.store import dedup_key, enqueue, stored_dedup_key

CALENDARS = {"AI 待办", "AI 课表"}
# Todos from these sources stay in the App and never reach Reminders or the calendar: the
# watchdog's notes about a broken data source are for the App, not the phone's alarms.
REMINDERS_EXCLUDED = {"watchdog", "goals"}


def is_app_only(source_plugin):
    return bool(REMINDERS_EXCLUDED.intersection(source_plugin.split(",")))


# Statuses with no copy in Reminders or the calendar; readback does not look at them.
GONE = {"deleted", "cancelled"}
NOT_GONE = "NOT IN ('deleted','cancelled')"
# Pending reminders disappearing together are more likely an iCloud re-sync (Apple says the IDs
# can change then) than the user deleting them, so none of them counts as deleted.
MASS_MISSING = 3
READ_ERRORS = {
    "apple_reminders_denied",
    "apple_reminders_list_unavailable",
    "apple_reminders_read_failed",
}


class ReminderSnapshot(Model):
    id: str
    exists: StrictBool
    list_id: str | None
    completed: StrictBool | None
    completed_at: AwareDatetime | None
    title: str | None
    due_at: AwareDatetime | None
    has_time: StrictBool | None
    last_modified_at: AwareDatetime | None
    # Found by its marker under another ID (Apple re-identified it).
    current_id: str | None = None

    @model_validator(mode="after")
    def check_found(self):
        if not self.exists and any(
            getattr(self, key) is not None
            for key in (
                "list_id",
                "completed",
                "completed_at",
                "title",
                "due_at",
                "has_time",
                "last_modified_at",
            )
        ):
            raise ValueError("metadata for missing reminder")
        if self.exists and (
            self.completed is None
            or self.title is None
            or not self.list_id
            or self.has_time is None
        ):
            raise ValueError("incomplete reminder")
        if not self.completed and self.completed_at is not None:
            raise ValueError("invalid completion time")
        if self.has_time and self.due_at is None:
            raise ValueError("invalid reminder date")
        return self


class ReminderBatch(Model):
    list_ids: list[str]
    items: list[ReminderSnapshot]


def reminder_changes(row, item, timezone):
    """Compare deadlines as EventKit stores them: whole seconds, or a local date."""
    changes = {}
    if item.title != row["title"]:
        changes["title"] = item.title
    previous = datetime.fromisoformat(row["due_at"]) if row["due_at"] else None
    due = item.due_at
    same = previous is None and due is None
    if previous and due:
        same = (
            previous.replace(microsecond=0) == due.replace(microsecond=0)
            if item.has_time
            else previous.astimezone(timezone).date() == due.astimezone(timezone).date()
        )
    if not same or bool(row["has_time"]) != item.has_time:
        changes.update(
            due_at=due.astimezone(UTC).isoformat() if due else None, has_time=item.has_time
        )
    return changes


def calendar_name(resource, payload):
    name = payload.get("calendar", "AI 待办")
    if name not in CALENDARS or (resource == "reminders" and name != "AI 待办"):
        raise ValueError("invalid apple calendar")
    return name


def reconcile_agent_fields(conn, row, item, changes, dirty, zone):
    """Keep pending Agent fields only while the phone still has their preflight values."""
    base = json.loads(row["agent_base"]) if row["write_reminders"] else {}
    if base:
        changed_on_phone = reminder_changes({**row, **base}, item, zone)
        # A matching local value also acknowledges a write whose receipt went missing.
        resolved = {key for key in base if key in changed_on_phone or key not in changes}
        if resolved:
            dirty.difference_update(resolved)
            conn.execute(
                "UPDATE outbox SET fields=?,agent_base=? WHERE todo_id=?",
                (
                    dump(sorted(dirty)),
                    dump({k: v for k, v in base.items() if k not in resolved}),
                    row["id"],
                ),
            )


class AppleBridge(Protocol):
    async def call(self, resource: str, action: str, payload: dict) -> dict: ...


class FakeAppleBridge:
    """Durable fake. Stable client IDs model retries after a crash before DB commit."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.reminders_authorized = True
        self.reminder_lists = {"fake-ai-todos"}
        self.other_reminder_lists = set()

    def items(self, resource):
        path = self.directory / f"{resource}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def save(self, resource, items):
        path = self.directory / f"{resource}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(dump(items), encoding="utf-8")
        os.replace(temp, path)

    def change_reminder(self, identifier, **changes):
        """Simulate edits in Reminders without sending an outbox write."""
        items = self.items("reminders")
        item = next(item for item in items.values() if item["id"] == identifier)
        item.update(changes, last_modified_at=now())
        if "completed" in changes:
            item["completed_at"] = now() if changes["completed"] else None
        self.save("reminders", items)

    def reidentify_reminder(self, identifier, new):
        """Simulate iCloud giving a reminder another ID; its marker stays."""
        items = self.items("reminders")
        next(item for item in items.values() if item["id"] == identifier)["id"] = new
        self.save("reminders", items)

    def delete_reminder(self, identifier):
        self.save(
            "reminders",
            {k: v for k, v in self.items("reminders").items() if v["id"] != identifier},
        )

    async def call(self, resource, action, payload):
        if resource == "reminders" and action == "read":
            calendar_name(resource, payload)
            if not self.reminders_authorized:
                raise RuntimeError("apple_reminders_denied")
            if not self.reminder_lists:
                raise RuntimeError("apple_reminders_list_unavailable")
            found = {
                v["id"]: v
                for v in self.items(resource).values()
                if v.get("list_id", "fake-ai-todos")
                in self.reminder_lists | self.other_reminder_lists
            }
            markers = dict(
                zip(payload["reminder_ids"], payload.get("client_ids", []), strict=False)
            )
            stored = self.items(resource)
            items = []
            for identifier in payload["reminder_ids"]:
                item = found.get(identifier)
                current = None
                if item is None and (moved := stored.get(markers.get(identifier))):
                    item, current = moved, moved["id"]
                items.append(
                    {
                        "id": identifier,
                        "current_id": current,
                        "exists": item is not None,
                        "list_id": item.get("list_id", "fake-ai-todos") if item else None,
                        "completed": item.get("completed", False) if item else None,
                        "completed_at": item.get("completed_at") if item else None,
                        "title": item.get("title") if item else None,
                        "due_at": item.get("due_at") if item else None,
                        "has_time": item.get("has_time", False) if item else None,
                        "last_modified_at": item.get("last_modified_at") if item else None,
                    }
                )
            return {"list_ids": sorted(self.reminder_lists), "items": items}
        if resource not in {"reminders", "calendar"} or action not in {"upsert", "delete"}:
            raise ValueError("invalid apple operation")
        name = calendar_name(resource, payload)
        items = self.items(resource)
        if resource == "reminders" and action == "upsert" and payload.get("require_existing"):
            current = next((item for item in items.values() if item["id"] == payload["id"]), None)
            if (
                current is None
                or current.get("list_id", "fake-ai-todos") not in self.reminder_lists
                or (
                    payload.get("expected_modified_at")
                    and (
                        not current.get("last_modified_at")
                        or datetime.fromisoformat(current["last_modified_at"])
                        != datetime.fromisoformat(payload["expected_modified_at"])
                    )
                )
            ):
                raise RuntimeError("apple_helper_failed")
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
            if resource == "reminders":
                items[key].update(
                    list_id="fake-ai-todos",
                    last_modified_at=now(),
                    completed_at=(previous.get("completed_at") or now())
                    if payload.get("completed")
                    else None,
                )
            if "alarms" not in payload and "alarms" in previous:
                items[key]["alarms"] = previous["alarms"]
        self.save(resource, items)
        result = {"id": identifier}
        if resource == "reminders" and action == "upsert":
            result["list_id"] = "fake-ai-todos"
            result["last_modified_at"] = items[key]["last_modified_at"]
        return result


class SwiftAppleBridge:
    def __init__(self, binary: Path, timeout=30):
        self.binary, self.timeout = binary, timeout

    async def call(self, resource, action, payload):
        calendar_name(resource, payload)
        if sys.platform != "darwin":
            raise RuntimeError("macos_required")
        returncode, output = await helper_process.run(
            [str(self.binary), resource, action],
            dump(payload).encode(),
            self.timeout,
            capture_error=True,
        )
        if returncode:
            if resource == "reminders" and action == "read":
                code = output.decode("utf-8", errors="replace").strip()
                if code in READ_ERRORS:
                    raise RuntimeError(code)
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
            for row in conn.execute(f"SELECT id FROM todos WHERE status {NOT_GONE}").fetchall():
                enqueue(conn, row["id"], fields=[])
            conn.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, dump(policy)))
        return settings

    def queue_app_only(self):
        """Take back what reached Reminders before its source became App-only."""
        with self.db.connect() as conn:
            for row in conn.execute(
                "SELECT id,source_plugin FROM todos WHERE "
                "(reminder_id IS NOT NULL OR calendar_event_id IS NOT NULL) "
                "AND id NOT IN (SELECT todo_id FROM outbox)",
            ).fetchall():
                if is_app_only(row["source_plugin"]):
                    enqueue(conn, row["id"])

    async def reconcile(self, moment=None, *, todo_ids=None):
        """Read one batch; a targeted read also protects immediate outbox writes."""
        async with self.lock:
            return await self._reconcile(moment, todo_ids=todo_ids)

    async def _reconcile(self, moment=None, *, todo_ids=None):
        if getattr(self.bridge, "error", None):
            return {"checked": 0, "changed": 0, "failed": 0, "mass_missing": 0}
        scoped = todo_ids is not None
        args = (dump(list(todo_ids)),) if scoped else ()
        rows = self.db.rows(
            "SELECT t.*,o.revision,o.reminders AS write_reminders,o.fields AS dirty_fields,"
            "o.agent_base,s.list_id AS known_list FROM todos t "
            "LEFT JOIN outbox o ON o.todo_id=t.id "
            "LEFT JOIN apple_reminder_state s ON s.todo_id=t.id AND s.reminder_id=t.reminder_id "
            f"WHERE t.reminder_id IS NOT NULL AND t.status {NOT_GONE}"
            + (" AND t.id IN (SELECT value FROM json_each(?))" if scoped else ""),
            args,
        )
        rows = [row for row in rows if not is_app_only(row["source_plugin"])]
        if not rows:
            return {"checked": 0, "changed": 0, "failed": 0, "mass_missing": 0}
        settings = RuntimeSettings.model_validate(self.db.get("settings", {}))
        try:
            batch = ReminderBatch.model_validate(
                await self.bridge.call(
                    "reminders",
                    "read",
                    {
                        "reminder_ids": [row["reminder_id"] for row in rows],
                        "client_ids": [row["id"] for row in rows],
                        "timezone": settings.timezone,
                    },
                )
            )
            found = {item.id: item for item in batch.items}
            if (
                not batch.list_ids
                or len(found) != len(batch.items)
                or set(found) != {row["reminder_id"] for row in rows}
            ):
                raise ValueError("invalid reminder batch")
        except (Exception, asyncio.CancelledError) as exc:
            # A failed read breaks the streak, but keeps writes held until a good read.
            self.db.execute(
                "UPDATE apple_reminder_state SET missing_reads=0 "
                "WHERE todo_id IN (SELECT value FROM json_each(?))",
                (dump([row["id"] for row in rows]),),
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError("apple_sync_failed") from exc
        moment = moment or datetime.now(UTC)
        changed = 0
        failures = 0
        failed_ids = set()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = [row for row in rows if row["status"] == "pending"]
            gone = sum(
                not found[row["reminder_id"]].exists and row["known_list"] in batch.list_ids
                for row in pending
            )
            mass_missing = (
                gone
                if todo_ids is None and gone >= MASS_MISSING and gone * 2 >= len(pending)
                else 0
            )
            for row in rows:
                current = conn.execute(
                    "SELECT t.*,o.revision,o.reminders AS write_reminders,o.fields AS dirty_fields FROM todos t "
                    "LEFT JOIN outbox o ON o.todo_id=t.id WHERE t.id=?",
                    (row["id"],),
                ).fetchone()
                # A PATCH/Agent edit while the process was running wins over this snapshot.
                if current is None or any(
                    current[k] != row[k]
                    for k in (
                        "updated_at",
                        "reminder_id",
                        "status",
                        "revision",
                        "write_reminders",
                    )
                ):
                    continue
                item = found[row["reminder_id"]]
                if item.current_id and item.current_id != row["reminder_id"]:
                    conn.execute(
                        "UPDATE todos SET reminder_id=? WHERE id=?", (item.current_id, row["id"])
                    )
                    conn.execute(
                        "UPDATE apple_reminder_state SET reminder_id=? WHERE todo_id=?",
                        (item.current_id, row["id"]),
                    )
                    row = {**row, "reminder_id": item.current_id}
                fields = {}
                state = conn.execute(
                    "SELECT * FROM apple_reminder_state WHERE todo_id=?", (row["id"],)
                ).fetchone()
                if state and state["reminder_id"] != row["reminder_id"]:
                    conn.execute("DELETE FROM apple_reminder_state WHERE todo_id=?", (row["id"],))
                    state = None
                # Items moved out of the dedicated lists stay owned by the user. Hold
                # writes until they return instead of deleting their local/calendar copy.
                if item.exists and item.list_id not in batch.list_ids:
                    conn.execute(
                        "INSERT INTO apple_reminder_state(todo_id,reminder_id,list_id,missing_since) "
                        "VALUES(?,?,?,?) ON CONFLICT(todo_id) DO UPDATE SET "
                        "list_id=excluded.list_id,missing_reads=0,missing_since=excluded.missing_since",
                        (row["id"], row["reminder_id"], item.list_id, moment.isoformat()),
                    )
                    continue
                if not item.exists:
                    visible = state and state["list_id"] in batch.list_ids
                    count = state["missing_reads"] if state else 0
                    since = state["missing_since"] if state and count else moment.isoformat()
                    # Only a later full poll confirms: not retries of another failed write in
                    # the same job, not a read for one Agent edit, not a mass disappearance.
                    if visible and (
                        not count
                        or (
                            todo_ids is None
                            and not mass_missing
                            and (moment - datetime.fromisoformat(since)).total_seconds() >= 300
                        )
                    ):
                        count += 1
                    if not visible:
                        count = 0
                    conn.execute(
                        "INSERT INTO apple_reminder_state(todo_id,reminder_id,list_id,missing_reads,missing_since) "
                        "VALUES(?,?,?,?,?) ON CONFLICT(todo_id) DO UPDATE SET "
                        "reminder_id=excluded.reminder_id,list_id=excluded.list_id,"
                        "missing_reads=excluded.missing_reads,missing_since=excluded.missing_since",
                        (
                            row["id"],
                            row["reminder_id"],
                            state["list_id"] if state else None,
                            count,
                            since,
                        ),
                    )
                    if count < 2:
                        continue
                    restoring = row["write_reminders"] and "restore_missing" in json.loads(
                        row["dirty_fields"]
                    )
                    if row["status"] == "completed" or restoring:
                        # A cleared completion keeps its calendar. An explicit restore
                        # retries creation by client_id once the old copy is confirmed gone.
                        conn.execute("UPDATE todos SET reminder_id=NULL WHERE id=?", (row["id"],))
                        conn.execute(
                            "DELETE FROM apple_reminder_state WHERE todo_id=?", (row["id"],)
                        )
                        changed += 1
                        continue
                    status, completed_at = "deleted", None
                else:
                    modified = item.last_modified_at.isoformat() if item.last_modified_at else None
                    stale = (
                        state
                        and state["last_modified_at"]
                        and item.last_modified_at
                        and item.last_modified_at
                        < datetime.fromisoformat(state["last_modified_at"])
                    )
                    conn.execute(
                        "INSERT INTO apple_reminder_state(todo_id,reminder_id,list_id,last_modified_at) "
                        "VALUES(?,?,?,?) ON CONFLICT(todo_id) DO UPDATE SET "
                        "reminder_id=excluded.reminder_id,list_id=excluded.list_id,"
                        "missing_reads=0,missing_since=NULL,last_modified_at=excluded.last_modified_at",
                        (
                            row["id"],
                            row["reminder_id"],
                            item.list_id,
                            state["last_modified_at"] if stale else modified,
                        ),
                    )
                    if stale:
                        continue
                    dirty = (
                        set(json.loads(row["dirty_fields"])) if row["write_reminders"] else set()
                    )
                    status = (
                        row["status"]
                        if "status" in dirty
                        else ("completed" if item.completed else "pending")
                    )
                    try:
                        zone = ZoneInfo(settings.timezone)
                        fields = reminder_changes(row, item, zone)
                        reconcile_agent_fields(conn, row, item, fields, dirty, zone)
                        if dirty & {"due_at", "has_time"}:
                            fields.pop("due_at", None)
                            fields.pop("has_time", None)
                        fields = {key: value for key, value in fields.items() if key not in dirty}
                        if fields:
                            TodoPatch.model_validate(fields)
                    except ValueError:
                        fields = {}
                        failures += 1
                        failed_ids.add(row["id"])
                    if status == row["status"] and not fields:
                        continue
                    completed_at = (
                        item.completed_at.astimezone(UTC).isoformat() if item.completed_at else None
                    )
                reclaim = status == "pending" and row["dedup_key"].startswith("cancelled:")
                if fields or reclaim:
                    values = {**row, **fields, "status": status}
                    locked = sorted(set(json.loads(row["user_modified_fields"])) | set(fields))
                    key = dedup_key(values["title"], values["due_at"], values["has_time"])
                    try:
                        conn.execute(
                            "UPDATE todos SET title=?,due_at=?,has_time=?,dedup_key=?,"
                            "user_modified_fields=?,updated_at=? WHERE id=?",
                            (
                                values["title"],
                                values["due_at"],
                                int(values["has_time"]),
                                stored_dedup_key(values, key),
                                dump(locked),
                                moment.isoformat(),
                                row["id"],
                            ),
                        )
                        conn.executemany(
                            "INSERT OR IGNORE INTO todo_dedup_keys VALUES(?,?)",
                            [
                                (
                                    dedup_key(row["title"], row["due_at"], row["has_time"]),
                                    row["id"],
                                ),
                                (key, row["id"]),
                            ],
                        )
                    except sqlite3.IntegrityError:
                        # A conflicting title/date must not block completion or other rows.
                        failures += 1
                        failed_ids.add(row["id"])
                        if status == row["status"] or reclaim:
                            continue
                if status != row["status"]:
                    conn.execute(
                        "UPDATE todos SET status=?,status_source='reminders',reminder_completed_at=?,"
                        "updated_at=? WHERE id=?",
                        (status, completed_at, moment.isoformat(), row["id"]),
                    )
                if status == "deleted":
                    conn.execute("UPDATE todos SET reminder_id=NULL WHERE id=?", (row["id"],))
                # Calendar changes remain durable, without writing the reminder back.
                enqueue(
                    conn,
                    row["id"],
                    reminders=bool(row["write_reminders"]) and status != "deleted",
                    fields=[],
                )
                changed += 1
        result = {
            "checked": len(rows),
            "changed": changed,
            "failed": failures,
            "mass_missing": mass_missing,
        }
        if failed_ids:
            result["failed_ids"] = sorted(failed_ids)
        return result

    async def flush(self, *, readback=True, read_failed=False, failed_ids=()):
        async with self.lock:
            return await self._flush(
                readback=readback, read_failed=read_failed, failed_ids=failed_ids
            )

    async def _flush(self, *, readback=True, read_failed=False, failed_ids=()):
        failed_ids = set(failed_ids)
        settings = self.queue_alarm_changes()
        self.queue_app_only()
        protected = {
            row["todo_id"]
            for row in self.db.rows(
                "SELECT o.todo_id FROM outbox o JOIN todos t ON t.id=o.todo_id "
                f"WHERE o.reminders=1 AND t.reminder_id IS NOT NULL AND t.status {NOT_GONE}"
            )
        }
        held = {
            row["todo_id"]
            for row in self.db.rows(
                "SELECT s.todo_id FROM apple_reminder_state s JOIN todos t ON t.id=s.todo_id "
                "WHERE s.reminder_id=t.reminder_id AND s.missing_since IS NOT NULL"
            )
        }
        if readback and (read_ids := protected - held):
            try:
                read = await self._reconcile(todo_ids=read_ids)
                failed_ids.update(read.get("failed_ids", []))
            except RuntimeError:
                read_failed = True
        entries = self.db.rows(
            "SELECT o.* FROM outbox o WHERE NOT EXISTS (SELECT 1 FROM apple_reminder_state s "
            "JOIN todos t ON t.id=s.todo_id WHERE s.todo_id=o.todo_id "
            "AND s.reminder_id=t.reminder_id AND s.missing_since IS NOT NULL "
            f"AND t.status {NOT_GONE})"
        )
        failures = 0
        for entry in entries:
            row = self.db.rows("SELECT * FROM todos WHERE id=?", (entry["todo_id"],))[0]
            app_only = is_app_only(row["source_plugin"])
            if (
                not app_only
                and entry["reminders"]
                and (
                    (read_failed and entry["todo_id"] in protected)
                    or entry["todo_id"] in failed_ids
                )
            ):
                failures += 1
                self.db.execute(
                    "UPDATE todos SET sync_error='apple_sync_failed' WHERE id=?", (row["id"],)
                )
                continue
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
                deleted = row["status"] in GONE or app_only
                if entry["reminders"]:
                    reminder = {"id": None}
                    # A todo finished with its reminder already cleared away needs no new one.
                    if row["reminder_id"] is not None or not (
                        app_only or row["status"] == "completed"
                    ):
                        state = self.db.rows(
                            "SELECT last_modified_at FROM apple_reminder_state "
                            "WHERE todo_id=? AND reminder_id=?",
                            (row["id"], row["reminder_id"]),
                        )
                        reminder = await self.bridge.call(
                            "reminders",
                            "delete" if deleted else "upsert",
                            {
                                **payload,
                                "id": row["reminder_id"],
                                "require_existing": row["reminder_id"] is not None and not deleted,
                                "expected_modified_at": state[0]["last_modified_at"]
                                if state
                                else None,
                            },
                        )
                    # A calendar retry must not overwrite a subsequent edit on the phone.
                    with self.db.connect() as conn:
                        conn.execute(
                            "UPDATE todos SET reminder_id=? WHERE id=?",
                            (reminder["id"], row["id"]),
                        )
                        conn.execute(
                            "UPDATE outbox SET reminders=0,fields='[]',agent_base='{}' "
                            "WHERE todo_id=? AND revision=?",
                            (row["id"], entry["revision"]),
                        )
                        if reminder.get("list_id"):
                            conn.execute(
                                "INSERT INTO apple_reminder_state(todo_id,reminder_id,list_id,last_modified_at) "
                                "VALUES(?,?,?,?) ON CONFLICT(todo_id) DO UPDATE SET "
                                "reminder_id=excluded.reminder_id,list_id=excluded.list_id,"
                                "missing_reads=0,missing_since=NULL,last_modified_at=excluded.last_modified_at",
                                (
                                    row["id"],
                                    reminder["id"],
                                    reminder["list_id"],
                                    reminder.get("last_modified_at"),
                                ),
                            )
                # The reminder rings; its copy in the calendar stays silent, or every
                # timed todo would ring twice on the iPhone and the watch.
                calendar = {"id": None}
                if not (app_only and row["calendar_event_id"] is None):
                    calendar = await self.bridge.call(
                        "calendar",
                        "upsert" if not deleted and row["has_time"] and row["due_at"] else "delete",
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
