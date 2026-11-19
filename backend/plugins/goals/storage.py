"""Goal plans and dated progress, independent of the current Agent."""

import json
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from core.db import dump, uid
from core.models import Model
from core.store import ConflictError

Short = Annotated[str, Field(min_length=1, max_length=80)]
SCHEMA = """
CREATE TABLE IF NOT EXISTS goals_profiles (
  id TEXT PRIMARY KEY, profile TEXT NOT NULL, revision INTEGER NOT NULL,
  status TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS goals_one_open ON goals_profiles((1)) WHERE status IN ('active','paused','waiting');
CREATE TABLE IF NOT EXISTS goals_drafts (
  id TEXT PRIMARY KEY, profile TEXT NOT NULL, goal_id TEXT, revision INTEGER,
  message_id TEXT NOT NULL, created_at TEXT NOT NULL, changes TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS goals_steps (
  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, day TEXT NOT NULL, todo_id TEXT NOT NULL,
  due_at TEXT NOT NULL, state TEXT NOT NULL, UNIQUE(goal_id,day));
CREATE TABLE IF NOT EXISTS goals_progress (
  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL,
  step_id TEXT, happened_at TEXT NOT NULL, text TEXT NOT NULL, criterion TEXT,
  value REAL, revoked INTEGER NOT NULL DEFAULT 0, UNIQUE(goal_id,kind,ref));
CREATE TABLE IF NOT EXISTS goals_questions (
  key TEXT PRIMARY KEY, goal_id TEXT NOT NULL, revision INTEGER NOT NULL,
  kind TEXT NOT NULL, payload TEXT NOT NULL, replies TEXT NOT NULL, answered_by TEXT);
"""


class Slot(Model):
    weekday: int = Field(ge=0, le=6)
    time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    minutes: int = Field(ge=5, le=45)


class NumberReply(Model):
    text: str = Field(min_length=1, max_length=30)
    value: float | None = None

    @model_validator(mode="after")
    def check_value(self):
        if self.text in {"我说个数", "先不说"}:
            if self.value is not None:
                raise ValueError("custom and skip replies cannot supply a value")
        elif self.value is None:
            raise ValueError("a numeric reply requires a value")
        return self


