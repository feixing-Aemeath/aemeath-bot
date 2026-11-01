"""Streamable HTTP MCP for Agents on other devices (through Tailscale), next to local stdio."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.api import create_app
from core.config import Settings
from core.service import Service

REMOTE = "mac-mini.tail1234.ts.net"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "school-pc", "version": "1"},
    },
}
ACCEPT = {"Accept": "application/json, text/event-stream"}


def service_with(tmp_path, **settings):
    return Service(
        Settings(
            token="test-token-000000000000000000000000",
            data_dir=tmp_path / "state",
            scheduler_enabled=False,
            _env_file=None,
            **settings,
        ),
        platform="linux",
    )


def post(service, host, token="test-token-000000000000000000000000", body=INITIALIZE):
    headers = dict(ACCEPT, Host=host)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    with TestClient(create_app(service=service)) as client:
        return client.post("/mcp", headers=headers, json=body)


def test_remote_host_needs_to_be_configured(tmp_path):
    service = service_with(tmp_path)
    assert post(service, REMOTE).status_code == 421  # DNS rebinding protection
    assert post(service, "127.0.0.1:8765").status_code == 200  # local stays as before


def test_configured_tailscale_name_works_with_the_token(tmp_path):
    service = service_with(tmp_path, mcp_allowed_hosts=f"{REMOTE.upper()}, other.example")
    response = post(service, REMOTE)  # `tailscale serve` on 443 keeps the plain name
    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"] == "assistant"
    assert post(service, f"{REMOTE}:8765").status_code == 200  # bound to the Tailscale IP
    listed = post(
        service, REMOTE, body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    assert len(listed.json()["result"]["tools"]) == 12
    assert post(service, "evil.example").status_code == 421


def test_remote_requests_always_need_the_bearer_token(tmp_path):
    service = service_with(tmp_path, mcp_allowed_hosts=[REMOTE])
    assert post(service, REMOTE, token=None).status_code == 401
    assert post(service, REMOTE, token="wrong").status_code == 401
    with TestClient(create_app(service=service), base_url=f"https://{REMOTE}") as client:
        assert client.get("/internal/tools").status_code == 401
        assert client.get("/api/v1/status").status_code == 401


def test_explicit_ports_are_kept(tmp_path):
    service = service_with(tmp_path, mcp_allowed_hosts=f"{REMOTE}:8443")
    assert post(service, f"{REMOTE}:8443").status_code == 200
    assert post(service, f"{REMOTE}:9999").status_code == 421


@pytest.mark.parametrize(
    "value", ["*", "*.ts.net", f"https://{REMOTE}", f"{REMOTE}/mcp", f"user@{REMOTE}", "a b/c"]
)
def test_only_plain_host_names_are_accepted(tmp_path, value):
    with pytest.raises(ValidationError, match="invalid host name"):
        service_with(tmp_path, mcp_allowed_hosts=value)


def test_hosts_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_MCP_ALLOWED_HOSTS", f"{REMOTE},mac-mini")
    settings = Settings(token="test-token-000000000000000000000000", _env_file=None)
    assert settings.mcp_allowed_hosts == [REMOTE, "mac-mini"]
    monkeypatch.setenv("ASSISTANT_MCP_ALLOWED_HOSTS", "")
    assert (
        Settings(token="test-token-000000000000000000000000", _env_file=None).mcp_allowed_hosts
        == []
    )
