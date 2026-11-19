"""Read whitelisted chats through a ScreenCapture and a TextRecognizer, incrementally.

Per chat the cursor remembers the newest message read (its ID and time). The next fetch
scrolls up from the newest screen until it reaches that message again, so nothing in between
is skipped; if it cannot reach it within `max_pages` it returns the resolved messages,
records screen_history_gap and keeps the old cursor. Cursors are written only by
commit_fetch(), after the core stored the batch. Re-reading a message is harmless: IDs are stable and the core ignores duplicates.
"""

import asyncio
import hashlib
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from fnmatch import fnmatchcase
from zoneinfo import ZoneInfo

from core.message_signals import has_roster_hint, message_flags, open_reason
from core.store import text_key
from plugins.screen import pixels
from plugins.screen.chat import ScreenGapError, assemble, stitch
from plugins.screen.layout import frame_lines, merge_rows, normalize
from plugins.screen.preview import conversation_rows, preview_items
from plugins.screen.session import DESKTOP_ERRORS
from plugins.screen.timeparse import parse_list_day, parse_separator

ANNOUNCEMENT_SENDER = "群公告"
ELLIPSES = re.compile(r"…+|⋯+|[.．]{2,}")
ROUND_TIMEOUT = 540  # Return/ingest before PluginManager's outer 600-second cancellation.
# Rounds start on the minute but not to the millisecond; a retry due within this of a round's
# start belongs to that round.
RETRY_SLACK = timedelta(seconds=30)
ROUND_ERRORS = DESKTOP_ERRORS | {"screen_read_timeout"}


def error_code(exc):
    code = str(exc)
    return code if re.fullmatch(r"screen_[a-z_]+", code) else "screen_open_failed"


def name_key(name):
    # Decorative emoji may contain zero-width joiners/variation selectors; these do not
    # carry a name's letters or digits. No edit-distance, prefix or OCR-letter guessing.
    return "".join(
        c
        for c in text_key(name)
        if unicodedata.category(c) != "Cf"
        and not 0xFE00 <= ord(c) <= 0xFE0F
        and not 0xE0100 <= ord(c) <= 0xE01EF
    )


def whitelisted(name, whitelist):
    """The whitelist entry a chat name read from the screen stands for, or None.

    OCR and the app differ from what the user typed in width, case, spaces and punctuation
    (《》, -, /), so names are compared by text_key, and only whole names match. A name the
    conversation list cut off ("信息学院2026级软件…") never does: another chat outside
    the whitelist can look exactly the same cut off (the same class's parents' group), and
    the list cannot tell them apart. See cut_off().
    """
    key = name_key(name)
    if not key or cut_off(name):
        return None
    matches = [entry for entry in whitelist if name_key(entry) == key]
    return matches[0] if len(matches) == 1 else None


def cut_off(name):
    """Whether the list shows the name cut off with an ellipsis."""
    return ELLIPSES.search(name) is not None


def hint_key(hint):
    when = hint.sent_at.isoformat() if hint.sent_at else ""
    return f"{when}|{normalize(hint.text)}"


