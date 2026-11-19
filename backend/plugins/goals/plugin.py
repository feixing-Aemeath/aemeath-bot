"""One goal, a small App-only step and a conversation that slows down when needed."""

import asyncio
import json
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.db import dump, uid
from core.models import TodoCreate, TodoPatch
from core.plugins import Plugin as BasePlugin
from core.store import ConflictError
from core.tokens import current_grant
from plugins.goals.exercise import exercise_name
from plugins.goals.planning import (
    conflicts,
    counted,
    effective,
    entries,
    gap,
    label,
    local,
    progress,
    satisfied,
    visible,
    week,
    week_input,
)
from plugins.goals.storage import Changes, Profile, Storage
from plugins.school.common import arguments_of


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        self.db = context.store.db
        self.storage = Storage(self.db)
        self.lock = asyncio.Lock()
        self.stopped = False
        self.speech = context.chat.proactive
        self.policy = {
            k: config[k]
            for k in ("daily_limit", "goal_limit", "quiet_start", "quiet_end")
            if k in config
        }
        self.speech.configure(**self.policy)
        self.context.put("enabled_since", self.context.get("enabled_since", self.now().isoformat()))

    def now(self):
        return datetime.now(ZoneInfo(self.context.store.settings.timezone))

    def jobs(self):
        return {"follow": {"cron": "*/5 * * * *", "callback": self.follow}}

    async def teardown(self):
        self.stopped = True
        async with self.lock:
            pass

    async def disabled(self):
        async with self.lock:
            goal = self.storage.current()
            if goal:
                self.speech.cancel(f"goals:{goal['id']}:")
                self.cancel_steps(goal, skip=True)

    def mcp_tools(self):
        return {
            name: getattr(self, name)
            for name in (
                "goals_get",
                "goals_propose",
                "goals_confirm",
                "goals_record",
                "goals_revoke",
            )
        }

    def is_goal_turn(self, bot_id, text):
        if bot_id != "assistant" or not self.context.get("building"):
            return False
        drafts = self.db.rows("SELECT * FROM goals_drafts ORDER BY rowid DESC LIMIT 1")
        latest = self.db.rows(
            "SELECT id,replies FROM chat_messages WHERE bot_id='assistant' AND author='bot' AND status='ready' ORDER BY rowid DESC LIMIT 1"
        )
        summary = (
            self.db.rows(
                "SELECT id FROM chat_messages WHERE bot_id='assistant' AND author='bot' AND status='ready' "
                "AND rowid>(SELECT rowid FROM chat_messages WHERE message_id=? AND author='me') ORDER BY rowid LIMIT 1",
                (drafts[0]["message_id"],),
            )
            if drafts
            else []
        )
        if (
            summary
            and latest
            and summary[0]["id"] == latest[0]["id"]
            and text in {"定下来", "再改改"}
            and {"定下来", "再改改"} <= set(json.loads(latest[0]["replies"] or "[]"))
        ):
            return True
        self.context.put("building", False)
        self.db.execute("DELETE FROM goals_drafts")
        if summary:
            self.db.execute("UPDATE chat_messages SET replies=NULL WHERE id=?", (summary[0]["id"],))
        return False

    def turn(self):
        grant = current_grant.get()
        turn = self.context.chat.turns.get("assistant")
        if (
            not turn
            or grant.kind != "bot"
            or grant.name != f"chat:{turn['reply']}"
            or grant.reader != "助理"
        ):
            raise ValueError("goal tools require the current assistant conversation")
        rows = self.db.rows(
            "SELECT c.message_id,c.text,c.author FROM chat_messages c WHERE c.bot_id='assistant' "
            "AND c.author='me' ORDER BY c.rowid DESC LIMIT 1"
        )
        if not rows or rows[0]["message_id"] != turn["message"]:
            raise ConflictError("conversation_changed")
        return rows[0]

    async def goals_get(self, arguments):
        """Read the goal, the plan waiting for 定下来 (draft: its id, profile and changes, or
        null), dated reversible records and code-computed counts. arguments: {}.
        Only available in the current assistant conversation; never invent progress."""
        arguments_of(arguments, set())
        self.turn()
        goal = self.storage.current()
        return {
            "goal": goal,
            "draft": self.latest_draft(),
            "records": self.storage.records(goal["id"], active=False) if goal else [],
            "week": self.weekly_input(self.now().date()),
        }

    def latest_draft(self):
        """The staged plan, if any: a confirmation turn is a new Agent session that did not see
        the draft id goals_propose returned a turn earlier, so it reads it here."""
        rows = self.db.rows(
            "SELECT id,profile,changes FROM goals_drafts ORDER BY rowid DESC LIMIT 1"
        )
        if not rows:
            return None
        return {
            "id": rows[0]["id"],
            "profile": json.loads(rows[0]["profile"]),
            "changes": json.loads(rows[0]["changes"]),
        }

    async def goals_propose(self, arguments):
        """Stage a plan without applying it. arguments: {profile: {title, why, deadline?,
        result_date?, criteria:[{id,title,kind:steps|number|result,target,comparison?:gte|lte,
        samples?:1,question?:text,replies?:[{text,value?}]}], schedule:[{weekday:0..6,time:HH:MM,minutes:5..45}], weekly_title,
        weekly_target:1..7, step_title (optional {n} placeholder), activity?:study|exercise|homework,
        blocker_choices?:[{text,kind:content|time|materials}], smaller_step?:{title,minutes,blocker?}, deadline_label?:text,
        result_question?:text, result_replies?:[text], waiting_triggers?:[text], homework_ids?:[]}, revision?:current revision, changes?:{status:active|paused|waiting|dropped, ask_on:date, blocker:text, blocker_day:date}}. Show the goal, criteria, rhythm and this week before asking
        定下来. Use chat_reply replies=[定下来,再改改]. No write until user confirms. To discard a draft, use {abandon:true}."""
        if arguments == {"abandon": True}:
            self.turn()
            self.context.put("building", False)
            self.db.execute("DELETE FROM goals_drafts")
            return {"abandoned": True}
        arguments_of(arguments, {"profile", "revision", "changes"}, required=("profile",))
        changes = Changes.model_validate(arguments.get("changes", {})).model_dump(
            mode="json", exclude_none=True
        )
        if changes.get("ask_on") and date.fromisoformat(changes["ask_on"]) <= self.now().date():
            raise ValueError("请选择以后再问的日期")
        turn = self.turn()
        profile = Profile.model_validate(arguments["profile"]).model_dump(mode="json")
        goal = self.storage.current()
        editing = goal is not None and goal["status"] not in {"achieved", "dropped"}
        if editing and arguments.get("revision") != goal["revision"]:
            raise ConflictError("goal_changed")
        # One draft per latest user line: fallback/retry does not create competing proposals.
        identifier = turn["message_id"]
        self.db.execute("DELETE FROM goals_drafts")
        self.db.execute(
            "INSERT INTO goals_drafts VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "profile=excluded.profile,goal_id=excluded.goal_id,revision=excluded.revision,changes=excluded.changes",
            (
                identifier,
                dump(profile),
                goal["id"] if editing else None,
                goal["revision"] if editing else None,
                turn["message_id"],
                self.now().isoformat(),
                dump(changes),
            ),
        )
        self.context.put("building", True)
        return {"draft": identifier, "profile": profile, "changes": changes, "requires": "定下来"}

    async def goals_confirm(self, arguments):
        """Apply the last plan ONLY after the latest user line is exactly 定下来.
        arguments: {draft?: the draft id from goals_propose or goals_get; left out, the plan
        staged last}. Repeated calls are idempotent."""
        arguments_of(arguments, {"draft"})
        turn = self.turn()
        if turn["text"] != "定下来":
            raise ValueError("请先让用户说定下来")
        async with self.lock:
            draft = arguments.get("draft") or (self.latest_draft() or {}).get("id")
            if not draft:
                raise ConflictError("plan_changed")
            return self.confirm(draft, turn)

    def confirm(self, draft, turn):
        rows = self.db.rows("SELECT * FROM goals_drafts ORDER BY rowid DESC LIMIT 1")
        if not rows or rows[0]["id"] != draft:
            raise ConflictError("plan_changed")
        proposal = rows[0]
        # Confirm must follow a displayed Bot summary containing the button, not a draft
        # proposed by the model in the same confirmation turn.
        lines = self.db.rows(
            "SELECT author,replies FROM chat_messages WHERE bot_id='assistant' "
            "AND status='ready' AND message_id IS NOT ? ORDER BY rowid DESC LIMIT 1",
            (turn["message_id"],),
        )
        if (
            proposal["message_id"] == turn["message_id"]
            or not lines
            or lines[0]["author"] != "bot"
            or "定下来" not in json.loads(lines[0]["replies"] or "[]")
        ):
            raise ValueError("请先复述计划再请用户确认")
        applied = self.context.get("confirmed", {})
        if applied.get(turn["message_id"]) == draft:
            return {"accepted": True, "goal": self.storage.current()}
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changes = json.loads(proposal["changes"])
            status = changes.pop("status", None)
            current = self.storage.current()
            state = (
                dict(current["state"])
                if proposal["goal_id"]
                else {"confirmed_message": turn["message_id"]}
            )
            state.update(changes)
            if status == "waiting":
                state["waiting_since"] = self.now().isoformat()
            if status == "dropped":
                state.update(ended_at=self.now().isoformat(), ask_on=None)
            status = status or (current["status"] if proposal["goal_id"] else "active")
            if proposal["goal_id"]:
                changed = conn.execute(
                    "UPDATE goals_profiles SET profile=?,state=?,status=?,revision=revision+1 WHERE id=? AND revision=?",
                    (
                        proposal["profile"],
                        dump(state),
                        status,
                        proposal["goal_id"],
                        proposal["revision"],
                    ),
                ).rowcount
                if not changed:
                    raise ConflictError("goal_changed")
            else:
                if conn.execute(
                    "SELECT 1 FROM goals_profiles WHERE status IN ('active','paused','waiting')"
                ).fetchone():
                    raise ConflictError("one_goal_at_a_time")
                conn.execute(
                    "INSERT INTO goals_profiles VALUES(?,?,1,?,?,?)",
                    (
                        uid(),
                        proposal["profile"],
                        status,
                        dump(state),
                        self.now().isoformat(),
                    ),
                )
            applied[turn["message_id"]] = draft
            conn.execute(
                "INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("plugin:goals:confirmed", dump(applied)),
            )
        self.context.put("building", False)
        goal = self.storage.current()
        self.speech.cancel(f"goals:{goal['id']}:")
        if proposal["goal_id"] or status != "active":
            self.cancel_steps(goal, skip=True)
        self.note_later(goal)
        return {
            "accepted": True,
            "goal": goal,
            "text": self.closed_text(goal) if status == "dropped" else None,
        }

    async def goals_record(self, arguments):
        """Record only the user's CURRENT exact words, never the model's statement.
        arguments: {message_id, quote:full current text, criterion?:id, value?:number}.
        A number must occur in the user's text. You must first establish from context that
        the user explicitly says they did this activity this time. After recording, say 算一次
        and mention 那次不算 can undo it. Every entry can be revoked with goals_revoke."""
        arguments_of(
            arguments,
            {"message_id", "quote", "criterion", "value"},
            required=("message_id", "quote"),
        )
        turn = self.turn()
        if arguments["message_id"] != turn["message_id"] or arguments["quote"] != turn["text"]:
            raise ValueError("只能记下用户这次亲口说的话")
        async with self.lock:
            goal = self.storage.current()
            if not goal or goal["status"] in {"achieved", "dropped"}:
                raise ValueError("no open goal")
            criterion = arguments.get("criterion")
            value = arguments.get("value")
            if criterion:
                rule = next(
                    (
                        c
                        for c in goal["profile"]["criteria"]
                        if c["id"] == criterion and c["kind"] != "steps"
                    ),
                    None,
                )
                if (
                    not rule
                    or isinstance(value, bool)
                    or value is None
                    or not isinstance(value, (int, float))
                ):
                    raise ValueError("invalid number")
                numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", turn["text"])]
                if value not in numbers:
                    raise ValueError("数字要来自用户原话")
                record = self.storage.record(
                    goal["id"],
                    "number",
                    turn["message_id"],
                    self.now().isoformat(),
                    turn["text"],
                    criterion=criterion,
                    value=value,
                )
            else:
                if value is not None:
                    raise ValueError("activity cannot supply a number without a criterion")
                record = self.record_speech(goal, turn["message_id"], turn["text"])
            self.note_later(goal)
            return {"record": record}

    async def goals_revoke(self, arguments):
        """Undo a dated entry after the user says 那次不算. arguments: {record_id}.
        No history is deleted; all counts and conditions are recalculated."""
        arguments_of(arguments, {"record_id"}, required=("record_id",))
        turn = self.turn()
        if not any(w in turn["text"] for w in ("不算", "撤销", "记错")):
            raise ValueError("需要用户说哪次不算")
        async with self.lock:
            goal = self.storage.current()
            self.revoke(goal, arguments["record_id"])
            return {"accepted": True}

    def steps(self, goal):
        return self.db.rows("SELECT * FROM goals_steps WHERE goal_id=? ORDER BY day", (goal["id"],))

    def cancel_steps(self, goal, *, skip=False):
        for step in self.steps(goal):
            if self.context.store.todo(step["todo_id"])["status"] == "pending":
                self.context.store.update_todo(
                    step["todo_id"], TodoPatch(status="cancelled"), from_agent=True
                )
            if skip and step["state"] in {"planned", "held"}:
                self.db.execute("UPDATE goals_steps SET state='skipped' WHERE id=?", (step["id"],))

    def revoke(self, goal, identifier):
        if not goal or not self.db.execute(
            "UPDATE goals_progress SET revoked=1 WHERE id=? AND goal_id=?", (identifier, goal["id"])
        ):
            raise ValueError("record_not_found")
        self.speech.cancel(f"goals:{goal['id']}:complete:")
        self.note_later(goal)

    def record_speech(self, goal, message_id, text):
        steps = [s for s in self.steps(goal) if s["day"] == self.now().date().isoformat()]
        step = steps[0] if steps else None
        record = self.storage.record(
            goal["id"],
            "speech",
            message_id,
            self.now().isoformat(),
            text,
            step_id=step["id"] if step else None,
        )
        if step:
            self.context.store.update_todo(
                step["todo_id"], TodoPatch(status="completed"), from_agent=True
            )
            self.db.execute("UPDATE goals_steps SET state='completed' WHERE id=?", (step["id"],))
        return record

    def ask(self, goal, suffix, kind, text, replies, payload=None, days=1, reactive=False):
        key = f"goals:{goal['id']}:{suffix}"
        self.db.execute(
            "INSERT OR IGNORE INTO goals_questions VALUES(?,?,?,?,?,?,NULL)",
            (key, goal["id"], goal["revision"], kind, dump(payload or {}), dump(replies)),
        )
        moment = self.now()
        end = datetime.combine(moment.date() + timedelta(days=days), time(), moment.tzinfo)
        self.speech.enqueue(
            key, "assistant", "reply" if reactive else "goals", text, replies, moment, end
        )
        return key

    def pending(self, goal):
        if not goal:
            return []
        return self.db.rows(
            "SELECT q.*,s.message_id,s.delivered_at,s.expires_at,s.cancelled FROM goals_questions q "
            "JOIN bot_speech s ON s.key=q.key WHERE q.goal_id=? AND q.revision=? "
            "AND q.answered_by IS NULL AND s.cancelled=0 ORDER BY q.rowid DESC",
            (goal["id"], goal["revision"]),
        )

    async def chat_input(self, bot_id, message_id, text):
        if bot_id != "assistant" or self.stopped:
            return None
        self.is_goal_turn(bot_id, text)
        self.speech.expire(self.now())
        async with self.lock:
            goal = self.storage.current()
            if not goal or goal["status"] in {"achieved", "dropped"}:
                return None
            state = dict(goal["state"])
            # A new user line resumes a silent active goal, but never resumes a pause or wait
            # without the user's explicit choice.
            if goal["status"] == "active":
                state.update(silent=False, misses=0, last_reply=self.now().isoformat())
                goal = self.storage.save(goal, state=state)
            latest = self.db.rows(
                "SELECT id FROM chat_messages WHERE bot_id='assistant' AND author='bot' AND status='ready' ORDER BY rowid DESC LIMIT 1"
            )
            for question in self.pending(goal):
                if not latest or question["message_id"] != latest[0]["id"]:
                    continue
                if not question["message_id"] or self.now() >= local(
                    question["expires_at"], self.now().tzinfo
                ):
                    continue
                if text not in json.loads(question["replies"]):
                    continue
                # Exact replies only, and one durable response per question.
                if self.db.execute(
                    "UPDATE goals_questions SET answered_by=? WHERE key=? AND answered_by IS NULL",
                    (message_id, question["key"]),
                ):
                    self.speech.answered(question["key"])
                    return self.answer_question(goal, question, message_id, text)
            return None

    def answer_question(self, goal, question, message_id, text):
        kind, payload = question["kind"], json.loads(question["payload"])
        state = dict(goal["state"])
        if kind == "number":
            if text == "先不说":
                return {"text": "好，先放着，不追问啦。"}
            if text == "我说个数":
                return {"text": "这次是多少？说个数字就行。"}
            value = payload["values"][text]
            self.storage.record(
                goal["id"],
                "number",
                message_id,
                self.now().isoformat(),
                text,
                step_id=payload.get("step_id"),
                criterion=payload["criterion"],
                value=value,
            )
            met = (
                value >= payload["target"]
                if payload["comparison"] == "gte"
                else value <= payload["target"]
            )
            records = [
                r
                for r in self.storage.records(goal["id"])
                if r["criterion"] == payload["criterion"] and r["value"] is not None
            ]
            latest = records[-2:]
            bad = len(latest) == 2 and all(
                r["value"] < payload["target"]
                if payload["comparison"] == "gte"
                else r["value"] > payload["target"]
                for r in latest
            )
            this_week = week(self.now().date()).isoformat()
            if not met and bad and state.get("number_smaller_week") != this_week:
                state["number_smaller_week"] = this_week
                self.storage.save(goal, state=state)
                return self.smaller_question(
                    goal, message_id, prefix=f"记下啦，这次 {text}。\n**下次** "
                )
            return {"text": f"记下啦，这次 {text}。做完就好，今天到这里～"}
        if kind == "smaller":
            if text == "好":
                state.update(
                    blocker=payload.get("blocker") or payload["title"],
                    next_minutes=payload["minutes"],
                    next_title=payload["title"],
                    blocker_kind="time",
                    blocker_day=None,
                )
                self.storage.save(goal, state=state, bump=True)
                self.speech.cancel(f"goals:{goal['id']}:")
                return {
                    "text": f"好，下次{visible(payload['title'], 18)}，约 {payload['minutes']} 分钟。"
                }
            return {"text": "好，照你定的量来。"}
        if kind == "move":
            if text == "好":
                state["approved_move"] = payload
                self.storage.save(goal, state=state)
                self.create_step(
                    goal,
                    local(payload["due_at"], self.now().tzinfo),
                    payload["minutes"],
                    "按刚才说好的时间",
                )
                return {"text": "好呀，时间已经挪好啦。"}
            return {"text": "好，今天先收起来。"}
        if kind in {"slow", "busy", "weekly", "resume"}:
            if text in ("先歇一周", "下周歇一歇", "再歇一周"):
                self.set_status(goal, "paused", self.now().date() + timedelta(days=7))
                return {"text": "好，先歇一周。到那天再问你。"}
            if text == "改小一点":
                original = goal["profile"]["weekly_target"]
                profile = effective(goal["profile"], state, self.now().date())
                target = max(1, profile["weekly_target"] - 1)
                state.update(
                    week_override={"week": week(self.now().date()).isoformat(), "target": target},
                    silent=False,
                    misses=0,
                )
                self.storage.save(goal, state=state, bump=True)
                self.speech.cancel(f"goals:{goal['id']}:")
                return {"text": f"这周先做 {target} 次，下周回到 {original} 次。"}
            if kind == "weekly":
                return {"text": f"好，下周还是 {goal['profile']['weekly_target']} 次。"}
            if kind == "resume":
                self.storage.save(
                    goal, status="active", state={**state, "silent": False, "misses": 0}, bump=True
                )
                self.speech.cancel(f"goals:{goal['id']}:")
            return {"text": "好，还是这个量，慢慢接上。"}
        if kind == "result":
            if text == "还没出":
                return {"text": "好，等有结果再告诉我。"}
            return {"text": "结果怎么样？说一句就行。"}
        if kind == "blocker_choice":
            choice = next(c for c in goal["profile"]["blocker_choices"] if c["text"] == text)
            state.update(blocker=text, blocker_kind=choice["kind"], blocker_day=None)
            if choice["kind"] == "content":
                state["blocker_day"] = (self.now().date() + timedelta(days=3)).isoformat()
            goal = self.storage.save(goal, state=state)
            if choice["kind"] == "content":
                return None
            if choice["kind"] == "time":
                return self.smaller_question(goal, message_id, prefix="那下次先")
            return self.direct_question(
                goal,
                f"materials:{message_id}",
                "materials_date",
                "好，等材料到了再接着。哪天再问你？",
                ["三天后", "下周", "我自己说"],
            )
        if kind == "materials_date":
            if text == "我自己说":
                return None
            state["pause_reason"] = "materials"
            goal = self.storage.save(goal, state=state)
            days = 3 if text == "三天后" else 7
            self.set_status(goal, "paused", self.now().date() + timedelta(days=days))
            return {"text": "好，先等材料，到那天再问你。"}
        if kind == "result_shortfall":
            if text == "先放下":
                return {"text": self.closed_text(self.set_status(goal, "dropped"))}
            return {"text": "好，下一次是哪天？说一句就行。"}
        if kind == "complete":
            if text == "达成了":
                records = self.storage.records(goal["id"])
                if not all(satisfied(goal["profile"], records)):
                    return {"text": "刚才的记录变了，我们再看一眼。"}
                self.storage.record(
                    goal["id"], "confirmation", message_id, self.now().isoformat(), text
                )
                goal = self.set_status(goal, "achieved")
                self.note_later(goal)
                return {"text": self.closed_text(goal)}
            return {"text": "好，我们照你的实际情况继续。"}
        if text == "有点卡":
            choices = [c["text"] for c in goal["profile"]["blocker_choices"]]
            key = self.ask(
                goal,
                f"blocker-choice:{message_id}",
                "blocker_choice",
                "卡在哪一小段？说一句就行。",
                choices,
            )
            self.bind_direct_question(key)
            return {"text": "卡在哪一小段？说一句就行。", "replies": choices}
        if text in {"做了", "做完了"}:
            self.record_speech(goal, message_id, text)
            return {"text": "记下啦，这一步勾好了。"}
        if text in ("今天也够呛", "没顾上"):
            return {"text": "没顾上也正常，下次再接着。"}
        if text == "明天再说":
            state["blocker_day"] = (self.now().date() + timedelta(days=1)).isoformat()
            state["blocker"] = state.get("blocker") or "改天再聊"
            self.storage.save(goal, state=state)
            return {"text": "好，明天有空再聊。"}
        return {"text": "好，做完勾一下就行～"}

    def direct_question(self, goal, suffix, kind, text, replies, payload=None):
        key = self.ask(goal, suffix, kind, text, replies, payload)
        self.bind_direct_question(key)
        return {"text": text, "replies": replies}

    def smaller_question(self, goal, message_id, prefix=""):
        smaller = goal["profile"]["smaller_step"]
        text = f"{prefix}{visible(smaller['title'], 18)}，约 {smaller['minutes']} 分钟？"
        return self.direct_question(
            goal, f"smaller:{message_id}", "smaller", text, ["好", "照原计划"], smaller
        )

    def closed_text(self, goal):
        records = self.storage.records(goal["id"])
        days = len(
            {
                local(r["happened_at"], self.now().tzinfo).date()
                for r in records
                if r["kind"] in {"app", "speech", "workout", "homework"}
            }
        )
        title = visible(goal["profile"]["title"], 22)
        start = local(goal["created_at"], self.now().tzinfo)
        end = local(goal["state"]["ended_at"], self.now().tzinfo)
        if goal["status"] == "dropped":
            return f"**先放下** {title}\n**走过的路** 实际推进了 {days} 天。\n先放在这里，已经做过的都记着。"
        return (
            f"**做到了** {title}\n**当初为什么** {visible(goal['profile']['why'], 22)}\n"
            f"**走过的路** 推进了 {days} 天\n{start:%Y/%m/%d} 到 {end:%Y/%m/%d}\n这段路记下了，好好歇一下吧。"
        )

    def bind_direct_question(self, key):
        # The caller will write the returned template into the pending Bot reply.
        rows = self.db.rows(
            "SELECT id FROM chat_messages WHERE bot_id='assistant' AND author='bot' AND status='pending' ORDER BY rowid DESC LIMIT 1"
        )
        if rows:
            self.db.execute(
                "UPDATE bot_speech SET message_id=?,delivered_at=?,cancelled=1 WHERE key=?",
                (rows[0]["id"], self.now().isoformat(), key),
            )
            # Direct questions don't consume the shared proactive budget.
            self.db.execute("UPDATE bot_speech SET topic='reply',cancelled=0 WHERE key=?", (key,))

    def set_status(self, goal, status, until=None):
        self.speech.cancel(f"goals:{goal['id']}:")
        self.cancel_steps(goal, skip=True)
        state = dict(goal["state"])
        state.update(ask_on=until.isoformat() if until else None, silent=False, misses=0)
        if status == "waiting":
            state["waiting_since"] = self.now().isoformat()
        if status in {"achieved", "dropped"}:
            state["ended_at"] = self.now().isoformat()
        goal = self.storage.save(goal, status=status, state=state, bump=True)
        self.note_later(goal)
        return goal

    def create_step(self, goal, due, minutes, reason):
        day = due.date().isoformat()
        rows = self.db.rows(
            "SELECT * FROM goals_steps WHERE goal_id=? AND day=?", (goal["id"], day)
        )
        if rows:
            step = rows[0]
            if step["state"] == "held":
                self.context.store.update_todo(
                    step["todo_id"], TodoPatch(status="pending", due_at=due), from_agent=True
                )
                self.db.execute(
                    "UPDATE goals_steps SET state='planned',due_at=? WHERE id=?",
                    (due.isoformat(), step["id"]),
                )
            return
        n = len(counted(self.storage.records(goal["id"]))) + 1
        title = goal["state"].get("next_title") or goal["profile"]["step_title"].replace(
            "{n}", str(n)
        )
        title = f"{visible(title)}（约 {minutes} 分钟）"
        external = f"{goal['id']}:{day}"
        # Deterministic provenance lets Store replay a crash between todo creation and saving
        # goals_steps; dedup alone must not resurrect yesterday's cancelled occurrence.
        source = self.db.rows(
            "SELECT id FROM messages WHERE plugin='goals' AND external_id=?", (external,)
        )
        message_id = source[0]["id"] if source else uid()
        self.db.execute(
            "INSERT OR IGNORE INTO messages(id,plugin,external_id,chat_name,sender,sent_at,text,processed) VALUES(?,'goals',?,?,'',?,?,1)",
            (
                message_id,
                external,
                visible(goal["profile"]["title"], 24),
                self.now().isoformat(),
                title,
            ),
        )
        result = self.context.store.add_todo(
            TodoCreate(
                title=title,
                notes=f"目标「{visible(goal['profile']['title'])}」\n{reason}",
                due_at=due,
                has_time=True,
                source_plugin="goals",
                source_refs=[message_id],
                source_excerpt=title,
                confidence=1,
                created_by="goals",
            )
        )
        self.db.execute(
            "INSERT OR IGNORE INTO goals_steps VALUES(?,?,?,?,?,'planned')",
            (uid(), goal["id"], day, result["todo"]["id"], due.isoformat()),
        )
        state = dict(self.storage.current()["state"])
        state["placement"] = {
            "day": day,
            "reason": reason,
            "due_at": due.isoformat(),
            "minutes": minutes,
        }
        state.pop("next_minutes", None)
        state.pop("next_title", None)
        self.storage.save(self.storage.current(), state=state)

    async def todo_changed(self, identifier):
        if self.stopped:
            return
        async with self.lock:
            self.collect_changes()
            goal = self.storage.current()
            if goal and goal["status"] == "active":
                self.speech.drain(self.now(), entries(self.context, self.now().date()))

    def collect_changes(self):
        goal = self.storage.current()
        if not goal or goal["status"] not in {"active", "waiting"}:
            return
        step_map = {s["todo_id"]: s for s in self.steps(goal)}
        rows = self.db.rows(
            "SELECT rowid AS sequence,* FROM todo_changes WHERE rowid>? ORDER BY rowid",
            (self.context.get("change_cursor", 0),),
        )
        for row in rows:
            step = step_map.get(row["todo_id"])
            if not row["from_agent"]:
                if row["status"] == "completed" and step and goal["status"] == "active":
                    self.storage.record(
                        goal["id"],
                        "app",
                        row["id"],
                        row["created_at"],
                        "你勾好了今天这一步",
                        step_id=step["id"],
                    )
                    self.db.execute(
                        "UPDATE goals_steps SET state='completed' WHERE id=?", (step["id"],)
                    )
                    numeric = next(
                        (c for c in goal["profile"]["criteria"] if c["kind"] == "number"), None
                    )
                    if (
                        numeric
                        and local(row["created_at"], self.now().tzinfo).date() == self.now().date()
                    ):
                        options = [reply["text"] for reply in numeric["replies"]]
                        values = {
                            reply["text"]: reply["value"]
                            for reply in numeric["replies"]
                            if reply["value"] is not None
                        }
                        question = visible(numeric["question"], 30)
                        n = len(counted(self.storage.records(goal["id"])))
                        done, _ = progress(
                            self.storage.records(goal["id"]), self.now().date(), self.now().tzinfo
                        )
                        prefix = (
                            "这周都做完啦～"
                            if done >= goal["profile"]["weekly_target"]
                            else f"第 {n} 次搞定～"
                        )
                        self.ask(
                            goal,
                            f"number:{step['id']}",
                            "number",
                            prefix + "\n" + question,
                            options,
                            {
                                "step_id": step["id"],
                                "criterion": numeric["id"],
                                "target": numeric["target"],
                                "values": values,
                                "comparison": numeric["comparison"],
                            },
                            days=2,
                            reactive=True,
                        )
                    state = dict(self.storage.current()["state"])
                    state.update(misses=0, silent=False)
                    self.storage.save(self.storage.current(), state=state)
                elif row["status"] in {"pending", "deleted", "cancelled"} and step:
                    self.db.execute(
                        "UPDATE goals_progress SET revoked=1 WHERE goal_id=? AND step_id=?",
                        (goal["id"], step["id"]),
                    )
                    self.db.execute(
                        "UPDATE goals_steps SET state='planned' WHERE id=?", (step["id"],)
                    )
            self.context.put("change_cursor", row["sequence"])
        self.collect_external(goal)

    def collect_external(self, goal):
        profile = goal["profile"]
        health = self.context.get_plugin("health") if self.context.get_plugin else None
        if profile["activity"] == "exercise" and health:
            workouts = self.db.rows(
                "SELECT * FROM health_workouts ORDER BY start_at",
            )
            for workout in workouts:
                at = local(workout["start_at"], self.now().tzinfo)
                if (
                    at < local(goal["created_at"], self.now().tzinfo)
                    or at > self.now()
                    or workout["duration_minutes"] < 5
                ):
                    continue
                steps = [s for s in self.steps(goal) if s["day"] == at.date().isoformat()]
                step = steps[0] if steps else None
                self.storage.record(
                    goal["id"],
                    "workout",
                    workout["id"],
                    at.isoformat(),
                    f"手表记了{exercise_name(workout['activity'])} {workout['duration_minutes']:g} 分钟",
                    step_id=step["id"] if step else None,
                )
                if step:
                    self.context.store.update_todo(
                        step["todo_id"], TodoPatch(status="completed"), from_agent=True
                    )
                    self.db.execute(
                        "UPDATE goals_steps SET state='completed' WHERE id=?", (step["id"],)
                    )
        homework = self.context.get_plugin("homework") if self.context.get_plugin else None
        if profile["activity"] == "homework" and homework:
            todos = {t["id"]: t for t in homework.homework()}
            for identifier in profile["homework_ids"]:
                todo = todos.get(identifier)
                changes = self.db.rows(
                    "SELECT * FROM todo_changes WHERE todo_id=? ORDER BY rowid DESC LIMIT 1",
                    (identifier,),
                )
                completed = (
                    todo
                    and todo["status"] == "completed"
                    and changes
                    and not changes[0]["from_agent"]
                )
                if not completed:
                    self.db.execute(
                        "UPDATE goals_progress SET revoked=1 WHERE goal_id=? AND kind='homework' AND ref LIKE ?",
                        (goal["id"], identifier + ":%"),
                    )
                    continue
                at = local(changes[0]["created_at"], self.now().tzinfo)
                if at < local(goal["created_at"], self.now().tzinfo) or at > self.now():
                    continue
                steps = [step for step in self.steps(goal) if step["day"] == at.date().isoformat()]
                step = steps[0] if steps else None
                record = self.storage.record(
                    goal["id"],
                    "homework",
                    identifier + ":" + changes[0]["id"],
                    at.isoformat(),
                    f"已交：{todo['title']}",
                    step_id=step["id"] if step else None,
                )
                if step and not record["revoked"]:
                    self.context.store.update_todo(
                        step["todo_id"], TodoPatch(status="completed"), from_agent=True
                    )
                    self.db.execute(
                        "UPDATE goals_steps SET state='completed' WHERE id=?", (step["id"],)
                    )

    async def follow(self):
        async with self.lock:
            if self.stopped:
                return
            retry = self.context.get("retry", {})
            if retry.get("after") and self.now() < local(retry["after"], self.now().tzinfo):
                return
            try:
                self.tick()
            except Exception:
                count = retry.get("count", 0) + 1
                self.context.put(
                    "retry",
                    {
                        "count": count,
                        "after": (
                            self.now() + timedelta(minutes=min(5 * 2 ** min(count - 1, 8), 360))
                        ).isoformat(),
                    },
                )
                raise
            else:
                self.context.put("retry", {})

    def tick(self):
        self.speech.expire(self.now())
        goal = self.storage.current()
        if goal and self.context.get("note_pending"):
            self.write_note(goal)
            self.context.put("note_pending", False)
        if not goal or goal["status"] in {"achieved", "dropped"}:
            self.speech.drain(self.now(), entries(self.context, self.now().date()))
            return
        now, day = self.now(), self.now().date()
        if goal["status"] in {"paused", "waiting"}:
            if goal["status"] == "waiting" and all(
                satisfied(goal["profile"], self.storage.records(goal["id"]))
            ):
                self.ask(
                    goal,
                    f"complete:{goal['revision']}",
                    "complete",
                    "做到你定的几条啦～确认达成了吗？",
                    ["达成了", "还没有"],
                    days=7,
                )
                self.speech.drain(now, entries(self.context, day))
                return
            if goal["status"] == "waiting" and self.ask_shortfall(goal):
                self.speech.drain(now, entries(self.context, day))
                return
            ask_on = goal["state"].get("ask_on")
            if ask_on and day == date.fromisoformat(ask_on):
                # The same key is used until delivered; once delivered it is never queued anew.
                kind = "resume" if goal["status"] == "paused" else "result"
                self.ask(
                    goal,
                    f"return:{goal['revision']}",
                    kind,
                    (
                        "材料到了吗，接着来吗？"
                        if goal["state"].get("pause_reason") == "materials"
                        else "歇得怎么样，接着来吗？"
                    )
                    if kind == "resume"
                    else goal["profile"]["result_question"],
                    ["接着来", "再歇一周"]
                    if kind == "resume"
                    else goal["profile"]["result_replies"],
                    days=2,
                )
            self.speech.drain(now, entries(self.context, day))
            return
        self.collect_changes()
        goal = self.storage.current()
        state = dict(goal["state"])
        profile = effective(goal["profile"], state, day)
        if (
            profile.get("result_date")
            and profile.get("deadline")
            and day > date.fromisoformat(profile["deadline"])
        ):
            goal = self.set_status(goal, "waiting", date.fromisoformat(profile["result_date"]))
            self.ask(
                goal,
                "deadline-passed",
                "notice",
                f"{visible(profile['title'], 12)}那天过去啦，辛苦了。\n出结果再告诉我～",
                [],
            )
            self.speech.drain(now, entries(self.context, day))
            return
        for step in self.steps(goal):
            if date.fromisoformat(step["day"]) < day and step["state"] in {"planned", "held"}:
                if self.context.store.todo(step["todo_id"])["status"] == "pending":
                    self.context.store.update_todo(
                        step["todo_id"], TodoPatch(status="cancelled"), from_agent=True
                    )
                self.db.execute("UPDATE goals_steps SET state='missed' WHERE id=?", (step["id"],))
                # An answer since the step was created breaks the silence chain.
                if not state.get("last_reply") or local(
                    state["last_reply"], now.tzinfo
                ).date() <= date.fromisoformat(step["day"]):
                    state["misses"] = state.get("misses", 0) + 1
        if state.get("misses", 0) >= 2:
            state["silent"] = True
            goal = self.storage.save(goal, state=state)
            self.cancel_steps(goal)
            self.ask(
                goal,
                f"slow:{state.get('last_reply', goal['created_at'])}",
                "slow",
                "这几天忙吧？要改小一点，还是歇一周？",
                ["改小一点", "先歇一周", "照原计划"],
                days=7,
            )
        else:
            goal = self.storage.save(goal, state=state)
        records = self.storage.records(goal["id"])
        if all(satisfied(profile, records)):
            self.ask(
                goal,
                f"complete:{goal['revision']}",
                "complete",
                "做到你定的几条啦～确认达成了吗？",
                ["达成了", "还没有"],
                days=7,
            )
        elif not state.get("silent"):
            slot = next((s for s in profile["schedule"] if s["weekday"] == day.weekday()), None)
            from core.proactive import DEFAULTS, quiet

            policy = DEFAULTS | self.policy
            if (
                slot
                and now.time() >= time.fromisoformat(policy["quiet_end"])
                and not quiet(now, policy)
            ):
                self.plan_day(goal, slot)
            if (
                state.get("blocker_kind") == "content"
                and state.get("blocker_day")
                and day >= date.fromisoformat(state["blocker_day"])
            ):
                self.ask(
                    goal,
                    f"blocker:{state['blocker_day']}",
                    "blocker",
                    "上次卡住的那一段，现在怎么样？",
                    ["做了", "有点卡", "明天再说"],
                    days=2,
                )
        self.speech.drain(now, entries(self.context, day))
        self.write_note(self.storage.current())

    def plan_day(self, goal, slot):
        day = self.now().date()
        records = self.storage.records(goal["id"])
        done, _ = progress(records, day, self.now().tzinfo)
        profile = effective(goal["profile"], goal["state"], day)
        if done >= profile["weekly_target"]:
            return
        approved = goal["state"].get("approved_move", {})
        clock = slot["time"]
        minutes = goal["state"].get("next_minutes", slot["minutes"])
        if approved.get("day") == day.isoformat():
            clock = local(approved["due_at"], self.now().tzinfo).strftime("%H:%M")
            minutes = approved["minutes"]
        placement = goal["state"].get("placement", {})
        if placement.get("day") == day.isoformat():
            minutes = placement.get("minutes", minutes)
        due, reason = gap(self.context, day, clock, minutes, self.policy)
        if due is None:
            return
        steps = [s for s in self.steps(goal) if s["day"] == day.isoformat()]
        existing = steps[0] if steps else None
        if not existing and due < self.now():
            return
        if existing and existing["state"] in {"completed", "missed", "held", "skipped"}:
            return
        preferred = (
            existing["due_at"]
            if existing
            else datetime.combine(
                day, time.fromisoformat(slot["time"]), self.now().tzinfo
            ).isoformat()
        )
        if due != local(preferred, self.now().tzinfo):
            # A timetable change is a proposal. Silence never accepts the new time.
            if existing:
                self.context.store.update_todo(
                    existing["todo_id"], TodoPatch(status="cancelled"), from_agent=True
                )
                self.db.execute("UPDATE goals_steps SET state='held' WHERE id=?", (existing["id"],))
            else:
                self.create_step(goal, local(preferred, self.now().tzinfo), minutes, "等你选个空档")
                existing = [s for s in self.steps(goal) if s["day"] == day.isoformat()][0]
                self.context.store.update_todo(
                    existing["todo_id"], TodoPatch(status="cancelled"), from_agent=True
                )
                self.db.execute("UPDATE goals_steps SET state='held' WHERE id=?", (existing["id"],))
            proposed = due
            clashes = conflicts(self.context, day, local(preferred, self.now().tzinfo), minutes)
            cause = visible(clashes[0]["title"], 10) if clashes else "原来的时间不合适"
            self.ask(
                goal,
                f"move:{day}",
                "move",
                f"今天 {local(preferred, self.now().tzinfo):%H:%M} 有{cause}，挪到 {proposed:%H:%M}？",
                ["好", "今天跳过"],
                {"day": day.isoformat(), "due_at": proposed.isoformat(), "minutes": minutes},
            )
            return
        if not existing:
            self.create_step(goal, due, minutes, reason)
        busy = self.busy_week(day)
        if busy:
            self.ask(
                goal,
                f"busy:{week(day)}",
                "busy",
                "这周作业和考试比较多，要减一点量吗？",
                ["改小一点", "照原计划", "先歇一周"],
                days=7,
            )
        elif goal["state"].get("misses", 0):
            self.ask(
                goal,
                f"missed:{day}",
                "missed",
                "上次没顾上也正常～今天接着来？",
                ["好", "今天也够呛", "有点卡"],
            )

    def busy_week(self, day):
        homework = self.context.get_plugin("homework") if self.context.get_plugin else None
        monday = week(day)
        tasks = homework.homework() if homework else []
        count = sum(
            t["status"] == "pending"
            and t["due_at"]
            and monday <= local(t["due_at"], self.now().tzinfo).date() < monday + timedelta(days=7)
            for t in tasks
        )
        count += sum(
            "考试" in e.get("title", "")
            for i in range(7)
            for e in entries(self.context, monday + timedelta(days=i))
        )
        return count >= self.config.get("busy_week_threshold", 4)

    def ask_shortfall(self, goal):
        records = self.storage.records(goal["id"])
        since = (
            goal["state"].get("waiting_since")
            or goal["profile"].get("result_date")
            or goal["created_at"]
        )
        numeric = [
            r
            for r in records
            if r["value"] is not None
            and local(r["happened_at"], self.now().tzinfo) > local(since, self.now().tzinfo)
        ]
        if not numeric:
            return False
        checks = satisfied(goal["profile"], records)
        if all(checks):
            return False
        missing = []
        for rule, met in zip(goal["profile"]["criteria"], checks, strict=True):
            if met:
                continue
            values = [
                r["value"]
                for r in records
                if r["criterion"] == rule["id"] and r["value"] is not None
            ]
            if rule["kind"] == "steps":
                difference = max(0, rule["target"] - len(counted(records)))
                detail = f"还差 {difference:g} 次"
            elif values:
                difference = abs(rule["target"] - values[-1])
                detail = f"还差 {difference:g}" if difference else "还需后续记录"
            else:
                detail = "还没有这项记录"
            missing.append(f"{visible(rule['title'], 18)}，{detail}")
        suffix = f"shortfall:{goal['revision']}:{numeric[-1]['id']}"
        key = f"goals:{goal['id']}:{suffix}"
        if not self.db.rows("SELECT 1 FROM goals_questions WHERE key=?", (key,)):
            self.speech.cancel(f"goals:{goal['id']}:shortfall:")
            self.ask(
                goal,
                suffix,
                "result_shortfall",
                "这次差了一点。\n"
                + "\n".join(visible(item, 30) for item in missing)
                + "\n接着准备下一次，还是先放下？",
                ["接着准备", "先放下"],
                days=7,
                reactive=True,
            )
        return True

    def weekly_input(self, day):
        goal = self.storage.current()
        result = week_input(
            goal,
            self.storage.records(goal["id"]) if goal else [],
            day,
            self.now().tzinfo,
            self.steps(goal) if goal else [],
        )
        finished = []
        for row in self.db.rows(
            "SELECT * FROM goals_profiles WHERE status IN ('achieved','dropped') ORDER BY rowid"
        ):
            closed = self.storage.decode(row)
            ended = closed["state"].get("ended_at")
            if ended and week(day) <= local(ended, self.now().tzinfo).date() <= day:
                records = self.storage.records(closed["id"])
                days = len(
                    {
                        local(r["happened_at"], self.now().tzinfo).date()
                        for r in records
                        if r["kind"] in {"app", "speech", "workout", "homework"}
                    }
                )
                finished.append(
                    {
                        "title": closed["profile"]["title"],
                        "status": closed["status"],
                        "ended_at": ended,
                        "days_advanced": days,
                    }
                )
        if finished:
            result = (result or {}) | {"finished": finished}
        return result

    async def weekly_finished(self, monday):
        async with self.lock:
            goal = self.storage.current()
            if (
                goal
                and goal["status"] == "active"
                and not goal["state"].get("silent")
                and week(self.now().date()) == monday
            ):
                self.ask(
                    goal,
                    f"weekly:{monday}",
                    "weekly",
                    "这周记好啦～下周照这个来？",
                    ["照这个来", "改小一点", "下周歇一歇"],
                    days=3,
                )
                self.speech.drain(self.now(), entries(self.context, self.now().date()))

    async def cards(self):
        goal = self.storage.current()
        if not goal or goal["status"] in {"achieved", "dropped"}:
            return [
                {
                    "type": "text",
                    "title": "下一个目标" if goal else "目标",
                    "data": {
                        "headline": "还没有在推进的目标。",
                        "text": "跟助理说一句“这学期我想……”就能开始。",
                        "symbol": "flag",
                    },
                }
            ]
        state = goal["state"]
        profile = effective(goal["profile"], state, self.now().date())
        if goal["status"] in {"paused", "waiting"}:
            title = "先歇一歇" if goal["status"] == "paused" else "等结果"
            when = date.fromisoformat(state["ask_on"])
            return [
                {
                    "type": "text",
                    "title": "这周的目标",
                    "data": {
                        "headline": f"{title} · {when.month}/{when.day} 再聊",
                        "symbol": "moon",
                    },
                }
            ]
        records = self.storage.records(goal["id"])
        done, _ = progress(records, self.now().date(), self.now().tzinfo)
        headline = visible(
            f"{visible(profile['title'], 8)} · {visible(profile['weekly_title'], 12)}（{done}/{profile['weekly_target']}）"
        )
        lines = []
        if state.get("silent"):
            days = [
                local(r["happened_at"], self.now().tzinfo).date()
                for r in records
                if r["kind"] in {"app", "speech", "workout", "homework"}
            ]
            last = max(days) if days else None
            lines.append(
                f"等你回来 · 上次推进 {last.month}/{last.day}" if last else "等你回来 · 还没开始"
            )
        else:
            today = [
                s
                for s in self.steps(goal)
                if s["day"] == self.now().date().isoformat() and s["state"] == "planned"
            ]
            if today:
                placement = state.get("placement", {})
                lines.append(
                    f"{label(local(today[0]['due_at'], self.now().tzinfo))} · {placement.get('reason', '按你定的时间')}"
                )
            else:
                for offset in range(1, 8):
                    day = self.now().date() + timedelta(days=offset)
                    slot = next(
                        (s for s in profile["schedule"] if s["weekday"] == day.weekday()), None
                    )
                    if slot:
                        due, _ = gap(self.context, day, slot["time"], slot["minutes"], self.policy)
                        if due:
                            lines.append(f"下一次在{label(due)}")
                        break
            if state.get("blocker"):
                lines.append(f"卡点：{visible(state['blocker'], 27)}")
            elif profile.get("deadline"):
                days = (date.fromisoformat(profile["deadline"]) - self.now().date()).days
                lines.append(
                    f"离{visible(profile['deadline_label'], 10)}还有 {days} 天"
                    if days >= 0
                    else f"{visible(profile['deadline_label'], 10)}已过 {abs(days)} 天"
                )
            if profile["activity"] == "exercise" and self.context.get_plugin("health"):
                nights = self.db.rows(
                    "SELECT asleep_minutes FROM sleep_sessions WHERE sleep_date=? AND quality='ok'",
                    (self.now().date().isoformat(),),
                )
                if nights and nights[0]["asleep_minutes"] < 360:
                    # 「力量训练 · 第 {n} 次」→「力量训练」: the step's name without its count.
                    name = re.sub(
                        r"[\s·・,，-]*第?\s*\{n\}\s*[次套遍篇章节回组天]?",
                        "",
                        profile["step_title"],
                    )
                    step = visible(name.strip(" ·・-") or "运动", 14)
                    lines = [f"昨晚睡得少，今天的{step}可以轻一点，约 20 分钟"] + lines[:1]
        if any(
            q["message_id"] and self.now() < local(q["expires_at"], self.now().tzinfo)
            for q in self.pending(goal)
        ):
            lines = lines[:2] + ["助理在下面问了你一句"]
        return [
            {
                "type": "text",
                "title": "这周的目标",
                "data": {
                    "headline": headline,
                    "text": "\n".join(f"- {visible(line, 30)}" for line in lines[:3]),
                    "symbol": "flag.checkered",
                },
            }
        ]

    def note_later(self, goal):
        try:
            self.write_note(goal)
        except (OSError, ValueError):
            self.context.put("note_pending", True)

    def write_note(self, goal):
        if not self.context.memory:
            return
        profile = goal["profile"]
        rows = self.storage.records(goal["id"], active=False)
        body = f"# {visible(profile['title'])}\n\n**目标** {profile['title']}\n\n**为什么** {profile['why']}\n\n**过程**\n"
        for row in rows:
            at = local(row["happened_at"], self.now().tzinfo)
            suffix = "（那次不算）" if row["revoked"] else ""
            body += f"- {at:%Y/%m/%d %H:%M} {row['text']}{suffix}\n"
        body += (
            "\n**结果** "
            + {
                "active": "还在推进",
                "paused": "先歇一歇",
                "waiting": "等结果",
                "achieved": "已达成",
                "dropped": "先放下了",
            }[goal["status"]]
        )
        if goal["state"].get("ended_at"):
            body += " · " + local(goal["state"]["ended_at"], self.now().tzinfo).date().isoformat()
        memory = self.context.memory
        title = re.sub(r"[\x00-\x1f/\\:]", " ", profile["title"]).strip()[:60] or "目标"
        month = local(goal["created_at"], self.now().tzinfo).strftime("%Y-%m")
        relative = f"目标/{month} {title}.md"
        previous = goal["state"].get("note_path", f"目标/{goal['id']}.md")
        path = memory.path(relative)
        suffix = 2
        while path.exists() and relative != previous:
            relative = f"目标/{month} {title} ({suffix}).md"
            path = memory.path(relative)
            suffix += 1
        old_path = memory.path(previous)
        moved = path != old_path and old_path.exists()
        if moved:
            path.parent.mkdir(parents=True, exist_ok=True)
            old_path.rename(path)
        if relative != goal["state"].get("note_path"):
            current = self.storage.current()
            if current and current["id"] == goal["id"]:
                self.storage.save(current, state={**current["state"], "note_path": relative})
        if moved:
            self.db.execute("DELETE FROM memory_fts WHERE path=?", (previous,))
            memory.index_one(path)
        from core.daily import keep_notes

        old = path.read_text(encoding="utf-8") if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        content = keep_notes(old, body)
        if old != content:
            memory.save(path, content)
        elif self.context.get("note_pending"):
            memory.index_one(path)

    async def watch(self):
        retry = self.context.get("retry", {})
        return (
            [
                {
                    "key": "delivery",
                    "title": "目标提醒暂时没发出来",
                    "ok_at": None,
                    "hours": 0,
                    "hint": "今天的目标提醒没发出来，我在重试，你不用做什么。",
                    "jobs": ["follow"],
                }
            ]
            if retry.get("count", 0) >= 3
            else []
        )
