import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.api import create_app
from core.models import MessageInput
from tests.conftest import RecordingAppleBridge, candidate


def client_for(service):
    return TestClient(
        create_app(service=service),
        headers={"Authorization": "Bearer " + service.settings.token.get_secret_value()},
    )


def test_every_route_contract_and_errors(seeded):
    seeded.store.add_todo(candidate(seeded))
    todo_id = seeded.store.todos()["items"][0]["id"]
    with client_for(seeded) as client:
        for path in (
            "status",
            "today",
            "todos",
            "digests",
            "runs",
            "plugins",
            "settings",
            "plugins/qq_screen/cards",
        ):
            response = client.get("/api/v1/" + path)
            assert response.status_code == 200, response.text
        status = client.get("/api/v1/status").json()
        assert status["apple_mode"] == "fake"
        # The models a chat line may pick: Claude Code's aliases, Codex's as Codex lists them.
        claude = status["agents"]["claude_code"]["models"]
        assert [m["id"] for m in claude] == ["fable", "opus", "sonnet"]
        assert isinstance(status["agents"]["codex"]["models"], list)
        assert client.get("/api/v1/todos?status=wrong").status_code == 422
        assert client.get("/api/v1/todos?limit=201").status_code == 422
        assert client.get("/api/v1/digests?date=invalid").status_code == 422
        assert (
            client.patch(f"/api/v1/todos/{todo_id}", json={"status": "completed"}).status_code
            == 200
        )
        assert client.patch(f"/api/v1/todos/{todo_id}", json={"title": None}).status_code == 422
        assert client.patch("/api/v1/todos/missing", json={"title": "a"}).status_code == 404
        assert client.patch("/api/v1/plugins/qq_screen", json={"enabled": True}).status_code == 409
        assert (
            client.patch(
                "/api/v1/plugins/feishu", json={"config": {"token": "do-not-store"}}
            ).status_code
            == 422
        )
        assert (
            client.patch(
                "/api/v1/plugins/feishu", json={"config": {"whitelist": ["chat"]}}
            ).status_code
            == 200
        )
        assert client.post("/api/v1/jobs/no-such-job/run").status_code == 404
        assert client.post("/api/v1/jobs/memory_index/run").status_code == 202
        assert client.patch("/api/v1/settings", json={"source_cron": "invalid"}).status_code == 422
        assert (
            client.patch("/api/v1/settings", json={"timezone": "Nonexistent/Zone"}).status_code
            == 422
        )
        assert (
            client.patch("/api/v1/settings", json={"confidence_threshold": 0.9}).json()[
                "confidence_threshold"
            ]
            == 0.9
        )
        health = {
            "samples": [
                {
                    "id": "one",
                    "type": "steps",
                    "value": 42,
                    "unit": "count",
                    "source": "phone",
                    "start_at": "2026-10-11T01:00:00Z",
                    "end_at": "2026-10-11T02:00:00Z",
                }
            ]
        }
        assert client.post("/api/v1/health/samples", json=health).json()["accepted"] == 1
        assert client.post("/api/v1/health/samples", json=health).json()["duplicates"] == 1
        assert client.delete("/api/v1/plugins/feishu").status_code == 204
        assert client.get("/api/v1/plugins/feishu/cards").status_code == 404


def test_alarm_settings_resync_existing_todos(seeded, tmp_path):
    bridge = RecordingAppleBridge(tmp_path / "apple")
    seeded.apple.bridge = bridge
    timed = seeded.store.add_todo(candidate(seeded))["todo"]["id"]
    dated = seeded.store.add_todo(
        candidate(seeded, title="日期任务", has_time=False, due_at="2026-10-12T00:00:00+08:00")
    )["todo"]["id"]
    with client_for(seeded) as client:
        defaults = client.get("/api/v1/settings").json()
        assert defaults["todo_alarm_minutes"] == [60, 0]
        assert defaults["todo_date_alarm_minutes"] == 480
        assert client.patch(f"/api/v1/todos/{timed}", json={"title": "定时任务"}).status_code == 200
        assert seeded.db.rows("SELECT * FROM outbox") == []
        bridge.calls.clear()
        settings = {"todo_alarm_minutes": [30, 0], "todo_date_alarm_minutes": 540}
        response = client.patch("/api/v1/settings", json=settings)
        assert response.status_code == 200
        assert all(response.json()[key] == value for key, value in settings.items())
        assert len(bridge.calls) == 4
        # The reminders ring; the timed todo's calendar copy stays silent.
        assert [(r, p["alarms"]) for r, _, p in bridge.calls if p["client_id"] == timed] == [
            ("reminders", [30, 0]),
            ("calendar", []),
        ]
        assert all(
            p["alarms"] == ([-540] if r == "reminders" else [])
            for r, _, p in bridge.calls
            if p["client_id"] == dated
        )
        assert seeded.store.todo(dated)["sync_status"] == "synced"
        bridge.calls.clear()
        client.patch("/api/v1/settings", json=settings)
        client.patch("/api/v1/settings", json={"confidence_threshold": 0.9})
        assert bridge.calls == []
        client.patch("/api/v1/settings", json={"todo_alarm_minutes": []})
        assert all(p["alarms"] == [] for _, _, p in bridge.calls if p["client_id"] == timed)


@pytest.mark.parametrize(
    "patch",
    [
        {"todo_alarm_minutes": [-1]},
        {"todo_alarm_minutes": [0, 0]},
        {"todo_alarm_minutes": [True]},
        {"todo_alarm_minutes": [1.5]},
        {"todo_alarm_minutes": [10081]},
        {"todo_alarm_minutes": [0, 1, 2, 3, 4, 5]},
        {"todo_date_alarm_minutes": -1},
        {"todo_date_alarm_minutes": 1440},
        {"todo_date_alarm_minutes": None},
    ],
)
def test_alarm_settings_validation(service, patch):
    with client_for(service) as client:
        assert client.patch("/api/v1/settings", json=patch).status_code == 422
        assert client.get("/api/v1/settings").json()["todo_alarm_minutes"] == [60, 0]


