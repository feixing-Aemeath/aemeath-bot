"""Chat with a Bot: the App sends a line, an Agent answers through MCP.

A Bot is the assistant (`assistant`), the knowledge base's keeper (`knowledge`) or an enabled
plugin that is not a message source. Every
line the user sends is also kept as a message of the `chat` source, already processed so
extraction skips it; a todo or a school record made from the conversation cites it like any
other message. The reply starts pending and the Agent writes it with the chat_reply tool;
only that write counts as success. One reply per Bot runs at a time.

A reply may carry up to two plugin cards (chat_cards lists the ones a Bot may attach, by
name only), or charts a plugin draws on request with a few parameters (最近 30 晚的睡眠, see
core/charts.py): the core takes each as it is at that moment and keeps that copy with the
reply, so the numbers in an old conversation stay what they were. The Agent only names a card
or a preset; the numbers never pass through it.
"""

import asyncio
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from core import charts
from core.bots import character, context
from core.db import now, uid
from core.history import page
from core.models import Replies
from core.proactive import Proactive
from core.prompts import render
from core.store import ConflictError
from core.tokens import Grant, current_grant

ASSISTANT = "assistant"
KNOWLEDGE = "knowledge"
SOURCE = "chat"
HISTORY = 12
CARDS = 2
# The accounts are 记账's alone: no other Bot attaches their cards.
FINANCE = "finance"
log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, author TEXT NOT NULL, text TEXT NOT NULL,
  status TEXT NOT NULL, error TEXT, message_id TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chat_messages_bot ON chat_messages(bot_id, created_at);
