import hashlib
import json
import sqlite3
import unicodedata
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from core.db import Database, dump, now, uid
from core.digests import merge as merge_digest
from core.history import page
from core.message_signals import SELF_MARK, URGENT_FLAGS, redact_self
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


def stored_dedup_key(row, key):
    if row["status"] == "cancelled" or (
        row["status"] != "pending" and row["dedup_key"].startswith("cancelled:")
    ):
        return f"cancelled:{row['id']}"
    return key


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

# Both Agent message tools expose the same fields, including optional share metadata.
AGENT_MESSAGES = (
    "SELECT m.id,m.plugin,m.chat_name,m.sender,m.sent_at,m.text,m.priority,m.preview,"
    "m.revised,m.flags,m.outgoing,i.metadata_json FROM messages m "
    "LEFT JOIN inbox_submissions i ON i.message_id=m.id "
)


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
    item["user_modified_fields"] = json.loads(item["user_modified_fields"])
    return item


def enqueue(conn, todo_id, *, reminders=True, fields=None, agent_base=None):
    fields = set(fields if fields is not None else ("title", "due_at", "has_time", "status"))
    base = {}
    if reminders:
        previous = conn.execute(
            "SELECT fields,agent_base FROM outbox WHERE todo_id=? AND reminders=1", (todo_id,)
        ).fetchone()
        previous_fields = set(json.loads(previous["fields"])) if previous else set()
        base = json.loads(previous["agent_base"]) if previous else {}
        for group in ({"title"}, {"due_at", "has_time"}):
            if fields & group:
                if agent_base is None:
                    for field in group:
                        base.pop(field, None)
                elif not previous_fields & group:
                    base.update({field: agent_base[field] for field in group})
        fields.update(previous_fields)
    conn.execute(
        "INSERT INTO outbox(todo_id,revision,reminders,fields,agent_base) VALUES(?,1,?,?,?) "
        "ON CONFLICT(todo_id) DO UPDATE SET revision=revision+1,"
        "reminders=excluded.reminders,fields=excluded.fields,agent_base=excluded.agent_base",
        (todo_id, int(reminders), dump(sorted(fields) if reminders else []), dump(base)),
    )
    conn.execute("UPDATE todos SET sync_status='pending',sync_error=NULL WHERE id=?", (todo_id,))


