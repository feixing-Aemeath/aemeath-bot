"""Server-side history and counts, using only synthetic conversations and runs."""

from datetime import UTC, datetime, timedelta

import pytest

from core.db import Database
from tests.test_api import client_for


def chat_line(service, identifier, timestamp, bot="assistant", author="me"):
    service.db.execute(
        "INSERT INTO chat_messages(id,bot_id,author,text,status,error,message_id,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (identifier, bot, author, identifier, "ready", None, None, timestamp, timestamp),
    )


def test_chat_history_reaches_beyond_the_latest_100_and_reports_the_next_day(service):
    for index in range(125):
        chat_line(service, f"today-{index}", "2026-11-04T02:00:00+00:00")
    chat_line(service, "yesterday", "2026-11-03T15:59:59+00:00")
    chat_line(service, "last-week", "2026-10-28T01:00:00+00:00")
    chat_line(service, "other-bot", "2026-11-03T15:00:00+00:00", bot="knowledge")
    with client_for(service) as client:
        path = "/api/v1/bots/assistant/messages"
        first = client.get(path, params={"limit": 100}).json()
        assert len(first["items"]) == 100 and first["total"] == 127
        assert first["has_more"] and first["earlier_date"] == "2026-11-04"
        second = client.get(path, params={"limit": 100, "before": first["next_before"]}).json()
        assert len(second["items"]) == 27
        assert {r["id"] for r in first["items"]}.isdisjoint(r["id"] for r in second["items"])
        assert second["total"] == 127 and not second["has_more"]
        assert second["next_before"] is None and second["earlier_date"] is None

        # A local midnight is an exclusive time boundary; gaps need no day-by-day probing.
        older = client.get(path, params={"before": "2026-11-04T00:00:00+08:00", "limit": 1}).json()
        assert [r["id"] for r in older["items"]] == ["yesterday"]
        assert older["earlier_date"] == "2026-10-28"
        default = client.get(path).json()
        offset = client.get(path, params={"limit": 1, "offset": 1}).json()
        assert len(default["items"]) == 50
        assert offset["items"][0]["id"] == default["items"][1]["id"]


def test_chat_cursor_preserves_same_timestamp_order_when_new_lines_arrive(service):
    timestamp = "2026-11-04T02:00:00+00:00"
    chat_line(service, "user", timestamp)
    chat_line(service, "reply", timestamp, author="bot")
    chat_line(service, "next-user", timestamp)
    with client_for(service) as client:
        path = "/api/v1/bots/assistant/messages"
        page = client.get(path, params={"limit": 1}).json()
        assert page["items"][0]["id"] == "next-user"
        chat_line(service, "arriving", "2026-11-04T03:00:00+00:00")
        chat_line(service, "same-time-arriving", timestamp)
        seen = ["next-user"]
        while page["has_more"]:
            page = client.get(path, params={"limit": 1, "before": page["next_before"]}).json()
            seen.extend(row["id"] for row in page["items"])
        assert seen == ["next-user", "reply", "user"]
        empty = client.get(path, params={"before": "2000-01-01T00:00:00Z"}).json()
        assert empty["items"] == [] and empty["total"] == 5 and not empty["has_more"]


def test_chat_next_day_uses_the_configured_timezone(service):
    service.db.put("settings", {"timezone": "America/New_York"})
    chat_line(service, "latest", "2026-11-04T05:10:00+00:00")
    chat_line(service, "previous-day", "2026-11-04T04:59:59+00:00")
    with client_for(service) as client:
        page = client.get("/api/v1/bots/assistant/messages", params={"limit": 1}).json()
        assert page["earlier_date"] == "2026-11-03"


def test_digest_history_keeps_date_and_plugin_order_and_filters(service):
    for plugin in ("qq_screen", "feishu", "homework"):
        for day in ("2026-11-04", "2026-11-03", "2026-10-28"):
            service.store.digest(day, plugin, "合成摘要")
    with client_for(service) as client:
        path = "/api/v1/digests"
        page = client.get(path, params={"limit": 2}).json()
        seen = list(page["items"])
        assert page["earlier_date"] == "2026-11-04"
        while page["has_more"]:
            page = client.get(path, params={"limit": 2, "before": page["next_before"]}).json()
            seen.extend(page["items"])
        assert [(r["date"], r["plugin"]) for r in seen] == [
            (day, plugin)
            for day in ("2026-11-04", "2026-11-03", "2026-10-28")
            for plugin in ("feishu", "homework", "qq_screen")
        ]
        filtered = client.get(
            path, params={"plugin": "qq_screen", "before": "2026-11-04", "limit": 1}
        ).json()
        assert filtered["total"] == 3
        assert filtered["items"][0]["date"] == "2026-11-03"
        assert filtered["earlier_date"] == "2026-10-28"
        next_page = client.get(
            path,
            params={"plugin": "qq_screen", "before": filtered["next_before"], "limit": 1},
        ).json()
        assert next_page["items"][0]["date"] == "2026-10-28"
        exact = client.get(path, params={"date": "2026-11-03", "limit": 1}).json()
        assert exact["total"] == 3 and exact["has_more"]