"""

COLUMNS = "id,bot_id,author,text,status,error,created_at,updated_at,replies,cards"


class Chat:
    def __init__(self, service):
        self.service, self.db = service, service.db
        with self.db.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)")}
            if "replies" not in columns:
                conn.execute("ALTER TABLE chat_messages ADD COLUMN replies TEXT")
            if "cards" not in columns:
                conn.execute("ALTER TABLE chat_messages ADD COLUMN cards TEXT")
        self.proactive = Proactive(self)
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

    def page(self, bot_id, limit=50, offset=0, before=None):
        self.proactive.expire(datetime.fromisoformat(now()))
        result = page(
            self.db,
            "chat_messages",
            COLUMNS,
            {"bot_id": bot_id},
            ("created_at", "rowid"),
            ["chat", bot_id],
            limit,
            offset,
            before,
            zone=self.service.store.settings.timezone,
        )

        result["items"] = [self.decode(row) for row in result["items"]]
        return result

    @staticmethod
    def decode(row):
        row["replies"] = json.loads(row["replies"]) if row.get("replies") else None
        row["cards"] = json.loads(row["cards"]) if row.get("cards") else None
        return row

    def get(self, identifier):
        self.proactive.expire(datetime.fromisoformat(now()))
        rows = self.db.rows(f"SELECT {COLUMNS} FROM chat_messages WHERE id=?", (identifier,))
        if not rows:
            raise KeyError("chat_message_not_found")
        return self.decode(rows[0])

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
                "INSERT INTO chat_messages(id,bot_id,author,text,status,error,message_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (line_id, bot_id, "me", text, "ready", None, message_id, timestamp, timestamp),
            )
            conn.execute(
                "INSERT INTO chat_messages(id,bot_id,author,text,status,error,message_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (reply_id, bot_id, "bot", "", "pending", None, None, timestamp, timestamp),
            )
        handled = await self.service.plugins.notify("chat_input", bot_id, message_id, text)
        if handled is not None:
            self.reply(reply_id, handled["text"], handled.get("replies"))
            return {"message": self.get(line_id), "reply": self.get(reply_id)}
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
            "SELECT author, text, cards FROM chat_messages WHERE bot_id=? AND status='ready' "
            "AND id!=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (bot_id, before, HISTORY),
        )
        lines = []
        for row in reversed(rows):
            speaker = "我" if row["author"] == "me" else name
            text = row["text"] if len(row["text"]) <= 500 else row["text"][:500] + "…"
            # Which cards went with a reply, by title; their numbers stay out of the prompt.
            if row["cards"]:
                titles = "、".join(card["title"] for card in json.loads(row["cards"]))
                text += f"（附了卡片：{titles}）"
            lines.append(f"{speaker}：{text}")
        return "\n".join(lines) or "（这是第一句）"

    async def answer(
        self, bot_id, bot, name, text, message_id, reply_id, history, harness=None, model=None
    ):
        try:
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
                goals_chat=render("goals_chat")
                if bot_id == ASSISTANT and "goals" in self.service.plugins.instances
                else "",
                **context(self.service.memory, bot["character"]),
            )
            await self.service.jobs.run_agent(
                f"chat:{bot_id}",
                prompt,
                succeeded=lambda: self.get(reply_id)["status"] == "ready",
                incomplete_error="reply_not_written",
                harness=harness,
                model=model,
                grant=self.grant(bot_id, reply_id, bot, text),
            )
        except asyncio.CancelledError:
            self.fail(reply_id, "cancelled")
            raise
        except Exception as error:
            self.fail(reply_id, str(error) if isinstance(error, RuntimeError) else "chat_failed")
        finally:
            if self.turns.get(bot_id, {}).get("reply") == reply_id:
                del self.turns[bot_id]

    def grant(self, bot_id, reply_id, bot, text):
        goals = self.service.plugins.instances.get("goals")
        if goals is not None and goals.is_goal_turn(bot_id, text):
            from core.task_grants import goal_chat_grant

            return goal_chat_grant(reply_id, bot["character"])
        return Grant(
            None,
            f"chat:{reply_id}",
            frozenset({"mcp"}),
            bot["character"],
            "bot" if bot["character"] else "system",
        )

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

    @staticmethod
    def check_turn(reply_id):
        """Only the turn's own temporary identity (or the main token) acts on its reply."""
        grant = current_grant.get()
        if grant.kind is not None or grant.reader is not None:
            if grant.kind not in {"bot", "system"} or grant.name != f"chat:{reply_id}":
                raise ValueError("只能回复当前对话轮次")

    def attachable(self, bot_id):
        """The plugins whose cards a Bot may attach: the assistant any running plugin's but the
        accounts; another Bot its own plugin's and those of plugins playing its character
        (知识库Bot's GoodNotes and videos)."""
        plugins = self.service.plugins
        running = [
            identifier for identifier in plugins.manifests if identifier in plugins.instances
        ]
        if bot_id == ASSISTANT:
            return [identifier for identifier in running if identifier != FINANCE]
        reader = self.bot(bot_id)["character"]
        return [
            identifier
            for identifier in running
            if identifier == bot_id
            or (
                reader is not None
                and character(identifier, plugins.manifests[identifier]) == reader
            )
        ]

    async def card_choices(self, reply_id):
        """What this turn's Bot may attach (MCP chat_cards): the cards by plugin, type and
        title only, and the charts its plugins draw on request with their parameters."""
        self.check_turn(reply_id)
        row = self.get(reply_id)
        if row["author"] != "bot" or row["status"] != "pending":
            raise ValueError("reply_not_pending")
        plugins = self.attachable(row["bot_id"])
        groups = await asyncio.gather(*(self.service.plugins.cards(p) for p in plugins))
        return {
            "cards": [
                {"plugin": card["plugin"], "type": card["type"], "title": card["title"]}
                for group in groups
                for card in group
            ],
            "charts": [
                {"plugin": plugin, **charts.public(name, spec)}
                for plugin in plugins
                for name, spec in self.service.plugins.charts(plugin).items()
            ],
        }

    async def pick(self, plugin, item):
        """The card an item names, as it is now: a card by its title, or a chart drawn with
        its parameters; None when there is no such card or the chart cannot be drawn."""
        if isinstance(item.get("chart"), str):
            try:
                return await self.service.plugins.chart(plugin, item["chart"], item.get("params"))
            except (KeyError, ValueError):
                return None
        title = item.get("title")
        if not isinstance(title, str):
            return None
        cards = await self.service.plugins.cards(plugin)
        return next((c for c in cards if c["title"] == title.strip()), None)

    async def snapshots(self, reply_id, requested):
        """The cards a reply names ([{plugin, title}] or [{plugin, chart, params}], at most
        two), each as it is now. One that is malformed, not this Bot's to attach, not there or
        asked with parameters the chart does not take drops out; the text stands."""
        self.check_turn(reply_id)
        if not isinstance(requested, list):
            log.warning("chat_cards_ignored")
            return None
        row = self.get(reply_id)
        allowed = set(self.attachable(row["bot_id"]))
        taken, seen = [], set()
        for item in requested:
            if len(taken) == CARDS:
                break
            plugin = item.get("plugin") if isinstance(item, dict) else None
            if not isinstance(plugin, str) or plugin not in allowed:
                log.warning("chat_card_ignored")
                continue
            card = await self.pick(plugin, item)
            if card is None or (plugin, card["title"]) in seen:
                log.warning("chat_card_ignored")
                continue
            seen.add((plugin, card["title"]))
            taken.append({**card, "taken_at": now()})
        return taken or None

    def reply(self, reply_id, text, replies=None, cards=None):
        """The Agent's answer (MCP chat_reply); `cards` are snapshots taken by `snapshots`."""
        self.check_turn(reply_id)
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
        if row["bot_id"] != ASSISTANT or "goals" not in self.service.plugins.instances:
            replies = None
        elif replies is not None:
            try:
                replies = TypeAdapter(Replies).validate_python(replies)
                if not replies or any(not reply.strip() for reply in replies):
                    raise ValueError("empty replies")
            except ValueError:
                replies = None
                log.warning("chat_replies_ignored")
        self.db.execute(
            "UPDATE chat_messages SET text=?, status='ready', updated_at=?, replies=?, cards=? "
            "WHERE id=? AND status='pending'",
            (
                text,
                now(),
                json.dumps(replies, ensure_ascii=False),
                json.dumps(cards, ensure_ascii=False) if cards else None,
                reply_id,
            ),
        )
        self.db.execute(
            "UPDATE chat_messages SET replies=NULL WHERE bot_id=? AND rowid<(SELECT rowid FROM chat_messages WHERE id=?) AND replies IS NOT NULL",
            (row["bot_id"], reply_id),
        )
        return self.get(reply_id)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