class Store:
    def __init__(self, db: Database):
        self.db = db
        self.on_attention = None

    @property
    def settings(self):
        return RuntimeSettings.model_validate(self.db.get("settings", {}))

    @staticmethod
    def _bot_preferences(conn):
        row = conn.execute("SELECT value FROM kv WHERE key='bot_preferences'").fetchone()
        return json.loads(row["value"]) if row else {"version": 0, "entries": {}}

    def bot_preferences(self):
        with self.db.connect() as conn:
            return self._bot_preferences(conn)

    def update_bot_preferences(self, update):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._bot_preferences(conn)
            if current["version"] != update.expected_version:
                return False, current
            saved = {
                "version": current["version"] + 1,
                "entries": update.model_dump(mode="json")["entries"],
            }
            conn.execute("INSERT OR REPLACE INTO kv VALUES('bot_preferences',?)", (dump(saved),))
            return True, saved

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
            AGENT_MESSAGES
            + "WHERE processed=0"
            + condition
            + " ORDER BY priority DESC,sent_at,id LIMIT ?",
            [*args, limit],
        )
        return self._agent_messages(rows, self._identities())

    def messages_search(self, query, chat_name=None, days=14, limit=20):
        """Matches what an Agent may see, so it cannot test guesses at the name behind [本人]."""
        keywords = query.split()
        if not keywords or not 1 <= days <= 90 or not 1 <= limit <= 200:
            raise ValueError("invalid message search")
        current = datetime.now(UTC)
        identities = self._identities()
        keywords = [word.casefold() for word in keywords]
        conditions = ["m.sent_at>=?", "m.sent_at<=?"]
        args = [stamp(current - timedelta(days=days)), stamp(current)]
        # Ordinary Chinese words survive case folding and cannot be introduced by masking.
        # Let SQLite reject those misses without running the redactor on each message.
        anchor = next(
            (
                word
                for word in keywords
                if all("一" <= c <= "鿿" and c not in SELF_MARK for c in word)
            ),
            None,
        )
        if anchor:
            conditions.append("instr(m.text,?)>0")
            args.append(anchor)

        def visible(plugin, value):
            return redact_self(value, identities.get(plugin, ()))

        def matches(plugin, value):
            folded = visible(plugin, value).casefold()
            return all(word in folded for word in keywords)

        with self.db.connect() as conn:
            if chat_name is not None:
                conn.create_function("visible_chat", 2, visible)
                conditions.append("visible_chat(m.plugin,m.chat_name)=?")
                args.append(chat_name)
            conn.create_function("matches_text", 2, matches)
            conditions.append("matches_text(m.plugin,m.text)")
            rows = [
                dict(row)
                for row in conn.execute(
                    AGENT_MESSAGES
                    + "WHERE "
                    + " AND ".join(conditions)
                    + " ORDER BY m.sent_at DESC,m.id LIMIT ?",
                    [*args, limit],
                )
            ]
        return self._agent_messages(rows, identities)

    def _identities(self):
        return {
            row["id"]: json.loads(row["config_json"]).get("self_names", [])
            for row in self.db.rows("SELECT id,config_json FROM plugins")
        }

    @staticmethod
    def _agent_messages(rows, by_plugin):
        for row in rows:
            row["flags"] = json.loads(row["flags"])
            row["outgoing"] = bool(row["outgoing"])
            identities = by_plugin.get(row["plugin"], ())
            for field in ("text", "sender", "chat_name"):
                row[field] = redact_self(row[field], identities)
            row["preview"], row["revised"] = bool(row["preview"]), bool(row["revised"])
            row["note"] = (
                PREVIEW_NOTE if row["preview"] else REVISED_NOTE if row["revised"] else None
            )
            metadata = row.pop("metadata_json")
            if row["plugin"] == "share" and metadata:
                metadata = json.loads(metadata)
                row["note"] = metadata.get("note")
                row["source_app"] = metadata.get("source_app")
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

    def todo_source_texts(self, identifier):
        todo = self.todo(identifier)
        found = {}
        refs = todo["source_refs"]
        for start in range(0, len(refs), 500):
            chunk = refs[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            for row in self.db.rows(
                "SELECT id AS message_id,plugin,chat_name,sender,sent_at,substr(text,1,4000) "
                f"AS text FROM messages WHERE id IN ({marks})",
                chunk,
            ):
                found[row["message_id"]] = row
        return {"items": [found[ref] for ref in refs if ref in found]}

    @staticmethod
    def _missed(conn, kind, reference, timestamp, source=None):
        conn.execute(
            "INSERT OR IGNORE INTO extraction_missed VALUES(?,?,?,?)",
            (kind, reference, timestamp, dump(source) if source else None),
        )

    def inbox(self, data):
        key = hashlib.sha256(data.text.encode()).hexdigest()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # The time is read under the write lock. Read before it, two retries of one share
            # could each hold a time earlier than the other's row and both be saved.
            timestamp = now()
            cutoff = stamp(datetime.fromisoformat(timestamp) - timedelta(minutes=10))
            previous = conn.execute(
                "SELECT message_id FROM inbox_submissions WHERE text_hash=? "
                "AND created_at>=? AND created_at<=? ORDER BY created_at DESC LIMIT 1",
                (key, cutoff, timestamp),
            ).fetchone()
            if previous:
                return previous["message_id"]
            identifier = uid()
            conn.execute(
                "INSERT INTO messages(id,plugin,external_id,chat_name,sender,sent_at,text,priority) "
                "VALUES(?,'share',?,?,'我',?,?,1)",
                (identifier, identifier, data.chat_name or "转给助手", timestamp, data.text),
            )
            conn.execute(
                "INSERT INTO inbox_submissions VALUES(?,?,?,?)",
                (identifier, key, timestamp, dump(data.model_dump(exclude={"text"}))),
            )
            if data.missed:
                self._missed(conn, "share", identifier, timestamp)
        return identifier

    def new_todo(self, data):
        timestamp, message_id = now(), uid()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Local, already-processed provenance keeps the same Todo schema and the same
            # dedup/sync pipeline as Agent writes; it never re-enters extraction.
            conn.execute(
                "INSERT INTO messages(id,plugin,external_id,chat_name,sender,sent_at,text,processed) "
                "VALUES(?,'app',?,'手动新建','我',?,?,1)",
                (message_id, message_id, timestamp, data.title),
            )
            candidate = TodoCreate(
                **data.model_dump(exclude={"missed", "missed_source"}),
                created_by="user",
                source_plugin="app",
                source_refs=[message_id],
                source_excerpt=data.title,
                confidence=1,
            )
            disposition, identifier = self._save_todo(
                conn, candidate, [message_id], [], manual=True
            )
            if disposition == "added":
                conn.execute(
                    "UPDATE todos SET user_modified_fields=? WHERE id=?",
                    (dump(["title", "due_at", "has_time"]), identifier),
                )
            else:
                conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
            # One the assistant already has was not missed, whatever the switch says.
            if data.missed and disposition == "added":
                self._missed(
                    conn,
                    "todo",
                    identifier,
                    timestamp,
                    data.missed_source.model_dump() if data.missed_source else None,
                )
        return {"disposition": disposition, "todo": self.todo(identifier)}

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
            status not in {None, "pending", "completed", "deleted", "cancelled"}
            or not 1 <= limit <= 200
            or offset < 0
        ):
            raise ValueError("invalid todo filter")
        where, args = (
            (" WHERE status=?", [status])
            if status
            else (" WHERE status NOT IN ('deleted','cancelled')", [])
        )
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
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            disposition, identifier = self._save_todo(conn, data, refs, sources)
        return {"disposition": disposition, "todo": self.todo(identifier)}

    def _save_todo(self, conn, data, refs, sources, *, manual=False):
        due = data.due_at.astimezone(UTC).isoformat() if data.due_at else None
        key = dedup_key(data.title, due, data.has_time)
        existing = None if manual else self._todo_replay(conn, key, refs, sources)
        if existing is None:
            existing = conn.execute("SELECT * FROM todos WHERE dedup_key=?", (key,)).fetchone()
        if manual and existing and existing["status"] != "pending":
            # Keep the retired key so Agent retries still find the closed occurrence.
            conn.execute(
                "UPDATE todos SET dedup_key=? WHERE id=?",
                (f"cancelled:{existing['id']}", existing["id"]),
            )
            existing = None
        if manual and existing:
            return "merged", existing["id"]
        if existing:
            identifier = existing["id"]
            merged = list(dict.fromkeys(json.loads(existing["source_refs"]) + refs))
            plugins = ",".join(
                sorted(set(existing["source_plugin"].split(",") + [s["plugin"] for s in sources]))
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
                    ",".join(sorted({data.source_plugin, *(s["plugin"] for s in sources)})),
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
        conn.execute("INSERT OR IGNORE INTO todo_dedup_keys VALUES(?,?)", (key, identifier))
        enqueue(conn, identifier, fields=[] if existing else None)
        return disposition, identifier

    @staticmethod
    def _todo_replay(conn, key, refs, sources):
        """Retired keys only match proven retries, never titles alone."""
        rows = conn.execute(
            "SELECT t.* FROM todos t JOIN todo_dedup_keys k ON k.todo_id=t.id WHERE k.dedup_key=?",
            (key,),
        ).fetchall()
        shared, reposts = [], []
        for row in rows:
            previous_refs = json.loads(row["source_refs"])
            if set(refs).intersection(previous_refs):
                shared.append(row)
            elif row["status"] == "cancelled" or row["dedup_key"].startswith("cancelled:"):
                fields = ("plugin", "chat_name", "sender", "text")
                signatures = set()
                for start in range(0, len(previous_refs), 500):
                    chunk = previous_refs[start : start + 500]
                    marks = ",".join("?" for _ in chunk)
                    previous = conn.execute(
                        f"SELECT plugin,chat_name,sender,text FROM messages WHERE id IN ({marks})",
                        chunk,
                    ).fetchall()
                    signatures.update(
                        tuple(source[field] for field in fields) for source in previous
                    )
                if any(
                    tuple(source[field] for field in fields) in signatures for source in sources
                ):
                    reposts.append(row)
            elif row["user_modified_fields"] != "[]":
                # Renamed or moved by the user on the phone: what they changed it from is still
                # this task, whichever message reports it again.
                reposts.append(row)
        matches = shared or reposts
        if len(matches) > 1:
            raise ConflictError("ambiguous_todo_replay")
        return matches[0] if matches else None

    def update_todo(self, identifier, patch: TodoPatch, *, from_agent=False):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = todo_row(
                conn.execute("SELECT * FROM todos WHERE id=?", (identifier,)).fetchone()
            )
            if item["status"] == "deleted" and patch.status != "deleted":
                raise ConflictError("deleted_todo_is_terminal")
            if item["status"] == "cancelled" and patch.status == "completed":
                raise ConflictError("cancelled_todo_requires_reopen")
            restoring = item["status"] == "cancelled" and patch.status == "pending"
            previous_key = dedup_key(item["title"], item["due_at"], item["has_time"])
            updates = patch.model_dump(exclude_unset=True)
            reason = updates.pop("reason", None)
            refs = updates.pop("source_refs", None)
            if refs is not None:
                refs = list(dict.fromkeys(refs))
                placeholders = ",".join("?" for _ in refs)
                sources = conn.execute(
                    f"SELECT id,plugin FROM messages WHERE id IN ({placeholders})", refs
                ).fetchall()
                if len(sources) != len(refs):
                    raise ValueError("source_reference_missing")
                merged = list(dict.fromkeys(item["source_refs"] + refs))
                plugins = ",".join(
                    sorted(set(item["source_plugin"].split(",")) | {s["plugin"] for s in sources})
                )
                conn.execute(
                    "UPDATE todos SET source_refs=?,source_plugin=? WHERE id=?",
                    (dump(merged), plugins, identifier),
                )
            if "status" in updates:
                if updates["status"] != item["status"]:
                    conn.execute(
                        "INSERT INTO todo_changes(id,todo_id,old_status,status,from_agent,created_at,reason) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (
                            uid(),
                            identifier,
                            item["status"],
                            updates["status"],
                            int(from_agent),
                            now(),
                            reason if updates["status"] in {"deleted", "cancelled"} else None,
                        ),
                    )
                conn.execute(
                    "UPDATE todos SET status_source=NULL,reminder_completed_at=NULL WHERE id=?",
                    (identifier,),
                )
            if "due_at" in updates:
                updates["due_at"] = (
                    updates["due_at"].astimezone(UTC).isoformat() if updates["due_at"] else None
                )
                if updates["due_at"] is None:
                    updates["has_time"] = False
            if from_agent and any(
                field in updates and updates[field] != item[field]
                for field in item["user_modified_fields"]
            ):
                raise ConflictError("todo_fields_user_modified")
            base = item.copy() if from_agent and item["reminder_id"] else None
            item.update(updates)
            if item["has_time"] and not item["due_at"]:
                raise ValueError("has_time requires due_at")
            key = dedup_key(item["title"], item["due_at"], item["has_time"])
            stored_key = stored_dedup_key(item, key)
            try:
                conn.executemany(
                    "INSERT OR IGNORE INTO todo_dedup_keys VALUES(?,?)",
                    [(previous_key, identifier), (key, identifier)],
                )
                conn.execute(
                    "UPDATE todos SET title=?,notes=?,due_at=?,has_time=?,status=?,"
                    "dedup_key=?,updated_at=? WHERE id=?",
                    (
                        item["title"],
                        item["notes"],
                        item["due_at"],
                        int(item["has_time"]),
                        item["status"],
                        stored_key,
                        now(),
                        identifier,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("duplicate_todo") from exc
            dirty = set(updates) & {"title", "due_at", "has_time", "status"}
            if restoring and item["reminder_id"]:
                # A delete may already have reached Apple despite a lost receipt.
                dirty.add("restore_missing")
            enqueue(conn, identifier, fields=dirty, agent_base=base)
        return self.todo(identifier)

    def digest(self, day, plugin, summary, append=False):
        from datetime import date

        # Python 3.11+ also parses forms like 20261012; always store YYYY-MM-DD.
        day = date.fromisoformat(day).isoformat()
        if not summary.strip() or len(summary) > 50000 or not plugin or len(plugin) > 80:
            raise ValueError("invalid digest")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT summary FROM digests WHERE date=? AND plugin=?", (day, plugin)
            ).fetchone()
            if append and old:
                summary = merge_digest(old["summary"], summary)
            conn.execute(
                "INSERT INTO digests VALUES(?,?,?,?,?) ON CONFLICT(date,plugin) DO UPDATE "
                "SET summary=excluded.summary",
                (uid(), day, plugin, summary, now()),
            )
        return self.db.rows("SELECT * FROM digests WHERE date=? AND plugin=?", (day, plugin))[0]

    def digests(self, day=None, plugin=None, limit=50, offset=0, before=None):
        return page(
            self.db,
            "digests",
            "*",
            {"date": day, "plugin": plugin},
            ("date", "plugin"),
            ["digests", day, plugin],
            limit,
            offset,
            before,
        )

    def runs(self, limit=50, offset=0, job=None, status=None, since=None, until=None):
        if since is not None and until is not None and since >= until:
            raise ValueError("invalid_time_range")
        clauses, args = [], []
        if job == "extract_todos" and status == "succeeded":
            # This is the App's extraction count. The scheduler also records its
            # enclosing job (even on empty polls), which is still in the raw history.
            clauses.append("harness IS NOT NULL")
        for column, value in (("job", job), ("status", status)):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        for operator, value in ((">=", since), ("<", until)):
            if value is not None:
                if value.tzinfo is None:
                    raise ValueError("timezone_required")
                clauses.append(f"started_at{operator}?")
                args.append(stamp(value))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            total = conn.execute("SELECT count(*) FROM runs" + where, args).fetchone()[0]
            items = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM runs"
                    + where
                    + " ORDER BY started_at DESC,id DESC LIMIT ? OFFSET ?",
                    [*args, limit, offset],
                )
            ]
        return {"items": items, "total": total}

    def health_reports(self, day=None, kind=None, status=None, limit=50, offset=0, before=None):
        columns = "id,date,kind,status,title,body,error,harness,created_at,updated_at"
        return page(
            self.db,
            "health_reports",
            columns,
            {"date": day, "kind": kind, "status": status},
            ("date", "kind"),
            ["health_reports", day, kind, status],
            limit,
            offset,
            before,
        )

    def health(self, batch):
        inserted = 0
        with self.db.connect() as conn:
            conn.executemany("DELETE FROM health_samples WHERE id=?", [(i,) for i in batch.deleted])
            for sample in batch.samples:
                inserted += conn.execute(
                    "INSERT OR IGNORE INTO health_samples VALUES(?,?)",
                    (sample.id, sample.model_dump_json()),
                ).rowcount
        # When the iPhone last uploaded, for the health plugin's watch() (core/watchdog.py).
        self.db.put("health_uploaded_at", now())
        return {"accepted": inserted, "duplicates": len(batch.samples) - inserted}
