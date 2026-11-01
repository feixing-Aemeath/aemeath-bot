"""Read whitelisted chats through a ScreenCapture and a TextRecognizer, incrementally.

Per chat the cursor remembers the newest message read (its ID and time). The next fetch
scrolls up from the newest screen until it reaches that message again, so nothing in between
is skipped; if it cannot reach it within `max_pages` it fails with screen_history_gap and
keeps the old cursor. Cursors are written only by commit_fetch(), after the core stored the
batch. Re-reading a message is harmless: IDs are stable and the core ignores duplicates.
"""

import contextlib
import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from core.message_signals import has_roster_hint, message_flags, open_reason
from core.store import text_key
from plugins.screen.chat import ScreenGapError, assemble, stitch
from plugins.screen.layout import frame_lines, merge_rows, normalize
from plugins.screen.preview import parse_preview
from plugins.screen.timeparse import parse_list_day, parse_separator

ANNOUNCEMENT_SENDER = "群公告"
ELLIPSES = ("…", "...", "⋯", "..")
# The helper checks these before it sends any input, so a first page failing with one of them
# never opened the chat.
BEFORE_INPUT = {"screen_recording_denied", "screen_accessibility_denied", "screen_app_not_running"}


class ChatNotOpenedError(RuntimeError):
    """The helper failed before clicking anything; the chat was not opened or marked read."""


def whitelisted(name, whitelist):
    """The whitelist entry a chat name read from the screen stands for, or None.

    OCR and the app differ from what the user typed in width, case, spaces and punctuation
    (《》, -, /), so names are compared by text_key, and only whole names match. A name the
    conversation list cut off ("人工智能学院2026级计算…") never does: another chat outside
    the whitelist can look exactly the same cut off (the same class's parents' group), and
    the list cannot tell them apart. See cut_off().
    """
    key = text_key(name)
    if not key or cut_off(name):
        return None
    for entry in whitelist:
        if text_key(entry) == key:
            return entry
    return None


def cut_off(name):
    """Whether the list shows the name cut off with an ellipsis."""
    return name.rstrip().endswith(ELLIPSES)


def hint_key(hint):
    when = hint.sent_at.isoformat() if hint.sent_at else ""
    return f"{when}|{normalize(hint.text)}"


