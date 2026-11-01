import hashlib
import json
import sqlite3
import unicodedata
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from core.db import Database, dump, now, uid
from core.message_signals import URGENT_FLAGS, redact_self
from core.models import MessageInput, RuntimeSettings, TodoCreate, TodoPatch


class ConflictError(Exception):
    pass


def dedup_key(title, due_at, has_time):
    title = "".join(
        c
        for c in unicodedata.normalize("NFKC", title).casefold()
        if not c.isspace() and not unicodedata.category(c).startswith("P")
    )
    return hashlib.sha256(dump([title, due_at, bool(has_time)]).encode()).hexdigest()


def text_key(text):
    """Text compared when matching a preview with its full message: NFKC, case-folded, no
    spaces, punctuation (the trailing "…" included) or symbols."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", text).casefold()
        if not c.isspace() and unicodedata.category(c)[0] not in "PS"
    )


def preview_key(text, sender):
    """Group previews read "李老师：…"; the sender prefix is not part of the message."""
    text = text.strip()
    for separator in ("：", ":"):
        if sender and text.startswith(sender + separator):
            text = text[len(sender) + 1 :]
            break
    return text_key(text)


PREVIEW_NOTE = "预览，内容可能被截断"
REVISED_NOTE = "之前按预览处理过，现在是完整内容"

# The conversation list shows the minute of the newest message; a full read dates a message
# by the time separator above it, which can be several minutes earlier.
PREVIEW_BEFORE = timedelta(minutes=1)
PREVIEW_AFTER = timedelta(minutes=10)


def preview_matches(preview, text):
    key = preview_key(preview["text"], preview["sender"])
    return len(key) >= 2 and text_key(text).startswith(key)


def stamp(moment):
    return moment.astimezone(UTC).isoformat()


def todo_row(row):
    if row is None:
        raise KeyError("todo_not_found")
    item = dict(row)
    item["source_refs"] = json.loads(item["source_refs"])
    item["has_time"] = bool(item["has_time"])
    return item


def enqueue(conn, todo_id):
    conn.execute(
        "INSERT INTO outbox VALUES(?,1) ON CONFLICT(todo_id) DO UPDATE SET revision=revision+1",
        (todo_id,),
    )
    conn.execute("UPDATE todos SET sync_status='pending',sync_error=NULL WHERE id=?", (todo_id,))


class Store:
    def __init__(self, db: Database):
        self.db = db
        self.on_attention = None

    @property
    def settings(self):
        return RuntimeSettings.model_validate(self.db.get("settings", {}))

    def ingest(self, plugin: str, messages: list[MessageInput], whitelist: list[str]):
        count = 0
        attention = False
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for message in messages:
                if message.chat_name not in whitelist:
                    raise ValueError("chat_not_whitelisted")
                if message.preview:
                    if full := self._full_copy(conn, plugin, message):
                        attention |= self._merge_signals(conn, full["id"], message)
                        continue  # the chat was already read in full
                elif self._replace_preview(conn, plugin, message):
                    count += 1
                    row = conn.execute(
                        "SELECT id FROM messages WHERE plugin=? AND external_id=?",
                        (plugin, message.external_id),
                    ).fetchone()
                    attention |= self._merge_signals(conn, row["id"], message)
                    continue
                identifier = uid()
                count += conn.execute(
                    "INSERT OR IGNORE INTO messages(id,plugin,external_id,chat_name,sender,"
                    "sent_at,text,screenshot_path,priority,preview,outgoing) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        plugin,
                        message.external_id,
                        message.chat_name,
                        message.sender,
                        stamp(message.sent_at),
                        message.text,
                        message.screenshot_path,
                        int(message.priority),
                        int(message.preview),
                        int(message.outgoing and not message.preview),
                    ),
                ).rowcount
                row = conn.execute(
                    "SELECT id FROM messages WHERE plugin=? AND external_id=?",
                    (plugin, message.external_id),
                ).fetchone()
                attention |= self._merge_signals(conn, row["id"], message)
        # Only committed flags can wake extraction. Multiple urgent messages in a batch
        # produce one submission; sticky flags make re-reads harmless, including restarts.
        if attention and self.on_attention:
            self.on_attention()
        return count

    @staticmethod
    def _merge_signals(conn, identifier, message):
        row = conn.execute("SELECT flags FROM messages WHERE id=?", (identifier,)).fetchone()
        old = set(json.loads(row["flags"]))
        flags = old | set(message.flags)
        urgent = bool(flags & URGENT_FLAGS)
        fresh = urgent and not old.intersection(URGENT_FLAGS)
        conn.execute(
            "UPDATE messages SET flags=?,priority=MAX(priority,?),outgoing=MAX(outgoing,?),"
            "processed=CASE WHEN ? THEN 0 ELSE processed END WHERE id=?",
            (
                dump(sorted(flags)),
                2 if urgent else int(message.priority),
                int(message.outgoing and not message.preview),
                fresh,
                identifier,
            ),
        )
        return fresh

    @staticmethod
    def _nearby(conn, plugin, chat, preview, start, end):
        return conn.execute(
            "SELECT id,sender,text,sent_at,processed FROM messages WHERE plugin=? AND "
            "chat_name=? AND preview=? AND sent_at>=? AND sent_at<=? ORDER BY sent_at DESC,id",
            (plugin, chat, int(preview), stamp(start), stamp(end)),
        ).fetchall()

    def _full_copy(self, conn, plugin, message):
        """A preview whose full message is already stored adds nothing."""
        rows = self._nearby(
            conn,
            plugin,
            message.chat_name,
            False,
            message.sent_at - PREVIEW_AFTER,
            message.sent_at + PREVIEW_BEFORE,
        )
        preview = {"sender": message.sender, "text": message.text}
        return next((row for row in rows if preview_matches(preview, row["text"])), None)

    def _replace_preview(self, conn, plugin, message):
        """Turn the matching preview row into this full message, keeping its ID so todos that
        cite it stay valid. A preview the Agent already processed is handed back to it, marked
        revised, so it is judged again on the complete text."""
        exists = conn.execute(
            "SELECT 1 FROM messages WHERE plugin=? AND external_id=?",
            (plugin, message.external_id),
        ).fetchone()
        if exists:
            return False
        rows = self._nearby(
            conn,
            plugin,
            message.chat_name,
            True,
            message.sent_at - PREVIEW_BEFORE,
            message.sent_at + PREVIEW_AFTER,
        )
        for row in rows:
            if preview_matches(row, message.text):
                conn.execute(
                    "UPDATE messages SET external_id=?,sender=?,sent_at=?,text=?,"
                    "screenshot_path=?,priority=MAX(priority,?),preview=0,revised=processed,"
                    "processed=0,outgoing=? WHERE id=?",
                    (
                        message.external_id,
                        message.sender,
                        stamp(message.sent_at),
                        message.text,
                        message.screenshot_path,
                        int(message.priority),
                        int(message.outgoing),
                        row["id"],
                    ),
                )
                return True
        return False

    def unprocessed(self, plugin=None, limit=100):
        if not 1 <= limit <= 200:
            raise ValueError("invalid limit")
        condition, args = (" AND plugin=?", [plugin]) if plugin else ("", [])
        rows = self.db.rows(
            "SELECT id,plugin,chat_name,sender,sent_at,text,priority,preview,revised,flags,outgoing "
            "FROM messages WHERE processed=0"
            + condition
            + " ORDER BY priority DESC,sent_at,id LIMIT ?",
            [*args, limit],
        )
        for row in rows:
            row["flags"] = json.loads(row["flags"])
            row["outgoing"] = bool(row["outgoing"])
            identities = self.local_identities(row["plugin"])
            for field in ("text", "sender", "chat_name"):
                row[field] = redact_self(row[field], identities)
            row["preview"], row["revised"] = bool(row["preview"]), bool(row["revised"])
            row["note"] = (
                PREVIEW_NOTE if row["preview"] else REVISED_NOTE if row["revised"] else None
            )
        return rows

    def local_identities(self, plugin):
        rows = self.db.rows("SELECT config_json FROM plugins WHERE id=?", (plugin,))
        return json.loads(rows[0]["config_json"]).get("self_names", []) if rows else []

    def mark_processed(self, ids):
        if not ids or len(ids) > 200:
            raise ValueError("invalid message batch")
        with self.db.connect() as conn:
            placeholders = ",".join("?" for _ in ids)
            found = conn.execute(
                f"SELECT id FROM messages WHERE id IN ({placeholders})", ids
            ).fetchall()
            if len(found) != len(set(ids)):
                raise KeyError("message_not_found")
            count = conn.execute(
                f"UPDATE messages SET processed=1 WHERE processed=0 AND id IN ({placeholders})", ids
            ).rowcount
        return {"updated": count}

    def todo(self, identifier):
        rows = self.db.rows("SELECT * FROM todos WHERE id=?", (identifier,))
        return self.with_sources([todo_row(rows[0] if rows else None)])[0]

    def with_sources(self, items):
        """Add `sources`: chat, sender and time of each cited message, for the App."""
        refs = list(dict.fromkeys(ref for item in items for ref in item["source_refs"]))
        found = {}
        for start in range(0, len(refs), 500):
            chunk = refs[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            for row in self.db.rows(
                "SELECT id AS message_id,plugin,chat_name,sender,sent_at FROM messages "
                f"WHERE id IN ({placeholders})",
                chunk,
            ):
                found[row["message_id"]] = row
        for item in items:
            item["sources"] = [found[ref] for ref in item["source_refs"] if ref in found]
        return items

    def todos(self, status=None, limit=50, offset=0):
        if (
            status not in {None, "pending", "completed", "deleted"}
            or not 1 <= limit <= 200
            or offset < 0
        ):
            raise ValueError("invalid todo filter")
        where, args = (" WHERE status=?", [status]) if status else (" WHERE status!='deleted'", [])
        rows = self.db.rows(
            "SELECT * FROM todos" + where + " ORDER BY created_at,id LIMIT ? OFFSET ?",
            [*args, limit, offset],
        )
        total = self.db.rows("SELECT count(*) AS n FROM todos" + where, args)[0]["n"]
        return {"items": self.with_sources([todo_row(r) for r in rows]), "total": total}

    def add_todo(self, data: TodoCreate):
        refs = list(dict.fromkeys(data.source_refs))
        placeholders = ",".join("?" for _ in refs)
        sources = self.db.rows(f"SELECT * FROM messages WHERE id IN ({placeholders})", refs)
        if len(sources) != len(refs):
            raise ValueError("source_reference_missing")
        if data.source_plugin not in {s["plugin"] for s in sources}:
            raise ValueError("source_plugin_mismatch")
        if not any(
            data.source_excerpt in s["text"]
            or data.source_excerpt in redact_self(s["text"], self.local_identities(s["plugin"]))
            for s in sources
        ):
            raise ValueError("source_excerpt_mismatch")
        if data.confidence < self.settings.confidence_threshold:
            day = datetime.now(ZoneInfo(self.settings.timezone)).date().isoformat()
            self.digest(
                day,
                data.source_plugin,
                f"低置信度，未建立待办：{data.title}\n{data.source_excerpt}",
                append=True,
            )
            return {"disposition": "below_threshold", "todo": None}
        due = data.due_at.astimezone(UTC).isoformat() if data.due_at else None
        key = dedup_key(data.title, due, data.has_time)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM todos WHERE dedup_key=?", (key,)).fetchone()
            if existing:
                identifier = existing["id"]
                merged = list(dict.fromkeys(json.loads(existing["source_refs"]) + refs))
                plugins = ",".join(
                    sorted(
                        set(existing["source_plugin"].split(",") + [s["plugin"] for s in sources])
                    )
                )
                conn.execute(
                    "UPDATE todos SET source_refs=?,source_plugin=?,updated_at=? WHERE id=?",
                    (dump(merged), plugins, now(), identifier),
                )
                disposition = "merged"
            else:
                identifier, timestamp = uid(), now()
                conn.execute(
                    "INSERT INTO todos(id,title,notes,due_at,has_time,source_plugin,source_refs,"
                    "source_excerpt,confidence,status,dedup_key,created_by,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)",
                    (
                        identifier,
                        data.title.strip(),
                        data.notes,
                        due,
                        int(data.has_time),
                        data.source_plugin,
                        dump(refs),
                        data.source_excerpt,
                        data.confidence,
                        key,
                        data.created_by,
                        timestamp,
                        timestamp,
                    ),
                )
                disposition = "added"
            enqueue(conn, identifier)
        return {"disposition": disposition, "todo": self.todo(identifier)}

    def update_todo(self, identifier, patch: TodoPatch):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = todo_row(
                conn.execute("SELECT * FROM todos WHERE id=?", (identifier,)).fetchone()
            )
            if item["status"] == "deleted" and patch.status != "deleted":
                raise ConflictError("deleted_todo_is_terminal")
            updates = patch.model_dump(exclude_unset=True)
            if "due_at" in updates:
                updates["due_at"] = (
                    updates["due_at"].astimezone(UTC).isoformat() if updates["due_at"] else None
                )
                if updates["due_at"] is None:
                    updates["has_time"] = False
            item.update(updates)
            if item["has_time"] and not item["due_at"]:
                raise ValueError("has_time requires due_at")
            key = dedup_key(item["title"], item["due_at"], item["has_time"])
            try:
                conn.execute(
                    "UPDATE todos SET title=?,notes=?,due_at=?,has_time=?,status=?,"
                    "dedup_key=?,updated_at=? WHERE id=?",
                    (
                        item["title"],
                        item["notes"],
                        item["due_at"],
                        int(item["has_time"]),
                        item["status"],
                        key,
                        now(),
                        identifier,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("duplicate_todo") from exc
            enqueue(conn, identifier)
        return self.todo(identifier)

    def digest(self, day, plugin, summary, append=False):
        from datetime import date

        # Python 3.11+ also parses forms like 20261012; always store YYYY-MM-DD.
        day = date.fromisoformat(day).isoformat()
        if not summary.strip() or len(summary) > 50000 or not plugin or len(plugin) > 80:
            raise ValueError("invalid digest")
        with self.db.connect() as conn:
            old = conn.execute(
                "SELECT summary FROM digests WHERE date=? AND plugin=?", (day, plugin)
            ).fetchone()
            if append and old and summary not in old["summary"]:
                summary = old["summary"] + "\n" + summary
            elif append and old:
                summary = old["summary"]
            conn.execute(
                "INSERT INTO digests VALUES(?,?,?,?,?) ON CONFLICT(date,plugin) DO UPDATE "
                "SET summary=excluded.summary",
                (uid(), day, plugin, summary, now()),
            )
        return self.db.rows("SELECT * FROM digests WHERE date=? AND plugin=?", (day, plugin))[0]

    def digests(self, day=None, plugin=None, limit=50, offset=0):
        clauses, args = [], []
        for column, value in [("date", day), ("plugin", plugin)]:
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.db.rows(
            "SELECT * FROM digests" + where + " ORDER BY date DESC,plugin LIMIT ? OFFSET ?",
            [*args, limit, offset],
        )
        return {
            "items": rows,
            "total": self.db.rows("SELECT count(*) AS n FROM digests" + where, args)[0]["n"],
        }

    def runs(self, limit=50, offset=0):
        return {
            "items": self.db.rows(
                "SELECT * FROM runs ORDER BY started_at DESC,id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ),
            "total": self.db.rows("SELECT count(*) AS n FROM runs")[0]["n"],
        }

    def health_reports(self, day=None, kind=None, status=None, limit=50, offset=0):
        clauses, args = [], []
        for column, value in [("date", day), ("kind", kind), ("status", status)]:
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        columns = "id,date,kind,status,title,body,error,harness,created_at,updated_at"
        rows = self.db.rows(
            f"SELECT {columns} FROM health_reports"
            + where
            + " ORDER BY date DESC,kind LIMIT ? OFFSET ?",
            [*args, limit, offset],
        )
        total = self.db.rows("SELECT count(*) AS n FROM health_reports" + where, args)[0]["n"]
        return {"items": rows, "total": total}

    def health(self, batch):
        inserted = 0
        with self.db.connect() as conn:
            for sample in batch.samples:
                inserted += conn.execute(
                    "INSERT OR IGNORE INTO health_samples VALUES(?,?)",
                    (sample.id, sample.model_dump_json()),
                ).rowcount
        # When the iPhone last uploaded, for the health plugin's watch() (core/watchdog.py).
        self.db.put("health_uploaded_at", now())
        return {"accepted": inserted, "duplicates": len(batch.samples) - inserted}