class Criterion(Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    title: Short
    kind: Literal["steps", "number", "result"]
    target: float = Field(gt=0, le=100000)
    comparison: Literal["gte", "lte"] = "gte"
    samples: int = Field(default=1, ge=1, le=1000)
    question: str | None = Field(default=None, min_length=1, max_length=30)
    replies: list[NumberReply] | None = Field(default=None, min_length=2, max_length=4)

    @model_validator(mode="after")
    def defaults(self):
        if self.kind == "number":
            self.question = self.question or f"{self.title[:18]}，这次是多少？"
            if self.replies is None:
                self.replies = [
                    NumberReply(text=f"{self.target:g}", value=self.target),
                    NumberReply(text="我说个数"),
                    NumberReply(text="先不说"),
                ]
        if self.replies and len({r.text for r in self.replies}) != len(self.replies):
            raise ValueError("duplicate reply")
        return self


class BlockerChoice(Model):
    text: str = Field(min_length=1, max_length=30)
    kind: Literal["content", "time", "materials"]


class SmallerStep(Model):
    title: str = Field(default="先做一小步", min_length=1, max_length=30)
    minutes: int = Field(default=15, ge=5, le=45)
    blocker: str | None = Field(default=None, max_length=60)


class Profile(Model):
    title: Short
    why: Short
    deadline: date | None = None
    result_date: date | None = None
    criteria: list[Criterion] = Field(min_length=1, max_length=5)
    schedule: list[Slot] = Field(min_length=1, max_length=7)
    weekly_title: Short
    weekly_target: int = Field(ge=1, le=7)
    step_title: str = Field(min_length=1, max_length=80)
    activity: Literal["study", "exercise", "homework"] = "study"
    blocker_choices: list[BlockerChoice] = Field(
        default_factory=lambda: [
            BlockerChoice(text="看不懂", kind="content"),
            BlockerChoice(text="时间不够", kind="time"),
            BlockerChoice(text="等材料", kind="materials"),
        ],
        min_length=2,
        max_length=3,
    )
    smaller_step: SmallerStep = Field(default_factory=SmallerStep)
    deadline_label: str = Field(default="截止", min_length=1, max_length=10)
    result_question: str = Field(default="结果出来了吗？", min_length=1, max_length=30)
    result_replies: list[Annotated[str, Field(min_length=1, max_length=30)]] = Field(
        default_factory=lambda: ["有结果了", "还没出"], min_length=2, max_length=4
    )
    waiting_triggers: list[Annotated[str, Field(min_length=1, max_length=30)]] = Field(
        default_factory=lambda: ["告一段落了", "结束了"], min_length=1, max_length=3
    )
    homework_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique(self):
        if len({c.text for c in self.blocker_choices}) != len(self.blocker_choices):
            raise ValueError("duplicate blocker choice")
        if len(set(self.result_replies)) != len(self.result_replies):
            raise ValueError("duplicate result reply")
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError("duplicate criterion")
        if len({s.weekday for s in self.schedule}) != len(self.schedule):
            raise ValueError("one step per day")
        if self.weekly_target > len(self.schedule):
            raise ValueError("weekly target exceeds schedule")
        if self.result_date and (not self.deadline or self.result_date <= self.deadline):
            raise ValueError("result date must follow deadline")
        if any(c.kind == "result" for c in self.criteria) and not self.result_date:
            raise ValueError("result criterion needs result_date")
        if set(self.step_title.replace("{n}", "")) & {"{", "}"}:
            raise ValueError("only {n} is supported in step_title")
        return self


class Changes(Model):
    status: Literal["active", "paused", "waiting", "dropped"] | None = None
    ask_on: date | None = None
    blocker: str | None = Field(default=None, max_length=80)
    blocker_day: date | None = None

    @model_validator(mode="after")
    def check_date(self):
        if self.status in {"paused", "waiting"} and self.ask_on is None:
            raise ValueError("a date to ask again is required")
        if self.blocker and self.blocker_day is None:
            raise ValueError("blocker needs a date to ask again")
        return self


class Storage:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("DROP INDEX IF EXISTS goals_one_open")
            conn.execute(
                "CREATE UNIQUE INDEX goals_one_open ON goals_profiles((1)) WHERE status IN ('active','paused','waiting')"
            )

    @staticmethod
    def decode(row):
        if row is None:
            return None
        row = dict(row)
        for key in ("profile", "state"):
            row[key] = json.loads(row[key])
        # Old saved plans keep working with generic defaults; no goal-specific inference.
        if row["profile"].get("activity") == "running":
            row["profile"]["activity"] = "exercise"
        if "blocker_choices" in row["profile"]:
            row["profile"]["blocker_choices"] = [
                {"text": c, "kind": {"时间不够": "time", "等材料": "materials"}.get(c, "content")}
                if isinstance(c, str)
                else c
                for c in row["profile"]["blocker_choices"]
            ]
        row["profile"] = Profile.model_validate(row["profile"]).model_dump(mode="json")
        return row

    def current(self):
        rows = self.db.rows("SELECT * FROM goals_profiles ORDER BY rowid DESC LIMIT 1")
        return self.decode(rows[0]) if rows else None

    def save(self, goal, *, profile=None, status=None, state=None, bump=False):
        revision = goal["revision"] + int(bump)
        if not self.db.execute(
            "UPDATE goals_profiles SET profile=?,status=?,state=?,revision=? WHERE id=? AND revision=?",
            (
                dump(profile or goal["profile"]),
                status or goal["status"],
                dump(goal["state"] if state is None else state),
                revision,
                goal["id"],
                goal["revision"],
            ),
        ):
            raise ConflictError("goal_changed")
        return self.current()

    def records(self, goal_id, *, active=True):
        return self.db.rows(
            "SELECT * FROM goals_progress WHERE goal_id=?"
            + (" AND revoked=0" if active else "")
            + " ORDER BY happened_at,rowid",
            (goal_id,),
        )

    def record(
        self, goal_id, kind, ref, happened_at, text, *, step_id=None, criterion=None, value=None
    ):
        identifier = uid()
        self.db.execute(
            "INSERT OR IGNORE INTO goals_progress"
            "(id,goal_id,kind,ref,step_id,happened_at,text,criterion,value) VALUES(?,?,?,?,?,?,?,?,?)",
            (identifier, goal_id, kind, ref, step_id, happened_at, text, criterion, value),
        )
        return self.db.rows(
            "SELECT * FROM goals_progress WHERE goal_id=? AND kind=? AND ref=?",
            (goal_id, kind, ref),
        )[0]
