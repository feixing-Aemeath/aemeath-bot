"""Claude's and Codex's 5-hour and weekly limits (plugins/token_usage/limits.py), with fakes:
a scripted app-server, rollout files in a temporary folder, a fake Keychain and HTTP."""

import json
import os
from datetime import datetime
from functools import partial
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.models import PluginPatch
from plugins.token_usage import limits
from plugins.token_usage.limits import (
    LimitError,
    codex_live,
    limit_cards,
    read_claude,
    read_codex,
    update,
)
from plugins.token_usage.limits import (
    renew_claude as real_renew_claude,  # conftest fakes limits.renew_claude
)
from tests.test_api import client_for

ZONE = ZoneInfo("Asia/Shanghai")
NOW = int(datetime(2026, 10, 24, 12, 0, tzinfo=ZONE).timestamp())


def at(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=ZONE).timestamp())


SNAPSHOT = {
    "limitId": "codex",
    "primary": {"usedPercent": 1, "windowDurationMins": 300, "resetsAt": at("2026-10-24T14:20")},
    "secondary": {
        "usedPercent": 7,
        "windowDurationMins": 10080,
        "resetsAt": at("2026-10-29T08:00"),
    },
    "planType": "plus",
}


def fake_app_server(tmp_path, answer=True):
    """A `codex` that speaks just enough of the app-server protocol (or never answers)."""
    script = tmp_path / "app_server.py"
    script.write_text(
        "import json, sys\n"
        f"ANSWER, SNAPSHOT = {answer!r}, {json.dumps(SNAPSHOT)!r}\n"
        "assert sys.argv[1:] == ['app-server']\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if not ANSWER:\n"
        "        continue\n"
        "    if message.get('method') == 'initialize':\n"
        "        print(json.dumps({'id': message['id'], 'result': {}}), flush=True)\n"
        "    elif message.get('method') == 'account/rateLimits/read':\n"
        "        print(json.dumps({'method': 'account/rateLimits/updated'}), flush=True)\n"
        "        result = {'rateLimits': json.loads(SNAPSHOT), 'accountId': 'acct-1'}\n"
        "        print(json.dumps({'id': message['id'], 'result': result}), flush=True)\n",
        encoding="utf-8",
    )
    return str(script)


def rollout(folder, name, timestamp, used):
    path = folder / "2026" / "10" / "23" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "primary": {"used_percent": used, "window_minutes": 300, "resets_at": NOW + 3600},
        "secondary": {"used_percent": 3.0, "window_minutes": 10080, "resets_at": NOW + 86400},
        "plan_type": "plus",
    }
    lines = [
        {"timestamp": timestamp, "type": "session_meta", "payload": {"cwd": "/private"}},
        {"timestamp": timestamp, "type": "event_msg",
         "payload": {"type": "token_count", "info": None, "rate_limits": snapshot}},
    ]  # fmt: skip
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def test_codex_limits_come_live_from_the_app_server(tmp_path):
    live = partial(codex_live, executable=fake_app_server(tmp_path))
    entry = read_codex([], live=live, now=lambda: NOW)
    assert entry["status"] == "ok" and entry["source"] == "live" and entry["plan"] == "Plus"
    assert [(w["name"], w["used_percent"], w["minutes"]) for w in entry["windows"]] == [
        ("5 小时", 1.0, 300),
        ("本周", 7.0, 10080),
    ]
    assert entry["as_of"] == NOW and "acct-1" not in json.dumps(entry)


def test_a_silent_or_missing_app_server_fails(tmp_path, monkeypatch):
    with pytest.raises(LimitError, match="codex_timeout"):
        codex_live(executable=fake_app_server(tmp_path, answer=False), timeout=0.5)
    monkeypatch.setattr(limits, "find_command", lambda name: None)
    with pytest.raises(LimitError, match="codex_not_found"):
        codex_live()


def test_codex_falls_back_to_the_newest_session_file(tmp_path):
    older = rollout(tmp_path, "rollout-a.jsonl", "2026-10-23T01:00:00.000Z", 50.0)
    newer = rollout(tmp_path, "rollout-b.jsonl", "2026-10-23T03:00:00.000Z", 33.0)
    os.utime(older, (NOW - 7200, NOW - 7200))
    os.utime(newer, (NOW, NOW))

    def broken():
        raise LimitError("codex_timeout")

    entry = read_codex([str(tmp_path)], live=broken)
    assert entry["source"] == "session" and entry["error"] == "codex_timeout"
    assert entry["windows"][0]["used_percent"] == 33.0
    assert entry["as_of"] == int(datetime.fromisoformat("2026-10-23T03:00:00+00:00").timestamp())
    with pytest.raises(LimitError, match="codex_timeout"):
        read_codex([str(tmp_path / "empty")], live=broken)


