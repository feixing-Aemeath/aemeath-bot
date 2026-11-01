"""SQLite queue, aliases and durable checkpoints (never in the chat window)."""

import json

from core.db import dump, now, uid
from core.store import ConflictError
from plugins.video_notes.urls import DOMAINS, normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS video_queue (
 id TEXT PRIMARY KEY, url TEXT NOT NULL, platform TEXT NOT NULL, video_id TEXT NOT NULL,
 topic TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', step TEXT NOT NULL DEFAULT 'queued',
 error TEXT, title TEXT, duration REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 output_path TEXT, completed_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
 state_json TEXT NOT NULL DEFAULT '{}', UNIQUE(platform, video_id));
CREATE TABLE IF NOT EXISTS video_urls (url TEXT PRIMARY KEY, queue_id TEXT NOT NULL);
"""
PUBLIC = (
    "id",
    "url",
    "platform",
    "video_id",
    "topic",
    "status",
    "step",
    "error",
    "title",
    "duration",
    "created_at",
    "updated_at",
    "output_path",
)


def view(row):
    return {key: row[key] for key in PUBLIC}


class Queue:
    def __init__(self, context, config):
        self.db, self.knowledge, self.config = context.store.db, context.memory.knowledge, config
        with self.db.connect() as connection:
            connection.executescript(SCHEMA)

    def get(self, identity):
        return self.db.rows("SELECT * FROM video_queue WHERE id=?", (identity,))[0]

    def list(self, limit=50, offset=0):
        return {
            "items": [
                view(r)
                for r in self.db.rows(
                    "SELECT * FROM video_queue ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ],
            "total": self.db.rows("SELECT count(*) n FROM video_queue")[0]["n"],
        }

    def add(self, url, topic=None):
        platform, video_id, url = normalize(url, self.config.get("domains", DOMAINS))
        topic = self.config.get("default_topic", "视频") if topic is None else topic
        if not isinstance(topic, str):
            raise ValueError("主题名称无效")
        self.knowledge.validate_topic(topic)
        existing = self.db.rows(
            "SELECT * FROM video_queue WHERE (platform=? AND video_id=?) OR id IN "
            "(SELECT queue_id FROM video_urls WHERE url=?)",
            (platform, video_id, url),
        )
        if existing:
            row = existing[0]
            if row["status"] == "failed":
                # Sending the link again tries again: the network was down, or the downloader
                # was broken and has been fixed since.
                self.db.execute(
                    "UPDATE video_queue SET status='queued',step='queued',error=NULL,attempts=0,"
                    "state_json='{}',updated_at=? WHERE id=? AND status='failed'",
                    (now(), row["id"]),
                )
                row = self.db.rows("SELECT * FROM video_queue WHERE id=?", (row["id"],))[0]
            return view(row)
        self.ensure_topic(topic)
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM video_queue WHERE (platform=? AND video_id=?) OR id IN "
                "(SELECT queue_id FROM video_urls WHERE url=?)",
                (platform, video_id, url),
            ).fetchone()
            if row:
                return view(dict(row))
            if (
                connection.execute(
                    "SELECT count(*) FROM video_queue WHERE status IN ('queued','processing','needs_setup')"
                ).fetchone()[0]
                >= 50
            ):
                raise ConflictError("video_queue_full")
            # Use Knowledge's validation, safe paths and default reader initialization.
            # topic() uses another SQLite connection, so do not call it inside this transaction.
            identity, timestamp = uid(), now()
            connection.execute(
                "INSERT INTO video_queue(id,url,platform,video_id,topic,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (identity, url, platform, video_id, topic, timestamp, timestamp),
            )
            connection.execute("INSERT INTO video_urls VALUES(?,?)", (url, identity))
        return view(self.get(identity))

    def ensure_topic(self, name):
        self.knowledge.validate_topic(name)
        path = self.knowledge.path(f"知识库/{name}", exists=False)
        if path.exists():
            if not path.is_dir():
                raise ValueError("主题路径不是文件夹")
            self.knowledge.access.discover()
        else:
            try:
                self.knowledge.topic(name)
            except ConflictError:
                pass

    def update(self, identity, **fields):
        fields["updated_at"] = now()
        if "state" in fields:
            fields["state_json"] = dump(fields.pop("state"))
        columns = ",".join(f"{key}=?" for key in fields)
        self.db.execute(
            f"UPDATE video_queue SET {columns} WHERE id=?", (*fields.values(), identity)
        )

    def checkpoint(self, identity, step, state, **fields):
        self.update(identity, step=step, state=state, attempts=0, error=None, **fields)

    def resolve(self, row, platform, video_id, url):
        """Short links/av aliases can only be deduplicated after yt-dlp resolves them.
        Merge the provisional row and retain all known URL aliases without touching vault files.
        """
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            other = connection.execute(
                "SELECT id FROM video_queue WHERE platform=? AND video_id=? AND id!=?",
                (platform, video_id, row["id"]),
            ).fetchone()
            if other:
                connection.execute(
                    "UPDATE video_urls SET queue_id=? WHERE queue_id=?",
                    (other[0], row["id"]),
                )
                connection.execute("DELETE FROM video_queue WHERE id=?", (row["id"],))
                return other[0]
            connection.execute(
                "UPDATE video_queue SET platform=?,video_id=?,url=? WHERE id=?",
                (platform, video_id, url, row["id"]),
            )
            connection.execute("INSERT OR REPLACE INTO video_urls VALUES(?,?)", (url, row["id"]))
        return row["id"]

    def pending(self, limit):
        return self.db.rows(
            "SELECT * FROM video_queue WHERE status IN ('queued','processing','needs_setup') "
            "ORDER BY status='needs_setup',created_at,id LIMIT ?",
            (limit,),
        )

    @staticmethod
    def state(row):
        return json.loads(row["state_json"])
