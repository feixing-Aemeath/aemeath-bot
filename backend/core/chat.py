"""Chat with a Bot: the App sends a line, an Agent answers through MCP.

A Bot is the assistant (`assistant`), the knowledge base's keeper (`knowledge`) or an enabled
plugin that is not a message source. Every
line the user sends is also kept as a message of the `chat` source, already processed so
extraction skips it; a todo or a school record made from the conversation cites it like any
other message. The reply starts pending and the Agent writes it with the chat_reply tool;
only that write counts as success. One reply per Bot runs at a time.
"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from core.bots import character, context
from core.db import now, uid
from core.prompts import render
from core.store import ConflictError
from core.tokens import Grant

ASSISTANT = "assistant"
KNOWLEDGE = "knowledge"
SOURCE = "chat"
HISTORY = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, author TEXT NOT NULL, text TEXT NOT NULL,
  status TEXT NOT NULL, error TEXT, message_id TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chat_messages_bot ON chat_messages(bot_id, created_at);
"""

COLUMNS = "id,bot_id,author,text,status,error,created_at,updated_at"


class Chat:
    def __init__(self, service):
        self.service, self.db = service, service.db
        with self.db.connect() as conn:
            conn.executescript(SCHEMA)
        self.tasks = {}
        # bot_id -> the turn being answered: whose character, the user's line and the reply,
        # and secrets to keep out of both (see redact).
        self.turns = {}

    def bot(self, bot_id):
        """Name and job of a Bot that can chat; KeyError otherwise."""
        if bot_id == ASSISTANT:
            return {
                "name": "助理Bot",
                "role": "总体待办：所有来源的待办、截止时间、每天的摘要和提醒",
                "character": character(bot_id),
            }
        if bot_id == KNOWLEDGE:
            return {
                "name": "知识库Bot",
                "role": "知识库：课程资料、GoodNotes 笔记、课堂照片和视频文字稿，以及每晚整理好的笔记",
                "character": character(bot_id),
            }
        plugins = self.service.plugins
        manifest = plugins.manifests.get(bot_id)
        if manifest is None or bot_id not in plugins.instances:
            raise KeyError("bot_not_found")
        if "source" in manifest["provides"]:
            raise KeyError("bot_not_found")  # message sources only read, they do not talk
        return {
            "name": manifest["name"],
            "role": manifest["description"],
            "character": character(bot_id, manifest),
        }

    def page(self, bot_id, limit=50, offset=0):
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("invalid page")
        items = self.db.rows(
            f"SELECT {COLUMNS} FROM chat_messages WHERE bot_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (bot_id, limit, offset),
        )
        total = self.db.rows("SELECT count(*) n FROM chat_messages WHERE bot_id=?", (bot_id,))
        return {"items": items, "total": total[0]["n"]}

    def get(self, identifier):
        rows = self.db.rows(f"SELECT {COLUMNS} FROM chat_messages WHERE id=?", (identifier,))
        if not rows:
            raise KeyError("chat_message_not_found")
        return rows[0]

    def recover(self):
        """Replies that were being written when the core stopped will not arrive."""
        self.db.execute(
            "UPDATE chat_messages SET status='failed', error='interrupted', updated_at=? "
            "WHERE status='pending'",
            (now(),),
        )

    async def send(self, bot_id, text, bot_name=None, harness=None, model=None):
        bot = self.bot(bot_id)
        running = self.tasks.get(bot_id)
        if running and not running.done():
            raise ConflictError("reply_pending")
        name = bot_name or bot["name"]
        timestamp = now()
        message_id, line_id, reply_id = uid(), uid(), uid()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO messages(id,plugin,external_id,chat_name,sender,sent_at,text,"
                "processed) VALUES(?,?,?,?,?,?,?,1)",
                (message_id, SOURCE, line_id, f"和{name}的对话", "我", timestamp, text),
            )
            conn.execute(
                "INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)",
                (line_id, bot_id, "me", text, "ready", None, message_id, timestamp, timestamp),
            )
            conn.execute(
                "INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)",
                (reply_id, bot_id, "bot", "", "pending", None, None, timestamp, timestamp),
            )
        history = self.history(bot_id, before=line_id, name=name)
        self.turns[bot_id] = {
            "reader": bot["character"],
            "line": line_id,
            "message": message_id,
            "reply": reply_id,
            "secrets": set(),
        }
        task = asyncio.create_task(
            self.answer(bot_id, bot, name, text, message_id, reply_id, history, harness, model),
            name=f"chat:{bot_id}",
        )
        self.tasks[bot_id] = task
        task.add_done_callback(
            lambda done: self.tasks.pop(bot_id, None) if self.tasks.get(bot_id) is done else None
        )
        return {"message": self.get(line_id), "reply": self.get(reply_id)}

    def history(self, bot_id, before, name):
        """The last lines of the conversation before this one, oldest first."""
        rows = self.db.rows(
            "SELECT author, text FROM chat_messages WHERE bot_id=? AND status='ready' AND id!=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (bot_id, before, HISTORY),
        )
        lines = []
        for row in reversed(rows):
            speaker = "我" if row["author"] == "me" else name
            text = row["text"] if len(row["text"]) <= 500 else row["text"][:500] + "…"
            lines.append(f"{speaker}：{text}")
        return "\n".join(lines) or "（这是第一句）"

    async def answer(
        self, bot_id, bot, name, text, message_id, reply_id, history, harness=None, model=None
    ):
        settings = self.service.store.settings
        prompt = render(
            "chat",
            bot_name=name,
            role=bot["role"],
            local_now=datetime.now(ZoneInfo(settings.timezone)).isoformat(),
            timezone=settings.timezone,
            history=history,
            text=text,
            message_id=message_id,
            reply_id=reply_id,
            **context(self.service.memory, bot["character"]),
        )
        try:
            await self.service.jobs.run_agent(
                f"chat:{bot_id}",
                prompt,
                succeeded=lambda: self.get(reply_id)["status"] == "ready",
                incomplete_error="reply_not_written",
                harness=harness,
                model=model,
                grant=Grant(
                    None,
                    "Bot 对话",
                    frozenset({"mcp"}),
                    bot["character"],
                    "bot" if bot["character"] else "system",
                ),
            )
        except asyncio.CancelledError:
            self.fail(reply_id, "cancelled")
            raise
        except Exception as error:
            self.fail(reply_id, str(error) if isinstance(error, RuntimeError) else "chat_failed")
        finally:
            if self.turns.get(bot_id, {}).get("reply") == reply_id:
                del self.turns[bot_id]

    def redact(self, reader, secret):
        """Hide a secret the user typed to the Bot `reader` plays (a bill's zip password): from
        the whole stored chat with that Bot, so later history no longer has it (the user may
        have given it a turn before naming the file), and from the reply about to be written.
        The current turn's prompt already had it, as it must."""
        hidden = "（密码已隐藏）"
        for bot_id, turn in self.turns.items():
            if turn["reader"] != reader or not secret:
                continue
            turn["secrets"].add(secret)
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE messages SET text=replace(text,?,?) WHERE id=? OR id IN "
                    "(SELECT message_id FROM chat_messages WHERE bot_id=?)",
                    (secret, hidden, turn["message"], bot_id),
                )
                conn.execute(
                    "UPDATE chat_messages SET text=replace(text,?,?) WHERE bot_id=?",
                    (secret, hidden, bot_id),
                )

    def fail(self, reply_id, error):
        self.db.execute(
            "UPDATE chat_messages SET status='failed', error=?, updated_at=? "
            "WHERE id=? AND status='pending'",
            (error, now(), reply_id),
        )

    def reply(self, reply_id, text):
        """The Agent's answer (MCP chat_reply)."""
        for turn in self.turns.values():
            if turn["reply"] == reply_id:
                for secret in turn["secrets"]:
                    text = text.replace(secret, "（密码已隐藏）")
        text = text.strip()
        if not 1 <= len(text) <= 4000:
            raise ValueError("reply must be 1-4000 characters")
        row = self.get(reply_id)
        if row["author"] != "bot" or row["status"] != "pending":
            raise ValueError("reply_not_pending")
        self.db.execute(
            "UPDATE chat_messages SET text=?, status='ready', updated_at=? "
            "WHERE id=? AND status='pending'",
            (text, now(), reply_id),
        )
        return self.get(reply_id)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