class ChatScreenReader:
    def __init__(self, capture, recognizer, layout, context, config, timezone="Asia/Shanghai"):
        self.capture, self.recognizer, self.layout = capture, recognizer, layout
        self.context, self.config = context, config
        self.zone = ZoneInfo(timezone)
        self.pending = {}
        self.pending_hints = {}
        self.handled_requests = {}

    def _now(self):
        return datetime.now(self.zone)

    def _budget(self, chat):
        state = self.context.get(f"screen_opens:{chat}", {})
        if state.get("day") != self._now().date().isoformat():
            state = {"day": self._now().date().isoformat(), "count": 0}
        return state

    def can_open(self, chat):
        # Exact configured names only: an Agent cannot expand its own permission.
        if chat not in self.config.get("open_on_demand", []) or chat not in self.config.get(
            "whitelist", []
        ):
            return "screen_open_not_allowed"
        if self._budget(chat)["count"] >= self.config.get("max_opens_per_chat_per_day", 4):
            return "screen_open_daily_limit"
        return None

    def request_open(self, chat):
        if reason := self.can_open(chat):
            return {"status": "denied", "reason": reason}
        requests = self.context.get("screen_open_requests", {})
        # Idempotent until consumed; repeated Agent calls cannot create a queue of opens.
        if chat not in requests:
            from core.db import uid

            requests[chat] = uid()
            self.context.put("screen_open_requests", requests)
        return {"status": "scheduled", "message": "已安排"}

    def cursor(self, chat):
        return self.context.get(f"screen_cursor:{chat}")

    async def fetch(self):
        self.pending = {}
        self.pending_hints = {}
        self.handled_requests = {}
        if self.config.get("preview_only", True):
            return await self.fetch_preview()
        collected = []
        requests = self.context.get("screen_open_requests", {})
        for chat in self.config.get("whitelist", []):
            collected.extend(await self.fetch_chat(chat))
            if chat in requests:
                # Full mode opens every chat anyway: an Agent's request is already met.
                self.handled_requests[chat] = requests[chat]
        return collected

    async def fetch_preview(self):
        """Read the list, then let fixed scripts open only explicitly authorized chats."""
        shot = await self.capture.preview()
        blocks = await self.recognizer.recognize(shot)
        reference = shot.captured_at.astimezone(self.zone)
        whitelist = self.config.get("whitelist", [])
        items = parse_preview(blocks, self.layout, reference)
        # Every conversation row has a time; none at all means the app shows another page
        # (contacts, a group list). Preview mode never clicks, so it cannot switch back.
        self.context.put("not_conversation_list", not items)
        if not items:
            raise RuntimeError("screen_not_conversation_list")
        messages = []
        opens = {}
        requests = self.context.get("screen_open_requests", {})
        # Cut-off names that could be a whitelisted chat: skipped, and shown on the card so
        # the user can widen the list until the whole name shows.
        keys = [text_key(entry) for entry in whitelist]
        hidden = sorted(
            {
                item.chat.strip()
                for item in items
                if cut_off(item.chat)
                and (key := text_key(item.chat))
                and any(entry.startswith(key) for entry in keys)
            }
        )
        self.context.put("cut_off_chats", hidden)
        # Chats whose newest row is from an earlier day with nothing of that day stored: the
        # reader was down then (QQ closed, the screen locked). Their key, to open them once.
        missed = {}
        for item in items:
            chat = whitelisted(item.chat, whitelist)
            if chat is None:
                continue
            sent_at = item.sent_at
            if sent_at is None:
                day = parse_list_day(item.label, reference)
                if day is None or self._stored_since(chat, day):
                    continue
                # 「昨天 20:59」 still has its minute and is kept like today's previews; a bare
                # 「星期二」 has none, and the reader never guesses one: the chat itself is read.
                sent_at = parse_separator(item.label, reference)
                if sent_at is None:
                    missed[chat] = (
                        f"{self.layout.app}:missed:"
                        + hashlib.sha256(
                            "\x1f".join([chat, day.isoformat(), normalize(item.text)]).encode()
                        ).hexdigest()[:32]
                    )
                    continue
            digest = hashlib.sha256(
                "\x1f".join(
                    [self.layout.app, chat, sent_at.isoformat(), normalize(item.text)]
                ).encode()
            ).hexdigest()[:32]
            message = {
                "external_id": f"{self.layout.app}:preview:{digest}",
                # Stored under the name as whitelisted, not as read from the screen.
                "chat_name": chat,
                "sender": chat if item.sender == item.chat else item.sender,
                "sent_at": sent_at,
                "text": item.text,
                "screenshot_path": shot.path,
                "priority": item.priority,
                "preview": True,
                "outgoing": False,
            }
            messages.append(message)
            local_text = item.text
            for separator in ("：", ":"):
                if not has_roster_hint(item.sender) and local_text.startswith(
                    item.sender + separator
                ):
                    local_text = local_text[len(item.sender) + 1 :]
                    break
            message["flags"] = message_flags(local_text, self.config.get("self_names", []))
            reason = open_reason(local_text, self.config.get("self_names", []), item.everyone)
            if reason and self.context.get(f"screen_open_hint:{chat}") != message["external_id"]:
                opens[chat] = (reason, message["external_id"])
        for chat, key in missed.items():
            if chat not in opens and self.context.get(f"screen_open_hint:{chat}") != key:
                opens[chat] = ("missed", key)
        for chat in requests:
            opens[chat] = ("agent_request", opens.get(chat, (None, None))[1])
        unread = set(missed)
        for chat, (reason, hint) in opens.items():
            if denied := self.can_open(chat):
                if denied == "screen_open_not_allowed" and chat in requests:
                    self.handled_requests[chat] = requests[chat]  # permission taken back
                continue
            try:
                messages.extend(await self._open_chat(chat, reason))
            except Exception:
                # _open_chat recorded the failure in runs; the previews above still count.
                continue
            unread.discard(chat)
            if hint:
                # This hint is remembered after ingestion, alongside the chat cursor.
                self.pending_hints[chat] = hint
            if chat in requests:
                self.handled_requests[chat] = requests[chat]
        # Missed chats the reader may not open, or opened before for the same row: the
        # watchdog asks the user to look (ScreenPlugin.watch).
        self.context.put("missed_chats", sorted(unread))
        return messages

    def _stored_since(self, chat, day):
        """Whether a message of this chat sent on `day` or later is stored."""
        [row] = self.context.store.db.rows(
            "SELECT max(sent_at) AS latest FROM messages WHERE plugin=? AND chat_name=?",
            (self.context.plugin_id, chat),
        )
        latest = row["latest"]
        return bool(latest) and datetime.fromisoformat(latest).astimezone(self.zone).date() >= day

    async def _open_chat(self, chat, reason):
        state = self._budget(chat)
        state["count"] += 1
        # Reserve before page 0: even an OCR/title failure may have marked the chat read.
        self.context.put(f"screen_opens:{chat}", state)
        chat_key = hashlib.sha256(chat.encode()).hexdigest()[:12]
        db = self.context.store.db
        run = db.start_run(
            f"plugin:{self.context.plugin_id}:screen_open:{chat_key}:{reason}:{state['count']}"
        )
        try:
            result = await self.fetch_chat(chat, page_limit=self.config.get("on_demand_pages", 2))
        except BaseException as exc:
            code = str(exc)
            if isinstance(exc, ChatNotOpenedError):
                # Nothing was clicked, so the reservation goes back.
                state["count"] -= 1
                self.context.put(f"screen_opens:{chat}", state)
            db.finish_run(
                run,
                "failed",
                code if re.fullmatch(r"screen_[a-z_]+", code) else "screen_open_failed",
            )
            raise
        else:
            db.finish_run(run)
            return result
        finally:
            # The helper cannot close a chat, so the one just opened stays on the right and
            # QQ may keep marking its new messages read. park_chat moves to one the user chose.
            park = self.config.get("park_chat")
            if park and park != chat:
                with contextlib.suppress(Exception):
                    await self.capture.capture_chat(park, 0)
            # Its preview action captures the conversation list without opening anything.
            await self.capture.preview()

    async def fetch_chat(self, chat, page_limit=None):
        cursor = self.cursor(chat)
        max_pages = page_limit or self.config.get("max_pages", 20)
        initial_pages = page_limit or self.config.get("initial_pages", 2)
        timeline = []
        partial = False
        for page in range(max_pages):
            try:
                shot = await self.capture.capture_chat(chat, page)
            except RuntimeError as exc:
                if str(exc) == "screen_chat_not_open":
                    raise ValueError("screen_chat_mismatch") from None
                if page == 0 and str(exc) in BEFORE_INPUT:
                    raise ChatNotOpenedError(str(exc)) from None
                raise
            # The helper reports the title it read on screen; it must be this chat's whole name.
            if whitelisted(shot.chat or "", [chat]) != chat:
                raise ValueError("screen_chat_mismatch")
            blocks = await self.recognizer.recognize(shot)
            reference = shot.captured_at.astimezone(self.zone)
            lines = frame_lines(blocks, self.layout, reference, shot.path)
            try:
                timeline, added = stitch(timeline, lines)
            except ScreenGapError:
                raise RuntimeError("screen_history_gap") from None
            at_top = shot.at_top or (page > 0 and added == 0)
            messages, hints = assemble(timeline, chat, self.layout)
            boundary = self._boundary(messages, cursor, page, initial_pages, at_top)
            if boundary is not None:
                break
        else:
            if page_limit is None:
                raise RuntimeError("screen_history_gap")
            # An on-demand read looks at the newest screens only. Keep what they show and leave
            # the cursor where it was, so a later full read still fills the gap.
            partial = True
            boundary = next((i for i, m in enumerate(messages) if m.external_id), len(messages))
        found = [m for m in messages[boundary:] if m.external_id]
        result = [self._message(chat, m) for m in found]
        seen = set((cursor or {}).get("hints", []))
        if any(hint.position >= boundary and hint_key(hint) not in seen for hint in hints):
            announcement = await self._announcement(chat)
            if announcement:
                result.append(announcement)
        resolved = [m for m in messages if m.external_id]
        if resolved and not partial:
            newest = resolved[-1]
            self.pending[chat] = {
                "anchor": newest.external_id,
                "sent_at": newest.sent_at.isoformat(),
                # Hints already handled, so an old notice does not reopen the announcement.
                "hints": [hint_key(h) for h in hints][-20:],
            }
        return result

    def _boundary(self, messages, cursor, page, initial_pages, at_top):
        """Index of the first unread message, or None to keep scrolling up."""
        resolved = [m for m in messages if m.external_id]
        if cursor:
            for index, message in enumerate(messages):
                if message.external_id == cursor["anchor"]:
                    return index + 1
            anchor_time = datetime.fromisoformat(cursor["sent_at"])
            if at_top or any(m.sent_at < anchor_time for m in resolved):
                # Scrolled past where the anchor was (e.g. it was recalled): take everything
                # from its minute on; already stored messages are ignored by their IDs.
                return next(
                    (i for i, m in enumerate(messages) if m.sent_at and m.sent_at >= anchor_time),
                    len(messages),
                )
            return None
        if at_top or (page + 1 >= initial_pages and resolved):
            # First run: start with what the initial pages show.
            return next((i for i, m in enumerate(messages) if m.external_id), len(messages))
        return None

    async def _announcement(self, chat):
        shot = await self.capture.capture_announcement(chat)
        if shot is None:
            return None
        if shot.chat not in (None, chat):
            raise ValueError("screen_chat_mismatch")
        rows = merge_rows(await self.recognizer.recognize(shot), self.layout)
        text = "\n".join(row.text for row in rows)
        if not text.strip():
            return None
        digest = hashlib.sha256(
            "\x1f".join([self.layout.app, chat, "announcement", normalize(text)]).encode()
        ).hexdigest()[:32]
        return {
            "external_id": f"{self.layout.app}:announcement:{digest}",
            "chat_name": chat,
            "sender": ANNOUNCEMENT_SENDER,
            "sent_at": shot.captured_at.astimezone(self.zone),
            "text": f"【群公告】\n{text}",
            "screenshot_path": shot.path,
            "priority": True,
            "flags": message_flags(text, self.config.get("self_names", [])),
        }

    def _message(self, chat, message):
        return {
            "external_id": message.external_id,
            "chat_name": chat,
            "sender": message.sender,
            "sent_at": message.sent_at,
            "text": message.text,
            "screenshot_path": message.screenshot,
            "priority": message.priority,
            "outgoing": message.outgoing,
            "flags": message_flags(message.text, self.config.get("self_names", [])),
        }

    async def commit_fetch(self):
        for chat, cursor in self.pending.items():
            self.context.put(f"screen_cursor:{chat}", cursor)
        for chat, hint in self.pending_hints.items():
            self.context.put(f"screen_open_hint:{chat}", hint)
        requests = self.context.get("screen_open_requests", {})
        for chat, token in self.handled_requests.items():
            if requests.get(chat) == token:
                requests.pop(chat)
        self.context.put("screen_open_requests", requests)
        self.pending = {}
