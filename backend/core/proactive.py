"""Durable Bot speech, with one shared quiet-time and daily allowance."""

import json
from datetime import datetime, time, timedelta

from pydantic import TypeAdapter

from core.db import dump, uid
from core.models import Replies

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_speech (
  key TEXT PRIMARY KEY, bot_id TEXT NOT NULL, topic TEXT NOT NULL, text TEXT NOT NULL,
  replies TEXT, not_before TEXT NOT NULL, expires_at TEXT NOT NULL,
  message_id TEXT, delivered_at TEXT, cancelled INTEGER NOT NULL DEFAULT 0);
"""
DEFAULTS = {"daily_limit": 2, "goal_limit": 1, "quiet_start": "23:00", "quiet_end": "07:30"}


def quiet(moment, policy):
    start, end = (time.fromisoformat(policy[k]) for k in ("quiet_start", "quiet_end"))
    clock = moment.time().replace(tzinfo=None)
    return start <= clock < end if start < end else clock >= start or clock < end


class Proactive:
    def __init__(self, chat):
        self.chat, self.db = chat, chat.db

    def configure(self, **policy):
        with self.db.connect() as conn:
            conn.executescript(SCHEMA)
        self.db.put("bot_speech_policy", DEFAULTS | policy)

    def enqueue(self, key, bot_id, topic, text, replies, moment, expires_at):
        TypeAdapter(Replies | None).validate_python(replies)
        if not text.strip() or len(text) > 4000:
            raise ValueError("invalid speech")
        if not self.db.get("bot_speech_policy"):
            self.configure()
        self.db.execute(
            "INSERT OR IGNORE INTO bot_speech"
            "(key,bot_id,topic,text,replies,not_before,expires_at) VALUES(?,?,?,?,?,?,?)",
            (key, bot_id, topic, text, dump(replies), moment.isoformat(), expires_at.isoformat()),
        )

    def cancel(self, prefix):
        # A cancelled question must also lose buttons on already delivered chat lines.
        self.db.execute(
            "UPDATE bot_speech SET cancelled=1 WHERE substr(key,1,?)=?",
            (len(prefix), prefix),
        )
        self.expire()

    def expire(self, moment=None):
        if not self.db.get("bot_speech_policy"):
            return
        # Runs on every chat page read: only lines that still show buttons are looked at.
        rows = self.db.rows(
            "SELECT s.message_id,s.expires_at,s.cancelled FROM bot_speech s "
            "JOIN chat_messages c ON c.id=s.message_id WHERE c.replies IS NOT NULL"
        )
        for row in rows:
            if row["cancelled"] or (moment and moment >= datetime.fromisoformat(row["expires_at"])):
                self.db.execute(
                    "UPDATE chat_messages SET replies=NULL WHERE id=? AND replies IS NOT NULL",
                    (row["message_id"],),
                )

    def answered(self, key):
        self.db.execute("UPDATE bot_speech SET cancelled=1 WHERE key=?", (key,))
        self.expire()

    def delivered(self, key):
        rows = self.db.rows("SELECT message_id FROM bot_speech WHERE key=?", (key,))
        return rows[0]["message_id"] if rows else None

    def drain(self, moment, busy=()):
        self.expire(moment)
        policy = self.db.get("bot_speech_policy", DEFAULTS)
        if quiet(moment, policy):
            return
        clock = moment.strftime("%H:%M")
        if any(row["start_time"] <= clock < row["end_time"] for row in busy):
            return
        start = datetime.combine(moment.date(), time(), moment.tzinfo)
        end = start + timedelta(days=1)
        # Claim and insert the chat line in one transaction. A crash cannot duplicate a line
        # or spend allowance without writing it. All Bots share this ledger.
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            delivered = conn.execute(
                "SELECT topic,delivered_at FROM bot_speech WHERE delivered_at IS NOT NULL"
            ).fetchall()
            today = [
                r
                for r in delivered
                if r["topic"] != "reply"
                and start <= datetime.fromisoformat(r["delivered_at"]) < end
            ]
            total, goals = len(today), sum(r["topic"] == "goals" for r in today)
            pending = conn.execute(
                "SELECT * FROM bot_speech WHERE message_id IS NULL AND cancelled=0 ORDER BY rowid"
            ).fetchall()
            for row in pending:
                if (
                    not datetime.fromisoformat(row["not_before"])
                    <= moment
                    < datetime.fromisoformat(row["expires_at"])
                ):
                    continue
                if row["topic"] != "reply" and total >= policy["daily_limit"]:
                    continue
                if row["topic"] == "goals" and goals >= policy["goal_limit"]:
                    continue
                if (
                    self.chat.tasks.get(row["bot_id"])
                    or conn.execute(
                        "SELECT 1 FROM chat_messages WHERE bot_id=? AND status='pending' LIMIT 1",
                        (row["bot_id"],),
                    ).fetchone()
                ):
                    continue
                identifier, stamp = uid(), moment.isoformat()
                conn.execute(
                    "INSERT INTO chat_messages"
                    "(id,bot_id,author,text,status,created_at,updated_at,replies) "
                    "VALUES(?,?,'bot',?,'ready',?,?,?)",
                    (identifier, row["bot_id"], row["text"], stamp, stamp, row["replies"]),
                )
                conn.execute(
                    "UPDATE bot_speech SET message_id=?,delivered_at=? WHERE key=?",
                    (identifier, stamp, row["key"]),
                )
                total += row["topic"] != "reply"
                goals += row["topic"] == "goals"

    def replies(self, key):
        rows = self.db.rows("SELECT replies FROM bot_speech WHERE key=?", (key,))
        return json.loads(rows[0]["replies"]) if rows else None