def keychain(token="sk-ant-oat01-secret", expires=NOW + 3600, plan="max"):
    login = {"accessToken": token, "expiresAt": expires * 1000, "subscriptionType": plan}
    return lambda: json.dumps({"claudeAiOauth": login})


USAGE = {
    "five_hour": {"utilization": 33.0, "resets_at": "2026-10-24T09:00:00.123456+00:00"},
    "seven_day": {"utilization": 6, "resets_at": "2026-10-29T00:00:00+00:00"},
    "seven_day_opus": {"utilization": 12.5, "resets_at": None},
    "seven_day_sonnet": {"utilization": None, "resets_at": None},
    "extra_usage": {"is_enabled": False},
    "something_new": 3,
}


def test_claude_limits_use_claude_codes_login(tmp_path):
    asked = []

    def get(url, headers):
        asked.append((url, headers))
        return 200, USAGE

    entry = read_claude(secret=keychain(), get=get, now=lambda: NOW)
    assert asked == [
        (
            "https://api.anthropic.com/api/oauth/usage",
            {"Authorization": "Bearer sk-ant-oat01-secret", "anthropic-beta": "oauth-2025-04-20"},
        )
    ]
    assert entry["plan"] == "Max" and entry["source"] == "live"
    assert [(w["name"], w["used_percent"]) for w in entry["windows"]] == [
        ("5 小时", 33.0),
        ("本周", 6.0),
        ("本周 · Opus", 12.5),
    ]
    assert entry["windows"][0]["resets_at"] == at("2026-10-24T17:00")
    assert "secret" not in json.dumps(entry)  # the token is never kept


def test_claude_without_a_usable_login(tmp_path):
    def never(*_):
        raise AssertionError("no request without a valid token")

    assert read_claude(secret=lambda: None, get=never)["status"] == "login_needed"
    assert read_claude(secret=lambda: "not json", get=never)["status"] == "login_needed"
    expired = read_claude(secret=keychain(expires=NOW - 1), get=never, now=lambda: NOW)
    assert expired == {"status": "expired", "plan": "Max"}
    rejected = read_claude(secret=keychain(), get=lambda *_: (401, None), now=lambda: NOW)
    assert rejected["status"] == "expired"
    with pytest.raises(LimitError, match="claude_http_500"):
        read_claude(secret=keychain(), get=lambda *_: (500, None), now=lambda: NOW)


def test_an_expired_claude_login_is_renewed_by_running_claude_once(monkeypatch):
    clock = [1_000_000.0]
    logins = iter([{"status": "expired"}, {"status": "ok", "windows": []}])
    renewals = []

    def renew():
        renewals.append(clock[0])
        return True

    entry, renewed = limits.read_claude_renewing(
        None, read=lambda: next(logins), renew=renew, now=lambda: clock[0]
    )
    assert entry["status"] == "ok" and renewed == clock[0] and len(renewals) == 1
    # Still expired ten minutes later (renewing failed): no new try within half an hour.
    clock[0] += 600
    entry, again = limits.read_claude_renewing(
        renewed, read=lambda: {"status": "expired"}, renew=renew, now=lambda: clock[0]
    )
    assert entry["status"] == "expired" and again == renewed and len(renewals) == 1
    clock[0] += limits.RENEW_EVERY
    limits.read_claude_renewing(
        renewed, read=lambda: {"status": "expired"}, renew=lambda: False, now=lambda: clock[0]
    )
    # A live login is read as it is.
    assert limits.read_claude_renewing(None, read=lambda: {"status": "ok"}, renew=renew)[1] is None


def test_renewing_runs_the_smallest_claude_request(monkeypatch):
    monkeypatch.setattr(limits, "find_command", lambda name: f"/bin/{name}")
    calls = []

    def run(command, **options):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    assert real_renew_claude(run=run) is True
    assert calls[0][:3] == ["/bin/claude", "-p", "ok"]
    assert {"--tools", "--strict-mcp-config", "--no-session-persistence"} <= set(calls[0])
    monkeypatch.setattr(limits, "find_command", lambda name: None)
    assert real_renew_claude(run=run) is False


def test_a_failed_read_keeps_the_last_numbers():
    def live():
        return {"rateLimits": SNAPSHOT}

    good = update(None, lambda: read_codex([], live=live, now=lambda: NOW), lambda: NOW)
    assert good["status"] == "ok" and good["checked_at"] == NOW

    def broken():
        raise LimitError("codex_timeout")

    later = update(good, broken, lambda: NOW + 600)
    assert later["status"] == "failed" and later["stale"] and later["checked_at"] == NOW + 600
    assert later["windows"] == good["windows"] and later["as_of"] == NOW
    assert update(None, broken, lambda: NOW) == {
        "status": "failed",
        "error": "codex_timeout",
        "checked_at": NOW,
    }