def test_alarm_settings_failure_stays_in_outbox(seeded, tmp_path):
    bridge = RecordingAppleBridge(tmp_path / "apple")
    seeded.apple.bridge = bridge
    todo_id = seeded.store.add_todo(candidate(seeded))["todo"]["id"]

    class Unavailable:
        async def call(self, *_args):
            raise RuntimeError("apple_unavailable")

    with client_for(seeded) as client:
        client.patch(f"/api/v1/todos/{todo_id}", json={"title": "定时任务"})
        seeded.apple.bridge = Unavailable()
        assert (
            client.patch("/api/v1/settings", json={"todo_alarm_minutes": [20]}).status_code == 200
        )
        assert seeded.store.todo(todo_id)["sync_status"] == "pending"
        assert seeded.db.rows("SELECT * FROM outbox")
        seeded.apple.bridge = bridge
        client.patch("/api/v1/settings", json={"todo_alarm_minutes": [20]})
        assert seeded.store.todo(todo_id)["sync_status"] == "synced"
        reminders = [p for r, _, p in bridge.calls if r == "reminders"]
        assert reminders[-1]["alarms"] == [20]


def test_auth_protects_http_mcp_and_schema(service):
    with TestClient(create_app(service=service)) as client:
        for path in ("/api/v1/status", "/mcp", "/openapi.json", "/internal/tools"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_openapi_exact_snapshot(service):
    expected = json.loads(
        (Path(__file__).resolve().parents[2] / "docs" / "openapi.json").read_text(encoding="utf-8")
    )
    app = create_app(service=service)
    assert app.openapi() == expected
    assert app.openapi()["security"] == [{"BearerAuth": []}]


def test_compact_digest_date_is_normalized(service):
    with client_for(service) as client:
        written = client.post(
            "/internal/tools/digest_write",
            json={"date": "20261012", "plugin": "feishu", "summary": "紧凑日期格式"},
        ).json()
        assert written["isError"] is False
        response = client.get("/api/v1/digests")
        assert response.status_code == 200
        assert [item["date"] for item in response.json()["items"]] == ["2026-10-12"]
        assert client.get("/api/v1/digests?date=2026-10-12").json()["total"] == 1
        rejected = client.post(
            "/internal/tools/digest_write",
            json={"date": "2026/10/12", "plugin": "feishu", "summary": "无效日期"},
        ).json()
        assert rejected["isError"] is True


def test_todo_with_many_merged_sources_is_served(service):
    reminders = [
        MessageInput(
            external_id=f"reminder-{index}",
            chat_name="学校",
            sender="机器人",
            sent_at=datetime(2026, 10, 11, 1, tzinfo=UTC),
            text="周五18点前提交周报",
        )
        for index in range(201)
    ]
    service.store.ingest("feishu", reminders, ["学校"])
    refs = [row["id"] for row in service.db.rows("SELECT id FROM messages")]
    for chunk in (refs[:200], refs[200:]):
        service.store.add_todo(
            candidate(
                service, "feishu", title="提交周报", source_refs=chunk, source_excerpt="提交周报"
            )
        )
    with client_for(service) as client:
        page = client.get("/api/v1/todos")
        assert page.status_code == 200
        todo = page.json()["items"][0]
        assert len(todo["source_refs"]) == 201
        patched = client.patch(f"/api/v1/todos/{todo['id']}", json={"status": "completed"})
        assert patched.status_code == 200


def test_internal_bridge_and_real_mcp_http(seeded):
    with client_for(seeded) as client:
        tools = client.get("/internal/tools").json()
        assert len(tools) == 12
        result = client.post("/internal/tools/messages_get_unprocessed", json={}).json()
        assert result["isError"] is False
        assert "screenshot_path" not in json.dumps(result)
        headers = {"Accept": "application/json, text/event-stream"}
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "offline-tests", "version": "1"},
                },
            },
        )
        assert response.status_code == 200, response.text
        response = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert len(response.json()["result"]["tools"]) == 12
        response = client.post(
            "/internal/tools/todos_add", json={"todo": candidate(seeded).model_dump(mode="json")}
        )
        assert response.json()["isError"] is False
        assert seeded.store.todos()["total"] == 1


def test_todo_responses_carry_their_sources(seeded):
    seeded.store.add_todo(candidate(seeded, due_at="2026-10-01T18:00:00+08:00"))  # overdue
    with client_for(seeded) as client:
        todo = client.get("/api/v1/todos").json()["items"][0]
        assert todo["sources"][0]["chat_name"] == "学校"
        assert todo["sources"][0]["sender"] == "老师"
        assert client.get("/api/v1/today").json()["todos"][0]["sources"] == todo["sources"]
        patched = client.patch(f"/api/v1/todos/{todo['id']}", json={"title": "改名"}).json()
        assert patched["sources"] == todo["sources"]


def test_bridge_tells_the_agent_why_a_tool_failed(seeded):
    row = seeded.store.unprocessed("qq_screen")[0]
    todo = {
        "title": "提交报名表",
        "source_plugin": "qq_screen",
        "source_refs": [row["id"]],
        "source_excerpt": "请在明天18点前提交报名表\n另一条消息",  # joined from two messages
        "confidence": 0.95,
    }
    with client_for(seeded) as client:
        result = client.post("/internal/tools/todos_add", json={"todo": todo}).json()
    assert result["isError"] is True
    assert "source_excerpt_mismatch" in result["content"][0]["text"]
