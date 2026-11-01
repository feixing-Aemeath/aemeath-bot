from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


TodoStatus = Literal["pending", "completed", "deleted"]
Title = Annotated[str, Field(min_length=1, max_length=500)]
Text = Annotated[str, Field(max_length=50000)]


class TodoCreate(Model):
    title: Title
    notes: Text = ""
    due_at: AwareDatetime | None = None
    has_time: bool = False
    source_plugin: str = Field(min_length=1, max_length=80)
    source_refs: list[str] = Field(min_length=1, max_length=200)
    source_excerpt: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    created_by: str = Field(default="agent", max_length=80)

    @model_validator(mode="after")
    def check_time(self):
        if not self.title.strip():
            raise ValueError("title cannot be blank")
        if self.has_time and self.due_at is None:
            raise ValueError("has_time requires due_at")
        return self


class TodoPatch(Model):
    title: Title | None = None
    notes: Text | None = None
    due_at: AwareDatetime | None = None
    has_time: bool | None = None
    status: TodoStatus | None = None

    @model_validator(mode="after")
    def check_patch(self):
        if not self.model_fields_set:
            raise ValueError("empty patch")
        for key in self.model_fields_set - {"due_at"}:
            if getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        if self.title is not None and not self.title.strip():
            raise ValueError("title cannot be blank")
        return self


class TodoSource(Model):
    """Where one cited message came from, so the App can show the chat and the sender."""

    message_id: str
    plugin: str
    chat_name: str
    sender: str
    sent_at: datetime


class Todo(TodoCreate):
    # Merging duplicates keeps adding provenance, so input limits must not reject stored todos.
    source_plugin: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    # The cited messages that still exist, in source_refs order.
    sources: list[TodoSource] = []
    id: str
    status: TodoStatus
    dedup_key: str
    reminder_id: str | None
    calendar_event_id: str | None
    created_at: datetime
    updated_at: datetime
    sync_status: Literal["pending", "synced"]
    sync_error: str | None


class TodoPage(Model):
    items: list[Todo]
    total: int


class Card(Model):
    type: Literal["list", "stat", "chart", "photo_grid", "text", "todo_list"]
    title: str
    data: dict[str, Any]


class Digest(Model):
    id: str
    date: date
    plugin: str
    summary: str
    created_at: datetime


class DigestPage(Model):
    items: list[Digest]
    total: int


class Run(Model):
    id: str
    job: str
    harness: str | None
    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "succeeded", "failed", "cancelled"]
    error: str | None
    attempt: int


class RunPage(Model):
    items: list[Run]
    total: int


Harness = Literal["claude_code", "codex", "deepseek"]


class AgentModel(Model):
    id: str
    name: str
    default: bool = False


class AgentStatus(Model):
    available: bool
    authentication: Literal["unchecked"] = "unchecked"
    # Models the App may pick for a chat line (ChatSend.model); empty when unknown.
    models: list[AgentModel] = []


class Status(Model):
    online: bool
    platform: str
    apple_mode: Literal["fake", "real"]
    apple_sync_error: str | None = None
    agents: dict[str, AgentStatus]
    last_run: Run | None
    pending_sync: int


class Today(Model):
    date: date
    summary: str
    todos: list[Todo]
    cards: list[Card]


class PluginPatch(Model):
    enabled: bool | None = None
    config: dict[str, Any] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(
            getattr(self, k) is None for k in self.model_fields_set
        ):
            raise ValueError("empty or null patch")
        return self


class PluginMascot(Model):
    """The plugin's sticker (docs/mascot.md): the file name, without .png, of an image in
    frontend/Design/stickers/, and an accent color for its status dot and badges. No sticker,
    or one the App does not have, shows the shared placeholder."""

    sticker: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    accent: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class PluginManifest(Model):
    """Typed manifest.yaml: a wrong value type disables one plugin, not listing or scheduling."""

    id: str
    name: str
    version: str
    icon: str
    description: str
    platforms: list[str]
    permissions: list[str]
    schedule: str | None
    provides: list[str]
    config_schema: dict[str, Any]
    mascot: PluginMascot | None = None


class PluginView(Model):
    id: str
    name: str
    version: str
    icon: str
    description: str
    platforms: list[str]
    permissions: list[str]
    schedule: str | None
    provides: list[str]
    config_schema: dict[str, Any]
    mascot: PluginMascot | None
    enabled: bool
    config: dict[str, Any]
    last_run_at: str | None
    state: Literal["disabled", "enabled", "unsupported", "error"]
    error: str | None


AlarmMinutes = Annotated[int, Field(strict=True, ge=0, le=10080)]