def test_cards_say_how_much_is_used_and_when_it_resets():
    codex = read_codex([], live=lambda: {"rateLimits": SNAPSHOT}, now=lambda: NOW)
    claude = read_claude(
        secret=keychain(), get=lambda *_: (200, USAGE), now=lambda: NOW
    )  # fmt: skip
    cards = limit_cards({"codex": codex, "claude": claude}, ZONE, NOW)
    assert [c["title"] for c in cards] == ["Claude 用量限额 · Max", "Codex 用量限额 · Plus"]
    assert all(c["type"] == "list" for c in cards)
    assert cards[1]["data"]["items"] == [
        {
            "title": "5 小时",
            "subtitle": "14:20 重置",
            "detail": "已用 1%",
            "progress": {"value": 1.0, "max": 100},
        },
        {
            "title": "本周",
            "subtitle": "周四 08:00 重置",
            "detail": "已用 7%",
            "progress": {"value": 7.0, "max": 100},
        },
    ]
    claude_items = cards[0]["data"]["items"]
    assert claude_items[0] == {
        "title": "5 小时",
        "subtitle": "17:00 重置",
        "detail": "已用 33%",
        "progress": {"value": 33.0, "max": 100},
    }
    assert claude_items[2] == {
        "title": "本周 · Opus",
        "subtitle": None,
        "detail": "已用 12.5%",
        "progress": {"value": 12.5, "max": 100},
    }
    assert claude_items[1]["subtitle"] == "周四 08:00 重置"  # within a week: the weekday
    assert limits.clock(at("2026-11-03T09:30"), ZONE, NOW) == "11月3日 09:30"


def test_cards_for_logins_failures_and_old_numbers():
    tomorrow = {
        "status": "ok", "source": "session", "plan": "Plus", "as_of": NOW - 3600,
        "windows": [{"name": "5 小时", "used_percent": 50.0, "minutes": 300,
                     "resets_at": at("2026-10-25T01:00")}],
    }  # fmt: skip
    cards = limit_cards(
        {
            "claude": {"status": "login_needed", "checked_at": NOW},
            "codex": tomorrow,
        },
        ZONE,
        NOW,
    )
    assert cards[0]["data"]["items"] == [
        {
            "title": "还没登录 Claude Code",
            "subtitle": "在终端运行 claude 登录后，这里显示 5 小时和每周限额",
        }
    ]
    assert cards[1]["data"]["items"] == [
        {
            "title": "5 小时",
            "subtitle": "明天 01:00 重置",
            "detail": "已用 50%",
            "progress": {"value": 50.0, "max": 100},
        },
        {"title": "来自 11:00 的数", "subtitle": "实时读取失败，用的是 Codex 会话记录"},
    ]
    stale = {**tomorrow, "status": "failed", "error": "codex_not_found", "stale": True}
    stale["windows"] = [{**tomorrow["windows"][0], "resets_at": NOW - 60}]
    items = limit_cards({"codex": stale}, ZONE, NOW)[0]["data"]["items"]
    assert items[0] == {
        "title": "5 小时",
        "subtitle": "新的用量等下次读取",
        "detail": "已重置",
        "progress": {"value": 0, "max": 100},
    }
    assert items[1] == {"title": "这次没读到", "subtitle": "本机找不到 codex；上面是 11:00 的数"}
    assert limit_cards({}, ZONE, NOW) == []


async def test_plugin_reads_limits_every_ten_minutes_and_shows_them_first(service, monkeypatch):
    monkeypatch.setenv("HOME", str(service.settings.data_dir))
    monkeypatch.setattr(
        limits, "read_codex", lambda dirs: read_codex(dirs, live=lambda: {"rateLimits": SNAPSHOT})
    )
    monkeypatch.setattr(limits, "read_claude", lambda: {"status": "login_needed"})
    with client_for(service) as client:  # starts the service, which finds the plugins
        assert (
            client.patch("/api/v1/plugins/token_usage", json={"enabled": True}).status_code == 200
        )
    await service.plugins.patch("token_usage", PluginPatch(enabled=True))
    plugin = service.plugins.instances["token_usage"]
    assert plugin.jobs()["limits"]["cron"] == "*/10 * * * *"
    await plugin.refresh_limits()
    titles = [c["title"] for c in await plugin.cards()]
    assert titles[:2] == ["Claude 用量限额", "Codex 用量限额 · Plus"]
    assert "总共使用的 token" not in titles[:2]

    plugin.config = {**plugin.config, "read_limits": False}
    assert "limits" not in plugin.jobs()
    assert not any("用量限额" in c["title"] for c in await plugin.cards())


def test_a_nearly_used_window_is_drawn_in_orange_then_red():
    assert limits.bar(50) == {"value": 50, "max": 100}
    assert limits.bar(82)["tone"] == "warn" and limits.bar(97)["tone"] == "alert"
    assert limits.bar(130) == {"value": 100, "max": 100, "tone": "alert"}
