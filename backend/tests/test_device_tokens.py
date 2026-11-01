"""Device tokens: one per device, scoped, revocable, stored as hashes only."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core.api import create_app
from core.tokens import PREFIX, DeviceTokens, required_scopes
from scripts import tokens as cli
from tests.test_remote_mcp import ACCEPT, INITIALIZE, REMOTE, post, service_with

MAIN = "test-token-000000000000000000000000"
SAMPLE = {
    "samples": [
        {
            "id": "s1",
            "type": "step_count",
            "value": 120,
            "unit": "count",
            "start_at": "2026-10-12T08:00:00+08:00",
            "end_at": "2026-10-12T08:10:00+08:00",
            "source": "示例用户的iPhone",
        }
    ]
}


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


def database_bytes(service):
    folder = service.settings.data_dir
    return b"".join(path.read_bytes() for path in folder.glob("assistant.sqlite3*"))


def test_only_the_hash_is_stored(service):
    token, view = service.tokens.create("学校电脑", ["mcp"])
    assert token.startswith(PREFIX) and len(token) >= len(PREFIX) + 43
    assert token not in str(view) and set(view) == {
        "id", "name", "scopes", "hint", "created_at", "expires_at", "last_used_at",
        "revoked_at", "status", "reader",
    }  # fmt: skip
    row = service.db.rows("SELECT * FROM device_tokens")[0]
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(row) and token[len(PREFIX) + 4 :].encode() not in database_bytes(
        service
    )
    assert view["hint"] == token[: len(PREFIX) + 4] and view["status"] == "active"


def test_check_scopes_names_and_validation(service):
    tokens = service.tokens
    school, _ = tokens.create("学校电脑", ["mcp"])
    phone, _ = tokens.create(" iPhone ", ["app", "health_upload", "app"])
    grant = tokens.check(phone)
    assert grant.name == "iPhone" and grant.scopes == {"app", "health_upload"}
    assert tokens.check(school).scopes == {"mcp"}
    assert tokens.check(school + "x") is None and tokens.check(MAIN) is None
    with pytest.raises(ValueError, match="已经有一个有效的令牌"):
        tokens.create("学校电脑", ["app"])
    for name, scopes, days in (
        ("", ["app"], None),
        ("x" * 41, ["app"], None),
        ("a\nb", ["app"], None),
        ("dev_123", ["app"], None),
        ("ok", [], None),
        ("ok", ["admin"], None),
        ("ok", ["app"], 0),
        ("ok", ["app"], 3651),
    ):
        with pytest.raises(ValueError):
            tokens.create(name, scopes, days)
    assert [v["name"] for v in tokens.list()] == ["学校电脑", "iPhone"]


def test_revoke_by_name_or_id_and_reuse_the_name(service):
    tokens = service.tokens
    first, view = tokens.create("学校电脑", ["mcp"])
    assert tokens.revoke("学校电脑")["status"] == "revoked"
    assert tokens.check(first) is None
    assert tokens.revoke(view["id"])["status"] == "revoked"  # again: no change, no error
    with pytest.raises(KeyError):
        tokens.revoke("学校电脑")  # no active token of that name any more
    second, again = tokens.create("学校电脑", ["mcp"])
    assert tokens.check(second).token_id == again["id"] != view["id"]
    assert [v["id"] for v in tokens.list()] == [again["id"]]
    assert [v["status"] for v in tokens.list(include_revoked=True)] == ["revoked", "active"]
    with pytest.raises(KeyError):
        tokens.revoke("dev_000000000000")


def test_expiry_and_throttled_last_use(service):
    clock = Clock()
    tokens = DeviceTokens(service.db, clock)
    token, view = tokens.create("快捷指令", ["health_upload"], days=30)
    assert view["expires_at"] == (clock.now + timedelta(days=30)).isoformat()

    def last_used():
        return tokens.list()[0]["last_used_at"]

    tokens.check(token)
    first = last_used()
    assert first == clock.now.isoformat()
    clock.now += timedelta(seconds=30)
    tokens.check(token)
    assert last_used() == first  # not written on every request
    clock.now += timedelta(seconds=31)
    tokens.check(token)
    assert last_used() == clock.now.isoformat()
    clock.now += timedelta(days=30)
    assert tokens.check(token) is None
    assert tokens.list()[0]["status"] == "expired"


def test_routes_and_scopes():
    assert required_scopes("POST", "/mcp") == {"mcp"}
    assert required_scopes("GET", "/internal/tools") == {"mcp"}
    assert required_scopes("POST", "/internal/tools/todos_add") == {"mcp"}
    assert required_scopes("POST", "/api/v1/health/samples") == {"app", "health_upload"}
    assert required_scopes("GET", "/api/v1/health/reports") == {"app"}
    assert required_scopes("GET", "/openapi.json") == {"app"}
    assert required_scopes("GET", "/mcpx") == {"app"}  # anything not MCP is App API
    assert required_scopes("GET", "/api/v2/new") == {"app"}


def call(client, token, method, path, **kwargs):
    headers = dict(kwargs.pop("headers", {}), Authorization=f"Bearer {token}")
    return client.request(method, path, headers=headers, **kwargs)


def test_each_scope_reaches_only_its_routes(service):
    school, _ = service.tokens.create("学校电脑", ["mcp"])
    phone, _ = service.tokens.create("iPhone", ["app"])
    shortcut, _ = service.tokens.create("快捷指令", ["health_upload"])
    with TestClient(create_app(service=service)) as client:

        def status(token, method, path, **kwargs):
            return call(client, token, method, path, **kwargs).status_code

        mcp = {"headers": ACCEPT, "json": INITIALIZE}
        for token in (MAIN, school):
            assert status(token, "POST", "/mcp", **mcp) == 200
            assert status(token, "GET", "/internal/tools") == 200
        for token in (MAIN, phone):
            assert status(token, "GET", "/api/v1/status") == 200
            assert status(token, "GET", "/openapi.json") == 200
            assert status(token, "POST", "/api/v1/health/samples", json=SAMPLE) == 200
        assert status(shortcut, "POST", "/api/v1/health/samples", json=SAMPLE) == 200

        denied = call(client, school, "GET", "/api/v1/today")
        assert denied.status_code == 403 and denied.json() == {"detail": "insufficient_scope"}
        assert denied.headers["www-authenticate"] == 'Bearer error="insufficient_scope"'
        for token, method, path in (
            (school, "GET", "/api/v1/status"),
            (school, "POST", "/api/v1/health/samples"),
            (school, "GET", "/openapi.json"),
            (phone, "POST", "/mcp"),
            (phone, "GET", "/internal/tools"),
            (shortcut, "GET", "/api/v1/status"),
            (shortcut, "GET", "/api/v1/health/reports"),
            (shortcut, "POST", "/mcp"),
        ):
            assert status(token, method, path) == 403, (token, path)
        assert status("a24_unknown", "GET", "/api/v1/status") == 401
        assert status("Bearer", "GET", "/api/v1/status") == 401
        bad = client.get("/api/v1/status", headers={"Authorization": b"Bearer \xff"})
        assert bad.status_code == 401
    assert all(v["last_used_at"] for v in service.tokens.list())


def test_revoking_from_another_process_needs_no_restart(service):
    token, _ = service.tokens.create("学校电脑", ["mcp", "app"])
    with TestClient(create_app(service=service)) as client:
        assert call(client, token, "GET", "/api/v1/status").status_code == 200
        DeviceTokens(service.db).revoke("学校电脑")  # what the CLI does, on the same file
        assert call(client, token, "GET", "/api/v1/status").status_code == 401
        assert call(client, MAIN, "GET", "/api/v1/status").status_code == 200


def test_remote_mcp_with_a_device_token(tmp_path):
    service = service_with(tmp_path, mcp_allowed_hosts=[REMOTE])
    school, _ = service.tokens.create("学校电脑", ["mcp"])
    phone, _ = service.tokens.create("iPhone", ["app"])
    assert post(service, REMOTE, token=school).status_code == 200
    assert post(service, REMOTE, token=phone).status_code == 403
    assert post(service, "evil.example", token=school).status_code == 421


def test_cli_create_list_revoke(service, capsys):
    folder = str(service.settings.data_dir)
    assert cli.main(["--data-dir", folder, "create", "学校电脑", "--scope", "mcp"]) == 0
    printed = capsys.readouterr().out
    token = next(line for line in printed.splitlines() if line.startswith(PREFIX))
    assert printed.count(token) == 1 and "只显示这一次" in printed
    assert service.tokens.check(token).scopes == {"mcp"}

    assert cli.main(["--data-dir", folder, "create", "iPhone", "--scope", "app,health_upload",
                     "--days", "90", "--quiet"]) == 0  # fmt: skip
    quiet = capsys.readouterr().out.strip()
    assert quiet.startswith(PREFIX) and service.tokens.check(quiet).name == "iPhone"

    assert cli.main(["--data-dir", folder, "list"]) == 0
    listing = capsys.readouterr().out
    assert "学校电脑" in listing and "app,health_upload" in listing and "有效" in listing
    assert token not in listing and quiet not in listing

    assert cli.main(["--data-dir", folder, "revoke", "学校电脑"]) == 0
    assert "已作废" in capsys.readouterr().out
    assert service.tokens.check(token) is None
    assert cli.main(["--data-dir", folder, "list", "--all"]) == 0
    assert "已作废" in capsys.readouterr().out

    assert cli.main(["--data-dir", folder, "revoke", "学校电脑"]) == 1
    assert cli.main(["--data-dir", folder, "create", "x", "--scope", "admin"]) == 1
    assert cli.main(["--data-dir", str(service.settings.data_dir / "nope"), "list"]) == 1
    errors = capsys.readouterr().err
    assert "没有找到令牌" in errors and "权限只能是" in errors and "找不到数据库" in errors
