from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from core.api import create_app

PATH = "/api/v1/app/bot-preferences"
ENTRY = {
    "pinned": True,
    "custom_name": "助手" * 10,
    "position": 0,
    "visible": False,
    "updated_at": "2026-11-18T10:00:00.123456+08:00",
}


def app_client(service):
    token, _ = service.tokens.create("测试设备", ["app"])
    return TestClient(create_app(service=service), headers={"Authorization": "Bearer " + token})


def test_versioned_preferences_and_concurrent_writes(service):
    client = app_client(service)
    assert client.get(PATH).json() == {"version": 0, "entries": {}}

    def write(bot):
        return client.put(PATH, json={"expected_version": 0, "entries": {bot: ENTRY}})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = sorted(pool.map(write, ["health", "assistant"]), key=lambda r: r.status_code)
    assert [r.status_code for r in responses] == [200, 409]
    first = responses[0].json()
    assert first == responses[1].json() == client.get(PATH).json()
    assert first["version"] == 1
    assert list(first["entries"].values()) == [
        ENTRY | {"updated_at": "2026-11-18T02:00:00.123456Z"}
    ]
    assert client.put(PATH, json={"expected_version": 1, "entries": {}}).json() == {
        "version": 2,
        "entries": {},
    }


def test_preferences_require_app_scope(service):
    client = TestClient(create_app(service=service))
    for method in ("GET", "PUT"):
        assert client.request(method, PATH).status_code == 401
        for scope in ("mcp", "health_upload", "dav"):
            token, _ = service.tokens.create(f"{method}-{scope}", [scope])
            assert (
                client.request(
                    method, PATH, headers={"Authorization": "Bearer " + token}
                ).status_code
                == 403
            )


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"expected_version": -1, "entries": {}},
        {"expected_version": 0, "entries": {}, "extra": True},
        *({"expected_version": 0, "entries": {k: ENTRY}} for k in ("../a", "A", "a" * 65)),
        {"expected_version": 0, "entries": {"a": ENTRY | {"custom_name": "字" * 21}}},
        {"expected_version": 0, "entries": {"a": ENTRY | {"updated_at": "2026-11-18T10:00:00"}}},
        {"expected_version": 0, "entries": {"a": ENTRY | {"position": 2**63}}},
        {"expected_version": 0, "entries": {"a": ENTRY | {"extra": True}}},
        {"expected_version": 0, "entries": {f"a{i}": ENTRY for i in range(101)}},
    ],
)
def test_preferences_validation(service, invalid):
    client = app_client(service)
    assert client.put(PATH, json=invalid).status_code == 422
    assert client.get(PATH).json() == {"version": 0, "entries": {}}
