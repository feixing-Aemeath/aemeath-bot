"""One accounting calculation for the App and weekly review. No message text is read."""

from datetime import UTC, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

# Message extraction sources, including the user-mediated inbox without a manifest.
MESSAGE_SOURCES = frozenset({"qq_screen", "wechat_screen", "feishu", "sms", "mail", "share"})
# What the assistant read by itself. A todo that exists only because the user forwarded the
# message was not caught: counting it would raise the caught share exactly when reading failed.
AUTOMATIC_SOURCES = MESSAGE_SOURCES - {"share"}
AGENT_CREATORS = frozenset({"agent", "claude_code", "codex", "deepseek"})


def extraction_stats(store, weeks=4, *, now=None, monday=None):
    if not 1 <= weeks <= 26:
        raise ValueError("invalid_weeks")
    zone = ZoneInfo(store.settings.timezone)
    now = (now or datetime.now(zone)).astimezone(zone)
    monday = monday or now.date() - timedelta(days=now.weekday())
    starts = [monday - timedelta(weeks=i) for i in range(weeks)]
    first = datetime.combine(starts[-1], time(), zone)
    end = datetime.combine(monday + timedelta(days=7), time(), zone)
    as_of = now.astimezone(UTC)

    def bucket(value):
        instant = datetime.fromisoformat(value)
        when = instant.astimezone(zone)
        if first <= when < end and instant.astimezone(UTC) <= as_of:
            return (when.date() - timedelta(days=when.weekday())).isoformat()
        return None

    result = {
        day.isoformat(): {
            "week_start": day.isoformat(),
            "caught": 0,
            "with_due": 0,
            "late": 0,
            "median_lead_hours": None,
            "missed": 0,
            "not_a_todo": 0,
            "recall": None,
        }
        for day in starts
    }
    leads = {day: [] for day in result}
    with store.db.connect() as conn:
        # A single snapshot keeps counts coherent with concurrent inbox/todo writes.
        conn.execute("BEGIN")
        todos = conn.execute(
            "SELECT id,source_plugin,created_by,created_at,due_at FROM todos"
        ).fetchall()
        changes = conn.execute(
            "SELECT todo_id,created_at FROM todo_changes WHERE reason='not_a_todo' "
            "AND from_agent=0 AND status IN ('deleted','cancelled')"
        ).fetchall()
        missed = conn.execute("SELECT created_at FROM extraction_missed").fetchall()
    agents = {
        t["id"]
        for t in todos
        if t["created_by"] in AGENT_CREATORS
        and AUTOMATIC_SOURCES.intersection(t["source_plugin"].split(","))
        and not {"watchdog", "goals", "test", "tests"}.intersection(t["source_plugin"].split(","))
    }
    for todo in todos:
        if todo["id"] not in agents or (key := bucket(todo["created_at"])) is None:
            continue
        row = result[key]
        row["caught"] += 1
        if todo["due_at"]:
            row["with_due"] += 1
            lead = (
                datetime.fromisoformat(todo["due_at"]) - datetime.fromisoformat(todo["created_at"])
            ).total_seconds() / 3600
            if lead < 0:
                row["late"] += 1
            else:
                leads[key].append(lead)
    for event in missed:
        if key := bucket(event["created_at"]):
            result[key]["missed"] += 1
    seen = set()
    for change in changes:
        key = bucket(change["created_at"])
        identity = (key, change["todo_id"])
        if key and change["todo_id"] in agents and identity not in seen:
            result[key]["not_a_todo"] += 1
            seen.add(identity)
    for key, row in result.items():
        row["median_lead_hours"] = median(leads[key]) if leads[key] else None
        total = row["caught"] + row["missed"]
        row["recall"] = row["caught"] / total if total else None
    return {"weeks": list(result.values())}