def test_health_report_history_pages_both_kinds_and_preserves_filters(service):
    timestamp = "2026-11-04T02:00:00+00:00"
    for day in ("2026-11-04", "2026-11-03", "2026-10-28"):
        for kind in ("morning", "weekly"):
            service.db.execute(
                "INSERT INTO health_reports(id,date,kind,status,created_at,updated_at) "
                "VALUES(?,?,?,'ready',?,?)",
                (f"{day}-{kind}", day, kind, timestamp, timestamp),
            )
    with client_for(service) as client:
        path = "/api/v1/health/reports"
        page = client.get(path, params={"limit": 1}).json()
        seen = list(page["items"])
        while page["has_more"]:
            page = client.get(path, params={"limit": 1, "before": page["next_before"]}).json()
            seen.extend(page["items"])
        assert [r["id"] for r in seen] == [
            f"{day}-{kind}"
            for day in ("2026-11-04", "2026-11-03", "2026-10-28")
            for kind in ("morning", "weekly")
        ]
        assert all("input_json" not in row for row in seen)
        filtered = client.get(
            path,
            params={"kind": "morning", "status": "ready", "before": "2026-11-04", "limit": 1},
        ).json()
        assert filtered["total"] == 3 and filtered["earlier_date"] == "2026-10-28"
        assert filtered["items"][0]["id"] == "2026-11-03-morning"


def add_run(service, identifier, timestamp, job="extract_todos", status="succeeded", attempt=1):
    service.db.execute(
        "INSERT INTO runs VALUES(?,?,?, ?,?,?,NULL,?)",
        (identifier, job, "codex", timestamp, timestamp, status, attempt),
    )


def test_runs_count_all_successes_in_a_local_day_without_downloading_them(service):
    midnight = datetime(2026, 11, 3, 16, tzinfo=UTC)
    for index in range(60):
        add_run(service, f"success-{index:02d}", (midnight + timedelta(minutes=index)).isoformat())
    add_run(service, "failed-attempt", midnight.isoformat(), status="failed")
    add_run(service, "cancelled", midnight.isoformat(), status="cancelled")
    add_run(service, "running", midnight.isoformat(), status="running")
    add_run(service, "different-job", midnight.isoformat(), job="chat:assistant")
    add_run(service, "yesterday", (midnight - timedelta(microseconds=1)).isoformat())
    add_run(service, "tomorrow", (midnight + timedelta(days=1)).isoformat())
    with client_for(service) as client:
        response = client.get(
            "/api/v1/runs",
            params={
                "job": "extract_todos",
                "status": "succeeded",
                "since": "2026-11-04T00:00:00+08:00",
                "until": "2026-11-05T00:00:00+08:00",
                "limit": 1,
            },
        )
        assert response.status_code == 200
        assert response.json()["total"] == 60
        assert [r["id"] for r in response.json()["items"]] == ["success-59"]
        assert len(client.get("/api/v1/runs").json()["items"]) == 50
        assert client.get("/api/v1/runs").json()["total"] == 66


def test_run_filters_are_independent_and_total_ignores_paging(service):
    add_run(service, "a", "2026-11-03T15:00:00+00:00")
    add_run(service, "b", "2026-11-04T01:00:00+00:00", status="failed")
    add_run(service, "c", "2026-11-04T02:00:00+00:00", job="chat:assistant")
    add_run(service, "d", "2026-11-04T03:00:00+00:00", attempt=2)
    with client_for(service) as client:
        for params, expected in (
            ({"job": "extract_todos"}, ["d", "b", "a"]),
            ({"status": "succeeded"}, ["d", "c", "a"]),
            ({"since": "2026-11-04T01:00:00Z"}, ["d", "c", "b"]),
            ({"until": "2026-11-04T02:00:00Z"}, ["b", "a"]),
            ({"job": "missing"}, []),
        ):
            page = client.get("/api/v1/runs", params=params).json()
            assert [r["id"] for r in page["items"]] == expected
            assert page["total"] == len(expected)
        page = client.get(
            "/api/v1/runs", params={"job": "extract_todos", "limit": 1, "offset": 1}
        ).json()
        assert page["items"][0]["id"] == "b" and page["total"] == 3