class RuntimeSettings(Model):
    timezone: str = "Asia/Shanghai"
    source_cron: str = "*/30 * * * *"
    extraction_cron: str = "0 7-23/3 * * *"
    knowledge_organize_cron: str = "0 3 * * *"
    dav_max_file_bytes: int = Field(default=500 * 1024 * 1024, strict=True, ge=1)
    dav_max_total_bytes: int = Field(default=20 * 1024**3, strict=True, ge=1)
    confidence_threshold: float = Field(default=0.8, ge=0, le=1)
    # DeepSeek (an API, not a CLI) is in the order only when chosen; jobs can pick it alone.
    harness_order: list[Harness] = Field(
        default_factory=lambda: ["claude_code", "codex"], min_length=1, max_length=3
    )
    job_harness: dict[str, Harness] = Field(default_factory=dict)
    agent_timeout: int = Field(default=300, ge=1, le=3600)
    retry_attempts: int = Field(default=3, ge=1, le=5)
    retry_base_seconds: float = Field(default=2, ge=0, le=300)
    todo_alarm_minutes: list[AlarmMinutes] = Field(default_factory=lambda: [60, 0], max_length=5)
    todo_date_alarm_minutes: int = Field(default=480, strict=True, ge=0, le=1439)

    @model_validator(mode="after")
    def validate_schedule(self):
        zone = ZoneInfo(self.timezone)
        CronTrigger.from_crontab(self.source_cron, timezone=zone)
        CronTrigger.from_crontab(self.extraction_cron, timezone=zone)
        CronTrigger.from_crontab(self.knowledge_organize_cron, timezone=zone)
        if len(set(self.harness_order)) != len(self.harness_order):
            raise ValueError("duplicate harness")
        if len(set(self.todo_alarm_minutes)) != len(self.todo_alarm_minutes):
            raise ValueError("duplicate alarm")
        return self


class SettingsPatch(Model):
    timezone: str | None = None
    source_cron: str | None = None
    extraction_cron: str | None = None
    knowledge_organize_cron: str | None = None
    dav_max_file_bytes: int | None = Field(default=None, strict=True, ge=1)
    dav_max_total_bytes: int | None = Field(default=None, strict=True, ge=1)
    confidence_threshold: float | None = None
    harness_order: list[Harness] | None = None
    job_harness: dict[str, Harness] | None = None
    agent_timeout: int | None = None
    retry_attempts: int | None = None
    retry_base_seconds: float | None = None
    todo_alarm_minutes: list[AlarmMinutes] | None = Field(default=None, max_length=5)
    todo_date_alarm_minutes: int | None = Field(default=None, strict=True, ge=0, le=1439)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(
            getattr(self, k) is None for k in self.model_fields_set
        ):
            raise ValueError("empty or null patch")
        return self


class HealthSample(Model):
    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)
    value: float
    unit: str = Field(max_length=32)
    start_at: AwareDatetime
    end_at: AwareDatetime
    source: str = Field(max_length=128)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_at < self.start_at:
            raise ValueError("end_at before start_at")
        return self


class HealthBatch(Model):
    samples: list[HealthSample] = Field(min_length=1, max_length=1000)


class HealthAccepted(Model):
    accepted: int
    duplicates: int


HealthReportKind = Literal["morning", "weekly"]
HealthReportStatus = Literal["pending", "ready", "failed"]


class HealthReport(Model):
    id: str
    date: date
    kind: HealthReportKind
    status: HealthReportStatus
    title: str | None
    body: str | None
    error: str | None
    harness: str | None
    created_at: datetime
    updated_at: datetime


class HealthReportPage(Model):
    items: list[HealthReport]
    total: int


class JobAccepted(Model):
    job_id: str
    status: Literal["accepted"] = "accepted"


class MessageInput(Model):
    external_id: str = Field(min_length=1, max_length=500)
    chat_name: str = Field(min_length=1, max_length=500)
    sender: str = Field(max_length=500)
    sent_at: AwareDatetime
    text: str = Field(min_length=1, max_length=50000)
    screenshot_path: str | None = None
    priority: bool = False
    flags: list[Literal["self_listed", "self_missing", "self_joined"]] = Field(default_factory=list)
    outgoing: bool = False
    # A conversation-list preview: possibly truncated. The core replaces it with the full
    # message once a later read finds one in the same chat that starts with the same text.
    preview: bool = False


ChatStatus = Literal["ready", "pending", "failed"]


class ChatMessage(Model):
    id: str
    bot_id: str
    author: Literal["me", "bot"]
    text: str
    status: ChatStatus
    error: str | None
    created_at: datetime
    updated_at: datetime


class ChatPage(Model):
    items: list[ChatMessage]
    total: int


class ChatSend(Model):
    text: str = Field(min_length=1, max_length=2000)
    # What the App calls this Bot (characters are named in the App, e.g. 作业Bot).
    bot_name: str | None = Field(default=None, min_length=1, max_length=40)
    # The Agent (and its model) to answer this line first; the usual order without it.
    harness: Harness | None = None
    model: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:-]{1,64}$")

    @model_validator(mode="after")
    def check_text(self):
        if not self.text.strip():
            raise ValueError("text cannot be blank")
        return self


class ChatAccepted(Model):
    message: ChatMessage
    reply: ChatMessage
