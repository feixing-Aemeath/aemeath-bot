"""Read an offline database copy and optionally compare a reminders read snapshot."""

import argparse
import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

from core import helper_process
from core.apple import GONE, READ_ERRORS, REMINDERS_EXCLUDED, ReminderBatch, reminder_changes
from core.models import RuntimeSettings

READ_TIMEOUT = 30
STATUSES = ("pending", "completed", "deleted", "cancelled")


class CheckError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error includes the supplied arguments, possibly private paths.
        raise CheckError("arguments_invalid")


def database_report(path, counts):
    path = path.resolve()
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise CheckError("db_sidecars_present")
    try:
        # immutable avoids SQLite creating WAL/SHM files even for a WAL-mode backup.
        # Only standalone, offline copies are accepted: ignoring a live WAL would lose rows.
        conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        raise CheckError("db_open_failed") from None
    with closing(conn):
        conn.row_factory = sqlite3.Row
        counts["db.user_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
        counts["db.integrity_errors"] = sum(
            row[0] != "ok" for row in conn.execute("PRAGMA integrity_check")
        )
        counts["db.foreign_key_errors"] = sum(1 for _ in conn.execute("PRAGMA foreign_key_check"))
        statuses = dict(conn.execute("SELECT status,COUNT(*) FROM todos GROUP BY status"))
        for status in STATUSES:
            counts[f"db.todos.{status}"] = statuses.get(status, 0)
        counts["db.todos.unknown"] = sum(
            n for status, n in statuses.items() if status not in STATUSES
        )
        for column in ("reminder_id", "calendar_event_id"):
            counts[f"db.{column}s"] = conn.execute(
                f"SELECT COUNT(*) FROM todos WHERE {column} IS NOT NULL"
            ).fetchone()[0]
        counts["db.outbox"] = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        state_exists = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='apple_reminder_state'"
            ).fetchone()
            is not None
        )
        counts["db.apple_reminder_state_exists"] = int(state_exists)
        counts["db.reminder_ids_without_state"] = (
            conn.execute(
                "SELECT COUNT(*) FROM todos t WHERE t.reminder_id IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM apple_reminder_state s "
                "WHERE s.todo_id=t.id AND s.reminder_id=t.reminder_id)"
            ).fetchone()[0]
            if state_exists
            else counts["db.reminder_ids"]
        )
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT id,title,due_at,has_time,status,source_plugin,reminder_id "
                "FROM todos WHERE reminder_id IS NOT NULL"
            )
            if row["status"] in {"pending", "completed"}
            and row["status"] not in GONE
            and row["source_plugin"] not in REMINDERS_EXCLUDED
        ]
        saved = conn.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
        try:
            settings = json.loads(saved[0]) if saved else {}
            timezone = ZoneInfo(settings.get("timezone", RuntimeSettings().timezone))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CheckError("db_settings_invalid") from None
        return rows, timezone


async def read_reminders(binary, rows, timezone):
    try:
        returncode, output = await helper_process.run(
            [str(binary.resolve()), "reminders", "read"],
            json.dumps(
                {
                    "reminder_ids": [row["reminder_id"] for row in rows],
                    "client_ids": [row["id"] for row in rows],
                    "timezone": str(timezone),
                }
            ).encode(),
            READ_TIMEOUT,
            capture_error=True,
        )
    except FileNotFoundError:
        raise CheckError("apple_sync_not_found") from None
    except TimeoutError:
        raise CheckError("apple_reminders_timeout") from None
    except OSError:
        raise CheckError("apple_sync_unavailable") from None
    if returncode:
        code = output.decode("utf-8", errors="replace").strip()
        raise CheckError(code if code in READ_ERRORS else "apple_reminders_read_failed")
    try:
        batch = ReminderBatch.model_validate_json(output)
    except ValueError:
        raise CheckError("apple_reminders_read_failed") from None
    found = {item.id: item for item in batch.items}
    if len(found) != len(batch.items) or set(found) != {row["reminder_id"] for row in rows}:
        raise CheckError("apple_reminders_read_failed")
    if not batch.list_ids:
        raise CheckError("apple_reminders_list_unavailable")
    return batch, found


def apple_report(binary, rows, timezone, counts):
    counts["apple.requested"] = len(rows)
    batch, found = asyncio.run(read_reminders(binary, rows, timezone))
    categories = dict.fromkeys(
        (
            "consistent",
            "status_only",
            "title_or_date_changed",
            "missing",
            "other_list",
            "current_id_changed",
        ),
        0,
    )
    for row in rows:
        item = found[row["reminder_id"]]
        if item.current_id and item.current_id != row["reminder_id"]:
            categories["current_id_changed"] += 1
        if not item.exists:
            category = "missing"
        elif item.list_id not in batch.list_ids:
            category = "other_list"
        else:
            try:
                changes = reminder_changes(row, item, timezone)
            except (ValueError, TypeError):
                raise CheckError("db_todo_date_invalid") from None
            if changes:
                category = "title_or_date_changed"
            elif item.completed != (row["status"] == "completed"):
                category = "status_only"
            else:
                category = "consistent"
        categories[category] += 1
    counts.update(
        {
            "apple.read": len(batch.items),
            "apple.found": sum(item.exists for item in batch.items),
            "apple.lists": len(batch.list_ids),
        }
    )
    counts.update({f"apple.{key}": value for key, value in categories.items()})


def main(argv=None):
    parser = Parser(
        prog="python -m scripts.predeploy_check",
        description="只读检查离线数据库副本，仅输出计数和错误码。",
        allow_abbrev=False,
    )
    parser.add_argument("--db", type=Path, required=True, metavar="COPY")
    parser.add_argument("--apple-sync", type=Path, metavar="HELPER")
    counts, reasons = {}, []
    try:
        args = parser.parse_args(argv)
    except CheckError as error:
        print(f"需要先看一下：{error.args[0]}")
        return 2
    try:
        rows, timezone = database_report(args.db, counts)
        if args.apple_sync is None:
            counts["apple.skipped"] = 1
            reasons.append("apple_check_skipped")
        else:
            apple_report(args.apple_sync, rows, timezone, counts)
    except CheckError as error:
        reasons.append(error.args[0])
    except sqlite3.OperationalError:
        reasons.append("db_schema_invalid")
    except sqlite3.DatabaseError:
        reasons.append("db_read_failed")
    except (OSError, ValueError):
        reasons.append("db_open_failed")
    for key, code in (
        ("db.integrity_errors", "db_integrity_failed"),
        ("db.foreign_key_errors", "db_foreign_key_failed"),
        ("db.todos.unknown", "db_status_invalid"),
        ("db.outbox", "outbox_pending"),
        ("apple.title_or_date_changed", "reminders_user_modified_fields"),
        ("apple.missing", "reminders_missing_manual_decision"),
        ("apple.other_list", "reminders_other_list_sync_held"),
    ):
        if counts.get(key):
            reasons.append(code)
    for key, value in counts.items():
        print(f"{key}={value}")
    print("需要先看一下：" + ",".join(reasons) if reasons else "可以部署")
    return int(bool(reasons))


if __name__ == "__main__":
    raise SystemExit(main())