@pytest.mark.parametrize("path", ["bots/assistant/messages", "digests", "health/reports"])
def test_history_rejects_invalid_boundaries_and_mixed_paging(service, path):
    with client_for(service) as client:
        for params in (
            {"before": "not-a-cursor"},
            {"before": ""},
            {"before": "h1.invalid"},
            {"before": "x" * 2049},
            {"before": "2026-11-04", "offset": 1},
            {"limit": 0},
            {"limit": 201},
            {"offset": -1},
        ):
            response = client.get("/api/v1/" + path, params=params)
            assert response.status_code == 422, (params, response.text)
        if path.startswith("bots"):
            for value in ("2026-11-04", "2026-11-04T00:00:00"):
                assert client.get("/api/v1/" + path, params={"before": value}).status_code == 422


def test_history_cursors_cannot_be_reused_with_another_bot_or_filter(service):
    for index in range(2):
        chat_line(service, str(index), "2026-11-04T02:00:00+00:00")
        service.store.digest(f"2026-11-0{index + 1}", "feishu", "合成摘要")
    with client_for(service) as client:
        chat = client.get("/api/v1/bots/assistant/messages", params={"limit": 1}).json()
        digest = client.get("/api/v1/digests", params={"limit": 1}).json()
        for path, params in (
            ("bots/knowledge/messages", {"before": chat["next_before"]}),
            ("digests", {"before": chat["next_before"]}),
            ("digests", {"before": digest["next_before"], "plugin": "feishu"}),
        ):
            assert client.get("/api/v1/" + path, params=params).status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"since": "2026-11-04T00:00:00"},
        {"until": "2026-11-04"},
        {"since": "invalid"},
        {"since": "2026-11-05T00:00:00Z", "until": "2026-11-04T00:00:00Z"},
        {"since": "2026-11-04T00:00:00Z", "until": "2026-11-04T00:00:00Z"},
        {"status": "ready"},
    ],
)
def test_runs_reject_invalid_filters(service, params):
    with client_for(service) as client:
        assert client.get("/api/v1/runs", params=params).status_code == 422


def test_history_indexes_migrate_idempotently_and_bound_queries(service):
    db = service.db
    indexes = (
        "runs_started",
        "runs_job_status_started",
        "digests_history",
        "digests_plugin_history",
        "health_reports_history",
    )
    for name in indexes:
        db.execute(f"DROP INDEX IF EXISTS {name}")
    add_run(service, "old-run", "2026-11-04T02:00:00+00:00")
    db.execute("PRAGMA user_version=5")
    Database(db.path)
    Database(db.path)
    assert db.rows("PRAGMA user_version")[0]["user_version"] == 10
    assert db.rows("SELECT id FROM runs") == [{"id": "old-run"}]
    names = {row["name"] for row in db.rows("SELECT name FROM sqlite_master WHERE type='index'")}
    assert set(indexes) <= names
    for sql, args, index in (
        (
            "SELECT * FROM chat_messages WHERE bot_id=? AND (created_at,rowid)<(?,?) "
            "ORDER BY created_at DESC,rowid DESC LIMIT 101",
            ("assistant", "2026-11-04T02:00:00+00:00", 100),
            "chat_messages_bot",
        ),
        (
            "SELECT count(*) FROM runs WHERE job=? AND status=? AND started_at>=? AND started_at<?",
            ("extract_todos", "succeeded", "2026-11-03", "2026-11-05"),
            "runs_job_status_started",
        ),
        ("SELECT * FROM runs ORDER BY started_at DESC,id DESC LIMIT 50", (), "runs_started"),
        (
            "SELECT * FROM digests WHERE plugin=? AND date<? ORDER BY date DESC,plugin LIMIT 101",
            ("feishu", "2026-11-04"),
            "digests_plugin_history",
        ),
    ):
        plan = " ".join(row["detail"] for row in db.rows("EXPLAIN QUERY PLAN " + sql, args))
        assert index in plan and "TEMP B-TREE" not in plan, plan


def test_successful_extraction_count_excludes_scheduler_and_empty_polls(service):
    for harness in (None, "codex", None):
        run = service.db.start_run("extract_todos", harness)
        service.db.finish_run(run)
    with client_for(service) as client:
        page = client.get(
            "/api/v1/runs", params={"job": "extract_todos", "status": "succeeded", "limit": 1}
        ).json()
        assert page["total"] == 1 and page["items"][0]["harness"] == "codex"
        assert client.get("/api/v1/runs", params={"job": "extract_todos"}).json()["total"] == 3