class ChatScreenReader:
    def __init__(self, capture, recognizer, layout, context, config, timezone=None):
        self.capture, self.recognizer, self.layout = capture, recognizer, layout
        self.context, self.config = context, config
        self.timezone = timezone
        self.pending = {}
        self.pending_hints = {}
        self.handled_requests = {}
        self.outcomes = {}
        self.abort_code = None
        self._deadline = None
        self._round_started = None

    async def _during_round(self, operation):
        if self._deadline is None:
            return await operation
        try:
            async with asyncio.timeout_at(self._deadline):
                return await operation
        except TimeoutError:
            raise RuntimeError("screen_read_timeout") from None

    @property
    def zone(self):
        return ZoneInfo(self.timezone or self.context.store.settings.timezone)

    def _now(self):
        return datetime.now(self.zone)

    def _budget(self, chat):
        state = self.context.get(f"screen_opens:{chat}", {})
        if state.get("day") != self._now().date().isoformat():
            state = {"day": self._now().date().isoformat(), "count": 0}
        return state

    def _retry(self, chat):
        state = self.context.get(f"screen_open_retry:{chat}", {})
        if state.get("day") != self._now().date().isoformat():
            state = {
                **{
                    k: state[k] for k in ("next_at", "pending_reason", "pending_hint") if k in state
                },
                "day": self._now().date().isoformat(),
                "attempts": 0,
                "failures": 0,
            }
        return state

    def can_open(self, chat, reason="agent_request"):
        # Exact configured names only: an Agent cannot expand its own permission.
        if chat not in self.config.get("open_on_demand", []) or chat not in self.config.get(
            "whitelist", []
        ):
            return "screen_open_not_allowed"
        budget = self._budget(chat)
        unread_limit = self.config.get("max_unread_opens_per_chat_per_day", 12)
        normal_limit = self.config.get("max_opens_per_chat_per_day", 4)
        # Opens for an unread badge come every round a busy group talks: their own budget, so
        # they never use up the opens kept for 「@全体成员」 and the Agent's requests.
        if reason == "unread":
            if not self.config.get("open_on_unread", True):
                return "screen_open_not_allowed"
            if budget.get("unread", 0) >= unread_limit:
                return "screen_open_daily_limit"
        elif budget["count"] >= normal_limit:
            return "screen_open_daily_limit"
        retry = self._retry(chat)
        failed_limit = self.config.get("max_failed_opens_per_chat_per_day", 4)
        # Legacy reservations only distinguished successful unread opens.
        unread_attempts = retry.get("unread_attempts", budget.get("unread", 0))
        used = unread_attempts if reason == "unread" else retry["attempts"] - unread_attempts
        if (
            retry["failures"] >= failed_limit
            or used >= failed_limit + (unread_limit if reason == "unread" else normal_limit)
            or retry["attempts"] >= failed_limit + normal_limit + unread_limit
        ):
            return "screen_open_retry_limit"
        if retry.get("next_at") and self._now() + RETRY_SLACK < datetime.fromisoformat(
            retry["next_at"]
        ):
            return "screen_open_backoff"
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

    def _outcome(self, chat, state, code=None, **values):
        preview = state == "preview" or self.outcomes.get(chat, {}).get("preview", False)
        self.outcomes[chat] = {"state": state, "error": code, "preview": preview, **values}

    async def fetch(self):
        self.pending = {}
        self.pending_hints = {}
        self.handled_requests = {}
        self.abort_code = None
        self.outcomes = {chat: {"state": "missing"} for chat in self.config.get("whitelist", [])}
        self._deadline = asyncio.get_running_loop().time() + ROUND_TIMEOUT
        self._round_started = self._now()
        started = self._round_started.isoformat()
        collected = []
        try:
            if self.config.get("preview_only", True):
                collected = await self.fetch_preview()
            else:
                requests = self.context.get("screen_open_requests", {})
                failures = []
                for chat in self.config.get("whitelist", []):
                    try:
                        collected.extend(await self.fetch_chat(chat))
                    except Exception as exc:
                        code = error_code(exc)
                        self._outcome(chat, "failed", code)
                        failures.append(code)
                        if code in ROUND_ERRORS:
                            self.abort_code = code
                    if self.outcomes[chat]["state"] == "full" and chat in requests:
                        self.handled_requests[chat] = requests[chat]
                    if self.abort_code:
                        break
                if not collected and failures:
                    raise RuntimeError(failures[0])
            return collected
        except Exception as exc:
            self.abort_code = error_code(exc)
            raise
        finally:
            if self.abort_code:
                for chat, outcome in list(self.outcomes.items()):
                    if outcome["state"] in {"missing", "preview"}:
                        self._outcome(chat, outcome["state"], self.abort_code)
            for chat, outcome in self.outcomes.items():
                if gap := self.context.get(f"screen_gap:{chat}"):
                    outcome["gap"] = gap["error"]
            reading = {
                "started_at": started,
                "finished_at": self._now().isoformat(),
                "committed": False,
                "error": self.abort_code,
                "chats": self.outcomes,
            }
            self.context.put("screen_reading", reading)
            history = self.context.get("screen_reading_history", [])
            history.append(reading)
            self.context.put("screen_reading_history", history[-336:])
            self._deadline = None
            self._round_started = None

    async def fetch_preview(self):
        """Read the list, then let fixed scripts open only explicitly authorized chats."""
        shot = await self._during_round(self.capture.preview())
        blocks = await self._during_round(self.recognizer.recognize(shot))
        reference = shot.captured_at.astimezone(self.zone)
        whitelist = self.config.get("whitelist", [])
        entries = conversation_rows(blocks, self.layout)
        headers = [name for name, _, _ in entries]
        # Every conversation row has a time; none at all means the app shows another page
        # (contacts, a group list). Preview mode never clicks, so it cannot switch back.
        has_preview_row = any(row for _, _, row in entries)
        self.context.put("not_conversation_list", not has_preview_row)
        if not has_preview_row:
            raise RuntimeError("screen_not_conversation_list")
        counts = Counter(name_key(name) for name in headers if not cut_off(name))
        ambiguous = {chat for chat in whitelist if counts[name_key(chat)] > 1}
        self.context.put("ambiguous_chats", sorted(ambiguous))
        for chat in ambiguous:
            self._outcome(chat, "missing", "screen_chat_ambiguous")
        messages = []
        opens = {}
        badges = {}
        requests = self.context.get("screen_open_requests", {})
        # Cut-off names that could be a whitelisted chat: skipped, and shown on the card so
        # the user can widen the list until the whole name shows.
        hidden, blocked = set(), set(ambiguous)
        for name in headers:
            if not cut_off(name) or not name_key(name):
                continue
            pattern = "*".join(name_key(part) for part in ELLIPSES.split(name))
            for chat in whitelist:
                if fnmatchcase(name_key(chat), pattern):
                    hidden.add(name.strip())
                    if name_key(chat) == name_key(name):
                        blocked.add(chat)  # normalization would erase the visible truncation
        self.context.put("cut_off_chats", sorted(hidden))
        # Chats whose newest row is from an earlier day with nothing of that day stored: the
        # reader was down then (QQ closed, the screen locked). Their key, to open them once.
        missed = {}
        for item in preview_items(entries, self.layout, reference):
            chat = whitelisted(item.chat, whitelist)
            if chat is None or chat in blocked:
                continue
            if (
                self.config.get("open_on_unread", True)
                and chat in self.config.get("open_on_demand", [])
                and pixels.badge(shot.path, item.top, item.bottom)
            ):
                badges[chat] = None
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
            self._outcome(chat, "preview")
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
        # A partial missed-day read may already have stored that day's newest messages.
        # Its saved gap must still be retried, even when _stored_since now returns True.
        for chat in whitelist:
            gap = self.context.get(f"screen_gap:{chat}") or {}
            retry = self._retry(chat)
            reason = retry.get("pending_reason") or gap.get("open_reason")
            if chat in blocked:
                continue
            if reason == "unread" and self.config.get("open_on_unread", True):
                badges[chat] = retry.get("pending_hint")
            elif reason in {"truncated", "membership_unknown", "everyone", "missed"}:
                opens.setdefault(chat, (reason, retry.get("pending_hint")))
        for chat in requests:
            if chat not in blocked:
                opens[chat] = ("agent_request", opens.get(chat, (None, None))[1])
        for chat, hint in badges.items():
            opens.setdefault(chat, ("unread", hint))
        unread = set(missed)
        for chat, (reason, hint) in opens.items():
            if self.abort_code:
                break
            denied = self.can_open(chat, reason)
            if (
                denied
                and reason != "unread"
                and chat in badges
                and not self.can_open(chat, "unread")
            ):
                reason, denied = "unread", None
            if denied:
                if chat in self.outcomes:
                    self.outcomes[chat]["error"] = denied
                if denied == "screen_open_not_allowed" and chat in requests:
                    self.handled_requests[chat] = requests[chat]  # permission taken back
                continue
            try:
                messages.extend(await self._open_chat(chat, reason, hint))
            except Exception as exc:
                code = error_code(exc)
                self._outcome(chat, "failed", code)
                if code in ROUND_ERRORS:
                    self.abort_code = code
                continue
            if self.outcomes[chat]["state"] != "full":
                continue  # Keep requests/hints pending until the missing range is recovered.
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

    def _retry_at(self, minutes):
        # Counted from the round's start: a chat opened minutes into a round, after others,
        # is retried by the round that starts `minutes` later, not by the one after it.
        return ((self._round_started or self._now()) + timedelta(minutes=minutes)).isoformat()

    async def _open_chat(self, chat, reason, hint=None):
        if denied := self.can_open(chat, reason):
            raise RuntimeError(denied)
        retry = self._retry(chat)
        state = self._budget(chat)  # charge the day the attempt started, including across midnight
        retry["unread_attempts"] = retry.get("unread_attempts", state.get("unread", 0)) + (
            reason == "unread"
        )
        retry["attempts"] += 1
        retry.update(pending_reason=reason, pending_hint=hint)
        # A crash/cancellation cannot cause an unbounded series of clicks after restart.
        retry["next_at"] = self._retry_at(self.config.get("open_retry_backoff_minutes", 60))
        self.context.put(f"screen_open_retry:{chat}", retry)
        chat_key = hashlib.sha256(chat.encode()).hexdigest()[:12]
        db = self.context.store.db
        run = db.start_run(
            f"plugin:{self.context.plugin_id}:screen_open:{chat_key}:{reason}:{retry['attempts']}"
        )
        round_failed = False
        try:
            result = await self.fetch_chat(chat, page_limit=self.config.get("on_demand_pages", 2))
            outcome = self.outcomes[chat]
            if outcome.get("error"):
                gap = self.context.get(f"screen_gap:{chat}") or {}
                self.context.put(f"screen_gap:{chat}", {**gap, "open_reason": reason})
            round_failed = outcome.get("error") in ROUND_ERRORS
            if result:
                used = "unread" if reason == "unread" else "count"
                state[used] = state.get(used, 0) + 1
                self.context.put(f"screen_opens:{chat}", state)
                if not outcome.get("error"):
                    retry.pop("next_at", None)
            elif outcome.get("error"):
                raise RuntimeError(outcome["error"])
            else:
                retry.pop("next_at", None)
            # A partial read is useful, but runs must not claim it recovered the whole range.
            db.finish_run(
                run, "failed" if outcome.get("error") else "succeeded", outcome.get("error")
            )
            return result
        except BaseException as exc:
            code = error_code(exc)
            round_failed = code in ROUND_ERRORS or isinstance(exc, asyncio.CancelledError)
            retry["failures"] += 1
            minutes = min(
                480,
                self.config.get("open_retry_backoff_minutes", 60)
                * 2 ** min(retry["failures"] - 1, 10),
            )
            retry["next_at"] = self._retry_at(minutes)
            db.finish_run(run, "failed", code)
            raise
        finally:
            self.context.put(f"screen_open_retry:{chat}", retry)
            # Restoration must never discard messages already read. A lost desktop stops
            # this entire round, including parking and the final list capture.
            if not round_failed:
                park = self.config.get("park_chat")
                if (
                    park
                    and park != chat
                    and park in self.config.get("open_on_demand", [])
                    and park in self.config.get("whitelist", [])
                ):
                    try:
                        await self._during_round(self.capture.capture_chat(park, 0))
                    except Exception as exc:
                        code = error_code(exc)
                        self.outcomes.setdefault(chat, {})["cleanup_error"] = code
                        if code in ROUND_ERRORS:
                            self.abort_code = code
                if not self.abort_code:
                    try:
                        await self._during_round(self.capture.preview())
                    except Exception as exc:
                        code = error_code(exc)
                        self.outcomes.setdefault(chat, {})["cleanup_error"] = code
                        if code in ROUND_ERRORS:
                            self.abort_code = code

    async def fetch_chat(self, chat, page_limit=None):
        cursor = self.cursor(chat)
        max_pages = page_limit or self.config.get("max_pages", 20)
        initial_pages = page_limit or self.config.get("initial_pages", 2)
        timeline, extra = [], []
        warning = None
        boundary = None
        messages, hints = [], []
        pages = 0
        previous = None
        for page in range(max_pages):
            try:
                shot = await self._during_round(self.capture.capture_chat(chat, page))
                # Never assemble or recognize the body of a different/uncertain chat.
                if whitelisted(shot.chat or "", [chat]) != chat:
                    raise RuntimeError("screen_chat_mismatch")
                if previous and pixels.unmoved(shot.path, previous):
                    # The scroll did not move the chat: the top of its history. The helper
                    # only sees that when the two screens are the same image byte for byte,
                    # which an animated sticker prevents; stitching would call it a gap.
                    boundary = self._boundary(messages, cursor, page, initial_pages, True)
                    break
                previous = shot.path
                blocks = await self._during_round(self.recognizer.recognize(shot))
                reference = shot.captured_at.astimezone(self.zone)
                lines = frame_lines(blocks, self.layout, reference, shot.path)
                if not lines:
                    raise RuntimeError("screen_empty_capture")
                pages += 1
                try:
                    timeline, _ = stitch(timeline, lines, scroll=self.layout.scroll_fraction)
                except ScreenGapError:
                    # Disconnected pages must never be concatenated into a fake message.
                    # Keep independently resolved messages from both sides of the gap.
                    extra, _ = assemble(lines, chat, self.layout)
                    warning = "screen_history_gap"
                    break
                messages, hints = assemble(timeline, chat, self.layout)
                boundary = self._boundary(messages, cursor, page, initial_pages, shot.at_top)
                if boundary is not None:
                    break
            except Exception as exc:
                code = error_code(exc)
                if code == "screen_chat_not_open":
                    code = "screen_chat_mismatch"
                if not timeline:
                    self._outcome(chat, "failed", code, pages=pages, messages=0)
                    raise RuntimeError(code) from None
                warning = code
                break
        else:
            warning = "screen_history_gap"
        if (
            not warning
            and any(m.external_id for m in messages)
            and any(not m.external_id for m in messages[boundary:])
        ):
            warning = "screen_history_gap"
        if warning:
            boundary = 0
            if warning in ROUND_ERRORS:
                self.abort_code = warning
        resolved = [m for m in extra + messages[boundary or 0 :] if m.external_id]
        # Duplicate pages/messages are idempotent, including across disconnected segments.
        found = {m.external_id: m for m in resolved}
        result = [self._message(chat, m) for m in found.values()]
        if not warning:
            seen = set((cursor or {}).get("hints", []))
            if any(hint.position >= boundary and hint_key(hint) not in seen for hint in hints):
                try:
                    announcement = await self._announcement(chat)
                    if announcement:
                        result.append(announcement)
                except Exception as exc:
                    warning = error_code(exc)
                    if warning in ROUND_ERRORS:
                        self.abort_code = warning
        if not result and not any(m.external_id for m in messages) and not warning:
            warning = "screen_no_messages"
        self._outcome(
            chat,
            "partial" if warning and result else "failed" if warning else "full",
            warning,
            pages=pages,
            messages=len(result),
        )
        if warning:
            self.context.put(
                f"screen_gap:{chat}", {"error": warning, "at": self._now().isoformat()}
            )
            if not result:
                raise RuntimeError(warning)
        else:
            resolved = [m for m in messages if m.external_id]
            if resolved:
                newest = resolved[-1]
                self.pending[chat] = {
                    "anchor": newest.external_id,
                    "sent_at": newest.sent_at.isoformat(),
                    "hints": [hint_key(h) for h in hints][-20:],
                }
        return result

    def _boundary(self, messages, cursor, page, initial_pages, at_top):
        """Index of the first unread message, or None to keep scrolling up."""
        resolved = [m for m in messages if m.external_id]
        if cursor:
            anchor_time = datetime.fromisoformat(cursor["sent_at"])
            if resolved and resolved[-1].sent_at < anchor_time:
                # Even the newest visible message predates the cursor; this view cannot
                # establish coverage of the newer end of the chat.
                raise ScreenGapError("screen_history_gap")
            for index, message in enumerate(messages):
                if message.external_id == cursor["anchor"]:
                    return index + 1
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
            return next((i for i, m in enumerate(messages) if m.has_separator), len(messages))
        return None

    async def _announcement(self, chat):
        shot = await self._during_round(self.capture.capture_announcement(chat))
        if shot is None:
            return None
        if whitelisted(shot.chat or "", [chat]) != chat:
            raise ValueError("screen_chat_mismatch")
        rows = merge_rows(await self._during_round(self.recognizer.recognize(shot)), self.layout)
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
            self.context.put(f"screen_gap:{chat}", None)
            retry = self.context.get(f"screen_open_retry:{chat}", {})
            if "pending_reason" in retry:
                retry.pop("pending_reason")
                retry.pop("pending_hint", None)
                self.context.put(f"screen_open_retry:{chat}", retry)
        for chat, hint in self.pending_hints.items():
            self.context.put(f"screen_open_hint:{chat}", hint)
        requests = self.context.get("screen_open_requests", {})
        for chat, token in self.handled_requests.items():
            if requests.get(chat) == token:
                requests.pop(chat)
        self.context.put("screen_open_requests", requests)
        reading = self.context.get("screen_reading", {})
        reading["committed"] = True
        for chat in self.pending:
            reading.get("chats", {}).get(chat, {}).pop("gap", None)
        self.context.put("screen_reading", reading)
        history = self.context.get("screen_reading_history", [])
        if history:
            history[-1] = reading
            self.context.put("screen_reading_history", history)
        self.pending = {}
