"""doctor.py on a simulated Mac: canned command outputs, no real system calls."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path, PurePosixPath

import pytest

from core.config import Settings
from scripts.doctor import (
    PERMISSIONS,
    Result,
    SystemProbe,
    check_environment,
    icloud_state,
    pmset_values,
    render,
)

USER = "aimis"
PYTHON = "/Users/aimis/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/bin/python3.12"
PMSET_GOOD = """System-wide power settings:
Currently in use:
 standby              0
 Sleep On Power Button 1
 autorestart          1
 hibernatefile        /var/vm/sleepimage
 disksleep            0
 sleep                0 (sleep prevented by powerd)
 displaysleep         0
"""
SERVE_GOOD = {
    "TCP": {"443": {"HTTPS": True}},
    "Web": {
        "mini.example-tailnet.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}
    },
}
DISPLAYS = {
    "SPDisplaysDataType": [{"sppci_model": "Apple M6", "spdisplays_ndrvs": [{"_name": "Dummy"}]}]
}
MAC_ONLY = {
    "doc_text",
    "photo_export",
    "homebrew",
    "xcode_clt",
    "power_restore",
    "system_sleep",
    "display_awake",
    "auto_login",
    "filevault",
    "lock_screen",
    "screensaver",
    "display_attached",
    "launchd",
    "qq_login",
    "wechat_login",
    "icloud",
    *(f"permission:{name}" for name in PERMISSIONS),
}


class FakeProbe:
    def __init__(self, platform="darwin", **overrides):
        self.platform, self.home, self.uid, self.user = (
            platform,
            PurePosixPath("/Users/aimis"),
            501,
            USER,
        )
        self.commands = {
            (
                "/Users/aimis/Library/Application Support/AemeathBot/bin/photo-export",
                "status",
            ): Result(0, '{"permission":"authorized"}'),
            ("xcode-select", "-p"): Result(0, "/Library/Developer/CommandLineTools\n"),
            ("pmset", "-g"): Result(0, PMSET_GOOD),
            (
                "defaults",
                "read",
                "/Library/Preferences/com.apple.loginwindow",
                "autoLoginUser",
            ): Result(0, USER + "\n"),
            ("fdesetup", "status"): Result(0, "FileVault is Off.\n"),
            ("sysadminctl", "-screenLock", "status"): Result(
                0, "", "2026-11-12 09:00:00.000 sysadminctl[812:4410] screenLock is off\n"
            ),
            ("defaults", "-currentHost", "read", "com.apple.screensaver", "idleTime"): Result(
                0, "0\n"
            ),
            ("system_profiler", "SPDisplaysDataType", "-json"): Result(0, json.dumps(DISPLAYS)),
            ("/opt/homebrew/bin/claude", "auth", "status", "--json"): Result(
                0, '{"loggedIn": true, "authMethod": "claude.ai"}'
            ),
            ("/opt/homebrew/bin/codex", "login", "status"): Result(0, "Logged in using ChatGPT\n"),
            ("/usr/local/bin/tailscale", "status", "--json"): Result(
                0, '{"BackendState": "Running"}'
            ),
            ("/usr/local/bin/tailscale", "serve", "status", "--json"): Result(
                0, json.dumps(SERVE_GOOD)
            ),
            ("launchctl", "print", "gui/501/local.assistant"): Result(
                0, "gui/501/local.assistant = {\n\tstate = running\n\tpid = 4242\n}\n"
            ),
            ("pgrep", "-x", "QQ"): Result(0, "777\n"),
            ("pgrep", "-x", "WeChat"): Result(1, ""),
            ("defaults", "read", "MobileMeAccounts", "Accounts"): Result(
                0, '(\n    {\n        AccountID = "someone@example.com";\n    }\n)\n'
            ),
        }
        self.tools = {
            "brew": "/opt/homebrew/bin/brew",
            "uv": "/opt/homebrew/bin/uv",
            "claude": "/opt/homebrew/bin/claude",
            "codex": "/opt/homebrew/bin/codex",
            "tailscale": "/usr/local/bin/tailscale",
        }
        self.http = {
            "http://127.0.0.1:8765/api/v1/status": 200,
            "http://127.0.0.1:1234/v1/models": 200,
        }
        self.tcc = {service: {PYTHON} for service, _, _ in PERMISSIONS.values()}
        self.paths = {
            "/Applications/QQ.app",
            "/Applications/WeChat.app",
            str(self.home / "Library/Application Support/AemeathBot/bin/doc-text"),
            str(self.home / "Library/Application Support/AemeathBot/bin/photo-export"),
        }
        self.backup_age_hours = 3
        for key, value in overrides.items():
            setattr(self, key, value)

    def which(self, name, extra=()):
        return self.tools.get(name)

    def run(self, args, timeout=20):
        args = tuple(str(a) for a in args)
        if args[:2] == ("git", "-C"):
            return Result(0)
        return self.commands.get(args)

    def exists(self, path):
        return str(path) in self.paths

    def realpath(self, path):
        return PYTHON if Path(path).as_posix().endswith(".venv/bin/python") else None

    def http_status(self, url, token=None, timeout=3):
        return self.http.get(url)

    def tcc_grants(self, database, service):
        return self.tcc if self.tcc is None else self.tcc.get(service, set())

    def newest_backup(self, data_dir):
        return None if self.backup_age_hours is None else 1_000_000 - self.backup_age_hours * 3600

    def now(self):
        return 1_000_000


@pytest.fixture
def backend(tmp_path):
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=synthetic\n", encoding="utf-8")
    (tmp_path / ".env").chmod(0o600)
    return tmp_path


@pytest.fixture
def settings(tmp_path):
    return Settings(token="d" * 40, data_dir=tmp_path / "state", _env_file=None)


def by_name(report):
    return {check["name"]: check for check in report["checks"]}


def test_well_configured_mac_is_ready(backend, settings):
    report = check_environment(FakeProbe(), backend, settings)
    checks = by_name(report)
    assert report["ready"] is True
    for name in (
        "power_restore", "system_sleep", "display_awake", "auto_login", "filevault",
        "lock_screen", "screensaver", "display_attached", "claude", "codex", "tailscale",
        "tailscale_serve", "launchd", "backend_api", "secrets", "backups", "local_model",
        *(f"permission:{name}" for name in PERMISSIONS),
    ):  # fmt: skip
        assert checks[name]["status"] == "ok", (name, checks[name])
    assert "mini.example-tailnet.ts.net" in checks["tailscale_serve"]["detail"]
    # Things a script cannot see stay with the user, and say so.
    assert checks["qq_login"]["status"] == "manual"
    assert checks["icloud"]["status"] == "manual"
    assert "someone@example.com" not in json.dumps(report)
    assert checks["apple_mode"]["status"] == "manual"  # still the fake reminders bridge
    assert report["verified"] is False
    assert checks["wechat_login"]["status"] == "fail" and checks["wechat_login"]["optional"]
    text = render(report)
    assert text.startswith("24 小时助手环境自检") and "需人工确认" in text
    assert "自动检查全部通过" in text


def test_problems_are_reported_with_fixes(backend, settings):
    probe = FakeProbe()
    probe.commands[("pmset", "-g")] = Result(0, PMSET_GOOD.replace("autorestart          1", "autorestart          0").replace("displaysleep         0", "displaysleep         10"))  # fmt: skip
    probe.commands[("fdesetup", "status")] = Result(0, "FileVault is On.\n")
    probe.commands[("sysadminctl", "-screenLock", "status")] = Result(
        0, "", "sysadminctl[1:2] screenLock delay is immediate\n"
    )
    probe.commands[("defaults", "-currentHost", "read", "com.apple.screensaver", "idleTime")] = (
        Result(0, "1200\n")
    )
    probe.commands[("system_profiler", "SPDisplaysDataType", "-json")] = Result(
        0, json.dumps({"SPDisplaysDataType": [{"sppci_model": "Apple M6"}]})
    )
    probe.commands[("/opt/homebrew/bin/claude", "auth", "status", "--json")] = Result(
        1, '{"loggedIn": false}'
    )
    probe.commands[("/opt/homebrew/bin/codex", "login", "status")] = Result(1, "Not logged in\n")
    funnel = {**SERVE_GOOD, "AllowFunnel": {"mini.example-tailnet.ts.net:443": True}}
    probe.commands[("/usr/local/bin/tailscale", "serve", "status", "--json")] = Result(
        0, json.dumps(funnel)
    )
    probe.commands[("launchctl", "print", "gui/501/local.assistant")] = Result(113, "")
    probe.http["http://127.0.0.1:8765/api/v1/status"] = 401
    probe.tcc = None  # this terminal may not read the TCC database
    probe.backup_age_hours = 80
    (backend / ".env").chmod(0o644)

    report = check_environment(probe, backend, settings)
    checks = by_name(report)
    assert report["ready"] is False
    expected_failures = {
        "power_restore": "autorestart",
        "display_awake": "sudo pmset -a displaysleep 0",
        "lock_screen": "永不",
        "screensaver": "永不",
        "display_attached": "HDMI",
        "claude": "claude auth login",
        "codex": "codex login",
        "tailscale_serve": "tailscale funnel reset",
        "launchd": "install_mac.sh",
        "secrets": "chmod 600",
    }
    if __import__("os").name == "nt":
        expected_failures.pop("secrets")  # POSIX mode bits are not an NTFS ACL.
    for name, fix in expected_failures.items():
        assert checks[name]["status"] == "fail", name
        assert fix in checks[name]["fix"], name
    # FileVault on is the user's call, not a failure; automatic login then does not apply.
    assert checks["filevault"]["status"] == "manual" and "不间断电源" in checks["filevault"]["fix"]
    assert checks["auto_login"]["status"] == "not_applicable"
    assert "token" in checks["backend_api"]["detail"]
    assert checks["backups"]["status"] == "fail"
    for name in PERMISSIONS:
        assert checks[f"permission:{name}"]["status"] == "manual"
    assert "完全磁盘访问" in checks["permission:accessibility"]["detail"]
    text = render(report)
    assert "未通过" in text and "→ sudo pmset -a autorestart 1" in text


def test_missing_grant_names_the_binary(backend, settings):
    probe = FakeProbe()
    probe.tcc = {"kTCCServiceScreenCapture": {"/Applications/Utilities/Terminal.app"}}
    checks = by_name(check_environment(probe, backend, settings))
    assert checks["permission:screen_recording"]["status"] == "fail"
    assert PYTHON in checks["permission:screen_recording"]["fix"]
    assert "apple-sync" in checks["permission:reminders"]["fix"]


def test_linux_marks_mac_items_not_applicable(backend, settings):
    probe = FakeProbe(platform="linux")
    checks = by_name(check_environment(probe, backend, settings))
    for name in MAC_ONLY:
        assert checks[name]["status"] == "not_applicable", name
    for name in ("python", "sqlite_fts5", "uv", "claude", "codex", "backend_api", "local_model"):
        assert checks[name]["status"] == "ok", name


def test_invalid_configuration_is_a_failure(backend):
    report = check_environment(FakeProbe(platform="linux"), backend / "nowhere", None)
    checks = by_name(report)
    assert checks["configuration"]["status"] == "fail"
    assert checks["secrets"]["status"] == "fail"
    assert report["ready"] is False


def test_pmset_parsing():
    values = pmset_values(PMSET_GOOD)
    assert values == {
        "standby": 0,
        "autorestart": 1,
        "disksleep": 0,
        "sleep": 0,
        "displaysleep": 0,
    }


def test_tcc_reader_uses_a_read_only_sqlite_connection(tmp_path):
    database = tmp_path / "Application Support" / "TCC.db"
    database.parent.mkdir()
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("CREATE TABLE access (service TEXT, client TEXT, auth_value INTEGER)")
        conn.executemany(
            "INSERT INTO access VALUES (?,?,?)",
            [
                ("kTCCServicePhotos", "/bin/allowed", 2),
                ("kTCCServicePhotos", "/bin/limited", 3),
                ("kTCCServicePhotos", "/bin/denied", 0),
                ("kTCCServiceCalendar", "/bin/other", 2),
            ],
        )
        conn.commit()
    probe = SystemProbe()
    assert probe.tcc_grants(database, "kTCCServicePhotos") == {"/bin/allowed", "/bin/limited"}
    assert probe.tcc_grants(tmp_path / "missing.db", "kTCCServicePhotos") is None


class StatusProbe:
    def __init__(self, status=None, mobile_me=None):
        self.status, self.mobile_me = status, mobile_me

    def run(self, args, timeout=20):
        if args[-1] == "status":
            return Result(0, json.dumps(self.status)) if self.status else Result(1)
        return Result(0, self.mobile_me) if self.mobile_me else Result(1)


def test_icloud_is_judged_by_where_apple_sync_writes():
    def account(title, kind):
        return {"access": "granted", "default_account": {"title": title, "type": kind}}

    icloud = {"reminders": account("iCloud", "caldav"), "calendar": account("iCloud", "caldav")}
    status, detail, _ = icloud_state(StatusProbe(icloud), None)
    assert status == "ok" and "iCloud" in detail
    local = {"reminders": account("我的 Mac", "local"), "calendar": account("iCloud", "caldav")}
    assert icloud_state(StatusProbe(local), None)[0] == "fail"
    # Before apple-sync has access: the account preferences, which macOS 27 may not have.
    not_asked = {"reminders": {"access": "not_asked"}, "calendar": {"access": "not_asked"}}
    assert icloud_state(StatusProbe(not_asked, "AccountID = x"), None)[0] == "manual"
    assert icloud_state(StatusProbe(), None)[0] == "manual"
