import json
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from scripts import feishu_oauth
from scripts.demo import run_demo


async def test_offline_demo(tmp_path):
    result = await run_demo(tmp_path)
    assert result["todos"] == result["reminders"] == 1
    assert result["digests"] == 2 and result["merged_sources"] == 2
    assert result["fallback_verified"] and result["unprocessed"] == 0


def test_oauth_callback_state_and_one_time_consumption(service, monkeypatch, capsys):
    monkeypatch.setattr(feishu_oauth, "Settings", lambda: service.settings)
    monkeypatch.setenv("FEISHU_APP_ID", "synthetic-app")
    redirect = "http://127.0.0.1:8766/callback"
    monkeypatch.setattr(sys, "argv", ["oauth", "start", "--redirect-uri", redirect])
    feishu_oauth.main()
    authorization = capsys.readouterr().out.strip()
    state = parse_qs(urlsplit(authorization).query)["state"][0]
    called = []

    class Stub:
        def __init__(self, store):
            self.store = store

        def oauth(self, body):
            called.append(body)

    monkeypatch.setattr(feishu_oauth, "OfficialFeishu", Stub)
    bad_url = redirect + "?" + urlencode({"code": "fake-code", "state": "wrong"})
    monkeypatch.setattr(sys, "argv", ["oauth", "finish", "--callback-url", bad_url])
    with pytest.raises(SystemExit, match="state invalid"):
        feishu_oauth.main()
    assert called == []
    good_url = redirect + "?" + urlencode({"code": "fake-code", "state": state})
    monkeypatch.setattr(sys, "argv", ["oauth", "finish", "--callback-url", good_url])
    feishu_oauth.main()
    assert called[0]["grant_type"] == "authorization_code"
    assert json.loads(capsys.readouterr().out)["authorized"] is True
    with pytest.raises(SystemExit, match="state invalid"):
        feishu_oauth.main()
    assert len(called) == 1


def test_http_entrypoint_is_single_worker_and_no_access_logs(service, monkeypatch):
    import core.__main__ as entry

    monkeypatch.setattr(entry, "Settings", lambda: service.settings)
    monkeypatch.setattr(entry, "create_app", lambda settings: "test-app")
    calls = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    entry.main()
    assert calls[0]["workers"] == 1
    assert calls[0]["access_log"] is False
    assert calls[0]["host"] == "127.0.0.1"
